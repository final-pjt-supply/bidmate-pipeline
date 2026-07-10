"""daily 첨부 다운로더의 멱등성(이미 받은 파일 건너뛰기) 테스트.

배경: DAG는 5분마다 돌지만 다운로드 창은 15분이라 같은 첨부가 3~4개 run에 반복
등장한다. 실측 결과 고유 파일 488건을 1,600번 내려받고 있었다(3.28배).
S3에 이미 있는 키는 다시 받지 않아야 한다.

S3와 HTTP는 스텁으로 대체하지만 json_file_download_daily 의 코드는 실제로 실행한다.
"""

import pytest
from botocore.exceptions import ClientError

import json_file_download_daily as dl


NOTICE = {
    "업무구분": "servc",
    "bidNtceNo": "R26BK01626662",
    "bidNtceOrd": "000",
    "bidNtceDt": "2026-07-08 08:20:00",
    "fileName": "제안요청서.hwpx",
    "fileUrl": "https://g2b.example/downloadFile.do?a=1",
    "fileSeq": "1",
    "docNo": 1,
}


class FakeS3:
    """head_object / upload_fileobj 만 흉내내는 최소 스텁."""

    def __init__(self, existing=None):
        self.objects = dict(existing or {})
        self.uploads = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": self.objects[Key], "ContentType": "application/octet-stream"}

    def upload_fileobj(self, body, bucket, key, ExtraArgs=None):
        self.uploads.append(key)
        self.objects[key] = 999


class FakeResponse:
    headers = {"Content-Type": "application/octet-stream", "Content-Length": "141312"}

    class _Raw:
        decode_content = False

    def __init__(self):
        self.raw = self._Raw()

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, stream=False, timeout=None):
        self.calls.append(url)
        return FakeResponse()


class ExplodingSession:
    """네트워크를 건드리면 즉시 실패시킨다."""

    def get(self, *args, **kwargs):
        raise AssertionError("이미 적재된 파일인데 다시 다운로드를 시도했다")


def expected_key():
    return dl.file_s3_key(dl.FILES_PREFIX, NOTICE, "", NOTICE["fileUrl"], include_hour=True)


# ------------------------------------------------- 다운로드 전 키 예측


def test_predict_s3_key_matches_actual_key():
    """content_type 없이 계산한 키가 실제 저장 키와 같아야 건너뛰기가 성립한다."""
    assert dl.predict_s3_key(NOTICE, NOTICE["fileUrl"]) == expected_key()


def test_predict_s3_key_returns_none_without_extension():
    """확장자를 모르면 키를 확정할 수 없으므로 건너뛰기를 포기하고 다운로드해야 한다."""
    meta = {**NOTICE, "fileName": "확장자없는파일"}
    assert dl.predict_s3_key(meta, meta["fileUrl"]) is None


# ------------------------------------------------- 멱등성 본체


def test_skips_download_when_object_already_exists():
    key = expected_key()
    s3 = FakeS3(existing={key: 141312})

    result = dl.upload_attachment(s3, "bidmate", ExplodingSession(), dict(NOTICE), 60)

    assert result["downloadStatus"] == "success"
    assert result["downloadCached"] is True
    assert result["downloadPath"] == f"s3://bidmate/{key}"
    assert result["downloadSize"] == 141312
    assert result["s3Key"] == key
    assert s3.uploads == [], "건너뛰었는데 S3 업로드가 발생했다"


def test_downloads_when_object_missing():
    s3 = FakeS3()
    session = FakeSession()

    result = dl.upload_attachment(s3, "bidmate", session, dict(NOTICE), 60)

    assert result["downloadStatus"] == "success"
    assert result["downloadCached"] is False
    assert len(session.calls) == 1
    assert s3.uploads == [expected_key()]


def test_redownloads_zero_byte_object():
    """0바이트 객체는 끊긴 업로드의 잔해다. 건너뛰면 영원히 복구되지 않는다.

    건너뛰기를 넣기 전에는 다음 run이 덮어써서 스스로 나았다. 그 성질을 지킨다.
    """
    key = expected_key()
    s3 = FakeS3(existing={key: 0})
    session = FakeSession()

    result = dl.upload_attachment(s3, "bidmate", session, dict(NOTICE), 60)

    assert result["downloadCached"] is False
    assert len(session.calls) == 1, "0바이트 객체를 그대로 두고 건너뛰었다"
    assert s3.uploads == [key]


def test_force_redownloads_existing_object():
    key = expected_key()
    s3 = FakeS3(existing={key: 141312})
    session = FakeSession()

    result = dl.upload_attachment(s3, "bidmate", session, dict(NOTICE), 60, skip_existing=False)

    assert result["downloadCached"] is False
    assert len(session.calls) == 1


def test_downloads_when_filename_has_no_extension():
    """키를 예측할 수 없으면 head_object 를 믿지 말고 그냥 받는다."""
    meta = {**NOTICE, "fileName": ""}
    s3 = FakeS3()
    session = FakeSession()

    result = dl.upload_attachment(s3, "bidmate", session, meta, 60)

    assert result["downloadCached"] is False
    assert len(session.calls) == 1


def test_head_object_error_other_than_404_propagates():
    """권한 오류(403)를 '없음'으로 오인해 전부 재다운로드하면 안 된다."""
    key = expected_key()

    class DeniedS3(FakeS3):
        def head_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    with pytest.raises(ClientError):
        dl.upload_attachment(DeniedS3(), "bidmate", ExplodingSession(), dict(NOTICE), 60)


# ------------------------------------------------- 기존 동작 회귀 가드


def test_dedup_skip_still_reports_empty_path():
    """중복 제거로 걸러진 첨부는 S3에 실물이 없으므로 downloadPath 가 비어야 한다."""
    meta = {**NOTICE, "dedupDropped": "같은 이름의 hwpx 문서를 우선 적재"}

    result = dl.upload_attachment(FakeS3(), "bidmate", ExplodingSession(), meta, 60)

    assert result["downloadStatus"] == "skipped"
    assert result["downloadPath"] == ""


def test_empty_url_is_skipped():
    meta = {**NOTICE, "fileUrl": ""}

    result = dl.upload_attachment(FakeS3(), "bidmate", ExplodingSession(), meta, 60)

    assert result["downloadStatus"] == "skipped"
    assert "fileUrl" in result["downloadError"]
