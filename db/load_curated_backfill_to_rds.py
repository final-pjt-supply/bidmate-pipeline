"""S3 curated backfill JSON을 RDS bid_table/bid_attachments에 제한 적재한다.

Example:
    python db/load_curated_backfill_to_rds.py --dry-run
    python db/load_curated_backfill_to_rds.py --yes
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from psycopg.types.json import Jsonb
from tqdm import tqdm

try:
    from apply_schema_to_rds import build_database_url, describe_target
    from inspect_curated_backfill_item import read_json_object
    from list_curated_backfill_keys import (
        DEFAULT_BUCKET,
        DEFAULT_PREFIX,
        create_s3_client,
        list_json_keys,
    )
except ImportError:  # pragma: no cover - unittest가 repo root에서 import할 때 사용된다.
    from db.apply_schema_to_rds import build_database_url, describe_target
    from db.inspect_curated_backfill_item import read_json_object
    from db.list_curated_backfill_keys import (
        DEFAULT_BUCKET,
        DEFAULT_PREFIX,
        create_s3_client,
        list_json_keys,
    )


DEFAULT_REGION = "ap-northeast-2"
DEFAULT_BID_LIMIT = 0
DEFAULT_ATTACHMENT_LIMIT = 0
DEFAULT_FETCH_WORKERS = 10
DEFAULT_WRITE_BATCH_SIZE = 500

BID_TABLE_COLUMNS = (
    "bid_ntce_no",
    "bid_ntce_ord",
    "bid_category",
    "bid_ntce_nm",
    "ntce_instt_cd",
    "ntce_instt_nm",
    "dminstt_cd",
    "dminstt_nm",
    "ntce_kind_nm",
    "re_ntce_yn",
    "intrbid_yn",
    "bid_ntce_dt",
    "bid_clse_dt",
    "openg_dt",
    "bid_qlfct_rgst_dt",
    "rgst_dt",
    "chg_dt",
    "presmpt_prce",
    "bdgt_amt",
    "vat",
    "govsply_amt",
    "cntrct_cncls_mthd_nm",
    "sucsfbid_mthd_cd",
    "sucsfbid_mthd_nm",
    "sucsfbid_lwlt_rate",
    "bid_methd_nm",
    "pq_eval_yn",
    "dsgnt_cmpt_yn",
    "bid_prtcpt_lmt_yn",
    "rbid_permsn_yn",
    "cnstrtsite_rgn_nm",
    "rgn_duty_jntcontrct_yn",
    "rgn_duty_jntcontrct_rt",
    "jntcontrct_duty_rgns",
    "cmmn_spldmd_methd_cd",
    "cmmn_spldmd_methd_nm",
    "cmmn_spldmd_agrmnt_clse_dt",
    "main_cnstty_nm",
    "main_cnstty_presmpt_prce",
    "indstryty_lmt_yn",
    "cnstty_share_rates",
    "subsi_cnstty",
    "bid_ntce_dtl_url",
    "unty_ntce_no",
    "expected_file_count",
    "raw_s3_key",
)
JSONB_COLUMNS = {
    "jntcontrct_duty_rgns",
    "cnstty_share_rates",
    "subsi_cnstty",
}
FIELD_ALIASES = {
    "bdgt_amt": ("bdgt_amt", "asign_bdgt_amt", "asignBdgtAmt"),
    "cnstty_share_rates": ("cnstty_share_rates", "cnstty_accot_shre_rate_list"),
}

JNTCONTRCT_DUTY_RGN_KEYS = (
    "jntcontrctDutyRgnNm1",
    "jntcontrctDutyRgnNm2",
    "jntcontrctDutyRgnNm3",
    "jntcontrct_duty_rgn_nm1",
    "jntcontrct_duty_rgn_nm2",
    "jntcontrct_duty_rgn_nm3",
)

ATTACHMENT_COLUMNS = (
    "file_id",
    "bid_ntce_no",
    "bid_ntce_ord",
    "file_seq",
    "file_url",
    "s3_key",
    "status",
)
# status는 이 로더가 최초 등록한 시점의 스냅샷이라, 재실행 시 뒷단(자격요건 병합) 파이프라인이
# 이미 진전시켜 놓은 상태(extracted/qualifications)를 덮어써서 되돌리면 안 된다.
ATTACHMENT_NO_REGRESS_COLUMNS = ("status",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load curated backfill JSON rows into bid_table and bid_attachments.",
    )
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--bid-limit",
        type=int,
        default=DEFAULT_BID_LIMIT,
        help="Maximum number of bid JSON objects to load. Defaults to 0, which means no limit.",
    )
    parser.add_argument(
        "--attachment-limit",
        type=int,
        default=DEFAULT_ATTACHMENT_LIMIT,
        help="Maximum number of attachment rows to load. Defaults to 0, which means no limit.",
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


def has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def first_value(item: dict[str, Any], column: str) -> Any:
    for key in FIELD_ALIASES.get(column, (column,)):
        value = item.get(key)
        if has_value(value):
            return value
    return None


def compact_values(values: list[Any]) -> list[Any]:
    return [value for value in values if has_value(value)]


def build_jntcontrct_duty_rgns(item: dict[str, Any]) -> list[Any] | None:
    # 원본 API의 jntcontrctDutyRgnNm1~3 또는 curated의 배열 필드를 DB JSONB 1컬럼으로 묶는다.
    for key in ("jntcontrct_duty_rgns", "jntcontrct_duty_rgn_nm"):
        value = item.get(key)
        if isinstance(value, list):
            # has_value([])는 str([])=="[]"라 "값 있음"으로 오판해 [[]] 이중 래핑을 만들던 버그가
            # 있었다. 이미 리스트로 확인했으니 비어있으면 그냥 다음 키로 넘어간다.
            regions = compact_values(value)
            if regions:
                return regions
            continue
        if has_value(value):
            return [value]

    regions = compact_values([item.get(key) for key in JNTCONTRCT_DUTY_RGN_KEYS])
    return regions or None


def adapt_value(column: str, value: Any) -> Any:
    if column in JSONB_COLUMNS and value is not None:
        return Jsonb(value)
    return value


def build_bid_row(item: dict[str, Any], curated_s3_key: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for column in BID_TABLE_COLUMNS:
        if column == "jntcontrct_duty_rgns":
            value = build_jntcontrct_duty_rgns(item)
        else:
            value = first_value(item, column)
        if column == "raw_s3_key" and not has_value(value):
            value = curated_s3_key
        if has_value(value):
            row[column] = adapt_value(column, value)

    missing = [
        column
        for column in ("bid_ntce_no", "bid_ntce_ord", "bid_category")
        if not has_value(row.get(column))
    ]
    if missing:
        raise ValueError(f"Missing required bid_table fields: {', '.join(missing)}")
    return row


def derive_attachment_s3_key(item: dict[str, Any], curated_s3_key: str, file_id: str, file_nm: Any) -> str:
    # curated JSON의 attachment 항목에는 s3_key가 없는 경우가 많다(원시 수집 단계가 아직
    # 첨부파일별 key를 안 넘겨줌). 스키마 주석("{biz_div}/... 파티션 구조")을 따라, 같은 공고의
    # raw_s3_key(또는 curated key)가 속한 파티션 디렉터리 아래 attachments/ 폴더에 있다고 가정한
    # best-effort 값이다 — 실제 오브젝트 존재를 보장하지 않으므로 원시 수집기가 실 키를 채워주면
    # 그 값(attachment.get("s3_key"))이 항상 우선한다.
    base_key = item.get("raw_s3_key") or curated_s3_key
    base_dir = base_key.rsplit("/", 1)[0] if "/" in base_key else base_key
    ext = ""
    if isinstance(file_nm, str) and "." in file_nm:
        ext = "." + file_nm.rsplit(".", 1)[-1]
    return f"{base_dir}/attachments/{file_id}{ext}"


def determine_attachment_status(file_url: Any, s3_key: Any) -> str:
    # 스키마 주석의 상태 값(collected/extracted/qualifications/failed) 중, 이 로더가 아는 범위
    # 안에서만 판단한다. 실제 추출/자격요건 병합은 별도 파이프라인이 수행하므로 그 이상은 함부로
    # 'extracted'/'qualifications'로 단정하지 않는다(뒷단 파이프라인이 진전시킨 상태는
    # ATTACHMENT_NO_REGRESS_COLUMNS로 보호되어 이 로더 재실행으로 되돌아가지 않는다).
    if not has_value(file_url):
        return "failed"
    if not has_value(s3_key):
        return "pending"
    return "collected"


def build_attachment_rows(
    item: dict[str, Any],
    curated_s3_key: str,
    attachment_limit: int | None,
    used_count: int,
) -> list[dict[str, Any]]:
    bid_ntce_no = item.get("bid_ntce_no")
    bid_ntce_ord = item.get("bid_ntce_ord")
    bid_id = item.get("bid_id") or f"{bid_ntce_no}_{bid_ntce_ord}"
    attachments = item.get("attachments")
    if not isinstance(attachments, list):
        return []

    rows: list[dict[str, Any]] = []
    truncated_count = 0
    for seq, attachment in enumerate(attachments, start=1):
        if attachment_limit is not None and used_count + len(rows) >= attachment_limit:
            truncated_count = len(attachments) - seq + 1
            break
        if not isinstance(attachment, dict):
            continue

        file_url = attachment.get("file_url") or attachment.get("ntceSpecDocUrl")
        if not has_value(file_url):
            continue

        # file_id는 스키마 주석 규칙({bid_id}_doc{seq:02d})에 맞춘다.
        file_id = f"{bid_id}_doc{seq:02d}"
        s3_key = attachment.get("s3_key") or derive_attachment_s3_key(
            item, curated_s3_key, file_id, attachment.get("file_nm"),
        )
        rows.append(
            {
                "file_id": file_id,
                "bid_ntce_no": bid_ntce_no,
                "bid_ntce_ord": bid_ntce_ord,
                "file_seq": seq,
                "file_url": file_url,
                "s3_key": s3_key,
                "status": determine_attachment_status(file_url, s3_key),
            },
        )

    if truncated_count:
        print(
            f"warning: attachment_limit truncated {bid_id} "
            f"({truncated_count} attachment(s) dropped)",
            file=sys.stderr,
        )
    return rows


def build_rows(
    s3_client,
    bucket: str,
    prefix: str,
    bid_limit: int | None,
    attachment_limit: int | None,
    fetch_workers: int = DEFAULT_FETCH_WORKERS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keys = list_json_keys(s3_client, bucket, prefix, bid_limit)
    bid_rows: list[dict[str, Any]] = []
    attachment_rows: list[dict[str, Any]] = []

    def fetch(key: str) -> Any:
        return read_json_object(s3_client, bucket, key)

    # boto3 client는 스레드 세이프하므로 S3 GET(네트워크 대기가 대부분)만 병렬화한다.
    # executor.map()은 완료 순서와 무관하게 입력 순서대로 결과를 돌려주므로, attachment_limit
    # 잘림 로직이 기대하는 "키 나열 순서" 그대로 뒤이은 row 조립에 넘길 수 있다.
    with ThreadPoolExecutor(max_workers=fetch_workers) as executor:
        items = tqdm(
            executor.map(fetch, keys),
            total=len(keys),
            desc="S3 curated JSON 읽는 중",
            unit="건",
        )
        for key, item in zip(keys, items):
            if not isinstance(item, dict):
                raise ValueError(f"Top-level JSON is not an object: {key}")

            bid_rows.append(build_bid_row(item, key))
            attachment_rows.extend(
                build_attachment_rows(item, key, attachment_limit, len(attachment_rows)),
            )

    return bid_rows, attachment_rows


def build_upsert_sql(
    table: str,
    columns: tuple[str, ...],
    conflict_columns: tuple[str, ...],
    no_regress_columns: tuple[str, ...] = (),
) -> str:
    column_sql = ", ".join(columns)
    value_sql = ", ".join(f"%({column})s" for column in columns)
    conflict_sql = ", ".join(conflict_columns)
    # no_regress_columns는 최초 INSERT 시에만 값을 넣고, 이후 재실행(UPDATE)에서는 건드리지
    # 않는다 — 이 로더보다 뒷단 파이프라인이 이미 진전시켜 놓은 상태를 되돌리지 않기 위함.
    update_columns = [
        column
        for column in columns
        if column not in conflict_columns and column not in no_regress_columns
    ]
    update_sql = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
    update_sql = f"{update_sql}, updated_at = NOW()" if update_sql else "updated_at = NOW()"
    return (
        f"INSERT INTO {table} ({column_sql}) VALUES ({value_sql}) "
        f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
    )


def normalize_row(row: dict[str, Any], columns: tuple[str, ...]) -> dict[str, Any]:
    return {column: row.get(column) for column in columns}


def confirm_write(bid_count: int, attachment_count: int, target: str) -> None:
    print(f"target={target}")
    print(f"bid_rows={bid_count}")
    print(f"attachment_rows={attachment_count}")
    answer = input("Write these rows to RDS? Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise SystemExit("Canceled.")


def write_batches(cur, sql: str, rows: list[dict[str, Any]], batch_size: int, desc: str) -> None:
    if not rows:
        return
    for start in tqdm(range(0, len(rows), batch_size), desc=desc, unit="batch"):
        cur.executemany(sql, rows[start : start + batch_size])


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

    # 배치로 나눠서 진행률만 보여줄 뿐, 커밋은 맨 마지막 한 번뿐이라 원자성은 그대로 유지된다
    # (중간에 실패하면 이번 실행에서 실행된 배치까지 전부 롤백된다).
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
    if attachment_rows:
        print(f"first_attachment={attachment_rows[0]['file_id']}")


def main() -> None:
    args = parse_args()
    bid_limit = None if args.bid_limit == 0 else args.bid_limit
    attachment_limit = None if args.attachment_limit == 0 else args.attachment_limit
    s3 = create_s3_client(args.region)

    try:
        bid_rows, attachment_rows = build_rows(
            s3,
            args.bucket,
            args.prefix,
            bid_limit,
            attachment_limit,
            args.fetch_workers,
        )
    except NoCredentialsError:
        print(
            "AWS credentials not found. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "or AWS_ACCESS_KEY/AWS_SECRET_KEY.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except (BotoCoreError, ClientError, ValueError) as exc:
        print(f"Curated backfill load failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.dry_run:
        print_dry_run(bid_rows, attachment_rows)
        return

    try:
        database_url = build_database_url()
    except ValueError as exc:
        print(f"RDS configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    target = describe_target(database_url)
    if not args.yes:
        confirm_write(len(bid_rows), len(attachment_rows), target)

    write_rows(database_url, bid_rows, attachment_rows, args.write_batch_size)
    print(f"loaded_bid_rows={len(bid_rows)}")
    print(f"loaded_attachment_rows={len(attachment_rows)}")


if __name__ == "__main__":
    main()
