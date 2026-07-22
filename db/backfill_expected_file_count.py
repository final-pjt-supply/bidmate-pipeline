"""daily bid_table.expected_file_count 소급 재계산 (#88 후속, #90).

#88은 신규 유입(curated 생성 시점)에만 적용된다. 이미 적재된 daily 행들은 dedup·미지원
확장자를 반영 못 한 옛 계산값이 expected_file_count에 그대로 박혀 있어, 병합 배치가
아무리 재시도해도 found_file_count가 못 따라잡아 partial/pending에 고착된다. 이 스크립트는
그 행들의 curated JSON을 다시 읽어 ingestion.schema._effective_expected_count()로
재계산한 값으로 expected_file_count만 UPDATE한다. qual_status는 건드리지 않는다 —
다음 병합 배치(pipeline/realtime)가 새 값을 보고 자동으로 재판정한다.

daily 전용이다. 백필(raw_s3_key LIKE '%backfill%')은 조원 소유라 절대 대상에 넣지 않는다.

Example:
    python db/backfill_expected_file_count.py                  # DRY_RUN 기본값 true — 계산/로그만
    DRY_RUN=false python db/backfill_expected_file_count.py --limit 300
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

try:
    from apply_schema_to_rds import build_database_url, describe_target
    from list_curated_backfill_keys import create_s3_client
except ImportError:  # pragma: no cover - unittest가 repo root에서 import할 때 사용된다.
    from db.apply_schema_to_rds import build_database_url, describe_target
    from db.list_curated_backfill_keys import create_s3_client

# ingestion/schema.py의 _effective_expected_count()를 그대로 재사용한다 — 로직을 여기 복제하면
# #88이 나중에 또 수정될 때 두 곳이 어긋날 위험이 있다. ingestion/은 저장소 루트 기준
# db/의 형제 디렉터리라 sys.path에 얹어야 import된다(ingestion 쪽이 db/를 참조하지 않는
# 단방향 의존이라 순환 임포트 걱정은 없음).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from schema import _effective_expected_count  # noqa: E402


DEFAULT_BUCKET = "bidmate"
DEFAULT_REGION = "ap-northeast-2"
DEFAULT_LIMIT = 300
DEFAULT_PROGRESS_EVERY = 50

# 이 스크립트는 daily 전용이다. 이 문자열이 실제 실행되는 SQL에 없으면 즉시 중단한다
# (아래 _assert_daily_only_filter) — 나중에 실수로 WHERE 절을 고쳐도 백필까지 조용히
# 덮어쓰는 사고가 나지 않게 하는 이중 방어선.
# psycopg(v3)는 %(name)s 플레이스홀더를 쓸 때 리터럴 %를 %%로 이스케이프해야 한다 —
# 안 하면 LIKE 패턴의 %가 파라미터 파싱을 깨뜨린다(params 딕셔너리를 넘기는 모든
# cur.execute() 호출에 적용됨).
BACKFILL_EXCLUSION_SQL = "raw_s3_key NOT LIKE '%%backfill%%'"

SELECT_TARGETS_SQL = f"""
    SELECT bid_id, bid_ntce_no, bid_ntce_ord, raw_s3_key, expected_file_count, qual_status
    FROM bid_table
    WHERE {BACKFILL_EXCLUSION_SQL}
      AND qual_status IN ('partial', 'pending')
      AND is_human_verified = FALSE
    ORDER BY bid_id
    LIMIT %(limit)s
"""

# expected_file_count 외 다른 컬럼은 의도적으로 손대지 않는다(updated_at 포함) — qual_status
# 전이는 병합 배치의 책임으로 남겨두고, 이 스크립트는 딱 이 값 하나만 고친다. WHERE에
# BACKFILL_EXCLUSION_SQL을 SELECT와 동일하게 다시 걸어 이중 방어한다 — SELECT 단계에서
# 걸러졌더라도 UPDATE 자체가 백필 행을 못 건드리게 하는 마지막 안전선.
UPDATE_SQL = f"""
    UPDATE bid_table
    SET expected_file_count = %(new_expected)s
    WHERE bid_ntce_no = %(bid_ntce_no)s AND bid_ntce_ord = %(bid_ntce_ord)s
      AND {BACKFILL_EXCLUSION_SQL}
