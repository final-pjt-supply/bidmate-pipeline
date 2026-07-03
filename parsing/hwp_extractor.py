# -*- coding: utf-8 -*-
"""HWP → hwp5proc xml(embedbin 없이) → 텍스트/표/이미지위치 추출.

HWP는 바이너리(OLE)라 hwp5proc로 XML을 뽑은 뒤 lxml로 파싱한다. embedbin은
대용량 무압축 이미지에서 폭주하므로 빼고, 본문·표·그림 '위치'만 추출한다.
이미지(ShapePicture)는 [이미지:img_XXX] placeholder만 남긴다(캡션 미구현).
"""
import os
import re
import subprocess
import tempfile

from lxml import etree

from parsing.contract import (
    ExtractResult, TABLE_OPEN, TABLE_CLOSE, image_placeholder,
)

HWP5PROC = "hwp5proc"  # PATH에 설치돼 있어야 함(pyhwp)


def _generate_xml(hwp_path: str) -> bytes:
    proc = subprocess.run(
        [HWP5PROC, "xml", "--no-validate-wellformed", hwp_path],
        capture_output=True, check=True,
    )
    return proc.stdout


def _register_image(shape, ctx) -> str:
    ctx["n"] += 1
    img_id = f"img_{ctx['n']:03d}"
    pi = shape.find(".//PictureInfo")
    ref = pi.get("bindata-id") if pi is not None else None
    ctx["images"][img_id] = {"source_type": "hwp", "ref": ref}
    return image_placeholder(img_id)


def _render_table(tc, ctx) -> str:
    rows = []
    for tr in tc.findall("TableBody/TableRow"):
        cells = []
        for cell in tr.findall("TableCell"):
            cell_text = "\n".join(
                _render_paragraph(p, ctx) for p in cell.findall("Paragraph")
            )
            cells.append(cell_text.strip())
        rows.append(" | ".join(cells))
    return f"{TABLE_OPEN}\n" + "\n".join(rows) + f"\n{TABLE_CLOSE}"


def _render_paragraph(p, ctx) -> str:
    out = []

    def walk(node):
        for child in node:
            tag = child.tag
            if tag == "Text":
                if child.text:
                    out.append(child.text)
            elif tag == "TableControl":
                out.append("\n" + _render_table(child, ctx) + "\n")
            elif tag == "ShapePicture":
                out.append(_register_image(child, ctx))
            else:
                walk(child)

    walk(p)
    return "".join(out)


def extract_hwp_file(path: str) -> ExtractResult:
    root = etree.fromstring(_generate_xml(path))
    body = root.find(".//BodyText")
    if body is None:
        body = root
    ctx = {"n": 0, "images": {}}
    lines = []
    for p in body.iter("Paragraph"):
        # 셀 내부 문단은 표 렌더링에서 처리하므로 최상위 문단만
        if any(a.tag == "TableCell" for a in p.iterancestors()):
            continue
        lines.append(_render_paragraph(p, ctx))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return {"source_type": "hwp", "text": text, "images": ctx["images"]}


def extract_hwp(data: bytes) -> ExtractResult:
    """bytes 입력(예: S3) → 임시파일로 저장 후 hwp5proc 처리."""
    with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        return extract_hwp_file(tmp)
    finally:
        os.unlink(tmp)
