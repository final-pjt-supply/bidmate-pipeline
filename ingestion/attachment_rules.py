"""Shared attachment metadata, dedup, and S3 key rules."""

import mimetypes
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from schema import parse_dt


SAFE_KEY = re.compile(r"[^0-9A-Za-z가-힣._=-]+")
DOC_EXT_PRIORITY = ("hwpx", "hwp", "pdf")
ORD_DIGITS = re.compile(r"\d+")


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


def build_file_metadata(bucket: str, record: dict[str, Any], src_key: str, extracted_at: str):
    notice_id = f"{record.get('bid_ntce_no') or 'no-bid-no'}-{record.get('bid_ntce_ord') or '000'}"
    base = {
        "noticeId": notice_id,
        "업무구분": record.get("bid_category") or "미분류",
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
    return apply_dedup(files)


def split_ext(file_name: Any) -> tuple[str, str]:
    name = str(file_name or "").strip()
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        return stem.strip(), ext.strip().lower()
    return name, ""


def apply_dedup(files: list) -> list:
    """중복 첨부를 걸러내고, 남은 첨부에 공고 내 순번(docNo)을 부여한다.

    두 가지 중복을 제거한다:
    1. 동일 URL: 나라장터는 표준공고서(stdNtceDocUrl)를 공고첨부와 같은 URL로 내려주는
       일이 잦다. 같은 URL을 두 번 받으면 파일명 없는 쪽이 _docNN.bin으로 중복 적재되므로,
       앞선 첨부에서 이미 본 URL은 제외한다. (schema.py에서 1차로 걸러지지만, 이미 S3에
       적재된 기존 curated JSON을 처리할 때를 대비한 방어선)
    2. 같은 이름(stem)의 문서가 hwpx/hwp/pdf 여러 확장자로 게시된 경우: 우선순위
       hwpx > hwp > pdf로 하나만 남긴다 (pdf는 hwpx/hwp가 없을 때만 적재). 우선순위 밖
       확장자(zip 등)나 파일명이 없는 첨부는 이 규칙의 대상이 아니다.

    제외된 첨부는 다운로드하지 않되 manifest에는 사유와 함께 남긴다.
    """
    best = {}
    for meta in files:
        stem, ext = split_ext(meta.get("fileName"))
        if stem and ext in DOC_EXT_PRIORITY:
            pri = DOC_EXT_PRIORITY.index(ext)
            best[stem] = min(best.get(stem, pri), pri)

    doc_no = 0
    seen_urls = set()
    for meta in files:
        url = str(meta.get("fileUrl") or "").strip()
        if url and url in seen_urls:
            meta["dedupDropped"] = "같은 URL의 첨부를 이미 적재 (중복 다운로드 방지)"
            continue

        stem, ext = split_ext(meta.get("fileName"))
        if stem and ext in DOC_EXT_PRIORITY and DOC_EXT_PRIORITY.index(ext) > best[stem]:
            meta["dedupDropped"] = (
                f"같은 이름의 {DOC_EXT_PRIORITY[best[stem]]} 문서를 우선 적재"
                " (우선순위 hwpx > hwp > pdf)"
            )
            continue

        if url:
            seen_urls.add(url)
        doc_no += 1
        meta["docNo"] = doc_no
    return files


def format_ord(value: Any) -> str:
    match = ORD_DIGITS.search(str(value or ""))
    return match.group(0).zfill(2) if match else "00"


def file_s3_key(
    prefix: str,
    metadata: dict[str, Any],
    content_type: str,
    file_url: str,
    include_hour: bool = False,
    member_no: int | None = None,
):
    """Build anonymized attachment S3 key from notice id, order, doc number, and extension.

    member_no를 주면 압축 첨부에서 풀려나온 파일로 보고 `_docNN_MM` 형태로 만든다.
    부모 docNo를 그대로 두므로 기존 첨부들의 번호가 밀리지 않고, 어느 압축에서
    나왔는지도 키만 보고 알 수 있다. 파이프라인 쪽 키 파서(pipeline/realtime/src/
    common/paths.py의 _RAW_KEY_RE)는 file_id를 "/가 없는 임의 문자열"로 받으므로
    이 형태를 그대로 통과시킨다(document_id는 "doc03_01"이 된다).
    """
    notice_dt = parse_dt(metadata.get("bidNtceDt")) or datetime.now()
    biz_div = safe_key_part(metadata.get("업무구분"), "미분류")
    bid_no = safe_key_part(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    ord_part = format_ord(metadata.get("bidNtceOrd"))
    ext = guess_ext(str(metadata.get("fileName") or ""), content_type, file_url).lower()
    doc_no = int(metadata.get("docNo") or metadata.get("fileSeq") or 0)
    doc_part = f"doc{doc_no:02d}" if member_no is None else f"doc{doc_no:02d}_{member_no:02d}"
    partition_path = f"{prefix}/biz_div={biz_div}/year={notice_dt:%Y}/month={notice_dt:%m}/day={notice_dt:%d}/"
    if include_hour:
        partition_path += f"hour={notice_dt:%H}/"

    return partition_path + f"{bid_no}_{ord_part}/{bid_no}_{ord_part}_{doc_part}{ext}"
