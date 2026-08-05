# -*- coding: utf-8 -*-
"""압축 첨부 해제·DRM 차단·expected_file_count 정정 테스트.

ingestion/ 모듈들은 서로를 flat하게 import하므로(`from attachment_rules import ...`)
그 디렉토리를 sys.path에 넣어야 한다.
"""
import io
import json
import os
import sys
import zipfile

import pytest

INGESTION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ingestion")
if INGESTION not in sys.path:
    sys.path.insert(0, INGESTION)

import archive_rules  # noqa: E402
import json_file_download_daily as dl  # noqa: E402
from attachment_rules import file_s3_key  # noqa: E402


# ---------------------------------------------------------------- 테스트용 더미

class _Raw(io.BytesIO):
    """requests의 response.raw 흉내. decode_content 대입이 가능해야 한다."""
    decode_content = False


class FakeResponse:
    def __init__(self, body: bytes, headers=None):
        self.raw = _Raw(body)
        self.headers = headers or {}

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, body: bytes, headers=None):
        self.body = body
        self.headers = headers or {}

    def get(self, url, stream=True, timeout=None):
        return FakeResponse(self.body, self.headers)


class _Pager:
    def __init__(self, objects):
        self.objects = objects

    def paginate(self, Bucket, Prefix):
        yield {
            "Contents": [
                {"Key": k, "Size": len(v)} for k, v in self.objects.items() if k.startswith(Prefix)
            ]
        }


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        self.puts.append(Key)

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        self.objects[key] = fileobj.read()
        self.puts.append(key)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name):
        return _Pager(self.objects)


# 내용 검사를 통과하려면 진짜 매직바이트가 필요하다(확장자만으로는 안 뚫린다).
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 56
PDF = b"%PDF-1.7\n" + b"\x00" * 32
HWPML = (b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE HWPML []>\n'
         b'<HWPML Version="2.1"><BODY><SECTION Id="0"/></BODY></HWPML>')
DRM = b"SCDSA004" + b"\x00" * 56


