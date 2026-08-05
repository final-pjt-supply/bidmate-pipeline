"""S3 curated JSON에서 첨부문서 URL을 읽고 파일을 S3에 저장한다.

기본 입력/출력:
- s3://bidmate/raw/curated/daily/
- s3://bidmate/raw/downloads/daily/
"""

import argparse
import io
import json
import logging
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import archive_rules
from attachment_rules import DOC_EXT_PRIORITY, build_file_metadata, file_s3_key, split_ext

logger = logging.getLogger(__name__)

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


class _Prefixed(io.RawIOBase):
    """이미 읽어버린 앞부분(head)을 스트림 앞에 되붙여 주는 읽기 전용 래퍼.

    첨부가 zip인지 DRM인지 보려면 앞 몇 바이트를 먼저 읽어야 하는데, 그러고 나면
    원본 스트림은 그만큼 소비돼 있다. 평범한 첨부(대다수)까지 메모리에 통째로
    올리지 않고 기존 스트리밍 업로드를 유지하려고 둔다.
    """

    def __init__(self, head: bytes, rest):
        self._head = io.BytesIO(head)
        self._rest = rest

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._head.read() + self._rest.read()
        chunk = self._head.read(size)
        if len(chunk) < size:
            chunk += self._rest.read(size - len(chunk))
        return chunk

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def _read_limited(stream, limit: int) -> bytes:
    """스트림을 limit+1 바이트까지 읽는다(상한 초과 여부를 호출부가 판단할 수 있게).

    read(n)이 항상 n바이트를 준다는 보장이 없어 EOF까지 반복해서 읽는다 — 한 번만
    호출하면 압축 파일이 조용히 잘려 해제 단계에서 깨진다.
    """
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        chunk = stream.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _skipped(reason: str) -> dict[str, Any]:
    """적재하지 않기로 한 첨부의 결과 행. 사유는 manifest에 그대로 남는다."""
    return {
        "downloadStatus": "skipped",
        "downloadPath": "",
        "downloadSize": 0,
        "contentType": "",
        "downloadError": reason,
    }


def _expand_archive(
    s3,
    bucket: str,
    metadata: dict[str, Any],
    head: bytes,
    stream,
    content_type: str,
    file_url: str,
) -> list[dict[str, Any]]:
    """압축 첨부를 풀어 지원 확장자 멤버만 적재하고, 멤버마다 manifest 행을 만든다.

    zip 원본은 멤버를 모두 올린 뒤 마지막에 올린다. 중간에 실패하면 원본이 없으므로
    다음 run의 skip_existing 캐시가 미스가 되어 처음부터 다시 시도한다(원본을 먼저
    올리면 멤버가 빠진 채로 캐시 히트가 나서 영구히 결손된다).
    """
    body = head + _read_limited(stream, archive_rules.MAX_ARCHIVE_BYTES - len(head))
    if len(body) > archive_rules.MAX_ARCHIVE_BYTES:
        return [_skipped(f"압축 첨부 크기 상한 초과: {archive_rules.MAX_ARCHIVE_BYTES:,}바이트")]

    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        infos = archive.infolist()
        problem = archive_rules.guard_archive(infos)
        if problem:
            return [_skipped(f"압축 해제 보류: {problem}")]

        members = archive_rules.select_zip_members(infos)
        if not members:
            return [_skipped("압축 안에 지원 확장자(hwpx/hwp/pdf) 파일이 없습니다.")]

        for member in members:
            member_meta = {
                **metadata,
                "fileName": member["name"],
                "fileId": f"{metadata.get('fileId')}-{member['memberNo']}",
                "zipMemberNo": member["memberNo"],
                "zipSourceFileName": metadata.get("fileName"),
            }
            member_key = file_s3_key(
                FILES_PREFIX, member_meta, "", file_url, include_hour=True,
                member_no=member["memberNo"],
            )
            payload = archive.read(member["info"])
            s3.put_object(Bucket=bucket, Key=member_key, Body=payload)
            rows.append(
                {
                    **member_meta,
                    "downloadStatus": "success",
                    "downloadCached": False,
                    "downloadPath": f"s3://{bucket}/{member_key}",
                    "downloadSize": len(payload),
                    "contentType": "",
                    "downloadError": "",
                    "s3Bucket": bucket,
                    "s3Key": member_key,
                }
            )

    archive_key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url, include_hour=True)
    s3.put_object(
        Bucket=bucket, Key=archive_key, Body=body,
        **({"ContentType": content_type} if content_type else {}),
    )
    logger.info("압축 첨부 해제: %s -> %d개 적재", archive_key, len(rows))

    head_row = {
        "downloadStatus": "success",
        "downloadCached": False,
        "downloadPath": f"s3://{bucket}/{archive_key}",
        "downloadSize": len(body),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": archive_key,
        "archiveMemberCount": len(rows),
    }
    return [head_row, *rows]


