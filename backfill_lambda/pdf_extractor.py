# -*- coding: utf-8 -*-
"""PDF 추출 — main의 parsing.pdf.pdf_extractor.extract_text를 재사용해 ExtractResult로 래핑.

무거운 fitz 추출 로직은 parsing/pdf/ 에 두어(단일 출처, main 개선 자동 반영) 여기선
물리 페이지 join + 계약(dict) 변환만 담당한다. 캡션 미연결(describe_fn 무시).
"""
import os
import tempfile

from parsing.pdf.pdf_extractor import extract_text


def extract_pdf_file(path: str, describe_fn=None) -> dict:
    """extract_text 결과({"pages":{n:text},"images":registry})를 ExtractResult로 변환.

    물리 페이지를 페이지 번호 순서로 join(1000자 청킹은 to_json_doc 담당).
    """
    raw = extract_text(path)
    pages = raw["pages"]
    text = "\n".join(pages[n] for n in sorted(pages, key=int))
    images = {
        img_id: {"source_type": "pdf", "ref": str(meta.get("xref"))}
        for img_id, meta in raw.get("images", {}).items()
    }
    return {"source_type": "pdf", "text": text, "images": images}


def extract_pdf(data: bytes, describe_fn=None) -> dict:
    """bytes(예: S3) → /tmp 임시파일 → extract_pdf_file."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        return extract_pdf_file(tmp, describe_fn=describe_fn)
    finally:
        os.unlink(tmp)
