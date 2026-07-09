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
