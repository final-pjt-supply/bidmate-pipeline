# -*- coding: utf-8 -*-
"""HWP 큐를 트리거로 하는 Lambda 진입점. 얇게 유지하고 실제 로직은 extractors/common에 위임."""
import json
import logging
import time

from common import paths, s3, sqs
from common.config import load_config
from common.logs import log_process_done, log_process_skip, log_process_start
from common.result import build_result
from extractors import hwp

logger = logging.getLogger(__name__)

# 조사 결과 INFO 로그가 CloudWatch에 전혀 안 찍히고 있었다(TestEvent skip 로그가
# 보존기간 전체에서 0건) — 어디에도 로거 레벨 설정이 없어 루트 로거 기본 레벨
# (WARNING)에 INFO가 걸러진 것으로 추정. 여기서 루트 레벨을 명시해 PROCESS_START/
# DONE(common.logs 경유)과 sqs.py의 skip 로그까지 실제로 찍히게 한다.
logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, _context):
    for bucket, key in sqs.iter_s3_records(event):
        _process(bucket, key)


def _process(bucket: str, key: str) -> None:
    parts = paths.parse_raw_key(key)
    bid_id = parts["bid_id"]
    document_id = paths.document_id_from_file_id(bid_id, parts["file_id"])
    extracted_key = paths.raw_key_to_extracted_key(key)

    # 같은 raw 파일에 대한 이벤트가 몇 분 간격으로 여러 번 들어와 같은 문서가
    # 중복으로 성공 처리되고 다음 큐(LLM)로도 중복 전송되는 사례가 실측으로
    # 확인됨(원인은 이 Lambda 밖 — raw 업로드/S3 알림 쪽으로 추정되나 통제 밖).
    # extracted_key가 이미 있으면 이미 처리된 것이므로 재처리·재전송하지 않는다.
    if s3.object_exists(bucket, extracted_key):
        log_process_skip(bid_id, document_id, "SKIPPED_DUPLICATE")
        return

    log_process_start(bid_id, document_id, key)
    start = time.monotonic()
    try:
        raw_bytes = s3.get_object(bucket, key)
        extracted = hwp.extract(raw_bytes)
        result = build_result(bid_id, document_id, extracted["pages"])

        s3.put_object(bucket, extracted_key, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        sqs.send_to_next_queue({"bucket": bucket, "key": extracted_key})
        _send_to_embed_queue(bucket, extracted_key, bid_id, document_id)
        log_process_done(bid_id, document_id, int((time.monotonic() - start) * 1000), extracted_key)
    except Exception:
        logger.exception(
            "HWP 추출 실패: bucket=%s key=%s bid_id=%s document_id=%s",
            bucket, key, bid_id, document_id,
        )
        raise


def _send_to_embed_queue(bucket: str, extracted_key: str, bid_id: str, document_id: str) -> None:
    """임베딩 큐(#64)로 병렬 분기. 실패해도 기존 LLM 파이프라인은 막지 않는다 —
    여기서 예외를 삼키고 로깅만 한다(바깥 try/except로 전파해 재시도/DLQ 되는 걸 방지).
    embed_queue_url이 없으면(SAM template 배포 전 상태) 조용히 건너뛴다."""
    embed_queue_url = load_config()["embed_queue_url"]
    if not embed_queue_url:
        return
    try:
        sqs.send_to_queue(embed_queue_url, {"bucket": bucket, "key": extracted_key})
    except Exception:
        logger.exception(
            "임베딩 큐 전송 실패(LLM 파이프라인은 정상 진행): bid_id=%s document_id=%s key=%s",
            bid_id, document_id, extracted_key,
        )
