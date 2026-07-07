# -*- coding: utf-8 -*-
"""PDF 텍스트 추출 (PyMuPDF) + 스캔본(텍스트 레이어 없는 페이지) 감지.

parsing.pdf.pdf_extractor의 표/이미지 위치 인식 로직을 그대로 재사용한다. PDF는
hwp/hwpx와 달리 원본 페이지 경계가 있으므로, 인위적으로 재분할하지 않고
원본 페이지를 그대로 최종 pages로 쓴다.

스캔본 감지: 페이지에 텍스트 레이어가 전혀 없으면(스캔 이미지만 있는 페이지)
OCR이 필요하다는 뜻이라 페이지별로 is_scanned를 표시한다. 예전
parsing/text_layer_detector.py(삭제됨)와 같은 방식 — get_text()가 비어있는지만 확인.
"""
import os
import tempfile

import fitz

from parsing.pdf.pdf_extractor import extract_text

from extractors.base import ExtractResult


def _detect_scanned_pages(pdf_path: str) -> set[int]:
    doc = fitz.open(pdf_path)
    scanned = {
        page_num
        for page_num, page in enumerate(doc, start=1)
        if not page.get_text().strip()
    }
    doc.close()
    return scanned


def extract(pdf_bytes: bytes) -> ExtractResult:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    try:
        result = extract_text(tmp)
        scanned_pages = _detect_scanned_pages(tmp)
    finally:
        os.unlink(tmp)

    pages = [
        {"page": page_num, "text": text, "is_scanned": page_num in scanned_pages}
        for page_num, text in sorted(result["pages"].items())
    ]
    return {"source_type": "pdf", "pages": pages, "images": result["images"]}