def make_zip(names_to_bytes: dict, utf8_flag: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in names_to_bytes.items():
            info = zipfile.ZipInfo(name)
            if utf8_flag:
                info.flag_bits |= 0x800
            zf.writestr(info, payload)
    return buf.getvalue()


def meta(**overrides):
    base = {
        "noticeId": "R26BK01650092-000",
        "업무구분": "cnstwk",
        "bidNtceNo": "R26BK01650092",
        "bidNtceOrd": "000",
        "bidNtceDt": None,
        "docNo": 3,
        "fileSeq": "3",
        "fileId": "R26BK01650092-000-3",
        "fileName": "첨부.zip",
        "fileUrl": "https://example.test/a.zip",
        "srcJsonPath": "s3://bidmate/raw/curated/daily/x.json",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- 규칙 단위

def test_hwpx는_zip이지만_압축으로_취급하지_않는다():
    """HWPX도 PK로 시작하는 zip이다. 매직바이트만 보면 hwpx를 풀어버린다."""
    assert archive_rules.detect_payload_kind(b"PK\x03\x04....", "문서.hwpx") == "plain"
    assert archive_rules.detect_payload_kind(b"PK\x03\x04....", "첨부.zip") == "zip"


def test_drm_판별은_확장자와_무관하다():
    assert archive_rules.detect_payload_kind(b"SCDSA004", "평범한이름.hwp") == "drm"
    assert archive_rules.detect_payload_kind(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "x.hwp") == "plain"


def test_zip_확장자여도_내용이_zip이_아니면_그대로_올린다():
    assert archive_rules.detect_payload_kind(b"%PDF-1.7", "이름만.zip") == "plain"


def test_cp949_멤버명_복원():
    info = zipfile.ZipInfo("설계설명서.hwp".encode("cp949").decode("cp437"))
    assert archive_rules.decode_member_name(info) == "설계설명서.hwp"


def test_utf8_플래그가_있으면_그대로_쓴다():
    info = zipfile.ZipInfo("설계설명서.hwp")
    info.flag_bits |= 0x800
    assert archive_rules.decode_member_name(info) == "설계설명서.hwp"


def _members(mapping):
    archive = zipfile.ZipFile(io.BytesIO(make_zip(mapping)))
    return archive_rules.select_zip_members(archive)


def test_멤버_선별은_지원확장자와_우선순위를_따른다():
    picked, dropped = _members({
        "설명서.hwp": OLE, "설명서.pdf": PDF,      # 같은 stem -> hwp만
        "내역서.hwpx": make_zip({"a": b"x"}),
        "사진.jpg": b"d", "목록.xlsx": b"e",       # 미지원 확장자 -> 제외
        "__MACOSX/._설명서.hwp": OLE,              # 잡파일 -> 제외
    })
    assert [(m["name"], m["memberNo"]) for m in picked] == [("설명서.hwp", 1), ("내역서.hwpx", 2)]
    assert {d["name"] for d in dropped} == {"사진.jpg", "목록.xlsx", "설명서.pdf"}


def test_압축_안_내용물도_바깥과_똑같이_내용_검사를_받는다():
    """확장자만 보면 압축 안이 무사통과가 된다 — 실제로 그 구멍으로 HWPML이 들어왔다."""
    picked, dropped = _members({
        "정상.hwp": OLE,
        "잠긴문서.hwp": DRM,          # 이름은 멀쩡한데 내용이 DRM
        "정체불명.hwp": b"\x00" * 64,  # 아는 형식이 아님
        "예규.hwp": HWPML,            # 이름은 .hwp인데 내용은 HWPML -> 읽을 수 있으니 통과
    })
    assert [m["name"] for m in picked] == ["정상.hwp", "예규.hwp"]
    assert [m["kind"] for m in picked] == ["hwp", "hwpml"]
    assert {d["name"] for d in dropped} == {"잠긴문서.hwp", "정체불명.hwp"}
    assert all("추출할 수 없는 내용" in d["reason"] for d in dropped)


def test_내용_판별은_아는_형식만_통과시킨다():
    assert archive_rules.detect_member_kind(OLE) == "hwp"
    assert archive_rules.detect_member_kind(PDF) == "pdf"
    assert archive_rules.detect_member_kind(b"PK\x03\x04...") == "hwpx"
    assert archive_rules.detect_member_kind(HWPML) == "hwpml"
    assert archive_rules.detect_member_kind(b"\xef\xbb\xbf" + HWPML) == "hwpml"   # BOM
    assert archive_rules.detect_member_kind(DRM) is None
    assert archive_rules.detect_member_kind(b"<?xml version='1.0'?><rss/>") is None
    assert archive_rules.detect_member_kind(b"") is None


def test_압축_해제_총량_상한(monkeypatch):
    monkeypatch.setattr(archive_rules, "MAX_TOTAL_UNCOMPRESSED_BYTES", 10)
    infos = zipfile.ZipFile(io.BytesIO(make_zip({"a.hwp": b"x" * 100}))).infolist()
    assert "총량 상한 초과" in archive_rules.guard_archive(infos)


def test_멤버_키는_부모_docNo를_유지한다():
    key = file_s3_key("raw/downloads/daily", meta(fileName="설명서.hwp"), "", "u", True, member_no=2)
    assert key.endswith("_doc03_02.hwp")
    assert file_s3_key("raw/downloads/daily", meta(), "", "u", True).endswith("_doc03.zip")


# ---------------------------------------------------------------- 업로드 경로

def test_zip은_풀어서_멤버만_적재하고_행이_늘어난다():
    s3 = FakeS3()
    body = make_zip({"설명서.hwp": OLE, "사진.jpg": b"X"})
    rows = dl.upload_attachment(s3, "bidmate", FakeSession(body), meta(), 10, skip_existing=False)

    uploaded = [r for r in rows if r.get("s3Key")]
    assert rows[0]["archiveMemberCount"] == 1
    member = next(r for r in uploaded if r["s3Key"].endswith("_doc03_01.hwp"))
    assert s3.objects[member["s3Key"]] == OLE
    # 제외된 멤버도 사유와 함께 manifest에 남는다
    assert any("사진.jpg" == r.get("fileName") and r["downloadStatus"] == "skipped" for r in rows)
    # 멤버를 먼저 올리고 zip 원본을 마지막에 올려야 중간 실패가 자가 치유된다
    assert s3.puts[-1].endswith("_doc03.zip")


def test_zip_안_DRM은_적재되지_않는다():
    s3 = FakeS3()
    body = make_zip({"정상.hwp": OLE, "잠긴문서.hwp": DRM})
    rows = dl.upload_attachment(s3, "bidmate", FakeSession(body), meta(), 10, skip_existing=False)

    assert not any("잠긴문서" in k for k in s3.puts)
    assert rows[0]["archiveMemberCount"] == 1
    dropped = next(r for r in rows if r.get("fileName") == "잠긴문서.hwp")
    assert dropped["downloadStatus"] == "skipped"
    assert "추출할 수 없는 내용" in dropped["downloadError"]


class _DripRaw(_Raw):
    """read(n)이 요청한 만큼 안 주는 스트림. urllib3는 부분 반환이 가능하다."""
    decode_content = False

    def read(self, size=-1):
        if size is None or size < 0:
            return super().read()
        return super().read(min(size, 4))


def test_찔끔_반환하는_스트림에서도_zip이_안_잘린다():
    s3 = FakeS3()
    body = make_zip({"설명서.hwp": OLE + b"BODY" * 500})
    session = FakeSession(body)
    session.get = lambda url, stream=True, timeout=None: type(
        "R", (), {"raw": _DripRaw(body), "headers": {}, "raise_for_status": lambda self: None}
    )()

    rows = dl.upload_attachment(s3, "bidmate", session, meta(), 10, skip_existing=False)
    member = next(r for r in rows if str(r.get("s3Key", "")).endswith("_doc03_01.hwp"))
    assert s3.objects[member["s3Key"]] == OLE + b"BODY" * 500


def test_압축_원본_크기_상한을_넘으면_건너뛴다(monkeypatch):
    monkeypatch.setattr(archive_rules, "MAX_ARCHIVE_BYTES", 32)
    s3 = FakeS3()
    rows = dl.upload_attachment(
        s3, "bidmate", FakeSession(make_zip({"a.hwp": b"x" * 4096})), meta(), 10,
        skip_existing=False,
    )
    assert rows[0]["downloadStatus"] == "skipped"
    assert s3.puts == []


def test_drm은_적재하지_않는다():
    s3 = FakeS3()
    rows = dl.upload_attachment(
        s3, "bidmate", FakeSession(b"SCDSA004rest"), meta(fileName="공고문.hwp"), 10,
        skip_existing=False,
    )
    assert len(rows) == 1
    assert rows[0]["downloadStatus"] == "skipped"
    assert "DRM" in rows[0]["downloadError"]
    assert s3.puts == []


def test_평범한_첨부는_앞바이트를_잃지_않는다():
    """판별하려고 먼저 읽은 앞부분이 업로드에서 누락되면 파일이 손상된다."""
    s3 = FakeS3()
    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"BODY" * 50
    rows = dl.upload_attachment(
        s3, "bidmate", FakeSession(body), meta(fileName="공고문.hwp"), 10, skip_existing=False,
    )
    assert s3.objects[rows[0]["s3Key"]] == body


def test_지원파일이_없는_zip은_건너뛴다():
    s3 = FakeS3()
    rows = dl.upload_attachment(
        s3, "bidmate", FakeSession(make_zip({"사진.jpg": b"X"})), meta(), 10, skip_existing=False,
    )
    assert rows[0]["downloadStatus"] == "skipped"
    assert s3.puts == []


# ---------------------------------------------------------------- 개수 정정

CURATED_KEY = "raw/curated/daily/x.json"


def _curated(count):
    return json.dumps(
        [{"bid_ntce_no": "R26BK01650092", "bid_ntce_ord": "000", "expected_file_count": count}],
        ensure_ascii=False,
    ).encode("utf-8")


def test_expected_count는_S3_실제_적재분으로_정정된다():
    prefix = "raw/downloads/daily/biz_div=cnstwk/year=2026/month=08/day=05/hour=14/R26BK01650092_000/"
    s3 = FakeS3({
        CURATED_KEY: _curated(0),                    # zip은 안 세서 0이었다
        prefix + "R26BK01650092_000_doc03.zip": b"z",  # 압축 원본은 세지 않는다
        prefix + "R26BK01650092_000_doc03_01.hwp": b"a",
        prefix + "R26BK01650092_000_doc03_02.pdf": b"b",
        prefix + "R26BK01650092_000_doc03_03.hwp": b"c",
    })
    rows = [{**meta(), "downloadStatus": "success", "s3Key": prefix + "R26BK01650092_000_doc03.zip"}]

    stats = dl.correct_expected_counts(s3, "bidmate", rows)

    assert stats == {"files": 1, "notices": 1}
    assert json.loads(s3.objects[CURATED_KEY])[0]["expected_file_count"] == 3


def test_다운로드_실패가_있으면_정정하지_않는다():
    """일시적 실패로 개수를 낮추면 덜 처리된 공고가 merged로 확정돼 되돌릴 수 없다."""
    prefix = "raw/downloads/daily/biz_div=cnstwk/year=2026/month=08/day=05/hour=14/R26BK01650092_000/"
    s3 = FakeS3({CURATED_KEY: _curated(2), prefix + "a_doc01.hwp": b"a"})
    rows = [
        {**meta(), "downloadStatus": "success", "s3Key": prefix + "a_doc01.hwp"},
        {**meta(), "downloadStatus": "failed", "s3Key": ""},
    ]

    assert dl.correct_expected_counts(s3, "bidmate", rows) == {"files": 0, "notices": 0}
    assert json.loads(s3.objects[CURATED_KEY])[0]["expected_file_count"] == 2


def test_값이_같으면_다시_쓰지_않는다():
    prefix = "raw/downloads/daily/biz_div=cnstwk/year=2026/month=08/day=05/hour=14/R26BK01650092_000/"
    s3 = FakeS3({CURATED_KEY: _curated(1), prefix + "a_doc01.hwp": b"a"})
    rows = [{**meta(), "downloadStatus": "success", "s3Key": prefix + "a_doc01.hwp"}]

    assert dl.correct_expected_counts(s3, "bidmate", rows) == {"files": 0, "notices": 0}
    assert CURATED_KEY not in s3.puts


@pytest.mark.parametrize("status", ["skipped", "success"])
def test_DRM_제외분은_기대치에서도_빠진다(status):
    """DRM을 안 올렸으면 그만큼 기대치도 줄어야 partial 영구 고착이 안 생긴다."""
    prefix = "raw/downloads/daily/biz_div=cnstwk/year=2026/month=08/day=05/hour=14/R26BK01650092_000/"
    s3 = FakeS3({CURATED_KEY: _curated(2), prefix + "a_doc01.hwp": b"a"})
    rows = [
        {**meta(), "downloadStatus": "success", "s3Key": prefix + "a_doc01.hwp"},
        {**meta(), "downloadStatus": status, "s3Key": ""},
    ]

    dl.correct_expected_counts(s3, "bidmate", rows)
    assert json.loads(s3.objects[CURATED_KEY])[0]["expected_file_count"] == 1
