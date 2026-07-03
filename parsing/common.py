# -*- coding: utf-8 -*-
"""HWP/HWPX 추출기가 공유하는 포맷 로직.

트리 순회는 형식마다 다르지만, 표 마커 래핑·이미지 id 생성·텍스트 정규화는
동일하므로 여기로 모은다. 추출기는 형식별로 '행×셀'과 '이미지 ref'만 만들어
넘기고, 마커/registry 규약은 이 모듈이 책임진다.
"""
import re

from parsing.contract import TABLE_OPEN, TABLE_CLOSE, image_placeholder


def register_image(ctx: dict, source_type: str, ref) -> str:
    """이미지를 registry에 등록하고 위치 placeholder 문자열을 반환.

    ctx = {"n": int, "images": dict}. ref는 형식별 식별자(HWP bindata-id,
    HWPX binaryItemIDRef 등).
    """
    ctx["n"] += 1
    img_id = f"img_{ctx['n']:03d}"
    ctx["images"][img_id] = {"source_type": source_type, "ref": ref}
    return image_placeholder(img_id)


def format_table(rows: list[list[str]]) -> str:
    """행×셀을 [표]\\n{셀 " | " / 행 "\\n"}\\n[/표]로 포맷."""
    lines = [" | ".join(cells) for cells in rows]
    return f"{TABLE_OPEN}\n" + "\n".join(lines) + f"\n{TABLE_CLOSE}"


def normalize_text(text: str) -> str:
    """3연속 이상 빈 줄을 2줄로 축소."""
    return re.sub(r"\n{3,}", "\n\n", text)
