# -*- coding: utf-8 -*-
"""s3_io.py 키 매핑·S3 헬퍼 단위테스트 — boto3 client를 fake로 대체."""
import json

import pytest
from botocore.exceptions import ClientError

from embedding.backfill import s3_io

SUB = "biz_div=cnstwk/year=2026/month=01/day=02/R25BK01213271_001/R25BK01213271_001_doc01.json"


def test_extracted_to_chunks_key():
    assert s3_io.extracted_to_chunks_key("extracted/downloads/backfill/" + SUB) == \
        "embeddings/backfill/chunks/" + SUB


def test_chunks_to_embedded_key():
    assert s3_io.chunks_to_embedded_key("embeddings/backfill/chunks/" + SUB) == \
        "embeddings/backfill/embedded/" + SUB


def test_extracted_to_chunks_key_rejects_bad_prefix():
    with pytest.raises(ValueError):
        s3_io.extracted_to_chunks_key("extracted/daily/x.json")


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, Bucket, Prefix):
        return iter(self._pages)


class _FakeClient:
    def __init__(self, pages=None, body=None, head_error=None):
        self._pages = pages or []
        self._body = body
        self._head_error = head_error

    def get_paginator(self, name):
        return _FakePaginator(self._pages)

    def get_object(self, Bucket, Key):
        return {"Body": _Body(self._body)}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.put = {"Bucket": Bucket, "Key": Key, "Body": Body}

    def head_object(self, Bucket, Key):
        if self._head_error:
            raise ClientError({"Error": {"Code": self._head_error}}, "HeadObject")
        return {"ContentLength": 1}


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def test_list_json_keys_filters_and_limits(monkeypatch):
    pages = [{"Contents": [
        {"Key": "p/a.json"}, {"Key": "p/b.txt"}, {"Key": "p/c.json"}, {"Key": "p/d.json"},
    ]}]
    monkeypatch.setattr(s3_io, "_get_client", lambda: _FakeClient(pages=pages))
    assert list(s3_io.list_json_keys("bidmate", "p/")) == ["p/a.json", "p/c.json", "p/d.json"]
    assert list(s3_io.list_json_keys("bidmate", "p/", limit=2)) == ["p/a.json", "p/c.json"]


def test_get_json_parses_body(monkeypatch):
    body = json.dumps({"x": 1}).encode("utf-8")
    monkeypatch.setattr(s3_io, "_get_client", lambda: _FakeClient(body=body))
    assert s3_io.get_json("bidmate", "k") == {"x": 1}


def test_put_json_encodes_utf8(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(s3_io, "_get_client", lambda: fake)
    s3_io.put_json("bidmate", "k", [{"text": "한글"}])
    assert json.loads(fake.put["Body"]) == [{"text": "한글"}]
    assert "\\u" not in fake.put["Body"].decode("utf-8")  # ensure_ascii=False


def test_object_exists_true(monkeypatch):
    monkeypatch.setattr(s3_io, "_get_client", lambda: _FakeClient())
    assert s3_io.object_exists("bidmate", "k") is True


def test_object_exists_false_on_404(monkeypatch):
    monkeypatch.setattr(s3_io, "_get_client", lambda: _FakeClient(head_error="404"))
    assert s3_io.object_exists("bidmate", "k") is False


def test_object_exists_reraises_other(monkeypatch):
    monkeypatch.setattr(s3_io, "_get_client", lambda: _FakeClient(head_error="AccessDenied"))
    with pytest.raises(ClientError):
        s3_io.object_exists("bidmate", "k")
