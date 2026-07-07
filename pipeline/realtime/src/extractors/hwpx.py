# -*- coding: utf-8 -*-
"""HWPX 텍스트 추출.

parsing.hwp_hwpx.hwpx_extractor의 검증된 로직을 그대로 재사용한다. 포맷 라우팅
(확장자 보고 hwp/hwpx 중 뭘 쓸지 고르는 것)은 SQS 큐가 이미 파일 형식별로
나눠서 넣어주므로, parsing.hwp_hwpx의 통합 라우터(extract/extract_bytes)를
거치지 않고 extract_hwpx를 직접 호출한다.

HWPX도 실제 페이지 개념이 없어 문서 전체가 텍스트 한 덩어리로 나온다. 백필
파이프라인과 동일하게 parsing.hwp_hwpx.json_output.paginate로 1000자 단위
인위적 페이지를 만든다(표는 안 잘리게 처리됨).
"""
from parsing.hwp_hwpx.hwpx_extractor import extract_hwpx
from parsing.hwp_hwpx.json_output import paginate

from extractors.base import ExtractResult


def extract(hwpx_bytes: bytes, describe_fn=None) -> ExtractResult:
    result = extract_hwpx(hwpx_bytes, describe_fn=describe_fn)
    pages = [
        {"page": i, "text": page_text}
        for i, page_text in enumerate(paginate(result["text"]), start=1)
    ]
    return {"source_type": "hwpx", "pages": pages, "images": result["images"]}
