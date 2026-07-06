# -*- coding: utf-8 -*-
"""PDF 텍스트 추출 (PyMuPDF) + 스캔본 감지. AWS 의존성 없음 — 로컬 테스트 대상."""
from extractors.base import ExtractResult


def extract(pdf_bytes: bytes) -> ExtractResult:
    raise NotImplementedError
