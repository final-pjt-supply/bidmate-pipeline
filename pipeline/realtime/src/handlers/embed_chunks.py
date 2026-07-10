# -*- coding: utf-8 -*-
"""임베딩 큐를 트리거로 하는 Lambda 진입점(#64).

extract_pdf/hwp/hwpx.py가 추출 완료 직후 LLM 큐와 병렬로 보낸 {"bucket","key"}
메시지(extracted/....json 위치)를 받아 청킹→임베딩하고 vectors/....json으로
저장한다.

2026-07-10 결정: 인덱싱 Lambda·VPC 설정은 후속으로 미루고 이 Lambda는 우선
S3 벡터 저장까지만 배포/검증한다(OpenSearch가 Private VPC 안이라 지금은 로컬
검증도 안 되고, 조원과 인덱스 공유 조율도 남아있음 — 그동안 청킹→임베딩→S3
저장 구간은 Cloudflare 외부 API만 쓰고 VPC 제약이 없어 먼저 완결 가능).
인덱싱 큐 전송은 INDEX_QUEUE_URL이 설정된 뒤에만 동작 — 값이 없으면 건너뛴다
(_send_to_index_queue 참고). 벡터가 이미 S3에 쌓여 있으니 나중에 인덱싱을
붙여도 재임베딩 없이 그걸 읽기만 하면 된다.
"""
import json
import logging
import time

from common import paths, s3, sqs
from common.config import load_embedding_config
from common.logs import log_process_done, log_process_start
from embedding import chunker, cloudflare_embedder

logger = logging.getLogger(__name__)

# 다른 handler와 동일한 이유(README 참고): 로거 레벨을 명시하지 않으면 루트 로거
# 기본값(WARNING)에 INFO가 걸러져 PROCESS_START/DONE이 CloudWatch에 안 남는다.
logging.getLogger().setLevel(logging.INFO)

# 청킹/임베딩 로직(embedding/chunker.py, cloudflare_embedder.py 이식본)을 바꿀
# 때만 올린다 — OpenSearch 문서의 embedding_version 필드로 저장돼 재인덱싱
# 필요 여부를 판단하는 기준이 된다.
EMBEDDING_VERSION = "v1"


def lambda_handler(event, _context):
    for bucket, key in sqs.iter_direct_messages(event):
        _process(bucket, key)


def _process(bucket: str, key: str) -> None:
    parts = paths.parse_extracted_key(key)
    bid_id = parts["bid_id"]
    file_id = parts["file_id"]
    document_id = paths.document_id_from_file_id(bid_id, file_id)

    log_process_start(bid_id, document_id, key)
    start = time.monotonic()
    try:
        text_bytes = s3.get_object(bucket, key)
        pages = json.loads(text_bytes)["pages"]
        text = "\n\n".join(p["text"] for p in pages)

        chunks = chunker.chunk(text, source=file_id)
        if not chunks:
            # 짧거나 언어적 내용이 없는 문서는 전량 드랍될 수 있다(chunker.py의
            # _merge_short_chunks 참고) — 실패가 아니라 정상적인 빈 결과이므로
            # 예외 없이 스킵.
            logger.warning(
                "청킹 결과 0건 — 임베딩/저장 건너뜀: bid_id=%s document_id=%s key=%s",
                bid_id, document_id, key,
            )
            log_process_done(bid_id, document_id, int((time.monotonic() - start) * 1000), "-")
            return

        config = load_embedding_config()
        embedded = cloudflare_embedder.embed(
            chunks,
            account_id=config["cloudflare_account_id"],
            api_token=config["cloudflare_api_token"],
        )

        # OpenSearch 문서 필드셋(docs/indexing-design-notes.md 6-2)에 맞춰 조립.
        # 청크 하나하나가 file_id/bid_id/document_id를 전부 들고 있어 인덱싱
        # Lambda가 envelope 없이 이 리스트만 순회해도 되게 한다.
        result_chunks = [
            {
                "file_id": file_id,
                "bid_id": bid_id,
                "document_id": document_id,
                "chunk_idx": c["chunk_idx"],
                "type": c["type"],
                "text": c["text"],
                "vector": c["vector"],
                "embedding_model": cloudflare_embedder.MODEL,
                "embedding_version": EMBEDDING_VERSION,
            }
            for c in embedded
        ]
        result = {
            "bid_id": bid_id,
            "document_id": document_id,
            "file_id": file_id,
            "chunks": result_chunks,
        }

        vectors_key = paths.extracted_key_to_vectors_key(key)
        s3.put_object(bucket, vectors_key, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        _send_to_index_queue(bucket, vectors_key, config["index_queue_url"])
        log_process_done(bid_id, document_id, int((time.monotonic() - start) * 1000), vectors_key)
    except Exception:
        logger.exception(
            "임베딩 실패: bucket=%s key=%s bid_id=%s document_id=%s",
            bucket, key, bid_id, document_id,
        )
        raise


def _send_to_index_queue(bucket: str, vectors_key: str, index_queue_url: str | None) -> None:
    """INDEX_QUEUE_URL이 없으면(인덱싱 Lambda 배포 전 상태, 2026-07-10 결정) 조용히
    건너뛴다 — S3 벡터 저장은 이미 끝난 뒤라 이 Lambda 본연의 목적은 달성됨.

    extract_pdf.py의 _send_to_embed_queue와 달리 실패를 별도로 삼키지 않는다 —
    거긴 "남의 파이프라인(LLM)에 새 분기를 얹은 것"이라 실패를 격리해야 했지만,
    여기 인덱싱 큐 전송은 이 Lambda 자신의 마지막 단계라 실패하면 그냥
    _process의 바깥 try/except를 타고 메시지 전체가 정상적으로 재시도되면 된다.
    """
    if not index_queue_url:
        logger.info(
            "INDEX_QUEUE_URL 미설정 — 인덱싱 큐 전송 건너뜀(S3 벡터 저장은 완료): key=%s",
            vectors_key,
        )
        return
    sqs.send_to_queue(index_queue_url, {"bucket": bucket, "key": vectors_key})
