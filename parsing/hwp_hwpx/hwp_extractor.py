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
import zlib

from lxml import etree

from parsing.contract import ExtractResult
from parsing.hwp_hwpx.common import register_image, format_table, normalize_text

# 실행 파일 경로. 기본은 PATH의 hwp5proc, HWP5PROC_PATH 환경변수로 재정의 가능.
HWP5PROC = os.getenv("HWP5PROC_PATH", "hwp5proc")


def _generate_xml(hwp_path: str) -> bytes:
    proc = subprocess.run(
        [HWP5PROC, "xml", "--no-validate-wellformed", hwp_path],
        capture_output=True, check=True,
    )
    return proc.stdout


def _make_bindata_resolver(hwp_path: str):
    """PictureInfo bindata-id -> 이미지 bytes 리졸버(HWP).

    ⚠️ 미검증: 현재 샘플에 이미지 포함 HWP가 없어 실제 문서로 확인 못 함. 어떤
    단계든 실패하면 None을 반환해 '캡션만 생략'되고 본문 추출은 정상 진행된다.
    실제 이미지 HWP 확보 시 스트림 이름 매핑·압축 처리를 검증·보정할 것.

    방식: hwp5proc로 BinData 스트림 목록을 한 번 얻어 숫자 id -> 스트림명 맵을
    만들고(지연 로딩), 필요한 스트림만 cat으로 추출한다(embedbin 불필요).
    """
    cache: dict = {}

    def _bin_map():
        if "map" in cache:
            return cache["map"]
        m: dict[int, str] = {}
        try:
            out = subprocess.run(
                [HWP5PROC, "ls", hwp_path], capture_output=True, check=True
            ).stdout.decode("utf-8", "replace")
            for line in out.splitlines():
                name = line.strip()
                # 예: "BinData/BIN0001.png" -> 0x0001
                mo = re.search(r"BinData/BIN([0-9A-Fa-f]+)\.", name)
                if mo:
                    m[int(mo.group(1), 16)] = name
        except Exception:
            pass
        cache["map"] = m
        return m

    def resolve(ref):
        if ref is None:
            return None
        try:
            key = int(str(ref), 0)
        except (TypeError, ValueError):
            return None
        name = _bin_map().get(key)
        if not name:
            return None
        try:
            raw = subprocess.run(
                [HWP5PROC, "cat", hwp_path, name], capture_output=True, check=True
            ).stdout
        except Exception:
            return None
        # BinData 스트림이 압축(zlib)돼 있으면 풀어서 반환. 아니면 원본 그대로.
        try:
            return zlib.decompress(raw, -15)
        except Exception:
            return raw

    return resolve


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


def extract_hwp_file(path: str, describe_fn=None) -> ExtractResult:
    root = etree.fromstring(_generate_xml(path))
    body = root.find(".//BodyText")
    if body is None:
        body = root
    ctx = {
        "n": 0, "images": {},
        "resolve": _make_bindata_resolver(path),
        "describe_fn": describe_fn,
    }
    lines = []
    for p in body.iter("Paragraph"):
        # 셀 내부 문단은 표 렌더링에서 처리하므로 최상위 문단만
        if any(a.tag == "TableCell" for a in p.iterancestors()):
            continue
        lines.append(_render_paragraph(p, ctx))
    text = normalize_text("\n".join(lines))
    return {"source_type": "hwp", "text": text, "images": ctx["images"]}


def extract_hwp(data: bytes, describe_fn=None) -> ExtractResult:
    """bytes 입력(예: S3) → 임시파일로 저장 후 hwp5proc 처리."""
    with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        return extract_hwp_file(tmp, describe_fn=describe_fn)
    finally:
        os.unlink(tmp)
