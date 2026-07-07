# -*- coding: utf-8 -*-
import fitz
from lambda_app.pdf_extractor import extract_pdf


def _two_page_pdf() -> bytes:
    doc = fitz.open()
    p1 = doc.new_page(); p1.insert_text((72, 72), "FIRST page body")
    p2 = doc.new_page(); p2.insert_text((72, 72), "SECOND page body")
    data = doc.tobytes()
    doc.close()
    return data


def test_extract_pdf_shape_and_page_join():
    result = extract_pdf(_two_page_pdf())
    assert result["source_type"] == "pdf"
    assert "FIRST page body" in result["text"]
    assert "SECOND page body" in result["text"]
    assert result["text"].index("FIRST") < result["text"].index("SECOND")
    assert isinstance(result["images"], dict)
