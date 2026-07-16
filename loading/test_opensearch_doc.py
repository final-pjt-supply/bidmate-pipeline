# -*- coding: utf-8 -*-
"""opensearch_doc 매핑 단위테스트(순수)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import opensearch_doc as od  # noqa: E402


def _chunk(idx=0, source="R25BK01152374_000_doc01"):
    return {
        "chunk_idx": idx, "type": "text", "text": f"본문{idx}",
        "source": source, "bid_id": "R25BK01152374_000", "document_id": "doc01",
        "vector": [0.1, 0.2, 0.3],
    }


def test_action_id_and_fields():
    a = od.chunk_to_action(_chunk(3), od.INDEX_NAME, "2026-07-15T00:00:00+00:00")
    assert a["_op_type"] == "index"
    assert a["_index"] == "bid_chunks"
    assert a["_id"] == "R25BK01152374_000_doc01::3"
    s = a["_source"]
    assert s["file_id"] == "R25BK01152374_000_doc01"
    assert s["bid_id"] == "R25BK01152374_000"
    assert s["document_id"] == "doc01"
    assert s["chunk_idx"] == 3
    assert s["type"] == "text"
    assert s["text"] == "본문3"
    assert s["vector"] == [0.1, 0.2, 0.3]
    assert s["embedding_model"] == "@cf/baai/bge-m3"
    assert s["embedding_version"] == "v1"
    assert s["indexed_at"] == "2026-07-15T00:00:00+00:00"
    # 정확히 10개 필드
    assert set(s) == {
        "bid_id", "document_id", "chunk_idx", "text", "type", "vector",
        "file_id", "embedding_model", "embedding_version", "indexed_at",
    }


def test_actions_for_chunks_skips_empty():
    assert list(od.actions_for_chunks([], "bid_chunks", "t")) == []
    assert list(od.actions_for_chunks(None, "bid_chunks", "t")) == []
    acts = list(od.actions_for_chunks([_chunk(0), _chunk(1)], "bid_chunks", "t"))
    assert [a["_id"] for a in acts] == [
        "R25BK01152374_000_doc01::0", "R25BK01152374_000_doc01::1"]
