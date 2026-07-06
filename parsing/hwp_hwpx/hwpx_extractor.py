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
from parsing.hwp_hwpx.common import register_image, format_table, normalize_text

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


def _make_bindata_resolver(z: zipfile.ZipFile):
    """binaryItemIDRef -> 이미지 bytes 리졸버. 매니페스트(content.hpf)로 href를
    찾고, 없으면 BinData/ 이름 매칭으로 폴백한다. 못 찾으면 None."""
    # 1) 매니페스트 id -> href 매핑 (네임스페이스 무시하고 localname으로 매칭)
    href_by_id: dict[str, str] = {}
    try:
        manifest = etree.fromstring(z.read("Contents/content.hpf"))
        for el in manifest.iter():
            if _local(el) == "item" and el.get("id") and el.get("href"):
                href_by_id[el.get("id")] = el.get("href")
    except Exception:
        pass  # 매니페스트 없거나 파싱 실패 → 폴백만 사용

    # 2) BinData 실제 경로 목록 (폴백용): stem -> full name
    bindata = {
        n.rsplit("/", 1)[-1].split(".")[0]: n
        for n in z.namelist() if n.startswith("BinData/")
    }

    def resolve(ref):
        if ref is None:
            return None
        # href는 보통 "BinData/xxx.png". zip 루트 기준 경로로 정규화.
        href = href_by_id.get(ref)
        candidates = []
        if href:
            candidates.append(href.lstrip("./"))
            candidates.append(f"Contents/{href}".replace("Contents/BinData", "BinData"))
        if ref in bindata:
            candidates.append(bindata[ref])
        for name in candidates:
            try:
                return z.read(name)
            except KeyError:
                continue
        return None

    return resolve


def extract_hwpx(data: bytes, describe_fn=None) -> ExtractResult:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted(
        n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)
    )
    ctx = {
        "n": 0, "images": {},
        "resolve": _make_bindata_resolver(z),
        "describe_fn": describe_fn,
    }
    parts = [_render_container(etree.fromstring(z.read(n)), ctx) for n in names]
    text = normalize_text("\n".join(parts))
    return {"source_type": "hwpx", "text": text, "images": ctx["images"]}


def extract_hwpx_file(path: str, describe_fn=None) -> ExtractResult:
    with open(path, "rb") as f:
        return extract_hwpx(f.read(), describe_fn=describe_fn)
