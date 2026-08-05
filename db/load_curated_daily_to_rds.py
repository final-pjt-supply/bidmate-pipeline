"""S3 curated DAILY JSON을 RDS bid_table/bid_attachments에 증분 적재한다.

백필 로더(load_curated_backfill_to_rds.py)와 달리, raw/curated/daily/는 매일 계속 쌓이는
구조라 매번 prefix 전체를 스캔하면 시간이 갈수록 느려진다. 대신 S3 ListObjectsV2가 이미
알려주는 LastModified로 걸러서, --since 이후 새로 쓰이거나 갱신된 오브젝트만 GET한다.

Airflow DAG(ingestion/dags/bidding_daily_dag.py)의 load_daily_to_rds 태스크가 커서
(bidding_daily_rds_last_success_at)를 --since로 넘겨 5분마다 호출한다. 수집 커서와는
별개로 관리되어, 이 스크립트가 실패해도 커서가 전진하지 않아 다음 실행이 그 구간을
자동으로 다시 처리한다(gap-threshold 같은 복잡한 로직 불필요 — 외부 API 호출 예산과
무관하게 우리 S3를 다시 읽는 것뿐이라 재시도 비용이 낮다).

Example:
    python db/load_curated_daily_to_rds.py --since 2026-07-13T05:00:00+00:00 --dry-run
    python db/load_curated_daily_to_rds.py --since 2026-07-13T05:00:00+00:00 --yes
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from tqdm import tqdm

try:
    from apply_schema_to_rds import build_database_url, describe_target
    from inspect_curated_backfill_item import read_json_object
    from list_curated_backfill_keys import DEFAULT_BUCKET, create_s3_client
    from load_curated_backfill_to_rds import (
        ATTACHMENT_COLUMNS,
        ATTACHMENT_NO_REGRESS_COLUMNS,
        BID_TABLE_COLUMNS,
        DEFAULT_FETCH_WORKERS,
        DEFAULT_WRITE_BATCH_SIZE,
        build_attachment_rows,
        build_bid_row,
        build_upsert_sql,
        normalize_row,
        write_batches,
    )
except ImportError:  # pragma: no cover - unittest가 repo root에서 import할 때 사용된다.
    from db.apply_schema_to_rds import build_database_url, describe_target
    from db.inspect_curated_backfill_item import read_json_object
    from db.list_curated_backfill_keys import DEFAULT_BUCKET, create_s3_client
    from db.load_curated_backfill_to_rds import (
        ATTACHMENT_COLUMNS,
        ATTACHMENT_NO_REGRESS_COLUMNS,
        BID_TABLE_COLUMNS,
        DEFAULT_FETCH_WORKERS,
        DEFAULT_WRITE_BATCH_SIZE,
        build_attachment_rows,
        build_bid_row,
        build_upsert_sql,
        normalize_row,
        write_batches,
    )


DEFAULT_REGION = "ap-northeast-2"
DEFAULT_DAILY_PREFIX = "raw/curated/daily"
BIZ_DIVS = ("cnstwk", "servc", "thng", "frgcpt")
# 시계 오차·리스트 경계에서 방금 쓰인 오브젝트가 누락되는 걸 막기 위한 여유.
LOOKBACK_BUFFER = timedelta(minutes=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load curated DAILY JSON objects modified since --since into bid_table/bid_attachments.",
    )
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--prefix", default=DEFAULT_DAILY_PREFIX)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--since",
        required=True,
        help="이 시각(ISO 8601) 이후 LastModified인 오브젝트만 적재.",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="이 시각(ISO 8601)까지. 생략 시 실행 시점의 현재 시각.",
    )
    parser.add_argument(
        "--fetch-workers",
        type=int,
        default=DEFAULT_FETCH_WORKERS,
        help=f"Concurrent S3 read threads. Defaults to {DEFAULT_FETCH_WORKERS}.",
    )
    parser.add_argument(
        "--write-batch-size",
        type=int,
        default=DEFAULT_WRITE_BATCH_SIZE,
        help=f"Rows per executemany() batch when writing to RDS. Defaults to {DEFAULT_WRITE_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build rows and print summary without writing to RDS.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Write to RDS without interactive confirmation.",
    )
    return parser.parse_args()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def list_recent_json_keys(
    s3_client,
    bucket: str,
    prefix: str,
    since: datetime,
    until: datetime,
) -> list[str]:
    """since~until 사이에 LastModified가 찍힌 curated JSON 키만 골라 돌려준다.

    day partition(biz_div/year/month/day)별로 저장되므로, 그 구간이 걸치는 날짜의
    파티션만 나열한다. GET 없이 ListObjectsV2 응답의 LastModified만으로 걸러서,
    변경 없는 오브젝트는 아예 읽지 않는다.
    """
    scan_since = since - LOOKBACK_BUFFER
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for biz_div in BIZ_DIVS:
        for day in daterange(scan_since.date(), until.date()):
            day_prefix = f"{prefix}/biz_div={biz_div}/year={day:%Y}/month={day:%m}/day={day:%d}/"
            for page in paginator.paginate(Bucket=bucket, Prefix=day_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.endswith(".json"):
                        continue
                    last_modified = obj["LastModified"]
                    if scan_since <= last_modified <= until:
                        keys.append(key)
    return keys


def build_rows(
    s3_client,
    bucket: str,
    keys: list[str],
    fetch_workers: int = DEFAULT_FETCH_WORKERS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bid_rows: list[dict[str, Any]] = []
    attachment_rows: list[dict[str, Any]] = []

    def fetch(key: str) -> Any:
        return read_json_object(s3_client, bucket, key)

    # 배치가 daily는 훨씬 작지만(5분 단위 소량), 백필에서 검증된 동일 패턴을 그대로 재사용한다
    # — I/O 대기 구간이라 스레드로도 충분하고, 코드 중복/리스크를 줄이는 게 이득이다.
    with ThreadPoolExecutor(max_workers=fetch_workers) as executor:
        items = tqdm(
            executor.map(fetch, keys),
            total=len(keys),
            desc="S3 daily curated JSON 읽는 중",
            unit="건",
        )
        for key, item in zip(keys, items):
            if not isinstance(item, dict):
                raise ValueError(f"Top-level JSON is not an object: {key}")

            bid_rows.append(build_bid_row(item, key))
            attachment_rows.extend(
                build_attachment_rows(item, key, None, len(attachment_rows)),
            )

    return bid_rows, attachment_rows


def write_rows(
    database_url: str,
    bid_rows: list[dict[str, Any]],
    attachment_rows: list[dict[str, Any]],
    batch_size: int = DEFAULT_WRITE_BATCH_SIZE,
) -> None:
    import psycopg

    bid_sql = build_upsert_sql("bid_table", BID_TABLE_COLUMNS, ("bid_ntce_no", "bid_ntce_ord"))
    attachment_sql = build_upsert_sql(
        "bid_attachments", ATTACHMENT_COLUMNS, ("file_id",), ATTACHMENT_NO_REGRESS_COLUMNS,
    )

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            write_batches(
                cur,
                bid_sql,
                [normalize_row(row, BID_TABLE_COLUMNS) for row in bid_rows],
                batch_size,
                "bid_table 적재 중",
            )
            write_batches(
                cur,
                attachment_sql,
                [normalize_row(row, ATTACHMENT_COLUMNS) for row in attachment_rows],
                batch_size,
                "bid_attachments 적재 중",
            )
        conn.commit()


def print_dry_run(bid_rows: list[dict[str, Any]], attachment_rows: list[dict[str, Any]]) -> None:
    print("dry_run=true")
    print(f"bid_rows={len(bid_rows)}")
    print(f"attachment_rows={len(attachment_rows)}")
    if bid_rows:
        print(f"first_bid={bid_rows[0]['bid_ntce_no']}_{bid_rows[0]['bid_ntce_ord']}")


def confirm_write(bid_count: int, attachment_count: int, target: str) -> None:
    print(f"target={target}")
    print(f"bid_rows={bid_count}")
    print(f"attachment_rows={attachment_count}")
    answer = input("Write these rows to RDS? Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise SystemExit("Canceled.")


def main() -> None:
    args = parse_args()
    since = parse_iso(args.since)
    until = parse_iso(args.until) if args.until else datetime.now(timezone.utc)
    s3 = create_s3_client(args.region)

    try:
        keys = list_recent_json_keys(s3, args.bucket, args.prefix, since, until)
        bid_rows, attachment_rows = build_rows(s3, args.bucket, keys, args.fetch_workers)
    except NoCredentialsError:
        print(
            "AWS credentials not found. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "or AWS_ACCESS_KEY/AWS_SECRET_KEY.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except (BotoCoreError, ClientError, ValueError) as exc:
        print(f"Curated daily load failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.dry_run:
        print_dry_run(bid_rows, attachment_rows)
        return

    if not bid_rows:
        print("loaded_bid_rows=0")
        print("loaded_attachment_rows=0")
        return

    try:
        database_url = build_database_url()
    except ValueError as exc:
        print(f"RDS configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if not args.yes:
        target = describe_target(database_url)
        confirm_write(len(bid_rows), len(attachment_rows), target)

    write_rows(database_url, bid_rows, attachment_rows, args.write_batch_size)
    print(f"loaded_bid_rows={len(bid_rows)}")
    print(f"loaded_attachment_rows={len(attachment_rows)}")


if __name__ == "__main__":
    main()
