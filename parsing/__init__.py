# -*- coding: utf-8 -*-
"""파일 형식을 보고 알맞은 추출기로 라우팅."""
import os

from parsing.contract import ExtractResult
from parsing.hwp_extractor import extract_hwp, extract_hwp_file
from parsing.hwpx_extractor import extract_hwpx, extract_hwpx_file

__all__ = [
    "extract", "extract_bytes", "to_txt", "ExtractResult",
]


def extract(path: str) -> ExtractResult:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".hwpx":
        return extract_hwpx_file(path)
    if ext == ".hwp":
        return extract_hwp_file(path)
    raise ValueError(f"지원하지 않는 형식: {ext}")


def extract_bytes(data: bytes, filename: str) -> ExtractResult:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".hwpx":
        return extract_hwpx(data)
    if ext == ".hwp":
        return extract_hwp(data)
    raise ValueError(f"지원하지 않는 형식: {ext}")


def to_txt(result: ExtractResult) -> str:
    return result["text"]
