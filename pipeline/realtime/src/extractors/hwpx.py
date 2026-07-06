# -*- coding: utf-8 -*-
"""HWPX 텍스트 추출. zip 압축 해제 후 section0.xml을 헤딩 기준으로 블록화한다."""
from extractors.base import ExtractResult


def extract(hwpx_bytes: bytes) -> ExtractResult:
    raise NotImplementedError
