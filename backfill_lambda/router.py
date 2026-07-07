# -*- coding: utf-8 -*-
"""매직바이트로 실제 포맷을 판정해 알맞은 추출기로 라우팅(확장자 오라벨 대응).

기존 parsing/ 을 수정하지 않고 읽기전용으로 재사용한다.
"""
import logging
import os

from parsing.contract import ExtractResult  # 타입 힌트용(읽기전용 재사용)

logger = logging.getLogger(__name__)

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _detect_format(data: bytes, filename: str) -> str:
    """매직바이트로 실제 포맷 판정, 불명 시 확장자 폴백."""
    head = data[:8]
    if head[:4] == b"PK\x03\x04":
        return "hwpx"
    if head == _OLE:
        return "hwp"
    if head[:4] == b"%PDF":
        return "pdf"
    return os.path.splitext(filename)[1].lower().lstrip(".")


def extract_document(data: bytes, filename: str, describe_fn=None) -> ExtractResult:
    fmt = _detect_format(data, filename)
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if fmt != ext:  # 오라벨 흔적 — 리포트↔로그 상관용 breadcrumb
        logger.info("format mismatch: filename=%s ext=%s detected=%s", filename, ext, fmt)
    if fmt == "hwpx":
        from parsing.hwpx_extractor import extract_hwpx
        return extract_hwpx(data, describe_fn=describe_fn)
    if fmt == "hwp":
        from parsing.hwp_extractor import extract_hwp
        return extract_hwp(data, describe_fn=describe_fn)
    if fmt == "pdf":
        from backfill_lambda.pdf_extractor import extract_pdf
        return extract_pdf(data, describe_fn=describe_fn)
    raise ValueError(f"지원하지 않는 형식: {fmt}")
