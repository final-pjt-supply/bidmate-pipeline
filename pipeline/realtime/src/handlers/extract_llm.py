# -*- coding: utf-8 -*-
"""LLM 추출 큐를 트리거로 하는 Lambda 진입점. 얇게 유지하고 실제 로직은 extractors/common에 위임.

파이프라인의 마지막 단계라 다음 큐로 보내는 게 없다(다른 3개 handler와의 차이).
"""
import json
import logging
import time

from common import paths, s3, sqs
from common.logs import log_process_done, log_process_start
from extractors.llm import extractor

logger = logging.getLogger(__name__)

# 조사 결과 INFO 로그가 CloudWatch에 전혀 안 찍히고 있었다(TestEvent skip 로그가
# 보존기간 전체에서 0건) — 어디에도 로거 레벨 설정이 없어 루트 로거 기본 레벨
# (WARNING)에 INFO가 걸러진 것으로 추정. 여기서 루트 레벨을 명시해 PROCESS_START/
# DONE(common.logs 경유)과 sqs.py의 skip 로그까지 실제로 찍히게 한다.
logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, _context):
    for bucket, key in sqs.iter_direct_messages(event):
        _process(bucket, key)


def _process(bucket: str, key: str) -> None:
    parts = paths.parse_extracted_key(key)
    bid_id = parts["bid_id"]
    document_id = paths.document_id_from_file_id(bid_id, parts["file_id"])
    log_process_start(bid_id, document_id, key)
    start = time.monotonic()
    try:
        text_bytes = s3.get_object(bucket, key)
        pages = json.loads(text_bytes)["pages"]
        qualifications = extractor.extract(pages)
        result = {"bid_id": bid_id, "document_id": document_id, **qualifications}

        qualifications_key = paths.extracted_key_to_qualifications_key(key)
        s3.put_object(bucket, qualifications_key, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        log_process_done(bid_id, document_id, int((time.monotonic() - start) * 1000), qualifications_key)
    except Exception:
        logger.exception(
            "LLM 자격요건 추출 실패: bucket=%s key=%s bid_id=%s document_id=%s",
            bucket, key, bid_id, document_id,
        )
        raise
