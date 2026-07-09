# -*- coding: utf-8 -*-
"""S3 Batch Operations 트리거로 backfill LLM 자격요건 추출을 수행하는 Lambda 진입점.

SQS가 아니라 S3 Batch task 계약(event["tasks"] → results[resultCode])을 따르며,
backfill_lambda/handler.py와 동형. 실제 처리는 backfill/processor.py에 위임한다.
"""
import logging
from urllib.parse import unquote

from backfill import processor

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, _context) -> dict:
    results = []
    for task in event["tasks"]:
        task_id = task["taskId"]
        key = task.get("s3Key", "")  # 로깅용 안전 기본값(unquote/추출 실패 시 NameError 방지)
        try:
            bucket = task["s3BucketArn"].rsplit(":", 1)[-1]
            key = unquote(task["s3Key"])
            out_key = processor.process_task(bucket, key)
            code, msg = "Succeeded", out_key
        except processor.TemporaryFailure as e:
            logger.exception("temporary failure: %s", key)
            code, msg = "TemporaryFailure", str(e)[:1024]
        except processor.PermanentFailure as e:
            logger.exception("permanent failure: %s", key)
            code, msg = "PermanentFailure", str(e)[:1024]
        except Exception as e:
            logger.exception("unexpected failure: %s", key)
            code, msg = "PermanentFailure", f"unexpected: {type(e).__name__}"
        results.append({"taskId": task_id, "resultCode": code, "resultString": msg})

    logger.info("batch 완료: %d tasks 처리", len(results))
    return {
        "invocationSchemaVersion": event.get("invocationSchemaVersion", "1.0"),
        "treatMissingKeysAs": "PermanentFailure",
        "invocationId": event["invocationId"],
        "results": results,
    }
