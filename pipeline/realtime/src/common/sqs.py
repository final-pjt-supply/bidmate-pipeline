# -*- coding: utf-8 -*-
"""추출 결과를 다음 단계(LLM 처리) 큐로 전송. SQS 이벤트에서 S3 알림을 꺼내는 것도 여기서 담당."""
import json
from typing import Iterator
from urllib.parse import unquote_plus

import boto3

from common.config import load_config

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("sqs")
    return _client


def iter_s3_records(event: dict) -> Iterator[tuple[str, str]]:
    """SQS 이벤트(각 레코드 body에 S3 이벤트 알림 JSON)에서 (bucket, key) 쌍을 꺼낸다."""
    for sqs_record in event["Records"]:
        body = json.loads(sqs_record["body"])
        for s3_record in body["Records"]:
            bucket = s3_record["s3"]["bucket"]["name"]
            key = unquote_plus(s3_record["s3"]["object"]["key"])
            yield bucket, key


def iter_direct_messages(event: dict) -> Iterator[tuple[str, str]]:
    """SQS 이벤트에서 (bucket, key) 쌍을 꺼낸다.

    iter_s3_records와 달리 이 큐(llm-extract-queue)는 S3 이벤트 알림이 아니라
    send_to_next_queue가 보낸 {"bucket": ..., "key": ...} 메시지를 그대로 받는다.
    """
    for sqs_record in event["Records"]:
        body = json.loads(sqs_record["body"])
        yield body["bucket"], body["key"]


def send_to_next_queue(message: dict) -> None:
    _get_client().send_message(
        QueueUrl=load_config()["next_queue_url"],
        MessageBody=json.dumps(message, ensure_ascii=False),
    )
