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
    """Keep only the best hwpx/hwp/pdf variant for files with the same stem."""
    best = {}
    for meta in files:
        stem, ext = split_ext(meta.get("fileName"))
        if stem and ext in DOC_EXT_PRIORITY:
            pri = DOC_EXT_PRIORITY.index(ext)
            best[stem] = min(best.get(stem, pri), pri)

    doc_no = 0
    for meta in files:
        stem, ext = split_ext(meta.get("fileName"))
        if stem and ext in DOC_EXT_PRIORITY and DOC_EXT_PRIORITY.index(ext) > best[stem]:
            meta["dedupDropped"] = (
                f"같은 이름의 {DOC_EXT_PRIORITY[best[stem]]} 문서를 우선 적재"
                " (우선순위 hwpx > hwp > pdf)"
            )
            continue
        doc_no += 1
        meta["docNo"] = doc_no
    return files


def format_ord(value: Any) -> str:
    match = ORD_DIGITS.search(str(value or ""))
    return match.group(0).zfill(2) if match else "00"


def file_s3_key(prefix: str, metadata: dict[str, Any], content_type: str, file_url: str):
    """Build anonymized attachment S3 key from notice id, order, doc number, and extension."""
    notice_dt = parse_dt(metadata.get("bidNtceDt")) or datetime.now()
    biz_div = safe_key_part(metadata.get("업무구분"), "미분류")
    bid_no = safe_key_part(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    ord_part = format_ord(metadata.get("bidNtceOrd"))
    ext = guess_ext(str(metadata.get("fileName") or ""), content_type, file_url).lower()
    doc_no = int(metadata.get("docNo") or metadata.get("fileSeq") or 0)

    return (
        f"{prefix}/year={notice_dt:%Y}/month={notice_dt:%m}/day={notice_dt:%d}/"
        f"biz_div={biz_div}/{bid_no}_{ord_part}/{bid_no}_{ord_part}_doc{doc_no:02d}{ext}"
    )
