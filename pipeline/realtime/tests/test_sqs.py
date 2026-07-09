# -*- coding: utf-8 -*-
"""common/sqs.py 단위테스트 — 특히 S3 TestEvent(Records 없는 메시지) skip 동작."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common import sqs  # noqa: E402


def _sqs_event(bodies: list[dict]) -> dict:
    return {"Records": [{"body": json.dumps(b, ensure_ascii=False)} for b in bodies]}


def test_iter_s3_records_yields_bucket_and_key():
    s3_notification = {
        "Records": [
            {"s3": {"bucket": {"name": "bidmate"}, "object": {"key": "raw/downloads/daily/biz_div=servc/x.pdf"}}}
        ]
    }
    event = _sqs_event([s3_notification])
    result = list(sqs.iter_s3_records(event))
    assert result == [("bidmate", "raw/downloads/daily/biz_div=servc/x.pdf")]


def test_iter_s3_records_url_decodes_key():
    s3_notification = {
        "Records": [
            {"s3": {"bucket": {"name": "bidmate"}, "object": {"key": "raw/downloads/daily/biz_div=servc/a+b.pdf"}}}
        ]
    }
    event = _sqs_event([s3_notification])
    result = list(sqs.iter_s3_records(event))
    assert result == [("bidmate", "raw/downloads/daily/biz_div=servc/a b.pdf")]


def test_iter_s3_records_skips_test_event_without_error():
    """S3가 알림 설정 직후 보내는 s3:TestEvent는 'Records' 키가 없다 — 에러 없이 건너뛰어야 한다."""
    test_event = {
        "Service": "Amazon S3",
        "Event": "s3:TestEvent",
        "Time": "2026-07-08T07:35:00.000Z",
        "Bucket": "bidmate",
        "RequestId": "ABC123",
        "HostId": "xyz",
    }
    event = _sqs_event([test_event])
    result = list(sqs.iter_s3_records(event))
    assert result == []


def test_iter_s3_records_skips_test_event_but_yields_real_ones_in_same_batch():
    test_event = {"Service": "Amazon S3", "Event": "s3:TestEvent"}
    s3_notification = {
        "Records": [
            {"s3": {"bucket": {"name": "bidmate"}, "object": {"key": "raw/downloads/daily/biz_div=servc/x.hwpx"}}}
        ]
    }
    event = _sqs_event([test_event, s3_notification])
    result = list(sqs.iter_s3_records(event))
    assert result == [("bidmate", "raw/downloads/daily/biz_div=servc/x.hwpx")]


def test_iter_direct_messages_unaffected():
    event = {"Records": [{"body": json.dumps({"bucket": "bidmate", "key": "extracted/daily/x.json"})}]}
    result = list(sqs.iter_direct_messages(event))
    assert result == [("bidmate", "extracted/daily/x.json")]
