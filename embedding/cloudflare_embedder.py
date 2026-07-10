# -*- coding: utf-8 -*-
"""Cloudflare Workers AI(BGE-M3)로 청크를 임베딩한다.

로컬 GPU/CPU 추론(embedder.py, FlagEmbedding) 대신 Cloudflare가 호스팅하는
BGE-M3를 API로 호출한다 — GPU 없이도, EC2를 상시 기동하지 않아도 스케일투제로로
쓸 수 있어서 실시간 파이프라인(Lambda) 편입을 염두에 두고 선택함. Cloudflare가
OpenAI 호환 엔드포인트(/v1/embeddings)를 제공해서, extractors/llm/client.py의
NVIDIA Build 호출과 동일한 OpenAI SDK 패턴을 그대로 쓴다.

인증: 환경변수 CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN 필요(.env 로딩은
호출하는 쪽 책임 — 이 모듈은 os.environ만 읽는다, Lambda 이식 시 그대로 재사용
가능하게).
"""
import os
import time

from openai import OpenAI

_MODEL = "@cf/baai/bge-m3"
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        api_token = os.environ["CLOUDFLARE_API_TOKEN"]
        _client = OpenAI(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            api_key=api_token,
        )
    return _client


def embed(chunks: list[dict], batch_size: int = 20) -> list[dict]:
    """청크 리스트를 받아 각 청크에 'vector' 필드를 추가해 반환.

    embedding/embedder.py(로컬 추론 버전)와 동일한 embed(chunks) -> list[dict]
    인터페이스를 유지한다 — 나중에 Lambda handler로 옮길 때 호출부가 안 바뀌게.
    batch_size는 Cloudflare 쪽 요청 payload 한도(10MB)에 맞춰 보수적으로 잡은
    값이라 실제 호출 결과 보고 조정할 수 있다.
    """
    if not chunks:
        return []

    client = _get_client()
    texts = [c["text"] for c in chunks]
    vectors: list[list[float]] = []

    t0 = time.time()
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        response = client.embeddings.create(model=_MODEL, input=batch)
        vectors.extend(item.embedding for item in response.data)
    elapsed = time.time() - t0

    print(
        f"[임베딩] {len(chunks)}개 청크 완료 "
        f"({elapsed:.1f}초, 청크당 {elapsed / len(chunks) * 1000:.0f}ms)"
    )

    return [{**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors)]
