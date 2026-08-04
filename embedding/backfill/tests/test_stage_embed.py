# -*- coding: utf-8 -*-
"""stage_embed.py 단위테스트 — s3_io·cloudflare_embedder를 monkeypatch."""
from embedding.backfill import stage_embed

CH_KEY = ("embeddings/backfill/chunks/biz_div=cnstwk/year=2026/month=01/day=02"
          "/R25BK01213271_001/R25BK01213271_001_doc01.json")
EM_KEY = ("embeddings/backfill/embedded/biz_div=cnstwk/year=2026/month=01/day=02"
          "/R25BK01213271_001/R25BK01213271_001_doc01.json")


def _patch(monkeypatch, *, exists=False, chunks=None, embed_ret=None):
    put = {}
    calls = {"embed": 0}
    monkeypatch.setattr(stage_embed.s3_io, "object_exists", lambda b, k: exists)
    monkeypatch.setattr(stage_embed.s3_io, "get_json", lambda b, k: chunks)
    monkeypatch.setattr(stage_embed.s3_io, "put_json",
                        lambda b, k, o: put.update(key=k, obj=o))

    def fake_embed(cs):
        calls["embed"] += 1
        return embed_ret if embed_ret is not None else [{**c, "vector": [0.1]} for c in cs]

    monkeypatch.setattr(stage_embed.cloudflare_embedder, "embed", fake_embed)
    return put, calls


def test_process_one_embeds_and_writes(monkeypatch):
    chunks = [{"chunk_idx": 0, "type": "text", "text": "x", "source": "B_D",
               "bid_id": "B", "document_id": "D"}]
    put, calls = _patch(monkeypatch, chunks=chunks)
    status, out = stage_embed.process_one("bidmate", CH_KEY)
    assert (status, out) == ("processed", EM_KEY)
    assert put["key"] == EM_KEY
    assert put["obj"][0]["vector"] == [0.1]
    assert calls["embed"] == 1


def test_process_one_skips_when_exists(monkeypatch):
    put, calls = _patch(monkeypatch, exists=True)
    status, out = stage_embed.process_one("bidmate", CH_KEY)
    assert (status, out) == ("skipped", EM_KEY)
    assert put == {}
    assert calls["embed"] == 0


def test_process_one_empty_chunks_writes_empty(monkeypatch):
    put, calls = _patch(monkeypatch, chunks=[], embed_ret=[])
    status, out = stage_embed.process_one("bidmate", CH_KEY)
    assert status == "processed"
    assert put["obj"] == []
