# -*- coding: utf-8 -*-
from backfill_lambda import router


def test_detect_format_by_magic():
    assert router._detect_format(b"PK\x03\x04rest", "x.hwp") == "hwpx"
    assert router._detect_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "x.hwp") == "hwp"
    assert router._detect_format(b"%PDF-1.7", "x.bin") == "pdf"


def test_detect_format_fallback_to_extension():
    assert router._detect_format(b"garbage!", "doc.hwpx") == "hwpx"
    assert router._detect_format(b"garbage!", "doc.pdf") == "pdf"


def test_router_oralabel_hwp_routes_to_hwpx(monkeypatch):
    called = {}
    import parsing.hwp_hwpx.hwpx_extractor as hx
    monkeypatch.setattr(hx, "extract_hwpx",
                        lambda data, describe_fn=None: called.setdefault("fn", "hwpx"))
    router.extract_document(b"PK\x03\x04xxxx", "R26_000_doc01.hwp")
    assert called["fn"] == "hwpx"


def test_router_pdf_routes_to_pdf(monkeypatch):
    called = {}
    import backfill_lambda.pdf_extractor as px
    monkeypatch.setattr(px, "extract_pdf",
                        lambda data, describe_fn=None: called.setdefault("fn", "pdf"))
    router.extract_document(b"%PDF-1.7 xxx", "x.pdf")
    assert called["fn"] == "pdf"
