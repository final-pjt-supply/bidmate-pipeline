# -*- coding: utf-8 -*-
"""HWP(구버전 바이너리 포맷) 텍스트 추출.

parsing.hwp_hwpx.hwp_extractor의 검증된 로직을 그대로 재사용한다. 포맷 라우팅
(확장자 보고 hwp/hwpx 중 뭘 쓸지 고르는 것)은 SQS 큐가 이미 파일 형식별로
나눠서 넣어주므로, parsing.hwp_hwpx의 통합 라우터(extract/extract_bytes)를
거치지 않고 extract_hwp를 직접 호출한다.

HWP엔 실제 페이지 개념이 없어 문서 전체가 텍스트 한 덩어리로 나온다. 백필
파이프라인과 동일하게 parsing.hwp_hwpx.json_output.paginate로 1000자 단위
인위적 페이지를 만든다(표는 안 잘리게 처리됨).

⚠️ extract_hwp는 hwp5proc(외부 CLI 바이너리)를 subprocess로 호출한다. Lambda에
배포하려면 해당 바이너리를 Layer나 컨테이너 이미지에 포함해야 동작한다
(로컬 테스트는 PATH에 hwp5proc이 설치돼 있으면 바로 됨).
"""
from parsing.hwp_hwpx.hwp_extractor import extract_hwp
from parsing.hwp_hwpx.json_output import paginate

from extractors.base import ExtractResult


def extract(hwp_bytes: bytes, describe_fn=None) -> ExtractResult:
    result = extract_hwp(hwp_bytes, describe_fn=describe_fn)
    pages = [
        {"page": i, "text": page_text}
        for i, page_text in enumerate(paginate(result["text"]), start=1)
    ]
    out: ExtractResult = {"source_type": "hwp", "pages": pages, "images": result["images"]}
    if result.get("partial"):
        out["partial"] = True
    return out
