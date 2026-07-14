# -*- coding: utf-8 -*-
"""merge/inventory.py 단위테스트 — common.s3를 monkeypatch로 대체해 실제 S3 호출 없이 검증."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common import s3  # noqa: E402
from merge import inventory  # noqa: E402

BACKFILL_KEY = (
    "qualifications/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
    "/R25BK01152374_000/R25BK01152374_000_doc01.json"
)
DAILY_KEY = (
    "qualifications/daily/biz_div=cnstwk/year=2026/month=07/day=08/hour=17"
    "/R26BK01623782_000/R26BK01623782_000_doc02.json"
)
LAST_MODIFIED = datetime(2026, 7, 9, 5, 32, 44, tzinfo=timezone.utc)


def test_qualifications_prefix_is_daily_only():
    """#80 — 백필(qualifications/backfill/)은 조원 파이프라인 소관이라 병합
    대상에서 영구 제외한다. list_objects가 daily/ 밑만 보도록 prefix 자체가
    좁혀져 있어야 한다(운영에서 실제로 backfill 키가 반환될 일이 없음)."""
    assert inventory.QUALIFICATIONS_PREFIX == "qualifications/daily/"


def test_parse_key_backfill_without_hour():
    """_parse_key 자체는 stage를 안 가리므로(파싱 유틸 단위테스트) backfill 키도
    형식만 맞으면 파싱된다 — 실제로 backfill이 인벤토리에 안 들어가는 건
    QUALIFICATIONS_PREFIX가 daily/로 좁혀져 list_objects가 애초에 그 키를 안
    돌려주기 때문(위 테스트 + build_inventory 테스트가 그 경로를 검증)."""
    result = inventory._parse_key(BACKFILL_KEY)
    assert result == ("R25BK01152374_000", "doc01")


def test_parse_key_daily_with_hour():
    result = inventory._parse_key(DAILY_KEY)
    assert result == ("R26BK01623782_000", "doc02")


def test_parse_key_rejects_malformed_key():
    assert inventory._parse_key("qualifications/backfill/not-a-valid-key.json") is None


def test_build_inventory_calls_list_objects_with_daily_prefix_only(monkeypatch):
    """build_inventory가 s3.list_objects에 넘기는 prefix가 daily/로 고정돼
    있는지 — 이 자체가 backfill을 인벤토리에서 원천 배제하는 지점이다."""
    def fake_list_objects(bucket, prefix):
        assert bucket == "bidmate"
        assert prefix == "qualifications/daily/"
        yield {"key": DAILY_KEY, "last_modified": LAST_MODIFIED}

    monkeypatch.setattr(s3, "list_objects", fake_list_objects)

    result = inventory.build_inventory("bidmate")
    assert set(result.keys()) == {"R26BK01623782_000"}
    ref = result["R26BK01623782_000"][0]
    assert ref.s3_key == DAILY_KEY
    assert ref.document_id == "doc02"
    assert ref.last_modified == LAST_MODIFIED.isoformat()


def test_build_inventory_skips_malformed_keys_with_warning(monkeypatch, caplog):
    def fake_list_objects(bucket, prefix):
        yield {"key": "qualifications/garbage.json", "last_modified": LAST_MODIFIED}

    monkeypatch.setattr(s3, "list_objects", fake_list_objects)

    with caplog.at_level("WARNING"):
        result = inventory.build_inventory("bidmate")
    assert result == {}
    assert any("건너뜀" in r.message for r in caplog.records)


def test_fetch_documents_injects_last_modified(monkeypatch):
    ref = inventory.QualificationFileRef(
        s3_key=BACKFILL_KEY, document_id="doc01", last_modified="2026-07-09T05:32:44+00:00",
    )

    def fake_get_object(bucket, key):
        assert key == BACKFILL_KEY
        return b'{"bid_id": "R25BK01152374_000", "document_id": "doc01"}'

    monkeypatch.setattr(s3, "get_object", fake_get_object)

    docs = inventory.fetch_documents("bidmate", [ref])
    assert docs == [{
        "bid_id": "R25BK01152374_000",
        "document_id": "doc01",
        "s3_last_modified": "2026-07-09T05:32:44+00:00",
    }]
