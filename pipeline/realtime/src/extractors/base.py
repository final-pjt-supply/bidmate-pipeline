# -*- coding: utf-8 -*-
"""pdf/hwp/hwpx 추출기가 공통으로 반환하는 결과 스키마.

AWS 의존성이 없는 순수 로직 계층이라 로컬에서 직접 테스트한다.
"""
from typing import TypedDict


class ExtractResult(TypedDict):
    source_type: str   # "pdf" | "hwp" | "hwpx"
    text: str          # 본문 (표/이미지 마커 포함)
    images: dict        # img_id -> 위치/참조 정보
