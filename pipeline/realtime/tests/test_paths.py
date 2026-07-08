# -*- coding: utf-8 -*-
"""common/paths.py 단위테스트 — bidmate 실버킷에서 실제로 확인한 key로 검증한다.

2026-07-08 bidmate 전수 조사 시 raw/downloads/daily/biz_div=servc/에서 직접 확인한 키.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common import paths  # noqa: E402

REAL_RAW_KEY = (
    "raw/downloads/daily/biz_div=servc/year=2026/month=07/day=07/hour=17"
    "/R26BK01620154_000/R26BK01620154_000_doc01.hwpx"
)
REAL_BACKFILL_KEY = (
    "raw/downloads/backfill/biz_div=servc/year=2026/month=01/day=02"
    "/R25BK01241102_001/R25BK01241102_001_doc01.hwp"
)


def test_parse_raw_key():
    parts = paths.parse_raw_key(REAL_RAW_KEY)
    assert parts == {
        "stage": "daily",
        "biz_div": "servc",
        "year": "2026",
        "month": "07",
        "day": "07",
        "hour": "17",
        "bid_id": "R26BK01620154_000",
        "file_id": "R26BK01620154_000_doc01",
        "ext": "hwpx",
    }


def test_parse_raw_key_rejects_backfill_without_hour():
    """backfill은 hour= 파티션이 없다 — 우리 realtime 파이프라인이 처리할 대상이 아니므로 거부해야 한다."""
    with pytest.raises(ValueError):
        paths.parse_raw_key(REAL_BACKFILL_KEY)


def test_raw_key_to_text_key_drops_downloads_layer_under_extracted_prefix():
    """extracted/qualifications는 raw의 downloads/ 계층을 뺀다(2026-07-08 확정)."""
    text_key = paths.raw_key_to_text_key(REAL_RAW_KEY)
    assert text_key == (
        "extracted/daily/biz_div=servc/year=2026/month=07/day=07/hour=17"
        "/R26BK01620154_000/R26BK01620154_000_doc01.json"
    )


def test_text_key_to_llm_key_drops_downloads_layer_under_qualifications_prefix():
    text_key = paths.raw_key_to_text_key(REAL_RAW_KEY)
    llm_key = paths.text_key_to_llm_key(text_key)
    assert llm_key == (
        "qualifications/daily/biz_div=servc/year=2026/month=07/day=07/hour=17"
        "/R26BK01620154_000/R26BK01620154_000_doc01.json"
    )


def test_parse_text_key_roundtrip():
    text_key = paths.raw_key_to_text_key(REAL_RAW_KEY)
    parts = paths.parse_text_key(text_key)
    assert parts["bid_id"] == "R26BK01620154_000"
    assert parts["hour"] == "17"


def test_document_id_from_file_id():
    assert paths.document_id_from_file_id("R26BK01620154_000", "R26BK01620154_000_doc01") == "doc01"


def test_document_id_from_file_id_rejects_mismatched_prefix():
    with pytest.raises(ValueError):
        paths.document_id_from_file_id("R26BK01620154_000", "other_doc01")
