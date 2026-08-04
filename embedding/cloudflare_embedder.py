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
import logging
import os
import random
import threading
import time

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

_MODEL = "@cf/baai/bge-m3"
_client: OpenAI | None = None

# 429/일시적 오류만 재시도. 그 외(인증·400 등)는 즉시 raise.
_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)
_MAX_RETRIES = 6
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0


def _create_with_retry(client, model, batch):
    """embeddings.create를 지수 백오프+jitter로 재시도한다.

    jitter는 워커 N개가 동시에 429를 맞고 동시에 재시도하는 thundering herd를
    흩뜨린다(대기 = base*2^n 의 50~100% 구간 랜덤).
    """
    attempt = 0
    while True:
        try:
            return client.embeddings.create(model=model, input=batch)
        except _RETRYABLE as e:
            attempt += 1
            if attempt > _MAX_RETRIES:
                raise
            delay = min(_MAX_DELAY, _BASE_DELAY * 2 ** (attempt - 1))
            delay *= 0.5 + random.random() * 0.5  # 50~100% jitter
            logger.warning("Cloudflare 재시도 %d/%d: %s (%.1fs 대기)",
                           attempt, _MAX_RETRIES, type(e).__name__, delay)
            time.sleep(delay)


_client_lock = threading.Lock()


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # 락 획득 후 재확인(동시 생성 방지)
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
        response = _create_with_retry(client, _MODEL, batch)
        # 응답 순서가 요청 순서와 다를 수 있으므로 item.index로 정렬해 매핑한다
        # (위치 기반 zip은 배치 내 벡터-청크가 조용히 뒤바뀔 위험).
        vectors.extend(
            item.embedding for item in sorted(response.data, key=lambda d: d.index)
        )
    elapsed = time.time() - t0

    logger.info(
        "[임베딩] %d개 청크 완료 (%.1f초, 청크당 %.0fms)",
        len(chunks), elapsed, elapsed / len(chunks) * 1000,
    )

    return [{**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors)]
