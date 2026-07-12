# -*- coding: utf-8 -*-
"""Cloudflare Workers AI(BGE-M3)로 청크를 임베딩한다.

embedding/cloudflare_embedder.py(experiments/embedding 로컬 검증본)에서 핵심
로직(배치 호출)을 그대로 가져옴 — 로컬 검증(#54)이 끝난 코드라 로직은 바꾸지
않는다. 이 파일이 새로 한 것은 두 가지뿐:
  1. 원본은 os.environ을 직접 읽었지만(Lambda 이식을 염두에 두고 그렇게 설계된
     것이었음), 이 프로젝트의 handler들은 common/config.py 한 곳에서만
     os.environ을 읽는 관례(config.py 독스트링 "버킷/큐 이름은 코드에
     하드코딩하지 않고 여기서만 읽는다")를 따르므로 account_id/api_token을
     파라미터로 주입받게 바꿈.
  2. print() 진행 로그 → logger.info() (Lambda/CloudWatch 환경에 맞춤).
"""
import logging
import time

from openai import OpenAI

logger = logging.getLogger(__name__)

MODEL = "@cf/baai/bge-m3"

_client: OpenAI | None = None


def _get_client(account_id: str, api_token: str) -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            api_key=api_token,
        )
    return _client


def embed(chunks: list[dict], account_id: str, api_token: str, batch_size: int = 20) -> list[dict]:
    """청크 리스트를 받아 각 청크에 'vector' 필드를 추가해 반환.

    embedding/cloudflare_embedder.py와 동일한 batch_size(Cloudflare 요청 payload
    한도 10MB에 맞춘 보수적인 값)와 배치 루프를 그대로 쓴다.
    """
    if not chunks:
        return []

    client = _get_client(account_id, api_token)
    texts = [c["text"] for c in chunks]
    vectors: list[list[float]] = []

    t0 = time.time()
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        response = client.embeddings.create(model=MODEL, input=batch)
        vectors.extend(item.embedding for item in response.data)
    elapsed = time.time() - t0

    logger.info(
        "임베딩 완료: chunks=%d elapsed_sec=%.1f ms_per_chunk=%.0f",
        len(chunks), elapsed, elapsed / len(chunks) * 1000,
    )

    return [{**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors)]
