# -*- coding: utf-8 -*-
"""backfill/s3.py object_exists 단위테스트 — boto3 client를 fake로 대체."""
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from backfill import s3  # noqa: E402


class _FakeClient:
    def __init__(self, error_code=None):
        self._error_code = error_code

    def head_object(self, Bucket, Key):
        if self._error_code:
            raise ClientError({"Error": {"Code": self._error_code}}, "HeadObject")
        return {"ContentLength": 123}


def test_returns_true_when_object_present(monkeypatch):
    monkeypatch.setattr(s3, "_get_client", lambda: _FakeClient())
    assert s3.object_exists("bidmate", "qualifications/backfill/x.json") is True


def test_returns_false_on_404(monkeypatch):
    monkeypatch.setattr(s3, "_get_client", lambda: _FakeClient(error_code="404"))
    assert s3.object_exists("bidmate", "qualifications/backfill/missing.json") is False


def test_reraises_on_other_error(monkeypatch):
    monkeypatch.setattr(s3, "_get_client", lambda: _FakeClient(error_code="AccessDenied"))
    with pytest.raises(ClientError):
        s3.object_exists("bidmate", "qualifications/backfill/x.json")
