# -*- coding: utf-8 -*-
"""임베딩 Lambda의 제목 조회·벡터 조립을 AWS/Cloudflare 없이 검증한다."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# 로컬 테스트 환경에는 Lambda 이미지 전용 openai 패키지가 없을 수 있다. 이
# 테스트는 Cloudflare 클라이언트가 아니라 핸들러 조립만 검증하므로 import만 대체.
sys.modules.setdefault("openai", MagicMock(OpenAI=MagicMock()))

from handlers import embed_chunks  # noqa: E402

EXTRACTED_KEY = (
    "extracted/daily/biz_div=servc/year=2026/month=07/day=07/hour=17"
    "/R26BK01620154_000/R26BK01620154_000_doc01.json"
)
CURATED_KEY = (
    "raw/curated/daily/biz_div=servc/year=2026/month=07/day=07/hour=17"
    "/R26BK01620154-000.json"
)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        embed_chunks,
        "load_embedding_config",
        lambda: {
            "cloudflare_account_id": "test-account",
            "cloudflare_api_token": "test-token",
            "index_queue_url": "https://sqs.example/index",
        },
    )


def _fake_embed(chunks, **_kwargs):
    return [{**chunk, "vector": [float(i)]} for i, chunk in enumerate(chunks)]


def test_process_adds_stable_title_document(monkeypatch, configured):
    def fake_get_object(_bucket, key):
        if key == EXTRACTED_KEY:
            return json.dumps({"pages": [{"page": 1, "text": "본문 내용"}]}).encode()
        if key == CURATED_KEY:
            return json.dumps(
                {
                    "bid_id": "R26BK01620154_000",
                    "bid_ntce_nm": "학교 네트워크 개선 사업",
                },
                ensure_ascii=False,
            ).encode()
        raise AssertionError(key)

    monkeypatch.setattr(embed_chunks.s3, "get_object", fake_get_object)
    monkeypatch.setattr(
        embed_chunks.chunker,
        "chunk",
        lambda *_args, **_kwargs: [
            {"chunk_idx": 0, "type": "text", "text": "본문 내용"}
        ],
    )
    monkeypatch.setattr(embed_chunks.cloudflare_embedder, "embed", _fake_embed)
    put_calls = []
    monkeypatch.setattr(
        embed_chunks.s3,
        "put_object",
        lambda bucket, key, body: put_calls.append((bucket, key, body)),
    )
    sent = []
    monkeypatch.setattr(
        embed_chunks.sqs,
        "send_to_queue",
        lambda url, body: sent.append((url, body)),
    )

    embed_chunks._process("bidmate", EXTRACTED_KEY)

    assert len(put_calls) == 1
    result = json.loads(put_calls[0][2])
    assert len(result["chunks"]) == 2
    title = next(c for c in result["chunks"] if c["type"] == "title")
    assert title == {
        "file_id": "R26BK01620154_000_title",
        "bid_id": "R26BK01620154_000",
        "document_id": "title",
        "chunk_idx": 0,
        "type": "title",
        "text": "학교 네트워크 개선 사업",
        "vector": [1.0],
        "embedding_model": embed_chunks.cloudflare_embedder.MODEL,
        "embedding_version": embed_chunks.EMBEDDING_VERSION,
    }
    assert sent == [
        (
            "https://sqs.example/index",
            {"bucket": "bidmate", "key": put_calls[0][1]},
        )
    ]


def test_missing_curated_metadata_keeps_body_embedding(monkeypatch, configured):
    missing = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
        "GetObject",
    )

    def fake_get_object(_bucket, key):
        if key == EXTRACTED_KEY:
            return json.dumps({"pages": [{"page": 1, "text": "본문 내용"}]}).encode()
        raise missing

    monkeypatch.setattr(embed_chunks.s3, "get_object", fake_get_object)
    monkeypatch.setattr(
        embed_chunks.chunker,
        "chunk",
        lambda *_args, **_kwargs: [
            {"chunk_idx": 0, "type": "text", "text": "본문 내용"}
        ],
    )
    monkeypatch.setattr(embed_chunks.cloudflare_embedder, "embed", _fake_embed)
    put_calls = []
    monkeypatch.setattr(
        embed_chunks.s3,
        "put_object",
        lambda bucket, key, body: put_calls.append((bucket, key, body)),
    )
    monkeypatch.setattr(embed_chunks.sqs, "send_to_queue", lambda *_args: None)

    embed_chunks._process("bidmate", EXTRACTED_KEY)

    result = json.loads(put_calls[0][2])
    assert [c["type"] for c in result["chunks"]] == ["text"]


def test_load_title_does_not_swallow_access_denied(monkeypatch):
    denied = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "GetObject",
    )
    monkeypatch.setattr(
        embed_chunks.s3,
        "get_object",
        lambda *_args: (_ for _ in ()).throw(denied),
    )

    with pytest.raises(ClientError):
        embed_chunks._load_bid_title(
            "bidmate", EXTRACTED_KEY, "R26BK01620154_000"
        )
