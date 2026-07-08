# -*- coding: utf-8 -*-
"""HWP 큐를 트리거로 하는 Lambda 진입점. 얇게 유지하고 실제 로직은 extractors/common에 위임."""
import json
import logging

from common import paths, s3, sqs
from common.result import build_result
from extractors import hwp

logger = logging.getLogger(__name__)


def lambda_handler(event, _context):
    for bucket, key in sqs.iter_s3_records(event):
        _process(bucket, key)


def _process(bucket: str, key: str) -> None:
    parts = paths.parse_raw_key(key)
    bid_id = parts["bid_id"]
    document_id = paths.document_id_from_file_id(bid_id, parts["file_id"])
    try:
        raw_bytes = s3.get_object(bucket, key)
        extracted = hwp.extract(raw_bytes)
        result = build_result(bid_id, document_id, extracted["pages"])

        extracted_key = paths.raw_key_to_extracted_key(key)
        s3.put_object(bucket, extracted_key, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        sqs.send_to_next_queue({"bucket": bucket, "key": extracted_key})
    except Exception:
        logger.exception(
            "HWP 추출 실패: bucket=%s key=%s bid_id=%s document_id=%s",
            bucket, key, bid_id, document_id,
        )
        raise
