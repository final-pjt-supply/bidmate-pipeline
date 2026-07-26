# -*- coding: utf-8 -*-
"""제목 백필의 대상 선택과 OpenSearch 문서 계약을 고정한다."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = (
    Path(__file__).parent.parent / "scripts" / "backfill_title_vectors.py"
)
SPEC = importlib.util.spec_from_file_location("backfill_title_vectors", SCRIPT)
backfill = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = backfill
SPEC.loader.exec_module(backfill)


def _record(bid_id="R26BK01620154_000", title="학교 네트워크 개선 사업"):
    return backfill.TitleRecord(
        bid_id=bid_id,
        title=title,
        key=f"raw/curated/{bid_id}.json",
        last_modified=datetime(2026, 7, 26, tzinfo=timezone.utc),
    )


def test_select_targets_skips_same_title_model_and_version():
    record = _record()
    targets, unchanged = backfill.select_targets(
        {record.bid_id: record},
        {
            record.bid_id: {
                "text": record.title,
                "embedding_model": backfill.MODEL,
                "embedding_version": backfill.EMBEDDING_VERSION,
            }
        },
    )

    assert targets == []
    assert unchanged == 1


def test_select_targets_reindexes_changed_title():
    record = _record(title="변경된 공고 제목")
    targets, unchanged = backfill.select_targets(
        {record.bid_id: record},
        {
            record.bid_id: {
                "text": "예전 공고 제목",
                "embedding_model": backfill.MODEL,
                "embedding_version": backfill.EMBEDDING_VERSION,
            }
        },
    )

    assert targets == [record]
    assert unchanged == 0


def test_build_title_action_matches_realtime_lambda_document_id():
    record = _record()
    metadata, source = backfill.build_title_action(
        record, [0.1, 0.2], "2026-07-26T00:00:00+00:00"
    )

    assert metadata["index"]["_id"] == "R26BK01620154_000_title::0"
    assert source["file_id"] == "R26BK01620154_000_title"
    assert source["document_id"] == "title"
    assert source["chunk_idx"] == 0
    assert source["type"] == "title"
    assert source["vector"] == [0.1, 0.2]
