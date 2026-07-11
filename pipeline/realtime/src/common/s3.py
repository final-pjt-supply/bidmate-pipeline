# -*- coding: utf-8 -*-
"""S3 get/put 및 key 규약."""
import boto3
from botocore.exceptions import ClientError

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3")
    return _client


def get_object(bucket: str, key: str) -> bytes:
    return _get_client().get_object(Bucket=bucket, Key=key)["Body"].read()


def object_exists(bucket: str, key: str) -> bool:
    try:
        _get_client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def put_object(bucket: str, key: str, body: bytes) -> None:
    _get_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )


def list_objects(bucket: str, prefix: str):
    """prefix 하위 전체 객체를 페이지네이션으로 순회하며 {"key", "last_modified"}를
    yield한다(last_modified는 boto3가 주는 datetime 그대로 — 문자열 변환은 호출부
    책임). 대량 prefix(예: qualifications/, #66 RDS 병합 배치용) 1회 전체 조회 용도."""
    paginator = _get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield {"key": obj["Key"], "last_modified": obj["LastModified"]}
