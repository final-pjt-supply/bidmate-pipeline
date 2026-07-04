"""S3 curated JSON에서 첨부문서 URL을 읽고 파일을 S3에 저장한다 (비동기 버전, 지정 기간 백필).

기본 입력/출력:
- s3://bidmate/raw/curated/backfill/
- s3://bidmate/raw/downloads/backfill/

실행 방법
- python3 backfill_async/json_file_download_backfill.py --start 2026-06-01 --end 2026-06-30
"""

import argparse
import asyncio
import io
import json
import mimetypes
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schema import parse_dt  # noqa: E402

try:  # .env가 있으면 로드, 없으면(IAM 역할·export 등) 무시
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")
CURATED_PREFIX = "raw/curated/backfill"
FILES_PREFIX = "raw/downloads/backfill"
METADATA_PREFIX = f"{FILES_PREFIX}/_metadata"
DEFAULT_CONCURRENCY = 8
SAFE_KEY = re.compile(r"[^0-9A-Za-z가-힣._=-]+")


def safe_key_part(value: Any, fallback: str) -> str:
    cleaned = SAFE_KEY.sub("_", str(value or "").strip())
    return cleaned[:180] or fallback


def guess_ext(file_name: str, content_type: str, url: str) -> str:
    if file_name and "." in file_name:
        return "." + file_name.rsplit(".", 1)[1]

    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed

    path = urlparse(url).path
    return "." + path.rsplit(".", 1)[1] if "." in path else ".bin"


def date_prefixes(prefix: str, start_day: datetime, end_day: datetime):
    day = start_day
    while day <= end_day:
        yield f"{prefix}/year={day:%Y}/month={day:%m}/day={day:%d}/"
        day += timedelta(days=1)


async def iter_curated_range(s3, bucket: str, prefix: str, start_day: datetime, end_day: datetime):
    paginator = s3.get_paginator("list_objects_v2")
    for day_prefix in date_prefixes(prefix, start_day, end_day):
        async for page in paginator.paginate(Bucket=bucket, Prefix=day_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".json"):
                    continue
                response = await s3.get_object(Bucket=bucket, Key=key)
                payload = await response["Body"].read()
                record = json.loads(payload.decode("utf-8"))
                for item in record if isinstance(record, list) else [record]:
                    if isinstance(item, dict):
                        yield key, item


def build_file_metadata(bucket: str, record: dict, src_key: str, extracted_at: str):
    notice_id = f"{record.get('bid_ntce_no') or 'no-bid-no'}-{record.get('bid_ntce_ord') or '000'}"
    base = {
        "noticeId": notice_id,
        "업무구분": record.get("src_biz_div") or "미분류",
        "bidNtceNo": record.get("bid_ntce_no"),
        "bidNtceOrd": record.get("bid_ntce_ord"),
        "bidNtceNm": record.get("bid_ntce_nm"),
        "dminsttCd": record.get("dminstt_cd"),
        "dminsttNm": record.get("dminstt_nm"),
        "ntceInsttCd": record.get("ntce_instt_cd"),
        "ntceInsttNm": record.get("ntce_instt_nm"),
        "bidNtceDt": record.get("bid_ntce_dt"),
        "srcJsonPath": f"s3://{bucket}/{src_key}",
        "extractedAt": extracted_at,
    }

    files = []
    for seq, attachment in enumerate(record.get("attachments") or [], start=1):
        file_url = str(attachment.get("file_url") or "").strip()
        file_name = str(attachment.get("file_nm") or "").strip()
        if file_url or file_name:
            files.append(
                {
                    **base,
                    "fileId": f"{notice_id}-{seq}",
                    "fileSeq": str(seq),
                    "fileKind": attachment.get("kind") or "공고첨부",
                    "fileName": file_name,
                    "fileUrl": file_url,
                }
            )
    return files


ORD_DIGITS = re.compile(r"\d+")


def format_ord(value: Any) -> str:
    match = ORD_DIGITS.search(str(value or ""))
    return match.group(0).zfill(2) if match else "00"


def file_stem(file_name: Any, fallback: str) -> str:
    name = str(file_name or "").strip()
    if not name:
        return fallback
    return name.rsplit(".", 1)[0] if "." in name else name


def file_s3_key(prefix, metadata, content_type, file_url, used_keys=None):
    notice_dt = parse_dt(metadata.get("bidNtceDt")) or datetime.now()
    biz_div = safe_key_part(metadata.get("업무구분"), "미분류")
    bid_no = safe_key_part(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    ord_part = format_ord(metadata.get("bidNtceOrd"))
    kind = safe_key_part(metadata.get("fileKind") or "공고첨부", "공고첨부")
    ext = guess_ext(str(metadata.get("fileName") or ""), content_type, file_url)
    stem = safe_key_part(file_stem(metadata.get("fileName"), bid_no), bid_no)

    notice_dir = (
        f"{prefix}/year={notice_dt:%Y}/month={notice_dt:%m}/day={notice_dt:%d}/"
        f"biz_div={biz_div}/bidNtceNo={bid_no}_ord={ord_part}"
    )
    base_name = f"{stem}_{kind}"
    key = f"{notice_dir}/{base_name}{ext}"
    if used_keys is None:
        return key

    suffix = 2
    while key in used_keys:
        key = f"{notice_dir}/{base_name}_{suffix}{ext}"
        suffix += 1
    used_keys.add(key)
    return key


if __name__ == "__main__":
    raise SystemExit("Task 12에서 CLI 진입점이 추가될 예정입니다.")
