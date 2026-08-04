# -*- coding: utf-8 -*-
"""HWP → hwp5proc xml(embedbin 없이) → 텍스트/표/이미지위치 추출.

HWP는 바이너리(OLE)라 hwp5proc로 XML을 뽑은 뒤 lxml로 파싱한다. embedbin은
대용량 무압축 이미지에서 폭주하므로 빼고, 본문·표·그림 '위치'만 추출한다.
이미지(ShapePicture)는 [이미지:img_XXX] placeholder만 남긴다(캡션 미구현).
"""
import logging
import os
import re
import subprocess
import tempfile
import zlib

from lxml import etree

from parsing.hwp_hwpx.contract import ExtractResult
from parsing.hwp_hwpx.common import register_image, format_table, normalize_text

logger = logging.getLogger(__name__)

# 실행 파일 경로. 기본은 PATH의 hwp5proc, HWP5PROC_PATH 환경변수로 재정의 가능.
HWP5PROC = os.getenv("HWP5PROC_PATH", "hwp5proc")

# hwp5proc는 컬러 출력이라 그대로 로그에 실으면 제어문자가 섞인다.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# stderr에는 경고가 길게 쌓이는데 실제 사유는 항상 끝(traceback)에 있어 꼬리만 남긴다.
_STDERR_TAIL = 1500
# XML 1.0이 허용하지 않는 문자(제어문자·비문자). hwp5proc 출력에 섞여 들어온다.
_ILLEGAL_XML = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)


class Hwp5procError(RuntimeError):
    """hwp5proc 비정상 종료. 사유(stderr)와 중단 직전까지의 출력(stdout)을 함께 들고 온다.

    subprocess의 CalledProcessError는 stderr를 메시지에 담지 않는다. check=True로
    그냥 두면 로그에 "returned non-zero exit status 1"만 남아서, 실제 사유
    (Not an OLE2 …, pyhwp의 AssertionError, KeyError …)를 알려면 실패한 문서를
    매번 내려받아 재현해야 했다. 원인별로 대응이 갈리므로 사유를 실어 보낸다.
    """

    def __init__(self, subcommand: str, returncode: int, stdout: bytes, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"hwp5proc {subcommand} 실패(exit {returncode}): {stderr}")


def _run_hwp5proc(args: list[str]) -> bytes:
    proc = subprocess.run([HWP5PROC, *args], capture_output=True)
    if proc.returncode != 0:
        stderr = _ANSI.sub("", proc.stderr.decode("utf-8", "replace")).strip()
        raise Hwp5procError(args[0], proc.returncode, proc.stdout, stderr[-_STDERR_TAIL:])
    return proc.stdout


def _generate_xml(hwp_path: str) -> bytes:
    return _run_hwp5proc(["xml", "--no-validate-wellformed", hwp_path])


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
            out = _run_hwp5proc(["ls", hwp_path]).decode("utf-8", "replace")
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
            raw = _run_hwp5proc(["cat", hwp_path, name])
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


def _strip_illegal_xml(data: bytes) -> bytes:
    return _ILLEGAL_XML.sub("", data.decode("utf-8", "replace")).encode("utf-8")


def _parse_xml(path: str):
    """hwp5proc 출력을 파싱한다. 반환값 두 번째는 '부분 추출' 여부."""
    partial = False
    try:
        raw = _generate_xml(path)
    except Hwp5procError as e:
        # pyhwp는 일부 문서에서 XML을 끝까지 쓰지 못하고 중간에 죽는다(다단 구조의
        # xmlmodel.wrap_columns AssertionError 등, 실측 3건). 그때도 중단 직전까지의
        # XML은 stdout에 남아 있어 통째로 버리는 대신 recover 파서로 살릴 수 있는
        # 만큼 살린다. 아무것도 못 받았으면(0바이트, 예: OLE가 아닌 파일) 살릴 게 없다.
        if not e.stdout:
            raise
        logger.warning(
            "hwp5proc 중단 — 부분 추출로 진행(%d바이트 회수): %s", len(e.stdout), e.stderr,
        )
        raw, partial = e.stdout, True

    parser = etree.XMLParser(recover=True) if partial else None
    try:
        root = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        # hwp5proc가 정상 종료(exit 0)했는데도 lxml이 거부하는 경우가 있다. DocInfo의
        # Style name 등에 원본 바이트를 그대로 실어 보내 XML 1.0 비허용 문자가 섞이기
        # 때문(실측 U+0002/U+0011/U+FFFF). 본문이 아니라 메타데이터 구간이라 지워도
        # 추출 텍스트에는 영향이 없다.
        logger.warning("XML 비허용 문자 제거 후 재파싱: %s", path)
        root = etree.fromstring(_strip_illegal_xml(raw), parser)
    if root is None:
        raise ValueError(f"hwp5proc 출력에서 XML 트리를 만들지 못함: {path}")
    return root, partial


def extract_hwp_file(path: str, describe_fn=None) -> ExtractResult:
    root, partial = _parse_xml(path)
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
    result: ExtractResult = {"source_type": "hwp", "text": text, "images": ctx["images"]}
    if partial:
        result["partial"] = True
    return result


def extract_hwp(data: bytes, describe_fn=None) -> ExtractResult:
    """bytes 입력(예: S3) → 임시파일로 저장 후 hwp5proc 처리."""
    with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        return extract_hwp_file(tmp, describe_fn=describe_fn)
    finally:
        os.unlink(tmp)
