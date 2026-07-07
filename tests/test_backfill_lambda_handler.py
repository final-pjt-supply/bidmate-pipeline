# -*- coding: utf-8 -*-
import json
import subprocess

import pytest

from backfill_lambda import handler as h


class FakeBody:
    def __init__(self, data): self._data = data
    def read(self): return self._data


class FakeS3:
    def __init__(self):
        self.put_calls = []
        self.get_error = None
        self.obj = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1data"
    def get_object(self, Bucket, Key):
        if self.get_error:
            raise self.get_error
        return {"Body": FakeBody(self.obj)}
    def put_object(self, **kw):
        self.put_calls.append(kw)


def _event(key, task_id="t1", inv="inv1"):
    return {"invocationId": inv, "invocationSchemaVersion": "1.0",
            "tasks": [{"taskId": task_id, "s3BucketArn": "arn:aws:s3:::bidmate",
                       "s3Key": key}]}


@pytest.fixture
def patched(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(h, "s3", fake)
    monkeypatch.setattr(h, "extract_document",
                        lambda data, key: {"source_type": "hwp", "text": "본문", "images": {}})
    monkeypatch.setenv("ALLOWED_EXT", ".hwp")
    return fake


def test_output_key_mirrors_partition_and_swaps_ext():
    src = "raw/downloads/backfill/biz_div=cnstwk/year=2026/R26_000_doc01.hwp"
    assert h.output_key(src) == (
        "extracted/downloads/backfill/biz_div=cnstwk/year=2026/R26_000_doc01.json")


def test_success_uploads_json(patched):
    resp = h.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    r = resp["results"][0]
    assert resp["invocationId"] == "inv1"
    assert resp["treatMissingKeysAs"] == "PermanentFailure"
    assert r["taskId"] == "t1" and r["resultCode"] == "Succeeded"
    body = json.loads(patched.put_calls[0]["Body"].decode("utf-8"))
    assert body["bid_id"] == "R26BK01269024_000" and body["document_id"] == "doc01"


def test_url_encoded_key_decoded(patched):
    key = "raw/downloads/%ED%95%9C%EA%B8%80/R26BK01269024_000_doc01.hwp"
    resp = h.handler(_event(key), None)
    assert resp["results"][0]["resultCode"] == "Succeeded"
    assert "한글" in patched.put_calls[0]["Key"]


def test_allowed_ext_unset_is_permanent_failure(patched, monkeypatch):
    monkeypatch.delenv("ALLOWED_EXT", raising=False)
    r = h.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "ALLOWED_EXT not configured" in r["resultString"]


def test_unsupported_extension(patched):
    r = h.handler(_event("raw/a/R26BK01269024_000_doc04.xlsx"), None)["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "unsupported extension" in r["resultString"]
    assert patched.put_calls == []


def test_bad_filename(patched):
    r = h.handler(_event("raw/a/randomfile.hwp"), None)["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "filename" in r["resultString"]


def test_empty_text(patched, monkeypatch):
    monkeypatch.setattr(h, "extract_document",
                        lambda data, key: {"source_type": "hwp", "text": "  \n ", "images": {}})
    r = h.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "no extractable text" in r["resultString"]


def test_import_error_is_packaging(patched, monkeypatch):
    def boom(data, key): raise ImportError("No module named 'fitz'")
    monkeypatch.setattr(h, "extract_document", boom)
    r = h.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "packaging error" in r["resultString"]


def test_parse_error_includes_stderr(patched, monkeypatch):
    def boom(data, key):
        raise subprocess.CalledProcessError(1, "hwp5proc",
                                            stderr=b"HWPTAG error: broken\nmore")
    monkeypatch.setattr(h, "extract_document", boom)
    r = h.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)["results"][0]
    assert r["resultString"].startswith("parse error: CalledProcessError")
    assert "broken" in r["resultString"] and "more" not in r["resultString"]


def test_s3_throttle_is_temporary(patched):
    from botocore.exceptions import ClientError
    patched.get_error = ClientError({"Error": {"Code": "SlowDown"}}, "GetObject")
    r = h.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)["results"][0]
    assert r["resultCode"] == "TemporaryFailure"


def test_s3_network_is_temporary(patched):
    from botocore.exceptions import EndpointConnectionError
    patched.get_error = EndpointConnectionError(endpoint_url="https://s3")
    r = h.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)["results"][0]
    assert r["resultCode"] == "TemporaryFailure"
