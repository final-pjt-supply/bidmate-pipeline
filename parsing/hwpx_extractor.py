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

from parsing.contract import (
    ExtractResult, TABLE_OPEN, TABLE_CLOSE, image_placeholder,
)

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"


def _local(el) -> str:
    return etree.QName(el).localname


def _register_image(pic, ctx) -> str:
    ctx["n"] += 1
    img_id = f"img_{ctx['n']:03d}"
    img = pic.find(f".//{HC}img")
    ref = img.get("binaryItemIDRef") if img is not None else None
    ctx["images"][img_id] = {"source_type": "hwpx", "ref": ref}
    return image_placeholder(img_id)


def _render_table(tbl, ctx) -> str:
    rows = []
    for tr in tbl.findall(f"{HP}tr"):
        cells = []
        for tc in tr.findall(f"{HP}tc"):
            sub = tc.find(f"{HP}subList")
            cell = _render_container(sub, ctx) if sub is not None else ""
            cells.append(cell.strip())
        rows.append(" | ".join(cells))
    return f"{TABLE_OPEN}\n" + "\n".join(rows) + f"\n{TABLE_CLOSE}"


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
                out.append(_register_image(child, ctx))
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
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(parts))
    return {"source_type": "hwpx", "text": text, "images": ctx["images"]}


def extract_hwpx_file(path: str) -> ExtractResult:
    with open(path, "rb") as f:
        return extract_hwpx(f.read())