"""


def _assert_daily_only_filter(sql: str) -> None:
    """백필 제외 필터가 실제로 쿼리에 박혀 있는지 실행 직전 재확인한다. 없으면 즉시 중단."""
    if BACKFILL_EXCLUSION_SQL not in sql:
        raise SystemExit(
            "안전 가드 실패: 대상 SELECT 쿼리에 백필 제외 필터가 없습니다 - "
            "daily 전용 스크립트가 백필 행까지 건드릴 위험이 있어 실행을 즉시 중단합니다."
        )


def is_dry_run() -> bool:
    # 기본값 true — 실제 UPDATE는 DRY_RUN=false를 명시해야만 실행된다
    # (pipeline/realtime/src/common/config.load_merge_db_config와 동일 관례).
    return os.environ.get("DRY_RUN", "true").strip().lower() != "false"


def curated_key_from_raw(raw_s3_key: str) -> str:
    """raw/raw/daily/... → raw/curated/daily/... (schema.py._attachments()가 만드는 그 curated
    JSON의 실제 저장 위치)."""
    return raw_s3_key.replace("raw/raw/", "raw/curated/", 1)


def fetch_targets(conn, limit: int) -> list[dict[str, Any]]:
    _assert_daily_only_filter(SELECT_TARGETS_SQL)
    with conn.cursor() as cur:
        cur.execute(SELECT_TARGETS_SQL, {"limit": limit})
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def write_backup_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """UPDATE를 한 건도 실행하기 전에, 이번 회차 대상 행의 bid_id/기존값/qual_status를
    그대로 남긴다 — 문제가 생기면 이 CSV로 원래 값을 확인할 수 있다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["bid_id", "old_expected_file_count", "qual_status"])
        for row in rows:
            writer.writerow([row["bid_id"], row["expected_file_count"], row["qual_status"]])
    print(f"backup_csv={path} rows={len(rows)}")


def compute_new_expected(s3_client, bucket: str, row: dict[str, Any]) -> tuple[int | None, str | None]:
    """(new_expected, skip_reason) 튜플을 반환한다. 성공하면 skip_reason은 None."""
    curated_key = curated_key_from_raw(row["raw_s3_key"])
    try:
        response = s3_client.get_object(Bucket=bucket, Key=curated_key)
        body = response["Body"].read()
        doc = json.loads(body.decode("utf-8-sig"))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return None, f"fetch_failed:{code}"
    except (BotoCoreError, json.JSONDecodeError) as exc:
        return None, f"fetch_failed:{type(exc).__name__}"

    attachments = doc.get("attachments")
    if not isinstance(attachments, list):
        return None, "fetch_failed:no_attachments_field"

    new_expected = _effective_expected_count(attachments)
    if new_expected == 0:
        return new_expected, "zero_expected"
    if new_expected == row["expected_file_count"]:
        return new_expected, "unchanged"
    return new_expected, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recalculate expected_file_count for existing daily bid_table rows (#88 backfill, #90).",
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
        "--backup-path",
        default=None,
        help="백업 CSV 경로. 기본값은 db/backfill_backups/expected_file_count_<UTC타임스탬프>.csv",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help=f"몇 건마다 진행 로그를 남길지. 기본값 {DEFAULT_PROGRESS_EVERY}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    limit = args.limit if args.limit and args.limit > 0 else None
    # SQL의 LIMIT은 파라미터라 0/None을 그대로 못 넘긴다 — 무제한이면 매우 큰 값으로 대체.
    sql_limit = limit if limit is not None else 10_000_000
    dry_run = is_dry_run()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = Path(args.backup_path) if args.backup_path else (
        Path(__file__).resolve().parent / "backfill_backups" / f"expected_file_count_{run_id}.csv"
    )

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

    # 비번은 여기서도, describe_target()이 마스킹한 문자열만 로그에 남긴다.
    print(f"target={describe_target(database_url)}")
    print(f"dry_run={dry_run}")

    s3 = create_s3_client(args.region)

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        targets = fetch_targets(conn, sql_limit)
        print(f"targets_fetched={len(targets)} (limit={limit if limit is not None else '무제한'})")

        write_backup_csv(targets, backup_path)

        skip_reason_counts: dict[str, int] = {}
        to_update: list[dict[str, Any]] = []

        for i, row in enumerate(targets, start=1):
            new_expected, skip_reason = compute_new_expected(s3, args.bucket, row)
            if skip_reason is not None:
                skip_reason_counts[skip_reason] = skip_reason_counts.get(skip_reason, 0) + 1
            else:
                to_update.append(
                    {
                        "bid_ntce_no": row["bid_ntce_no"],
                        "bid_ntce_ord": row["bid_ntce_ord"],
                        "new_expected": new_expected,
                    }
                )

            if args.progress_every and i % args.progress_every == 0:
                print(f"progress: processed={i}/{len(targets)} changed_so_far={len(to_update)}")

        if not dry_run and to_update:
            _assert_daily_only_filter(UPDATE_SQL)
            with conn.cursor() as cur:
                for params in to_update:
                    cur.execute(UPDATE_SQL, params)
            conn.commit()

    print("===== 결과 =====")
    print(f"처리 건수={len(targets)}")
    print(f"변경 건수(UPDATE {'실행' if not dry_run else '예정(DRY_RUN)'})={len(to_update)}")
    print("스킵 사유별 건수:")
    for reason, count in sorted(skip_reason_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {reason}: {count}")
    if dry_run:
        print("DRY_RUN=true - 실제 UPDATE는 실행되지 않았습니다. DRY_RUN=false로 재실행해야 반영됩니다.")


if __name__ == "__main__":
    main()
