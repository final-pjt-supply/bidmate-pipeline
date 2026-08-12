"""라이브 공고의 bid_table.ntce_kind_nm 소급 채움 (#122).

#122로 신규 유입분은 curated에 ntce_kind_nm이 포함되지만, 이미 적재된 행들은 이 컬럼이
전부 NULL이라 취소공고 노출 제외 판정을 할 수 없다. 이 스크립트는 각 행의 raw_s3_key가
가리키는 원본 JSON(공고 1건당 1파일)에서 ntceKindNm을 읽어 해당 행에 UPDATE한다.

대상은 라이브 공고(bid_clse_dt IS NULL 또는 미래)로 한정한다 — 마감 지난 공고는 어차피
조회 필터가 걸러서 취소 여부가 화면에 영향을 주지 않는다.

Example:
    python db/backfill_ntce_kind_nm.py                  # DRY_RUN 기본값 true — 계산/로그만
    DRY_RUN=false python db/backfill_ntce_kind_nm.py --limit 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

try:
    from apply_schema_to_rds import build_database_url, describe_target
    from list_curated_backfill_keys import create_s3_client
except ImportError:  # pragma: no cover - unittest가 repo root에서 import할 때 사용된다.
    from db.apply_schema_to_rds import build_database_url, describe_target
    from db.list_curated_backfill_keys import create_s3_client


DEFAULT_BUCKET = "bidmate"
DEFAULT_REGION = "ap-northeast-2"
DEFAULT_LIMIT = 0
DEFAULT_PROGRESS_EVERY = 50

# 라이브 공고 + 아직 안 채워진 행만. NOW()는 DB 서버 시각(KST naive 규약, 01_bid_table.sql).
SELECT_TARGETS_SQL = """
    SELECT bid_ntce_no, bid_ntce_ord, raw_s3_key
    FROM bid_table
    WHERE ntce_kind_nm IS NULL
      AND raw_s3_key IS NOT NULL
      AND (bid_clse_dt IS NULL OR bid_clse_dt >= NOW())
    ORDER BY bid_ntce_no, bid_ntce_ord
    LIMIT %(limit)s
"""

UPDATE_SQL = """
    UPDATE bid_table
    SET ntce_kind_nm = %(ntce_kind_nm)s
    WHERE bid_ntce_no = %(bid_ntce_no)s AND bid_ntce_ord = %(bid_ntce_ord)s
      AND ntce_kind_nm IS NULL
"""


def is_dry_run() -> bool:
    # 기본값 true — 실제 UPDATE는 DRY_RUN=false를 명시해야만 실행된다
    # (db/backfill_expected_file_count.py와 동일 관례).
    return os.environ.get("DRY_RUN", "true").strip().lower() != "false"


def fetch_targets(conn, limit: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(SELECT_TARGETS_SQL, {"limit": limit})
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def read_ntce_kind_nm(s3_client, bucket: str, raw_s3_key: str) -> tuple[str | None, str | None]:
    """(ntce_kind_nm, skip_reason). raw JSON은 공고 1건당 1파일이라 그대로 읽으면 된다."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=raw_s3_key)
        record = json.loads(response["Body"].read().decode("utf-8-sig"))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return None, f"fetch_failed:{code}"
    except (BotoCoreError, json.JSONDecodeError) as exc:
        return None, f"fetch_failed:{type(exc).__name__}"

    value = str(record.get("ntceKindNm") or "").strip()
    if not value:
        return None, "no_ntce_kind_nm"
    return value, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill bid_table.ntce_kind_nm for live bids from raw S3 JSON (#122).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"회당 처리 상한. 0이면 무제한. 기본값 {DEFAULT_LIMIT}.",
    )
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help=f"몇 건마다 진행 로그를 남길지. 기본값 {DEFAULT_PROGRESS_EVERY}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # SQL의 LIMIT은 파라미터라 0/None을 그대로 못 넘긴다 — 무제한이면 매우 큰 값으로 대체.
    sql_limit = args.limit if args.limit and args.limit > 0 else 10_000_000
    dry_run = is_dry_run()

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit(
            "psycopg is not installed. Run: python -m pip install -r requirement.txt",
        ) from exc

    try:
        database_url = build_database_url()
    except ValueError as exc:
        print(f"RDS configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print(f"target={describe_target(database_url)}")
    print(f"dry_run={dry_run}")

    s3 = create_s3_client(args.region)

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        targets = fetch_targets(conn, sql_limit)
        print(f"targets_fetched={len(targets)}")

        skip_reason_counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        to_update: list[dict[str, Any]] = []

        for i, row in enumerate(targets, start=1):
            kind, skip_reason = read_ntce_kind_nm(s3, args.bucket, row["raw_s3_key"])
            if skip_reason is not None:
                skip_reason_counts[skip_reason] = skip_reason_counts.get(skip_reason, 0) + 1
            else:
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                to_update.append(
                    {
                        "bid_ntce_no": row["bid_ntce_no"],
                        "bid_ntce_ord": row["bid_ntce_ord"],
                        "ntce_kind_nm": kind,
                    }
                )

            if args.progress_every and i % args.progress_every == 0:
                print(f"progress: processed={i}/{len(targets)} changed_so_far={len(to_update)}")

        if not dry_run and to_update:
            with conn.cursor() as cur:
                for params in to_update:
                    cur.execute(UPDATE_SQL, params)
            conn.commit()

    print("===== 결과 =====")
    print(f"처리 건수={len(targets)}")
    print(f"변경 건수(UPDATE {'실행' if not dry_run else '예정(DRY_RUN)'})={len(to_update)}")
    print("공고종류별 건수:")
    for kind, count in sorted(kind_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {kind}: {count}")
    print("스킵 사유별 건수:")
    for reason, count in sorted(skip_reason_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {reason}: {count}")
    if dry_run:
        print("DRY_RUN=true - 실제 UPDATE는 실행되지 않았습니다. DRY_RUN=false로 재실행해야 반영됩니다.")


if __name__ == "__main__":
    main()
