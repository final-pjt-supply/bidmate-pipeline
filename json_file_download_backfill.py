"""S3 curated JSON에서 첨부문서 URL을 읽고 파일을 S3에 저장한다 (비동기, 지정 기간 백필).

기본 입력/출력:
- s3://bidmate/raw/curated/backfill/
- s3://bidmate/raw/downloads/backfill/

실행 방법
- python3 json_file_download_backfill.py --start 2026-06-01 --end 2026-06-30
"""

import argparse
import asyncio
import io
import json
import mimetypes
import os
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from schema import parse_dt

try:  # .env가 있으면 로드, 없으면(IAM 역할·export 등) 무시
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")
CURATED_PREFIX = "raw/curated/backfill"
FILES_PREFIX = "raw/downloads/backfill"
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


async def upload_attachment(s3, bucket: str, client, metadata: dict, timeout: int, used_keys: set):
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = await client.get(file_url, timeout=timeout)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url, used_keys)
    extra_args = {"ContentType": content_type} if content_type else None

    body = io.BytesIO(response.content)
    if extra_args:
        await s3.upload_fileobj(body, bucket, key, ExtraArgs=extra_args)
    else:
        await s3.upload_fileobj(body, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": len(response.content),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }


def s3_session():
    try:
        import aioboto3
    except ImportError as exc:
        raise SystemExit("S3 사용을 위해 aioboto3 설치가 필요합니다. 예: pip install aioboto3") from exc
    return aioboto3.Session()


async def put_manifest(s3, bucket: str, metadata: list, run_dt: datetime) -> list:
    """다운로드 메타데이터를 각 공고의 bidNtceDt 기준 연/월로 묶어
    downloads/year=Y/month=M/manifest.json에 저장한다.

    실행 시각(run_dt)이 아니라 공고 자체의 날짜로 묶는 이유: 백필은 실행일과
    수집 대상 기간이 다른 게 정상이라, 실행일 기준으로 쌓으면 어떤 데이터를
    처리한 매니페스트인지 헷갈린다. bidNtceDt가 없으면 run_dt로 대체한다.
    같은 월을 여러 번 나눠 실행하면 이번 실행분으로 그 달의 manifest.json
    전체가 교체된다(병합 아님).
    """
    by_month = {}
    for item in metadata:
        notice_dt = parse_dt(item.get("bidNtceDt")) or run_dt
        by_month.setdefault((notice_dt.year, notice_dt.month), []).append(item)

    keys = []
    for (year, month), items in by_month.items():
        key = f"{FILES_PREFIX}/year={year:04d}/month={month:02d}/manifest.json"
        await s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        keys.append(key)
    return keys


async def run(args: argparse.Namespace) -> None:
    session = s3_session()
    run_dt = datetime.now()
    extracted_at = run_dt.isoformat()
    sem = asyncio.Semaphore(args.concurrency)

    async with session.client("s3") as s3:
        metadata = []
        curated_count = 0
        async for src_key, record in iter_curated_range(s3, args.bucket, args.curated_prefix, args.start, args.end):
            curated_count += 1
            metadata.extend(build_file_metadata(args.bucket, record, src_key, extracted_at))

        print(f"[시작] {args.start:%Y-%m-%d} ~ {args.end:%Y-%m-%d} curated JSON={curated_count}건")
        print(f"[추출] 첨부문서 메타데이터={len(metadata)}건")

        used_keys: set = set()

        async def bound_download(client, file_meta):
            async with sem:
                return await upload_attachment(s3, args.bucket, client, file_meta, args.timeout, used_keys)

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *(bound_download(client, file_meta) for file_meta in metadata),
                return_exceptions=True,
            )

        success = failed = skipped = 0
        for file_meta, result in zip(metadata, results):
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            if isinstance(result, Exception):
                failed += 1
                file_meta.update(
                    {
                        "downloadStatus": "failed",
                        "downloadPath": "",
                        "downloadSize": 0,
                        "contentType": "",
                        "downloadError": str(result)[:1000],
                    }
                )
                print(f"[실패] {label}: {result}")
                continue

            file_meta.update(result)
            if result["downloadStatus"] == "success":
                success += 1
                print(f"[성공] {label} -> {result['downloadPath']}")
            else:
                skipped += 1
                print(f"[건너뜀] {label}: {result['downloadError']}")

        manifest_keys = await put_manifest(s3, args.bucket, metadata, run_dt)

    for manifest_key in manifest_keys:
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
    parser = argparse.ArgumentParser(description="S3 curated JSON 첨부문서 백필 다운로드 도구 (비동기)")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    parser.add_argument("--curated-prefix", default=CURATED_PREFIX, help="curated JSON S3 prefix")
    parser.add_argument("--start", type=to_day, default=today, help="다운로드 대상 시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--end", type=to_day, help="다운로드 대상 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    parser.add_argument("--timeout", type=positive_int, default=60, help="파일 다운로드 제한 시간 초")
    parser.add_argument(
        "--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY, help="동시 다운로드 수 제한 (기본: 8)"
    )
    args = parser.parse_args()
    args.end = args.end or args.start
    if args.start > args.end:
        parser.error(f"--start({args.start:%Y-%m-%d})가 --end({args.end:%Y-%m-%d})보다 늦습니다.")
    return args


def main() -> None:
    try:
        asyncio.run(run(parse_args()))
    except Exception as exc:
        raise SystemExit(f"실패: {exc}") from None


if __name__ == "__main__":
    main()
