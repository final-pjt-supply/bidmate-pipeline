# -*- coding: utf-8 -*-
"""추출 결과 공통 인메모리 계약 + 마커 상수.

- 표: [표]\\n{행}\\n[/표] (셀 " | " join, 행 "\\n" join)
- 이미지: [이미지:img_XXX] 위치 placeholder + registry(ref만, 캡션 없음)
"""
from typing import NotRequired, TypedDict

TABLE_OPEN = "[표]"
TABLE_CLOSE = "[/표]"


def image_placeholder(img_id: str, caption: str | None = None) -> str:
    # 캡션이 있으면 본문에 인라인으로 붙여 임베딩 텍스트에 바로 포함시킨다.
    if caption:
        return f"[이미지:{img_id}: {caption}]"
    return f"[이미지:{img_id}]"


class ExtractResult(TypedDict):
    source_type: str   # "hwp" | "hwpx"
    text: str          # [표]…[/표], [이미지:img_XXX] 마커 포함 전체 본문
    images: dict       # img_id -> {"source_type", "ref"} (위치만; 캡션 미구현)
    # 추출기가 중간에 죽어 본문 일부만 회수된 경우에만 True. 완전한 문서와
    # 구분이 안 되면 잘린 공고가 조용히 인덱스에 들어가므로 표시를 남긴다.
    partial: NotRequired[bool]
