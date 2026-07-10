"""첨부문서 백필 다운로더의 실패 처리·매니페스트 보존·재시도 동작 테스트.

네트워크와 S3는 각각 httpx.MockTransport와 FakeS3로 대체하지만,
검증 대상인 json_file_download_backfill의 코드 자체는 실제로 실행한다.
"""

import types
from datetime import datetime

import httpx
import pytest

import json_file_download_backfill as dl


class FakeS3:
    """put_object / upload_fileobj만 흉내내는 최소 S3 스텁."""

    def __init__(self):
        self.objects = {}

    async def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    async def upload_fileobj(self, body, bucket, key, ExtraArgs=None):
        self.objects[key] = body.read()


class FakeAsyncCM:
    def __init__(self, obj):
        self.obj = obj

    async def __aenter__(self):
        return self.obj

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    def __init__(self, s3):
        self.s3 = s3

    def client(self, name):
        return FakeAsyncCM(self.s3)


REAL_ASYNC_CLIENT = httpx.AsyncClient  # 몽키패치 전에 원본을 붙잡아 둔다 (재귀 방지)


def mock_client_factory(handler):
    """httpx.AsyncClient() 호출을 가로채 MockTransport를 물린 실제 클라이언트를 준다."""
    return lambda *args, **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- 이슈 3: 재시도


async def test_download_retries_on_server_error(monkeypatch):
    """5xx는 일시적 장애이므로 재시도해서 결국 성공해야 한다."""
    monkeypatch.setattr(dl, "RETRY_BACKOFF_BASE", 0, raising=False)
    attempts = []

    def handler(request):
        attempts.append(request.url)
        if len(attempts) < 3:
            return httpx.Response(500)
        return httpx.Response(200, content=b"HWPDATA")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await dl.fetch_attachment(client, "https://g2b.example/a.hwp", 5)

    assert len(attempts) == 3
    assert response.content == b"HWPDATA"


async def test_download_does_not_retry_on_client_error(monkeypatch):
    """404는 재시도해도 달라지지 않으므로 한 번만 시도하고 즉시 실패해야 한다."""
    monkeypatch.setattr(dl, "RETRY_BACKOFF_BASE", 0, raising=False)
    attempts = []

    def handler(request):
        attempts.append(request.url)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await dl.fetch_attachment(client, "https://g2b.example/missing.hwp", 5)

    assert len(attempts) == 1


# ------------------------------------------------------- 이슈 2: 매니페스트 보존


async def test_put_manifest_keeps_previous_run(monkeypatch):
    """같은 달을 나눠 실행해도 앞선 실행의 매니페스트가 남아 있어야 한다."""
    s3 = FakeS3()
    first_half = [{"fileId": "R26-A-1", "bidNtceDt": "2026-06-05 10:00:00"}]
    second_half = [{"fileId": "R26-B-1", "bidNtceDt": "2026-06-20 10:00:00"}]

    await dl.put_manifest(s3, "bidmate", first_half, datetime(2026, 7, 10, 15, 30, 0))
    await dl.put_manifest(s3, "bidmate", second_half, datetime(2026, 7, 10, 18, 12, 0))

    assert len(s3.objects) == 2, f"실행 2회인데 매니페스트가 {len(s3.objects)}개 (덮어쓰기 발생)"

    written = b"\n".join(s3.objects.values()).decode("utf-8")
    assert "R26-A-1" in written, "1차 실행분이 사라졌다"
    assert "R26-B-1" in written


async def test_put_manifest_groups_by_notice_month():
    """실행일이 아니라 공고 게시월(bidNtceDt)로 묶인다 (기존 동작 유지)."""
    s3 = FakeS3()
    items = [
        {"fileId": "june", "bidNtceDt": "2026-06-05 10:00:00"},
        {"fileId": "july", "bidNtceDt": "2026-07-05 10:00:00"},
    ]

    await dl.put_manifest(s3, "bidmate", items, datetime(2026, 7, 10, 15, 30, 0))

    keys = sorted(s3.objects)
    assert any("year=2026/month=06/" in key for key in keys), keys
    assert any("year=2026/month=07/" in key for key in keys), keys


# --------------------------------------------------- 이슈 1: 실패의 종료코드 반영


async def test_run_returns_number_of_failed_downloads(monkeypatch):
    """다운로드가 실패하면 run()이 실패 건수를 반환해야 한다."""
    monkeypatch.setattr(dl, "RETRY_BACKOFF_BASE", 0, raising=False)
    s3 = FakeS3()
    monkeypatch.setattr(dl, "s3_session", lambda: FakeSession(s3))

    async def fake_iter(s3_client, bucket, prefix, start_day, end_day):
        yield "raw/curated/backfill/x.json", {
            "bid_ntce_no": "R26BK01541024",
            "bid_ntce_ord": "000",
            "bid_category": "servc",
            "bid_ntce_dt": "2026-06-01 10:00:00",
            "attachments": [{"file_nm": "a.hwp", "file_url": "https://g2b.example/a.hwp", "kind": "공고첨부"}],
        }

    monkeypatch.setattr(dl, "iter_curated_range", fake_iter)
    monkeypatch.setattr(dl.httpx, "AsyncClient", mock_client_factory(lambda request: httpx.Response(500)))

    args = types.SimpleNamespace(
        bucket="bidmate",
        curated_prefix="raw/curated/backfill",
        start=datetime(2026, 6, 1),
        end=datetime(2026, 6, 1),
        timeout=5,
        concurrency=2,
    )

    failed = await dl.run(args)
    assert failed == 1


async def test_run_returns_zero_when_all_downloads_succeed(monkeypatch):
    monkeypatch.setattr(dl, "RETRY_BACKOFF_BASE", 0, raising=False)
    s3 = FakeS3()
    monkeypatch.setattr(dl, "s3_session", lambda: FakeSession(s3))

    async def fake_iter(s3_client, bucket, prefix, start_day, end_day):
        yield "raw/curated/backfill/x.json", {
            "bid_ntce_no": "R26BK01541024",
            "bid_ntce_ord": "000",
            "bid_category": "servc",
            "bid_ntce_dt": "2026-06-01 10:00:00",
            "attachments": [{"file_nm": "a.hwp", "file_url": "https://g2b.example/a.hwp", "kind": "공고첨부"}],
        }

    monkeypatch.setattr(dl, "iter_curated_range", fake_iter)
    monkeypatch.setattr(
        dl.httpx,
        "AsyncClient",
        mock_client_factory(lambda request: httpx.Response(200, content=b"HWPDATA")),
    )

    args = types.SimpleNamespace(
        bucket="bidmate",
        curated_prefix="raw/curated/backfill",
        start=datetime(2026, 6, 1),
        end=datetime(2026, 6, 1),
        timeout=5,
        concurrency=2,
    )

    failed = await dl.run(args)
    assert failed == 0


def test_main_exits_nonzero_when_downloads_failed(monkeypatch):
    """실패가 있으면 종료코드 1. 셸·Airflow가 실패를 감지할 수 있어야 한다."""

    async def fake_run(args):
        return 3

    monkeypatch.setattr(dl, "run", fake_run)
    monkeypatch.setattr(dl, "parse_args", lambda: types.SimpleNamespace())

    with pytest.raises(SystemExit) as exc_info:
        dl.main()

    assert exc_info.value.code == 1


def test_main_exits_zero_when_everything_succeeded(monkeypatch):
    async def fake_run(args):
        return 0

    monkeypatch.setattr(dl, "run", fake_run)
    monkeypatch.setattr(dl, "parse_args", lambda: types.SimpleNamespace())

    dl.main()  # SystemExit이 발생하지 않아야 한다
