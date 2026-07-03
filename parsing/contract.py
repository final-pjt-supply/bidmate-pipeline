# -*- coding: utf-8 -*-
"""추출 결과 공통 인메모리 계약 + 마커 상수.

- 표: [표]\\n{행}\\n[/표] (셀 " | " join, 행 "\\n" join)
- 이미지: [이미지:img_XXX] 위치 placeholder + registry(ref만, 캡션 없음)
"""
from typing import TypedDict

TABLE_OPEN = "[표]"
TABLE_CLOSE = "[/표]"


def image_placeholder(img_id: str) -> str:
    return f"[이미지:{img_id}]"


class ExtractResult(TypedDict):
    source_type: str   # "hwp" | "hwpx"
    text: str          # [표]…[/표], [이미지:img_XXX] 마커 포함 전체 본문
    images: dict       # img_id -> {"source_type", "ref"} (위치만; 캡션 미구현)
