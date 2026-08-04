# -*- coding: utf-8 -*-
"""단계 1: extracted JSON → 청크 JSON.

pages 텍스트를 이어붙여 embedding.chunker로 청킹하고 bid_id/document_id를
부착한다. 출력 키가 이미 있으면 skip(멱등). 청크가 0개여도 빈 리스트를 기록해
완료로 표시한다(안 그러면 재실행마다 재처리).
"""
from embedding.backfill import s3_io
from embedding.chunker import chunk as _chunk


def chunk_extracted(doc: dict) -> list[dict]:
    """파싱된 extracted JSON(dict)에서 청크 리스트를 만든다. dict가 아니면 빈 리스트."""
    if not isinstance(doc, dict):
        return []
    bid_id = doc.get("bid_id", "")
    document_id = doc.get("document_id", "")
    pages = doc.get("pages") or []
    text = "\n\n".join(p.get("text", "") for p in pages)
    chunks = _chunk(text, source=f"{bid_id}_{document_id}")
    for c in chunks:
        c["bid_id"] = bid_id
        c["document_id"] = document_id
    return chunks


def process_one(bucket: str, extracted_key: str) -> tuple[str, str]:
    """extracted 오브젝트 1건 → chunks 오브젝트. (status, chunks_key) 반환."""
    chunks_key = s3_io.extracted_to_chunks_key(extracted_key)
    if s3_io.object_exists(bucket, chunks_key):
        return "skipped", chunks_key
    doc = s3_io.get_json(bucket, extracted_key)
    chunks = chunk_extracted(doc)
    s3_io.put_json(bucket, chunks_key, chunks)  # 빈 리스트여도 기록
    return "processed", chunks_key
