# -*- coding: utf-8 -*-
"""extractors/router.py 단위테스트 — 매직바이트 판정 + 올바른 추출기 디스패치 검증.

dispatch()가 실제로 부르는 extractors.pdf/hwp/hwpx.extract()는 monkeypatch로
대체한다(무거운 실제 파싱 의존성 없이 "어느 추출기가 불렸는지"만 검증).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# extractors/pdf.py·hwp.py·hwpx.py가 리포 루트의 parsing/ 패키지를 import하므로
# (Dockerfile.extractor와 동일 구조) 리포 루트도 sys.path에 있어야 한다.
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from extractors import router  # noqa: E402

OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 8
ZIP_HEAD = b"PK\x03\x04" + b"\x00" * 12
PDF_HEAD = b"%PDF-1.7" + b"\x00" * 8
# HWPML(한글 XML 저장 형식). 나라장터는 .hwp 이름으로 내려주는데 내용이 OLE가 아니다.
HWPML_HEAD = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<!DOCTYPE HWPML [\n\t<!ENTITY nbsp\t"&#160;">\n]>\n'
    b'<HWPML Version="2.1"><BODY><SECTION Id="0"></SECTION></BODY></HWPML>'
)


def test_detect_format_ole_is_hwp():
    assert router.detect_format(OLE_HEAD, "x.hwp") == "hwp"


def test_detect_format_zip_is_hwpx():
    assert router.detect_format(ZIP_HEAD, "x.hwpx") == "hwpx"


def test_detect_format_pdf_signature():
    assert router.detect_format(PDF_HEAD, "x.pdf") == "pdf"


def test_detect_format_unknown_falls_back_to_extension():
    assert router.detect_format(b"garbage!!!!!!!!", "doc.hwpx") == "hwpx"
    assert router.detect_format(b"garbage!!!!!!!!", "doc.pdf") == "pdf"


def test_detect_format_hwpml():
    """이름은 .hwp지만 내용은 HWPML — 확장자 폴백에 맡기면 hwp5proc가 죽는다."""
    assert router.detect_format(HWPML_HEAD, "x.hwp") == "hwpml"
    assert router.detect_format(b"\xef\xbb\xbf" + HWPML_HEAD, "x.hwp") == "hwpml"  # BOM


def test_detect_format_other_xml_is_not_hwpml():
    """HWPML이 아닌 XML까지 삼키면 안 된다 — 루트 태그까지 확인한다."""
    assert router.detect_format(b"<?xml version='1.0'?><rss><channel/></rss>", "x.hwp") == "hwp"


def test_dispatch_hwp_named_hwpml_content_goes_to_hwpml_extractor(monkeypatch):
    calls = []
    monkeypatch.setattr("extractors.hwp.extract", lambda data: calls.append("hwp"))
    monkeypatch.setattr(
        "extractors.hwpml.extract",
        lambda data: calls.append("hwpml") or {"source_type": "hwpml", "pages": [], "images": {}},
    )

    result = router.dispatch(HWPML_HEAD, "raw/downloads/daily/.../R26_000_doc03_01.hwp")

    assert calls == ["hwpml"]
    assert result["source_type"] == "hwpml"


def test_dispatch_hwpx_named_ole_content_goes_to_hwp_extractor(monkeypatch):
    """오배송 재현 — 확장자는 .hwpx인데 실제 내용은 OLE(진짜 hwp)."""
    calls = []
    monkeypatch.setattr("extractors.hwp.extract", lambda data: calls.append(("hwp", data)) or {"source_type": "hwp", "pages": [], "images": {}})
    monkeypatch.setattr("extractors.hwpx.extract", lambda data: calls.append(("hwpx", data)) or {"source_type": "hwpx", "pages": [], "images": {}})

    result = router.dispatch(OLE_HEAD, "raw/downloads/daily/.../R26_000_doc01.hwpx")

    assert calls == [("hwp", OLE_HEAD)]
    assert result["source_type"] == "hwp"


def test_dispatch_hwp_named_zip_content_goes_to_hwpx_extractor(monkeypatch):
    """반대 오배송 재현 — 확장자는 .hwp인데 실제 내용은 ZIP(진짜 hwpx)."""
    calls = []
    monkeypatch.setattr("extractors.hwp.extract", lambda data: calls.append(("hwp", data)) or {"source_type": "hwp", "pages": [], "images": {}})
    monkeypatch.setattr("extractors.hwpx.extract", lambda data: calls.append(("hwpx", data)) or {"source_type": "hwpx", "pages": [], "images": {}})

    result = router.dispatch(ZIP_HEAD, "raw/downloads/daily/.../R26_000_doc01.hwp")

    assert calls == [("hwpx", ZIP_HEAD)]
    assert result["source_type"] == "hwpx"


def test_dispatch_matching_extension_no_warning(monkeypatch, caplog):
    monkeypatch.setattr("extractors.hwp.extract", lambda data: {"source_type": "hwp", "pages": [], "images": {}})

    with caplog.at_level("WARNING"):
        router.dispatch(OLE_HEAD, "raw/downloads/daily/.../R26_000_doc01.hwp")

    assert not any("불일치" in r.message for r in caplog.records)


def test_dispatch_mismatch_logs_warning_with_key_ext_and_detected_format(monkeypatch, caplog):
    monkeypatch.setattr("extractors.hwp.extract", lambda data: {"source_type": "hwp", "pages": [], "images": {}})
    key = "raw/downloads/daily/.../R26_000_doc01.hwpx"

    with caplog.at_level("WARNING"):
        router.dispatch(OLE_HEAD, key)

    warnings = [r for r in caplog.records if "불일치" in r.message]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert key in msg
    assert "hwpx" in msg  # 확장자
    assert "hwp" in msg   # 실제 판정된 포맷


def test_dispatch_pdf_extension_matches_pdf_content(monkeypatch):
    monkeypatch.setattr("extractors.pdf.extract", lambda data: {"source_type": "pdf", "pages": [], "images": {}})

    result = router.dispatch(PDF_HEAD, "raw/downloads/daily/.../R26_000_doc01.pdf")

    assert result["source_type"] == "pdf"


def test_dispatch_unsupported_format_raises():
    with pytest.raises(ValueError):
        router.dispatch(b"garbage!!!!!!!!", "raw/downloads/daily/.../R26_000_doc01.zip")
