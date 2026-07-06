# -*- coding: utf-8 -*-
"""HWP(구버전 바이너리 포맷) 텍스트 추출. 파싱 방식 미정 — 로컬에서 먼저 뚫고 확정한다."""
from extractors.base import ExtractResult


def extract(hwp_bytes: bytes) -> ExtractResult:
    raise NotImplementedError
