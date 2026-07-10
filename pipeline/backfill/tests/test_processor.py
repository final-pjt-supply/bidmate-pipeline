# -*- coding: utf-8 -*-
"""backfill/processor.py 단위테스트 — s3·LLM을 monkeypatch로 격리."""
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "realtime" / "src"))

from backfill import processor  # noqa: E402

BUCKET = "bidmate"
KEY = (
    "extracted/downloads/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
    "/R25BK01213271_001/R25BK01213271_001_doc01.json"
)
OUT_KEY = (
    "qualifications/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
    "/R25BK01213271_001/R25BK01213271_001_doc01.json"
)
EXTRACTED_DOC = json.dumps({
    "bid_id": "R25BK01213271_001",
    "document_id": "doc01",
    "pages": [{"page": 1, "text": "2. 입찰참가자격 ..."}],
}).encode("utf-8")


def _patch(monkeypatch, *, exists=False, get_body=EXTRACTED_DOC, extract_ret=None,
           get_exc=None, put_exc=None, extract_exc=None):
    monkeypatch.setattr(processor.s3, "object_exists", lambda b, k: exists)
    put_calls = {}

    def fake_get(b, k):
        if get_exc:
            raise get_exc
        return get_body

    def fake_put(b, k, body):
        if put_exc:
            raise put_exc
        put_calls["bucket"], put_calls["key"], put_calls["body"] = b, k, body

    def fake_extract(pages):
        if extract_exc:
            raise extract_exc
        return extract_ret if extract_ret is not None else {"joint_venture_allowed": False}

    monkeypatch.setattr(processor.s3, "get_object", fake_get)
    monkeypatch.setattr(processor.s3, "put_object", fake_put)
    monkeypatch.setattr(processor, "extract", fake_extract)
    return put_calls


def test_happy_path_writes_qualifications(monkeypatch):
    put_calls = _patch(monkeypatch)
    out = processor.process_task(BUCKET, KEY)
    assert out == OUT_KEY
    assert put_calls["key"] == OUT_KEY
    written = json.loads(put_calls["body"])
    assert written["bid_id"] == "R25BK01213271_001"
    assert written["document_id"] == "doc01"
    assert written["joint_venture_allowed"] is False


def test_idempotent_skip_when_output_exists(monkeypatch):
    put_calls = _patch(monkeypatch, exists=True)
    out = processor.process_task(BUCKET, KEY)
    assert out == OUT_KEY
    assert put_calls == {}


def test_bad_key_is_permanent(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(processor.PermanentFailure):
        processor.process_task(BUCKET, "extracted/daily/biz_div=x/y.json")


def test_missing_pages_is_permanent(monkeypatch):
    _patch(monkeypatch, get_body=json.dumps({"bid_id": "b", "document_id": "d"}).encode("utf-8"))
    with pytest.raises(processor.PermanentFailure):
        processor.process_task(BUCKET, KEY)


def test_object_exists_slowdown_is_temporary(monkeypatch):
    _patch(monkeypatch)
    err = ClientError({"Error": {"Code": "SlowDown"}}, "HeadObject")
    def boom(b, k):
        raise err
    monkeypatch.setattr(processor.s3, "object_exists", boom)
    with pytest.raises(processor.TemporaryFailure):
        processor.process_task(BUCKET, KEY)


def test_s3_get_slowdown_is_temporary(monkeypatch):
    err = ClientError({"Error": {"Code": "SlowDown"}}, "GetObject")
    _patch(monkeypatch, get_exc=err)
    with pytest.raises(processor.TemporaryFailure):
        processor.process_task(BUCKET, KEY)


def test_s3_get_nosuchkey_is_permanent(monkeypatch):
    err = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    _patch(monkeypatch, get_exc=err)
    with pytest.raises(processor.PermanentFailure):
        processor.process_task(BUCKET, KEY)


def test_llm_schema_error_is_permanent(monkeypatch):
    _patch(monkeypatch, extract_exc=ValueError("LLM 응답에 필드 누락"))
    with pytest.raises(processor.PermanentFailure):
        processor.process_task(BUCKET, KEY)


def test_llm_throttling_is_temporary(monkeypatch):
    err = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")
    _patch(monkeypatch, extract_exc=err)
    with pytest.raises(processor.TemporaryFailure):
        processor.process_task(BUCKET, KEY)


def test_llm_5xx_is_temporary(monkeypatch):
    err = ClientError({"Error": {"Code": "InternalServerException"}}, "Converse")
    _patch(monkeypatch, extract_exc=err)
    with pytest.raises(processor.TemporaryFailure):
        processor.process_task(BUCKET, KEY)


def test_llm_validation_is_permanent(monkeypatch):
    err = ClientError({"Error": {"Code": "ValidationException"}}, "Converse")
    _patch(monkeypatch, extract_exc=err)
    with pytest.raises(processor.PermanentFailure):
        processor.process_task(BUCKET, KEY)
