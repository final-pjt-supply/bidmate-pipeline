# -*- coding: utf-8 -*-
"""HWP/HWPX 추출기가 공유하는 포맷 로직.

트리 순회는 형식마다 다르지만, 표 마커 래핑·이미지 id 생성·텍스트 정규화는
동일하므로 여기로 모은다. 추출기는 형식별로 '행×셀'과 '이미지 ref'만 만들어
넘기고, 마커/registry 규약은 이 모듈이 책임진다.
"""
import re

from parsing.hwp_hwpx.contract import TABLE_OPEN, TABLE_CLOSE, image_placeholder


def register_image(ctx: dict, source_type: str, ref) -> str:
    """이미지를 registry에 등록하고 위치 placeholder 문자열을 반환.

    ctx = {"n": int, "images": dict, "resolve": fn|None, "describe_fn": fn|None}.
    ref는 형식별 식별자(HWP bindata-id, HWPX binaryItemIDRef 등).

    resolve(ref) -> bytes|None 가 주어지면 이미지 바이트를 얻고, describe_fn(bytes)
    -> str|None 가 주어지면 캡션을 만들어 placeholder에 인라인으로 붙인다. 둘 중
    하나라도 없거나 실패(None)하면 캡션 없이 placeholder만 남긴다(그 이미지만 스킵).
    """
    ctx["n"] += 1
    img_id = f"img_{ctx['n']:03d}"
    ctx["images"][img_id] = {"source_type": source_type, "ref": ref}

    caption = None
    resolve = ctx.get("resolve")
    describe_fn = ctx.get("describe_fn")
    if resolve is not None and describe_fn is not None:
        image_bytes = resolve(ref)
        # --- 장식 이미지 필터 (현재 비활성) ---
        # 로고·구분선·아이콘 같은 자잘한 장식 이미지가 발견되면, 아래 한 줄로
        # 캡션을 생략한다(원본 픽셀 크기·종횡비 기준). 지금은 전부 설명한다.
        # if image_bytes and is_decorative(image_bytes): image_bytes = None
        if image_bytes:
            caption = describe_fn(image_bytes)

    return image_placeholder(img_id, caption)


def format_table(rows: list[list[str]]) -> str:
    """행×셀을 [표]\\n{셀 " | " / 행 "\\n"}\\n[/표]로 포맷."""
    lines = [" | ".join(cells) for cells in rows]
    return f"{TABLE_OPEN}\n" + "\n".join(lines) + f"\n{TABLE_CLOSE}"


def normalize_text(text: str) -> str:
    """3연속 이상 빈 줄을 2줄로 축소."""
    return re.sub(r"\n{3,}", "\n\n", text)
