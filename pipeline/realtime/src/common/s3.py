# -*- coding: utf-8 -*-
"""S3 get/put 및 key 규약."""


def get_object(bucket: str, key: str) -> bytes:
    raise NotImplementedError


def put_object(bucket: str, key: str, body: bytes) -> None:
    raise NotImplementedError
