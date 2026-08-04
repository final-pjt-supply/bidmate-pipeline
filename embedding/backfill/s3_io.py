# -*- coding: utf-8 -*-
"""backfill 배치의 S3 접근·키 변환. 도메인 로직 없음.

키 변환: 입력 하위경로를 유지한 채 prefix만 바꾼다.
  extracted/downloads/backfill/<sub> → embeddings/backfill/chunks/<sub>
  embeddings/backfill/chunks/<sub>   → embeddings/backfill/embedded/<sub>
"""
import json
from typing import Iterator

import boto3
from botocore.exceptions import ClientError

from embedding.backfill import config

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3")
    return _client


def _swap_prefix(key: str, src_prefix: str, dst_prefix: str) -> str:
    if not key.startswith(src_prefix):
        raise ValueError(f"prefix 불일치: {key!r} (기대 {src_prefix!r})")
    return dst_prefix + key[len(src_prefix):]


def extracted_to_chunks_key(key: str) -> str:
    return _swap_prefix(key, config.EXTRACTED_PREFIX, config.CHUNKS_PREFIX)


def chunks_to_embedded_key(key: str) -> str:
    return _swap_prefix(key, config.CHUNKS_PREFIX, config.EMBEDDED_PREFIX)


def list_json_keys(bucket: str, prefix: str, limit: int | None = None) -> Iterator[str]:
    """prefix 아래 .json 오브젝트 키를 순회한다(페이지네이션). limit 지정 시 그만큼만."""
    paginator = _get_client().get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            yield key
            n += 1
            if limit is not None and n >= limit:
                return


def get_json(bucket: str, key: str) -> object:
    body = _get_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def put_json(bucket: str, key: str, obj: object) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    _get_client().put_object(
        Bucket=bucket, Key=key, Body=body,
        ContentType="application/json; charset=utf-8",
    )


def object_exists(bucket: str, key: str) -> bool:
    """key가 존재하면 True, 404/NoSuchKey면 False, 그 외 오류는 재raise."""
    try:
        _get_client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
