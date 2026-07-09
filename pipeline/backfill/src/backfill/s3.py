# -*- coding: utf-8 -*-
"""backfill 전용 S3 헬퍼. realtime common/s3.py를 수정하지 않기 위해 자체 구현하며,
멱등 skip에 필요한 object_exists를 포함한다."""
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


def put_object(bucket: str, key: str, body: bytes) -> None:
    _get_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )


def object_exists(bucket: str, key: str) -> bool:
    """key가 버킷에 존재하면 True. 404/NoSuchKey면 False. 그 외 오류는 재raise."""
    try:
        _get_client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
