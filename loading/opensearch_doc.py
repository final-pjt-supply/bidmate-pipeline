# -*- coding: utf-8 -*-
"""S3 backfill 임베딩 청크 → bid_chunks OpenSearch 문서 매핑(순수, I/O 없음).

_id·필드 규칙은 기존 bid_chunks 문서와 동일하게 맞춘다(멱등 upsert).
"""

INDEX_NAME = "bid_chunks"
EMBEDDING_MODEL = "@cf/baai/bge-m3"
EMBEDDING_VERSION = "v1"


def chunk_to_action(chunk: dict, index_name: str, indexed_at: str) -> dict:
    """S3 청크 dict → streaming_bulk index 액션. _id = {source}::{chunk_idx}."""
    source = chunk["source"]
    return {
        "_op_type": "index",
        "_index": index_name,
        "_id": f"{source}::{chunk['chunk_idx']}",
        "_source": {
            "bid_id": chunk["bid_id"],
            "document_id": chunk["document_id"],
            "chunk_idx": chunk["chunk_idx"],
            "text": chunk["text"],
            "type": chunk["type"],
            "vector": chunk["vector"],
            "file_id": source,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_version": EMBEDDING_VERSION,
            "indexed_at": indexed_at,
        },
    }


def actions_for_chunks(chunks, index_name: str, indexed_at: str):
    """청크 리스트 → 액션 제너레이터. 빈 리스트/None이면 아무것도 내지 않는다."""
    for chunk in chunks or []:
        yield chunk_to_action(chunk, index_name, indexed_at)
