# -*- coding: utf-8 -*-
"""HWP(구버전 바이너리 포맷) 텍스트 추출.

parsing.hwp_hwpx.hwp_extractor의 검증된 로직을 그대로 재사용한다. 포맷 라우팅
(확장자 보고 hwp/hwpx 중 뭘 쓸지 고르는 것)은 SQS 큐가 이미 파일 형식별로
나눠서 넣어주므로, parsing.hwp_hwpx의 통합 라우터(extract/extract_bytes)를
거치지 않고 extract_hwp를 직접 호출한다.

⚠️ extract_hwp는 hwp5proc(외부 CLI 바이너리)를 subprocess로 호출한다. Lambda에
배포하려면 해당 바이너리를 Layer나 컨테이너 이미지에 포함해야 동작한다
(로컬 테스트는 PATH에 hwp5proc이 설치돼 있으면 바로 됨).
"""
from parsing.hwp_hwpx.hwp_extractor import extract_hwp

from extractors.base import ExtractResult


def extract(hwp_bytes: bytes, describe_fn=None) -> ExtractResult:
    return extract_hwp(hwp_bytes, describe_fn=describe_fn)
