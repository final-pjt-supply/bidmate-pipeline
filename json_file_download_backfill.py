"""S3 curated JSON에서 첨부문서 URL을 읽고 파일을 S3에 저장한다 (지정 기간 백필).

기본 입력/출력:
- s3://bidmate/raw/curated/backfill/
- s3://bidmate/raw/downloads/backfill/

실행 방법
- python3 json_file_download_backfill.py --start 2026-06-01 --end 2026-06-30
"""

import argparse
import json
import mimetypes
import os
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

from schema import parse_dt

BUCKET_NAME = os.environ.get("S3_BUCKET", "bidmate")
CURATED_PREFIX = "raw/curated/backfill"
FILES_PREFIX = "raw/downloads/backfill"
METADATA_PREFIX = f"{FILES_PREFIX}/_metadata"
CHUNK_SIZE = 1024 * 256
SAFE_KEY = re.compile(r"[^0-9A-Za-z가-힣._=-]+")


def s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit("S3 사용을 위해 boto3 설치가 필요합니다. 예: pip install boto3") from exc
    return boto3.client("s3")


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


def iter_curated_range(s3, bucket: str, prefix: str, start_day: datetime, end_day: datetime):
    paginator = s3.get_paginator("list_objects_v2")
    for day_prefix in date_prefixes(prefix, start_day, end_day):
        for page in paginator.paginate(Bucket=bucket, Prefix=day_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".json"):
                    continue
                payload = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                record = json.loads(payload.decode("utf-8"))
                for item in record if isinstance(record, list) else [record]:
                    if isinstance(item, dict):
                        yield key, item


def build_file_metadata(bucket: str, record: dict[str, Any], src_key: str, extracted_at: str):
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


def file_s3_key(prefix: str, metadata: dict[str, Any], content_type: str, file_url: str):
    notice_dt = parse_dt(metadata.get("bidNtceDt")) or datetime.now()
    biz_div = safe_key_part(metadata.get("업무구분"), "미분류")
    bid_no = safe_key_part(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    file_seq = safe_key_part(metadata.get("fileSeq"), "unknown")
    original_name = str(metadata.get("fileName") or "")
    ext = guess_ext(original_name, content_type, file_url)
    filename = safe_key_part(original_name, f"{bid_no}_{file_seq}{ext}")
    if "." not in filename:
        filename = f"{filename}{ext}"

    return (
        f"{prefix}/year={notice_dt:%Y}/month={notice_dt:%m}/day={notice_dt:%d}/"
        f"biz_div={biz_div}/notice_id={bid_no}/{file_seq}_{filename}"
    )


def upload_attachment(s3, bucket: str, session: requests.Session, metadata: dict[str, Any], timeout: int):
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()
    response.raw.decode_content = True

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3.upload_fileobj(response.raw, bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_fileobj(response.raw, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": int(response.headers.get("Content-Length") or 0),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }


def put_manifest(s3, bucket: str, metadata: list[dict[str, Any]], run_dt: datetime):
    key = (
        f"{METADATA_PREFIX}/year={run_dt:%Y}/month={run_dt:%m}/day={run_dt:%d}/"
        f"bid_files_backfill_{run_dt:%Y%m%d%H%M%S}.json"
    )
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )
    return key


def run(args: argparse.Namespace) -> None:
    s3 = s3_client()
    run_dt = datetime.now()
    extracted_at = run_dt.isoformat()

    metadata = []
    curated_count = 0
    for src_key, record in iter_curated_range(s3, args.bucket, args.curated_prefix, args.start, args.end):
        curated_count += 1
        metadata.extend(build_file_metadata(args.bucket, record, src_key, extracted_at))

    print(f"[시작] {args.start:%Y-%m-%d} ~ {args.end:%Y-%m-%d} curated JSON={curated_count}건")
    print(f"[추출] 첨부문서 메타데이터={len(metadata)}건")

    success = failed = skipped = 0
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                result = upload_attachment(s3, args.bucket, session, file_meta, args.timeout)
                file_meta.update(result)
                if result["downloadStatus"] == "success":
                    success += 1
                    print(f"[성공] {label} -> {result['downloadPath']}")
                else:
                    skipped += 1
                    print(f"[건너뜀] {label}: {result['downloadError']}")
            except Exception as exc:
                failed += 1
                file_meta.update(
                    {
                        "downloadStatus": "failed",
                        "downloadPath": "",
                        "downloadSize": 0,
                        "contentType": "",
                        "downloadError": str(exc)[:1000],
                    }
                )
                print(f"[실패] {label}: {exc}")

    manifest_key = put_manifest(s3, args.bucket, metadata, run_dt)
    print(f"[완료] 메타데이터 저장=s3://{args.bucket}/{manifest_key}")
    print(f"[완료] 다운로드 성공={success}건, 실패={failed}건, 건너뜀={skipped}건")


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("양의 정수를 입력하세요.")
    return number


def to_day(value):
    value = value.replace("/", "-")
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"날짜 형식 오류: {value} (예: 2026-06-01)")


def parse_args() -> argparse.Namespace:
    today = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(description="S3 curated JSON 첨부문서 백필 다운로드 도구")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    parser.add_argument("--curated-prefix", default=CURATED_PREFIX, help="curated JSON S3 prefix")
    parser.add_argument("--start", type=to_day, default=today, help="다운로드 대상 시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--end", type=to_day, help="다운로드 대상 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    parser.add_argument("--timeout", type=positive_int, default=60, help="파일 다운로드 제한 시간 초")
    args = parser.parse_args()
    args.end = args.end or args.start
    if args.start > args.end:
        parser.error(f"--start({args.start:%Y-%m-%d})가 --end({args.end:%Y-%m-%d})보다 늦습니다.")
    return args


def main() -> None:
    try:
        run(parse_args())
    except Exception as exc:
        raise SystemExit(f"실패: {exc}") from None


if __name__ == "__main__":
    main()
