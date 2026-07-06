# -*- coding: utf-8 -*-
"""pdf/hwp/hwpx 추출기가 공통으로 반환하는 결과 스키마.

AWS 의존성이 없는 순수 로직 계층이라 로컬에서 직접 테스트한다.
"""
from typing import TypedDict


class ExtractResult(TypedDict):
    pass  # TODO: 필드 확정 (예: text, pages, images 등)
