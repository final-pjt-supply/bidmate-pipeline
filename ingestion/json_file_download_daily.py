"""S3 curated JSON에서 첨부문서 URL을 읽고 파일을 S3에 저장한다.

기본 입력/출력:
- s3://bidmate/raw/curated/daily/
- s3://bidmate/raw/downloads/daily/
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from attachment_rules import build_file_metadata, file_s3_key

try:  # .env가 있으면 로드, 없으면(IAM 역할·export 등) 무시
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")
CURATED_PREFIX = "raw/curated/daily"
FILES_PREFIX = "raw/downloads/daily"
METADATA_PREFIX = f"{FILES_PREFIX}/_metadata"
CHUNK_SIZE = 1024 * 256


def s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit("S3 사용을 위해 boto3 설치가 필요합니다. 예: pip install boto3") from exc
    return boto3.client("s3")


def predict_s3_key(metadata: dict[str, Any], file_url: str) -> str | None:
    """다운로드하기 전에 이 첨부가 저장될 S3 키를 예측한다. 확정할 수 없으면 None.

    file_s3_key()는 확장자를 정할 때 content_type을 참고하지만, guess_ext()는 파일명에
    확장자가 있으면 content_type을 아예 보지 않는다. 실측상 daily 첨부 495건 전부
    파일명에 확장자가 있어(hwpx/zip/hwp/pdf/xlsx…) 다운로드 전에 키가 확정된다.
    확장자가 없는 예외적 첨부는 None을 돌려 호출자가 그냥 내려받게 한다.
    """
    if "." not in str(metadata.get("fileName") or ""):
        return None
    return file_s3_key(FILES_PREFIX, metadata, "", file_url, include_hour=True)


def find_existing_object(s3, bucket: str, key: str):
    """이미 적재된 객체의 head를 반환한다. 없으면 None.

    404가 아닌 오류(권한 등)는 그대로 올린다. 이를 '없음'으로 오인하면 전 파일을
    다시 내려받게 된다.
    """
    from botocore.exceptions import ClientError  # boto3 지연 임포트 정책을 따른다

    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def day_suffixes(base: str, start_dt: datetime, end_dt: datetime):
    day = datetime(start_dt.year, start_dt.month, start_dt.day)
    end_day = datetime(end_dt.year, end_dt.month, end_dt.day)
    while day <= end_day:
        yield f"{base}year={day:%Y}/month={day:%m}/day={day:%d}/"
        day += timedelta(days=1)


def list_biz_div_prefixes(s3, bucket: str, prefix: str):
    """{prefix}/ 아래 biz_div=CAT/ 공통 프리픽스 목록을 반환."""
    paginator = s3.get_paginator("list_objects_v2")
    prefixes = []
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            prefixes.append(cp["Prefix"])
    return prefixes


def iter_recent_curated(s3, bucket: str, prefix: str, start_utc: datetime, end_utc: datetime):
    paginator = s3.get_paginator("list_objects_v2")
    start_local = start_utc.astimezone().replace(tzinfo=None)
    end_local = end_utc.astimezone().replace(tzinfo=None)

    for base in list_biz_div_prefixes(s3, bucket, prefix):
        for day_prefix in day_suffixes(base, start_local, end_local):
            for page in paginator.paginate(Bucket=bucket, Prefix=day_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.endswith(".json"):
                        continue
                    last_modified = obj["LastModified"]
                    if start_utc <= last_modified <= end_utc:
                        payload = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                        records = json.loads(payload.decode("utf-8"))
                        for record in records if isinstance(records, list) else [records]:
                            if isinstance(record, dict):
                                yield key, record


def upload_attachment(
    s3,
    bucket: str,
    session: requests.Session,
    metadata: dict[str, Any],
    timeout: int,
    skip_existing: bool = True,
):
    dedup_reason = metadata.get("dedupDropped")
    if dedup_reason:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": f"중복 제거: {dedup_reason}",
        }

    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    # DAG는 5분마다 돌지만 다운로드 창(DOWNLOAD_MINUTES)은 15분이라 같은 첨부가 3~4개
    # run에 반복 등장한다. 실측: 고유 488건을 1,600번 내려받아 71%(1GB/2일)를 버렸다.
    # 정정공고는 차수(bidNtceOrd)를 올려 새 키를 만들므로, 키가 이미 있다는 것은
    # 같은 내용을 이미 받았다는 뜻이다.
    if skip_existing:
        cached_key = predict_s3_key(metadata, file_url)
        if cached_key:
            head = find_existing_object(s3, bucket, cached_key)
            # 0바이트는 끊긴 업로드의 잔해다. 건너뛰기 이전에는 다음 run이 덮어써서
            # 스스로 나았으므로, 그 자가 치유 성질을 잃지 않도록 다시 내려받는다.
            cached_size = int((head or {}).get("ContentLength") or 0)
            if head is not None and cached_size > 0:
                return {
                    "downloadStatus": "success",
                    "downloadCached": True,
                    "downloadPath": f"s3://{bucket}/{cached_key}",
                    "downloadSize": cached_size,
                    "contentType": head.get("ContentType", ""),
                    "downloadError": "",
                    "s3Bucket": bucket,
                    "s3Key": cached_key,
                }

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()
    response.raw.decode_content = True

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url, include_hour=True)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3.upload_fileobj(response.raw, bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_fileobj(response.raw, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadCached": False,
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": int(response.headers.get("Content-Length") or 0),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }


def put_manifest(s3, bucket: str, metadata: list[dict[str, Any]], run_dt: datetime):
    key = (
        f"{METADATA_PREFIX}/year={run_dt:%Y}/month={run_dt:%m}/day={run_dt:%d}/hour={run_dt:%H}/"
        f"bid_files_{run_dt:%Y%m%d%H%M%S}.json"
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
    run_end = datetime.now(timezone.utc)
    run_start = run_end - timedelta(minutes=args.minutes)
    extracted_at = run_end.isoformat()

    metadata = []
    curated_count = 0
    for src_key, record in iter_recent_curated(s3, args.bucket, args.curated_prefix, run_start, run_end):
        curated_count += 1
        metadata.extend(build_file_metadata(args.bucket, record, src_key, extracted_at))

    print(f"[시작] 최근 {args.minutes}분 curated JSON={curated_count}건")
    print(f"[추출] 첨부문서 메타데이터={len(metadata)}건")

    success = failed = skipped = cached = 0
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                result = upload_attachment(
                    s3, args.bucket, session, file_meta, args.timeout, skip_existing=not args.force
                )
                file_meta.update(result)
                if result["downloadStatus"] == "success":
                    success += 1
                    if result.get("downloadCached"):
                        cached += 1
                        print(f"[캐시] {label} -> 이미 적재됨, 재다운로드 생략")
                    else:
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

    manifest_key = put_manifest(s3, args.bucket, metadata, run_end)
    print(f"[완료] 메타데이터 저장=s3://{args.bucket}/{manifest_key}")
    print(
        f"[완료] 다운로드 성공={success}건(신규={success - cached}, 캐시={cached}), "
        f"실패={failed}건, 건너뜀={skipped}건"
    )
    if failed:
        raise RuntimeError(
            f"첨부 다운로드 실패 {failed}건이 발생했습니다. manifest=s3://{args.bucket}/{manifest_key}"
        )


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("양의 정수를 입력하세요.")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="S3 curated JSON 첨부문서 5분 단위 다운로드 도구")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    parser.add_argument("--curated-prefix", default=CURATED_PREFIX, help="curated JSON S3 prefix")
    parser.add_argument("--minutes", type=positive_int, default=5, help="최근 몇 분의 curated JSON을 처리할지")
    parser.add_argument("--timeout", type=positive_int, default=60, help="파일 다운로드 제한 시간 초")
    parser.add_argument(
        "--force",
        action="store_true",
        help="S3에 이미 있는 첨부도 다시 내려받는다 (기본: 건너뜀)",
    )
    return parser.parse_args()


def main() -> None:
    try:
        run(parse_args())
    except Exception as exc:
        raise SystemExit(f"실패: {exc}") from None

if __name__ == "__main__":
    main()
