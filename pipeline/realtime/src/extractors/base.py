# -*- coding: utf-8 -*-
"""pdf/hwp/hwpx 추출기가 공통으로 반환하는 결과 스키마.

pages는 포맷마다 '단위'가 다르다 — pdf는 원본 페이지 그대로, hwp/hwpx는
문서 전체 텍스트를 1000자 단위로 인위적으로 나눈 것(실제 페이지 개념이 없음).
그 차이는 각 추출기가 흡수하고, 여기서는 이미 pages로 정리된 형태만 다룬다.

AWS 의존성이 없는 순수 로직 계층이라 로컬에서 직접 테스트한다.
"""
from typing import NotRequired, TypedDict


class PageText(TypedDict):
    page: int
    text: str


class ExtractResult(TypedDict):
    source_type: str          # "pdf" | "hwp" | "hwpx"
    pages: list[PageText]
    images: dict               # img_id -> 위치/참조 정보 (내부용, 최종 출력엔 안 실림)
    # 추출기가 중간에 죽어 본문 일부만 회수된 경우에만 True (현재는 hwp만 발생).
    partial: NotRequired[bool]
