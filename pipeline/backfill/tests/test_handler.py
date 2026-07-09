# -*- coding: utf-8 -*-
"""backfill/handler.py 단위테스트 — processor를 monkeypatch로 격리, S3 Batch 응답 계약 검증."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "realtime" / "src"))

from backfill import handler, processor  # noqa: E402

KEY = (
    "extracted/downloads/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
    "/R25BK01213271_001/R25BK01213271_001_doc01.json"
)


def _event(task_id="t1", key=KEY):
    return {
        "invocationId": "inv-123",
        "invocationSchemaVersion": "1.0",
        "tasks": [{
            "taskId": task_id,
            "s3BucketArn": "arn:aws:s3:::bidmate",
            "s3Key": key,
        }],
    }


def test_success_maps_to_succeeded(monkeypatch):
    monkeypatch.setattr(processor, "process_task", lambda b, k: "qualifications/backfill/x.json")
    resp = handler.lambda_handler(_event(), None)
    assert resp["invocationId"] == "inv-123"
    assert resp["treatMissingKeysAs"] == "PermanentFailure"
    assert resp["results"][0]["resultCode"] == "Succeeded"
    assert resp["results"][0]["taskId"] == "t1"


def test_permanent_failure_maps(monkeypatch):
    def boom(b, k):
        raise processor.PermanentFailure("bad json")
    monkeypatch.setattr(processor, "process_task", boom)
    resp = handler.lambda_handler(_event(), None)
    assert resp["results"][0]["resultCode"] == "PermanentFailure"


def test_temporary_failure_maps(monkeypatch):
    def boom(b, k):
        raise processor.TemporaryFailure("SlowDown")
    monkeypatch.setattr(processor, "process_task", boom)
    resp = handler.lambda_handler(_event(), None)
    assert resp["results"][0]["resultCode"] == "TemporaryFailure"


def test_unexpected_exception_is_permanent(monkeypatch):
    def boom(b, k):
        raise RuntimeError("surprise")
    monkeypatch.setattr(processor, "process_task", boom)
    resp = handler.lambda_handler(_event(), None)
    assert resp["results"][0]["resultCode"] == "PermanentFailure"


def test_bucket_parsed_from_arn(monkeypatch):
    seen = {}
    def capture(b, k):
        seen["bucket"], seen["key"] = b, k
        return "qualifications/backfill/x.json"
    monkeypatch.setattr(processor, "process_task", capture)
    handler.lambda_handler(_event(), None)
    assert seen["bucket"] == "bidmate"
    assert seen["key"] == KEY
