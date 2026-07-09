# -*- coding: utf-8 -*-
"""backfill/paths.py 단위테스트 — bidmate 실버킷 backfill 키로 검증."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from backfill import paths  # noqa: E402

REAL_EXTRACTED_BACKFILL_KEY = (
    "extracted/downloads/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
    "/R25BK01213271_001/R25BK01213271_001_doc01.json"
)
EXPECTED_QUALIFICATIONS_KEY = (
    "qualifications/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
    "/R25BK01213271_001/R25BK01213271_001_doc01.json"
)


def test_converts_extracted_backfill_to_qualifications():
    assert (
        paths.extracted_backfill_key_to_qualifications_key(REAL_EXTRACTED_BACKFILL_KEY)
        == EXPECTED_QUALIFICATIONS_KEY
    )


def test_drops_downloads_keeps_backfill():
    out = paths.extracted_backfill_key_to_qualifications_key(REAL_EXTRACTED_BACKFILL_KEY)
    assert out.startswith("qualifications/backfill/")
    assert "downloads/" not in out


def test_rejects_realtime_daily_key():
    daily_key = (
        "extracted/daily/biz_div=servc/year=2026/month=07/day=07/hour=17"
        "/R26BK01620154_000/R26BK01620154_000_doc01.json"
    )
    with pytest.raises(ValueError):
        paths.extracted_backfill_key_to_qualifications_key(daily_key)


def test_rejects_non_json_key():
    bad = "extracted/downloads/backfill/biz_div=cnstwk/year=2026/month=01/day=02/R25BK01213271_001/R25BK01213271_001_doc01.hwp"
    with pytest.raises(ValueError):
        paths.extracted_backfill_key_to_qualifications_key(bad)
