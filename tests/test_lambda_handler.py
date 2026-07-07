# -*- coding: utf-8 -*-
import json

import pytest

from pipeline import lambda_handler as lh


class FakeBody:
    def __init__(self, data): self._data = data
    def read(self): return self._data


class FakeS3:
    def __init__(self):
        self.put_calls = []
        self.get_error = None
        self.obj = b"HWPDATA"
    def get_object(self, Bucket, Key):
        if self.get_error:
            raise self.get_error
        return {"Body": FakeBody(self.obj)}
    def put_object(self, **kw):
        self.put_calls.append(kw)


def _event(key, task_id="t1", inv="inv1"):
    return {
        "invocationId": inv,
        "invocationSchemaVersion": "1.0",
        "tasks": [{"taskId": task_id, "s3BucketArn": "arn:aws:s3:::bidmate",
                   "s3Key": key}],
    }


@pytest.fixture
def patched(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(lh, "s3", fake)
    monkeypatch.setattr(lh, "extract_bytes",
                        lambda data, key: {"source_type": "hwp",
                                           "text": "본문", "images": {}})
    monkeypatch.setenv("ALLOWED_EXT", ".hwp")
    return fake


def test_output_key_mirrors_partition_and_swaps_ext():
    src = "raw/downloads/backfill/year=2026/R26BK01269024_000_doc01.hwp"
    assert lh.output_key(src) == (
        "extracted/downloads/backfill/year=2026/R26BK01269024_000_doc01.json")


def test_success_uploads_json_and_reports_succeeded(patched):
    key = "raw/downloads/backfill/year=2026/R26BK01269024_000_doc01.hwp"
    resp = lh.handler(_event(key), None)
    assert resp["invocationId"] == "inv1"
    assert resp["treatMissingKeysAs"] == "PermanentFailure"
    r = resp["results"][0]
    assert r["taskId"] == "t1"
    assert r["resultCode"] == "Succeeded"
    assert len(patched.put_calls) == 1
    put = patched.put_calls[0]
    assert put["Key"].startswith("extracted/") and put["Key"].endswith(".json")
    body = json.loads(put["Body"].decode("utf-8"))
    assert body["bid_id"] == "R26BK01269024_000"
    assert body["document_id"] == "doc01"


def test_url_encoded_key_is_decoded(patched):
    # 한글/슬래시가 %-인코딩되어 들어와도 정상 처리
    key = "raw/downloads/%ED%95%9C%EA%B8%80/R26BK01269024_000_doc01.hwp"
    resp = lh.handler(_event(key), None)
    assert resp["results"][0]["resultCode"] == "Succeeded"
    assert "한글" in patched.put_calls[0]["Key"]


def test_unsupported_extension_is_permanent_failure(patched):
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc04.xlsx"), None)
    r = resp["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "extension" in r["resultString"]
    assert patched.put_calls == []


def test_bad_filename_is_permanent_failure(patched):
    resp = lh.handler(_event("raw/a/randomfile.hwp"), None)
    r = resp["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "filename" in r["resultString"]


def test_parse_crash_is_permanent_failure(patched, monkeypatch):
    def boom(data, key): raise ValueError("BadOLEFile")
    monkeypatch.setattr(lh, "extract_bytes", boom)
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    r = resp["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "parse error" in r["resultString"]


def test_s3_throttle_is_temporary_failure(patched):
    from botocore.exceptions import ClientError
    patched.get_error = ClientError(
        {"Error": {"Code": "SlowDown", "Message": "slow"}}, "GetObject")
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    assert resp["results"][0]["resultCode"] == "TemporaryFailure"


def test_s3_network_error_is_temporary_failure(patched):
    from botocore.exceptions import EndpointConnectionError
    patched.get_error = EndpointConnectionError(endpoint_url="https://s3.amazonaws.com")
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    assert resp["results"][0]["resultCode"] == "TemporaryFailure"


def test_allowed_ext_unset_is_permanent_failure(patched, monkeypatch):
    monkeypatch.delenv("ALLOWED_EXT", raising=False)
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    r = resp["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "ALLOWED_EXT not configured" in r["resultString"]


def test_allowed_ext_parses_comma_and_normalizes():
    assert lh._parse_allowed_ext("hwp, .HWPX ") == {".hwp", ".hwpx"}
    assert lh._parse_allowed_ext("") == set()


def test_empty_text_is_permanent_failure(patched, monkeypatch):
    monkeypatch.setattr(lh, "extract_bytes",
                        lambda data, key: {"source_type": "hwp", "text": "   \n ", "images": {}})
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    r = resp["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "no extractable text" in r["resultString"]
    assert patched.put_calls == []


def test_import_error_is_packaging_failure(patched, monkeypatch):
    def boom(data, key): raise ImportError("No module named 'fitz'")
    monkeypatch.setattr(lh, "extract_bytes", boom)
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    r = resp["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert "packaging error" in r["resultString"]


def test_parse_error_includes_stderr_first_line(patched, monkeypatch):
    import subprocess
    def boom(data, key):
        raise subprocess.CalledProcessError(
            1, "hwp5proc", stderr=b"HWPTAG error: broken record\nmore lines")
    monkeypatch.setattr(lh, "extract_bytes", boom)
    resp = lh.handler(_event("raw/a/R26BK01269024_000_doc01.hwp"), None)
    r = resp["results"][0]
    assert r["resultCode"] == "PermanentFailure"
    assert r["resultString"].startswith("parse error: CalledProcessError")
    assert "broken record" in r["resultString"]
    assert "more lines" not in r["resultString"]   # 첫 줄만
