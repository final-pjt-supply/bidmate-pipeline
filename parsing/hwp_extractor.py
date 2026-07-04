# -*- coding: utf-8 -*-
"""HWP → hwp5proc xml(embedbin 없이) → 텍스트/표/이미지위치 추출.

HWP는 바이너리(OLE)라 hwp5proc로 XML을 뽑은 뒤 lxml로 파싱한다. embedbin은
대용량 무압축 이미지에서 폭주하므로 빼고, 본문·표·그림 '위치'만 추출한다.
이미지(ShapePicture)는 [이미지:img_XXX] placeholder만 남긴다(캡션 미구현).
"""
import os
import subprocess
import tempfile

from lxml import etree

from parsing.contract import ExtractResult
from parsing.common import register_image, format_table, normalize_text

# 실행 파일 경로. 기본은 PATH의 hwp5proc, HWP5PROC_PATH 환경변수로 재정의 가능.
HWP5PROC = os.getenv("HWP5PROC_PATH", "hwp5proc")


def _generate_xml(hwp_path: str) -> bytes:
    proc = subprocess.run(
        [HWP5PROC, "xml", "--no-validate-wellformed", hwp_path],
        capture_output=True, check=True,
    )
    return proc.stdout


def _image_ref(shape):
    pi = shape.find(".//PictureInfo")
    return pi.get("bindata-id") if pi is not None else None


def _render_table(tc, ctx) -> str:
    rows = []
    for tr in tc.findall("TableBody/TableRow"):
        cells = []
        for cell in tr.findall("TableCell"):
            cell_text = "\n".join(
                _render_paragraph(p, ctx) for p in cell.findall("Paragraph")
            )
            cells.append(cell_text.strip())
        rows.append(cells)
    return format_table(rows)


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
                out.append(register_image(ctx, "hwp", _image_ref(child)))
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
    text = normalize_text("\n".join(lines))
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
