# -*- coding: utf-8 -*-
"""stage_chunk.py 단위테스트 — chunker는 실제 사용, s3_io는 monkeypatch."""
from embedding.backfill import stage_chunk

EX_KEY = ("extracted/downloads/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
          "/R25BK01213271_001/R25BK01213271_001_doc01.json")
CH_KEY = ("embeddings/backfill/chunks/biz_div=cnstwk/year=2026/month=01/day=02"
          "/R25BK01213271_001/R25BK01213271_001_doc01.json")


def test_chunk_extracted_attaches_ids_and_source():
    doc = {"bid_id": "B1", "document_id": "D1",
           "pages": [{"text": "1. 입찰개요\n용역명: 테스트 사업"},
                     {"text": "2. 입찰참가자격\n중소기업만 참여 가능"}]}
    chunks = stage_chunk.chunk_extracted(doc)
    assert len(chunks) >= 1
    for c in chunks:
        assert c["bid_id"] == "B1"
        assert c["document_id"] == "D1"
        assert c["source"] == "B1_D1"
        assert set(c) >= {"chunk_idx", "type", "text", "source", "bid_id", "document_id"}


def test_chunk_extracted_empty_pages_yields_empty():
    assert stage_chunk.chunk_extracted({"bid_id": "B", "document_id": "D", "pages": []}) == []
    assert stage_chunk.chunk_extracted({"bid_id": "B", "document_id": "D"}) == []


def _patch_s3(monkeypatch, *, exists=False, doc=None):
    put = {}
    monkeypatch.setattr(stage_chunk.s3_io, "object_exists", lambda b, k: exists)
    monkeypatch.setattr(stage_chunk.s3_io, "get_json", lambda b, k: doc)
    monkeypatch.setattr(stage_chunk.s3_io, "put_json",
                        lambda b, k, o: put.update(bucket=b, key=k, obj=o))
    return put


def test_process_one_writes_chunks(monkeypatch):
    doc = {"bid_id": "B1", "document_id": "D1", "pages": [{"text": "1. 개요\n내용"}]}
    put = _patch_s3(monkeypatch, doc=doc)
    status, out = stage_chunk.process_one("bidmate", EX_KEY)
    assert (status, out) == ("processed", CH_KEY)
    assert put["key"] == CH_KEY
    assert isinstance(put["obj"], list) and len(put["obj"]) >= 1


def test_process_one_skips_when_exists(monkeypatch):
    put = _patch_s3(monkeypatch, exists=True)
    status, out = stage_chunk.process_one("bidmate", EX_KEY)
    assert (status, out) == ("skipped", CH_KEY)
    assert put == {}  # put_json 호출 안 됨


def test_process_one_writes_empty_list_for_no_chunks(monkeypatch):
    doc = {"bid_id": "B", "document_id": "D", "pages": [{"text": "   "}]}
    put = _patch_s3(monkeypatch, doc=doc)
    status, out = stage_chunk.process_one("bidmate", EX_KEY)
    assert status == "processed"
    assert put["obj"] == []  # 빈 출력도 기록(멱등)


def test_chunk_extracted_non_dict_yields_empty():
    assert stage_chunk.chunk_extracted([]) == []
    assert stage_chunk.chunk_extracted("garbage") == []
