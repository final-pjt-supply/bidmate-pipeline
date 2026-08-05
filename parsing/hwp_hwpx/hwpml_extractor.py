# -*- coding: utf-8 -*-
"""HWPML(한글 XML 저장 형식) → 텍스트/표/이미지위치 추출.

HWP/HWPX와 컨테이너가 다른 제3의 형식이다:
  - .hwp   : OLE2 복합문서(바이너리)          → hwp5proc 경유
  - .hwpx  : ZIP + OWPML(네임스페이스 있는 XML) → zipfile + lxml
  - HWPML  : 단일 XML 파일 하나, 네임스페이스 없음 → lxml만
정식 확장자는 .hml이지만 나라장터 첨부는 .hwp로 이름이 붙어 들어온다(실측).
그래서 확장자가 아니라 매직바이트로 판별해야 한다(router.detect_format).

본문 구조(실측, HWPML 2.1):
  HWPML/BODY/SECTION/P/TEXT/{CHAR | TABLE | PICTURE | HEADER | FOOTER | ...}
  TABLE/ROW/CELL/PARALIST/P/... (셀 안이 다시 문단 구조)
출력 형식은 hwp/hwpx 추출기와 동일하게 맞춘다 — 표는 [표]…[/표], 이미지는
[이미지:img_XXX] placeholder + registry.
"""
from lxml import etree

from parsing.hwp_hwpx.common import format_table, normalize_text, register_image
from parsing.hwp_hwpx.contract import ExtractResult

# 본문에 싣지 않는 요소.
# HEADER/FOOTER는 쪽 머리말·꼬리말(문서 제목과 쪽번호가 반복될 뿐)이고,
# SECDEF/COLDEF는 편집용지 설정, AUTONUM은 자동 쪽번호 필드다. hwp/hwpx
# 추출기도 이런 페이지 장식은 본문에 넣지 않는다.
_SKIP = frozenset({"HEADER", "FOOTER", "SECDEF", "COLDEF", "AUTONUM"})

# 문서가 내부 DTD로 선언하는 엔티티. 실측된 건 nbsp 하나뿐이다.
# ⚠ resolve_entities=True로 파서에 맡기지 않는다 — 내부 서브셋에
# `<!ENTITY x SYSTEM "file:///...">` 같은 외부 엔티티가 들어오면 그대로 읽어버린다(XXE).
# 아는 엔티티만 여기서 문자로 바꾸고 나머지는 버린다.
_ENTITIES = {"nbsp": " "}


def _parser() -> etree.XMLParser:
    return etree.XMLParser(recover=True, resolve_entities=False, no_network=True, load_dtd=False)


def _text_of(node) -> str:
    """요소의 텍스트를 엔티티까지 살려서 모은다.

    resolve_entities=False라 `&nbsp;`는 자식 Entity 노드로 남는다. 단순히 node.text만
    읽으면 엔티티 뒤쪽 글자가 통째로 사라지므로(실측 CHAR 13~14개가 해당) tail까지 따라간다.
    """
    parts = [node.text or ""]
    for child in node:
        if isinstance(child, etree._Entity):
            parts.append(_ENTITIES.get(child.name.strip("&;"), ""))
        parts.append(child.tail or "")
    return "".join(parts)


def _render_table(table, ctx) -> str:
    rows = []
    for row in table.findall("ROW"):
        cells = []
        for cell in row.findall("CELL"):
            parts = [
                _render_paragraph(p, ctx)
                for para_list in cell.findall("PARALIST")
                for p in para_list.findall("P")
            ]
            cells.append("\n".join(parts).strip())
        rows.append(cells)
    return format_table(rows)


def _image_ref(picture) -> str | None:
    image = picture.find(".//IMAGE")
    return image.get("BinItem") if image is not None else None


def _render_paragraph(p, ctx) -> str:
    out = []
    for text_el in p.findall("TEXT"):
        for child in text_el:
            if isinstance(child, etree._Entity) or not isinstance(child.tag, str):
                continue
            tag = child.tag
            if tag == "CHAR":
                out.append(_text_of(child))
            elif tag == "TABLE":
                out.append("\n" + _render_table(child, ctx) + "\n")
            elif tag == "PICTURE":
                out.append(register_image(ctx, "hwpml", _image_ref(child)))
            elif tag in _SKIP:
                continue
    return "".join(out)


def _make_bindata_resolver(root):
    """BinItem 번호 -> 이미지 bytes 리졸버. 못 찾으면 None을 돌려준다.

    IMAGE@BinItem은 BINDATALIST 안 BINITEM의 1-based 순번이고, 그 BINITEM의
    BinData 속성이 BINDATA@Id를 가리킨다. 본문 이미지는 Base64로 문서 안에 들어 있다.
    """
    import base64

    items = root.findall(".//BINDATALIST/BINITEM")
    blobs = {b.get("Id"): b for b in root.findall(".//BINDATA")}

    def resolve(ref):
        if not ref:
            return None
        try:
            item = items[int(ref) - 1]
        except (ValueError, IndexError):
            return None
        blob = blobs.get(item.get("BinData"))
        if blob is None or not (blob.text or "").strip():
            return None
        if (blob.get("Encoding") or "").lower() != "base64":
            return None
        try:
            return base64.b64decode(blob.text)
        except Exception:                          # noqa: BLE001 - 깨진 이미지 하나로 문서 전체를 버리지 않는다
            return None

    return resolve


def extract_hwpml(data: bytes, describe_fn=None) -> ExtractResult:
    """HWPML 바이트에서 본문·표·이미지 위치를 뽑는다.

    describe_fn(bytes) -> str|None 을 주면 이미지 캡션을 placeholder에 인라인으로 붙인다
    (hwp/hwpx 추출기와 동일 규약).
    """
    root = etree.fromstring(data, _parser())
    if root is None:
        raise ValueError("HWPML 파싱 실패: XML 트리를 만들지 못함")
    if etree.QName(root).localname != "HWPML":
        raise ValueError(f"HWPML 문서가 아님: 루트=<{root.tag}>")

    ctx = {
        "n": 0,
        "images": {},
        "resolve": _make_bindata_resolver(root),
        "describe_fn": describe_fn,
    }

    paragraphs = [
        _render_paragraph(p, ctx)
        for section in root.findall("BODY/SECTION")
        for p in section.findall("P")
    ]
    return {
        "source_type": "hwpml",
        "text": normalize_text("\n".join(paragraphs)).strip(),
        "images": ctx["images"],
    }
