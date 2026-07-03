# -*- coding: utf-8 -*-
"""HWPX(zip 안의 XML) → 텍스트/표/이미지위치 추출.

HWPX는 내부가 이미 XML이라 변환 없이 zipfile+lxml로 바로 파싱한다.
문단(hp:p)을 문서 순서대로 렌더링하고, 표(hp:tbl)는 행×셀로 누적해 마커로 감싼다.
이미지(hp:pic)는 위치에 [이미지:img_XXX] placeholder만 남긴다(캡션 미구현).
"""
import io
import re
import zipfile

from lxml import etree

from parsing.contract import ExtractResult
from parsing.common import register_image, format_table, normalize_text

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"


def _local(el) -> str:
    return etree.QName(el).localname


def _image_ref(pic):
    img = pic.find(f".//{HC}img")
    return img.get("binaryItemIDRef") if img is not None else None


def _render_table(tbl, ctx) -> str:
    rows = []
    for tr in tbl.findall(f"{HP}tr"):
        cells = []
        for tc in tr.findall(f"{HP}tc"):
            sub = tc.find(f"{HP}subList")
            cell = _render_container(sub, ctx) if sub is not None else ""
            cells.append(cell.strip())
        rows.append(cells)
    return format_table(rows)


def _render_paragraph(p, ctx) -> str:
    out = []

    def walk(node):
        for child in node:
            ln = _local(child)
            if ln == "t":
                out.append("".join(child.itertext()))
            elif ln == "tbl":
                out.append("\n" + _render_table(child, ctx) + "\n")
            elif ln == "pic":
                out.append(register_image(ctx, "hwpx", _image_ref(child)))
            else:
                walk(child)

    walk(p)
    return "".join(out)


def _render_container(container, ctx) -> str:
    return "\n".join(_render_paragraph(p, ctx) for p in container.findall(f"{HP}p"))


def extract_hwpx(data: bytes) -> ExtractResult:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted(
        n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)
    )
    ctx = {"n": 0, "images": {}}
    parts = [_render_container(etree.fromstring(z.read(n)), ctx) for n in names]
    text = normalize_text("\n".join(parts))
    return {"source_type": "hwpx", "text": text, "images": ctx["images"]}


def extract_hwpx_file(path: str) -> ExtractResult:
    with open(path, "rb") as f:
        return extract_hwpx(f.read())
