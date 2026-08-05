# -*- coding: utf-8 -*-
"""HWPML(한글 XML 저장 형식) 텍스트 추출.

parsing.hwp_hwpx.hwpml_extractor의 로직을 그대로 재사용한다 — hwp.py/hwpx.py와 같은 구조.

이 형식은 SQS 큐가 따로 없다. 나라장터 첨부는 HWPML인데도 파일명이 `.hwp`라
S3 알림이 hwp 큐로 보내고, router.dispatch가 매직바이트로 실제 포맷을 판정해
여기로 넘긴다. 그래서 큐·알림 설정 변경 없이 처리된다.

HWP/HWPX와 마찬가지로 실제 페이지 개념이 없어 문서 전체가 텍스트 한 덩어리로
나온다. paginate로 1000자 단위 인위적 페이지를 만든다(표는 안 잘리게 처리됨).
"""
from parsing.hwp_hwpx.hwpml_extractor import extract_hwpml
from parsing.hwp_hwpx.json_output import paginate

from extractors.base import ExtractResult


def extract(data: bytes, describe_fn=None) -> ExtractResult:
    result = extract_hwpml(data, describe_fn=describe_fn)
    pages = [
        {"page": i, "text": page_text}
        for i, page_text in enumerate(paginate(result["text"]), start=1)
    ]
    return {"source_type": "hwpml", "pages": pages, "images": result["images"]}