def upload_attachment(
    s3,
    bucket: str,
    session: requests.Session,
    metadata: dict[str, Any],
    timeout: int,
    skip_existing: bool = True,
) -> list[dict[str, Any]]:
    """첨부 1건을 적재하고 manifest 행 목록을 돌려준다.

    반환이 리스트인 이유는 압축 첨부 때문이다 — zip 하나가 문서 여러 개가 되므로
    "첨부 1건 = manifest 1행"이 성립하지 않는다. 첫 원소는 언제나 원본 첨부의 결과이고,
    뒤따르는 원소는 압축에서 풀려나온 문서들이다.
    """
    dedup_reason = metadata.get("dedupDropped")
    if dedup_reason:
        return [_skipped(f"중복 제거: {dedup_reason}")]

    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return [_skipped("fileUrl이 비어 있습니다.")]

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
                return [
                    {
                        "downloadStatus": "success",
                        "downloadCached": True,
                        "downloadPath": f"s3://{bucket}/{cached_key}",
                        "downloadSize": cached_size,
                        "contentType": head.get("ContentType", ""),
                        "downloadError": "",
                        "s3Bucket": bucket,
                        "s3Key": cached_key,
                    }
                ]

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()
    response.raw.decode_content = True

    content_type = response.headers.get("Content-Type", "")
    file_name = str(metadata.get("fileName") or "")

    # 확장자만으로는 DRM도 압축도 구분할 수 없어(둘 다 .hwp/.zip 같은 평범한 이름) 앞
    # 몇 바이트를 먼저 본다. 평범한 첨부는 _Prefixed로 되붙여 기존 스트리밍 업로드 유지.
    peek = response.raw.read(archive_rules.PEEK_BYTES)
    kind = archive_rules.detect_payload_kind(peek, file_name)

    if kind == "drm":
        logger.warning("DRM 첨부라 적재하지 않음: %s (%s)", file_name, file_url)
        return [_skipped("DRM으로 보호된 첨부라 추출이 불가능해 적재하지 않았습니다.")]

    if kind == "zip":
        return _expand_archive(s3, bucket, metadata, peek, response.raw, content_type, file_url)

    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url, include_hour=True)
    body = _Prefixed(peek, response.raw)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3.upload_fileobj(body, bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_fileobj(body, bucket, key)

    return [
        {
            "downloadStatus": "success",
            "downloadCached": False,
            "downloadPath": f"s3://{bucket}/{key}",
            "downloadSize": int(response.headers.get("Content-Length") or 0),
            "contentType": content_type,
            "downloadError": "",
            "s3Bucket": bucket,
            "s3Key": key,
        }
    ]


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


def _count_supported_objects(s3, bucket: str, prefix: str) -> int:
    """공고 폴더 아래에 실제로 적재된 지원 확장자(hwpx/hwp/pdf) 객체 수를 센다.

    0바이트는 끊긴 업로드의 잔해라 세지 않는다(find_existing_object 쪽과 같은 판단).
    """
    paginator = s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            _, ext = split_ext(obj["Key"].rsplit("/", 1)[-1])
            if ext in DOC_EXT_PRIORITY and int(obj.get("Size") or 0) > 0:
                count += 1
    return count


def _notice_key(row: dict[str, Any]) -> tuple[str, str] | None:
    bid_no = str(row.get("bidNtceNo") or "").strip()
    if not bid_no:
        return None
    return bid_no, str(row.get("bidNtceOrd") or "").strip()


def correct_expected_counts(s3, bucket: str, rows: list[dict[str, Any]]) -> dict[str, int]:
    """S3에 실제로 적재된 문서 수로 curated JSON의 expected_file_count를 정정한다.

    수집 단계(raw_json_daily.py -> schema._effective_expected_count)는 다운로드 전에
    첨부 URL·파일명만 보고 이 값을 매긴다. 그래서 압축 안에 문서가 몇 개인지, 그게
    DRM이라 못 여는지 알 수 없다. 실제로 받아본 여기서만 확정할 수 있어 사후에 고쳐 쓴다.
    bid_table.expected_file_count의 정의("처리 대상 파일 수(다운로드 성공+지원 형식만)",
    db/schema/01_bid_table.sql)와도 다운로드 후 집계 쪽이 더 가깝다.

    이번 run이 전부 캐시 히트라 아무것도 안 올렸을 수 있으므로, 이 run의 적재 결과가
    아니라 S3의 현재 상태를 센다. 다운로드 실패가 섞인 공고는 건드리지 않는다 — 일시적
    실패로 개수를 낮춰 잡으면 덜 처리된 공고가 merged로 확정되고, merged 행은 병합
    배치가 다시 뽑지 않아 되돌릴 수 없다.
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        src = str(row.get("srcJsonPath") or "")
        notice = _notice_key(row)
        parts = src.split("/", 3)
        if notice is None or not src.startswith("s3://") or len(parts) < 4:
            continue
        grouped.setdefault((parts[3], notice[0], notice[1]), []).append(row)

    wanted: dict[str, dict[tuple[str, str], int]] = {}
    for (curated_key, bid_no, bid_ord), group in grouped.items():
        if any(r.get("downloadStatus") == "failed" for r in group):
            logger.warning(
                "다운로드 실패가 있어 expected_file_count 정정을 보류: %s-%s", bid_no, bid_ord
            )
            continue
        s3_keys = [str(r.get("s3Key")) for r in group if r.get("s3Key")]
        if not s3_keys:
            continue
        prefix = s3_keys[0].rsplit("/", 1)[0] + "/"
        wanted.setdefault(curated_key, {})[(bid_no, bid_ord)] = _count_supported_objects(
            s3, bucket, prefix
        )

    stats = {"files": 0, "notices": 0}
    for curated_key, per_notice in wanted.items():
        payload = json.loads(
            s3.get_object(Bucket=bucket, Key=curated_key)["Body"].read().decode("utf-8")
        )
        records = payload if isinstance(payload, list) else [payload]
        changed = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            key = (
                str(record.get("bid_ntce_no") or "").strip(),
                str(record.get("bid_ntce_ord") or "").strip(),
            )
            new_count = per_notice.get(key)
            if new_count is None or record.get("expected_file_count") == new_count:
                continue
            logger.info(
                "expected_file_count 정정: %s-%s %s -> %d",
                key[0], key[1], record.get("expected_file_count"), new_count,
            )
            record["expected_file_count"] = new_count
            changed += 1

        if not changed:
            continue
        s3.put_object(
            Bucket=bucket,
            Key=curated_key,
            Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        stats["files"] += 1
        stats["notices"] += changed
    return stats


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

    logger.info("최근 %d분 curated JSON=%d건", args.minutes, curated_count)
    logger.info("첨부문서 메타데이터=%d건", len(metadata))

    # 압축 첨부 하나가 문서 여러 개가 되므로 manifest 행 수는 첨부 수와 달라진다.
    # 원본 리스트를 제자리 갱신하지 않고 결과 행을 새로 쌓는다.
    rows: list[dict[str, Any]] = []
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                results = upload_attachment(
                    s3, args.bucket, session, file_meta, args.timeout, skip_existing=not args.force
                )
            except Exception as exc:
                file_meta.update(
                    {
                        "downloadStatus": "failed",
                        "downloadPath": "",
                        "downloadSize": 0,
                        "contentType": "",
                        "downloadError": str(exc)[:1000],
                    }
                )
                rows.append(file_meta)
                logger.warning("[실패] %s: %s", label, exc)
                continue

            head_result, *member_results = results
            file_meta.update(head_result)
            rows.append(file_meta)
            rows.extend(member_results)

            if head_result["downloadStatus"] != "success":
                logger.info("[건너뜀] %s: %s", label, head_result["downloadError"])
            elif head_result.get("downloadCached"):
                logger.info("[캐시] %s -> 이미 적재됨, 재다운로드 생략", label)
            elif member_results:
                logger.info(
                    "[해제] %s -> %s (문서 %d개)",
                    label, head_result["downloadPath"], len(member_results),
                )
            else:
                logger.info("[성공] %s -> %s", label, head_result["downloadPath"])

    success = sum(1 for r in rows if r.get("downloadStatus") == "success")
    cached = sum(1 for r in rows if r.get("downloadCached"))
    skipped = sum(1 for r in rows if r.get("downloadStatus") == "skipped")
    failed = sum(1 for r in rows if r.get("downloadStatus") == "failed")

    corrected = correct_expected_counts(s3, args.bucket, rows)
    logger.info(
        "expected_file_count 정정: curated %d개 파일의 공고 %d건",
        corrected["files"], corrected["notices"],
    )

    manifest_key = put_manifest(s3, args.bucket, rows, run_end)
    logger.info("메타데이터 저장=s3://%s/%s", args.bucket, manifest_key)
    logger.info(
        "다운로드 성공=%d건(신규=%d, 캐시=%d), 실패=%d건, 건너뜀=%d건",
        success, success - cached, cached, failed, skipped,
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
    # Airflow가 이 파일을 subprocess로 돌리고 stdout을 태스크 로그로 걷어간다. 루트 로거
    # 기본 레벨이 WARNING이라 설정을 안 하면 INFO 진행 로그가 통째로 사라진다
    # (realtime 추출 Lambda에서 실제로 겪은 함정 — handlers/extract_hwp.py 주석 참고).
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run(parse_args())
    except Exception as exc:
        raise SystemExit(f"실패: {exc}") from None

if __name__ == "__main__":
    main()
