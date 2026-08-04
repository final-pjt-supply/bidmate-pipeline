# -*- coding: utf-8 -*-
"""단계 2: 청크 JSON → 임베딩된 청크 JSON.

chunks/*.json을 읽어 Cloudflare BGE-M3로 임베딩(vector 부착)하고
embedded/*.json에 기록한다. 출력 키가 이미 있으면 skip(멱등). 빈 청크
입력이면 빈 리스트를 기록한다(cloudflare_embedder.embed가 []→[] 반환).
"""
from embedding import cloudflare_embedder
from embedding.backfill import s3_io


def process_one(bucket: str, chunks_key: str) -> tuple[str, str]:
    """chunks 오브젝트 1건 → embedded 오브젝트. (status, embedded_key) 반환."""
    embedded_key = s3_io.chunks_to_embedded_key(chunks_key)
    if s3_io.object_exists(bucket, embedded_key):
        return "skipped", embedded_key
    chunks = s3_io.get_json(bucket, chunks_key)
    embedded = cloudflare_embedder.embed(chunks)  # [] → []
    s3_io.put_json(bucket, embedded_key, embedded)
    return "processed", embedded_key
