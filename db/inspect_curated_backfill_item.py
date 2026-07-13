"""S3 curated backfill JSON 1건의 구조를 확인한다.

Example:
    python db/inspect_curated_backfill_item.py \
        --key raw/curated/backfill/biz_div=cnstwk/year=2026/month=01/day=02/R25BK01152374-000.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

try:
    from list_curated_backfill_keys import (
        DEFAULT_BUCKET,
        DEFAULT_PREFIX,
        create_s3_client,
        list_json_keys,
    )
except ImportError:  # pragma: no cover - unittest가 repo root에서 import할 때 사용된다.
    from db.list_curated_backfill_keys import (
        DEFAULT_BUCKET,
        DEFAULT_PREFIX,
        create_s3_client,
        list_json_keys,
    )


DEFAULT_REGION = "ap-northeast-2"
REQUIRED_FIELD_ALIASES = {
    "bid_ntce_no": ("bid_ntce_no", "bidNtceNo"),
    "bid_ntce_ord": ("bid_ntce_ord", "bidNtceOrd"),
}
CORE_FIELD_ALIASES = {
    "bid_ntce_no": ("bid_ntce_no", "bidNtceNo"),
    "bid_ntce_ord": ("bid_ntce_ord", "bidNtceOrd"),
    "bid_category": ("bid_category", "bidCategory"),
    "bid_ntce_nm": ("bid_ntce_nm", "bidNtceNm"),
    "ntce_instt_nm": ("ntce_instt_nm", "ntceInsttNm"),
    "dminstt_nm": ("dminstt_nm", "dminsttNm"),
    "bid_ntce_dt": ("bid_ntce_dt", "bidNtceDt"),
    "bid_clse_dt": ("bid_clse_dt", "bidClseDt"),
    "openg_dt": ("openg_dt", "opengDt"),
    "presmpt_prce": ("presmpt_prce", "presmptPrce"),
    "bdgt_amt": ("bdgt_amt", "asign_bdgt_amt", "asignBdgtAmt"),
    "cntrct_cncls_mthd_nm": ("cntrct_cncls_mthd_nm", "cntrctCnclsMthdNm"),
    "sucsfbid_mthd_nm": ("sucsfbid_mthd_nm", "sucsfbidMthdNm"),
    "bid_ntce_dtl_url": ("bid_ntce_dtl_url", "bidNtceDtlUrl"),
    "unty_ntce_no": ("unty_ntce_no", "untyNtceNo"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect one curated backfill JSON object from S3.",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET") or DEFAULT_BUCKET,
        help="S3 bucket name. Defaults to S3_BUCKET_NAME, S3_BUCKET, or bidmate.",
    )
    parser.add_argument(
        "--key",
        help="S3 key to inspect. If omitted, the first JSON key under --prefix is used.",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"S3 prefix used when --key is omitted. Defaults to {DEFAULT_PREFIX}.",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or DEFAULT_REGION,
        help=f"AWS region. Defaults to AWS_REGION, AWS_DEFAULT_REGION, or {DEFAULT_REGION}.",
    )
    return parser.parse_args()


def configure_output_encoding() -> None:
    # Windows PowerShell에서 한글 값이 깨져 보이지 않도록 가능한 경우 UTF-8로 출력한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def derive_bid_category(key: str) -> str | None:
    # S3 key의 파티션 경로에서 biz_div 값을 꺼내 bid_category 후보로 사용한다.
    match = re.search(r"(?:^|/)biz_div=([^/]+)/", key)
    if match:
        return match.group(1)
    return None


def read_json_object(s3_client, bucket: str, key: str) -> Any:
    # JSON 본문은 UTF-8 BOM이 섞일 수 있어 utf-8-sig로 디코딩한다.
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    return json.loads(body.decode("utf-8-sig"))


def count_expected_files(item: dict[str, Any]) -> int:
    existing_count = item.get("expected_file_count")
    if isinstance(existing_count, int):
        return existing_count
    if isinstance(existing_count, str) and existing_count.strip().isdigit():
        return int(existing_count)

    attachments = item.get("attachments")
    if isinstance(attachments, list):
        return len([attachment for attachment in attachments if attachment])

    count = 0
    for index in range(1, 11):
        url = item.get(f"ntceSpecDocUrl{index}")
        name = item.get(f"ntceSpecFileNm{index}")
        # URL 또는 파일명 중 하나라도 있으면 첨부 후보 1개로 본다.
        if has_value(url) or has_value(name):
            count += 1
    return count


def has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def first_value(item: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for alias in aliases:
        value = item.get(alias)
        if has_value(value):
            return value
    return None


def summarize_item(item: Any, key: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "root_type": type(item).__name__,
            "key": key,
            "error": "top-level JSON is not an object",
        }

    missing_required = [
        field
        for field, aliases in REQUIRED_FIELD_ALIASES.items()
        if not has_value(first_value(item, aliases))
    ]
    core_values = {
        field: value
        for field, aliases in CORE_FIELD_ALIASES.items()
        if has_value(value := first_value(item, aliases))
    }
    return {
        "root_type": "dict",
        "key": key,
        "bid_category_from_key": derive_bid_category(key),
        "field_count": len(item),
        "top_level_fields": sorted(item.keys()),
        "missing_required_fields": missing_required,
        "expected_file_count": count_expected_files(item),
        "core_values": core_values,
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(f"key={summary['key']}")
    print(f"root_type={summary['root_type']}")

    if "error" in summary:
        print(f"error={summary['error']}")
        return

    print(f"bid_category_from_key={summary['bid_category_from_key']}")
    print(f"field_count={summary['field_count']}")
    print(f"expected_file_count={summary['expected_file_count']}")

    missing = summary["missing_required_fields"]
    print(f"missing_required_fields={','.join(missing) if missing else 'none'}")

    print("\n[core_values]")
    for field, value in summary["core_values"].items():
        print(f"{field}={value}")

    print("\n[top_level_fields]")
    for field in summary["top_level_fields"]:
        print(field)


def resolve_key(s3_client, bucket: str, key: str | None, prefix: str) -> str:
    if key:
        return key

    keys = list_json_keys(s3_client, bucket, prefix, limit=1)
    if not keys:
        raise ValueError(f"No JSON keys found under prefix: {prefix}")
    return keys[0]


def main() -> None:
    configure_output_encoding()
    args = parse_args()
    s3 = create_s3_client(args.region)

    try:
        key = resolve_key(s3, args.bucket, args.key, args.prefix)
        item = read_json_object(s3, args.bucket, key)
    except NoCredentialsError:
        print(
            "AWS credentials not found. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "or AWS_ACCESS_KEY/AWS_SECRET_KEY.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except (BotoCoreError, ClientError, ValueError, json.JSONDecodeError) as exc:
        print(f"S3 JSON inspect failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print_summary(summarize_item(item, key))


if __name__ == "__main__":
    main()
