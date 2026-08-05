# -*- coding: utf-8 -*-
"""HWPML 추출기와 라우팅 테스트.

픽스처는 실제 나라장터 첨부(HWPML 2.1)에서 확인된 구조를 그대로 축소한 것이다:
  HWPML/BODY/SECTION/P/TEXT/{CHAR | TABLE | PICTURE | HEADER | FOOTER}
  TABLE/ROW/CELL/PARALIST/P/TEXT/CHAR
"""
import base64

import pytest

from parsing.hwp_hwpx.hwpml_extractor import extract_hwpml

# 1x1 투명 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

DOC = ("""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE HWPML [
	<!ENTITY nbsp	"&#160;">
]>
<HWPML Version="2.1">
<HEAD SecCnt="1"><MAPPINGTABLE><BINDATALIST Count="2">
<BINITEM Type="Embedding" BinData="7" Format="bmp"/>
<BINITEM Type="Embedding" BinData="9" Format="png"/>
</BINDATALIST></MAPPINGTABLE></HEAD>
<BODY><SECTION Id="0">
<P><TEXT>
<HEADER><PARALIST><P><TEXT><CHAR>머리말은버린다</CHAR></TEXT></P></PARALIST></HEADER>
<CHAR>제1조(총칙)&nbsp;신의성실</CHAR>
<PICTURE><SHAPEOBJECT/><IMAGE Effect="RealPic" BinItem="2"/></PICTURE>
</TEXT></P>
<P><TEXT>
<TABLE RowCount="2" ColCount="2">
<ROW><CELL><PARALIST><P><TEXT><CHAR>구분</CHAR></TEXT></P></PARALIST></CELL>
<CELL><PARALIST><P><TEXT><CHAR>내용</CHAR></TEXT></P></PARALIST></CELL></ROW>
<ROW><CELL><PARALIST><P><TEXT><CHAR>기간</CHAR></TEXT></P></PARALIST></CELL>
<CELL><PARALIST><P><TEXT><CHAR>30일</CHAR></TEXT></P></PARALIST></CELL></ROW>
</TABLE>
</TEXT></P>
<P><TEXT><FOOTER><PARALIST><P><TEXT><CHAR>꼬리말도버린다</CHAR></TEXT></P></PARALIST></FOOTER></TEXT></P>
</SECTION></BODY>
<TAIL><BINDATASTORAGE>
<BINDATA Id="9" Size="70" Encoding="Base64">"""
+ base64.b64encode(PNG).decode()
+ """</BINDATA>
</BINDATASTORAGE></TAIL>
</HWPML>
""").encode("utf-8")


def test_본문_텍스트를_문서_순서대로_뽑는다():
    out = extract_hwpml(DOC)
    assert out["source_type"] == "hwpml"
    assert "제1조(총칙)" in out["text"]
    assert out["text"].index("제1조") < out["text"].index("[표]")


def test_엔티티_뒤_글자가_잘리지_않는다():
    """&nbsp;는 자식 노드로 남아서, node.text만 읽으면 뒤쪽이 통째로 사라진다."""
    assert "신의성실" in extract_hwpml(DOC)["text"]


def test_표는_공통_마커_형식으로_렌더된다():
    text = extract_hwpml(DOC)["text"]
    assert "[표]\n구분 | 내용\n기간 | 30일\n[/표]" in text


def test_머리말_꼬리말은_본문에_안_들어간다():
    """쪽 머리말·꼬리말은 페이지 장식이라 hwp/hwpx 추출기도 본문에 넣지 않는다."""
    text = extract_hwpml(DOC)["text"]
    assert "머리말은버린다" not in text
    assert "꼬리말도버린다" not in text


def test_이미지는_placeholder와_registry로_남는다():
    out = extract_hwpml(DOC)
    assert "[이미지:img_001]" in out["text"]
    assert out["images"] == {"img_001": {"source_type": "hwpml", "ref": "2"}}


def test_describe_fn이_있으면_캡션이_인라인된다():
    seen = []

    def describe(image_bytes):
        seen.append(image_bytes)
        return "1x1 투명 이미지"

    out = extract_hwpml(DOC, describe_fn=describe)
    assert "[이미지:img_001: 1x1 투명 이미지]" in out["text"]
    # BinItem="2" -> BINITEM 2번째 -> BinData="9" -> BINDATA@Id="9" 를 base64 디코드
    assert seen == [PNG]


def test_이미지를_못_찾아도_본문은_살린다():
    broken = DOC.replace(b'BinItem="2"', b'BinItem="99"')
    out = extract_hwpml(broken, describe_fn=lambda b: "설명")
    assert "[이미지:img_001]" in out["text"]      # 캡션 없이 placeholder만
    assert "제1조(총칙)" in out["text"]


def test_HWPML이_아니면_거부한다():
    with pytest.raises(ValueError):
        extract_hwpml(b"<?xml version='1.0'?><NotHwpml/>")


# ---------------------------------------------------------------- 라우팅
# realtime 쪽 라우터 테스트는 pipeline/realtime/tests/test_router.py에 있다
# (그 패키지는 src/를 sys.path에 넣어야 import된다). 여기서는 백필만 본다.


def test_backfill_router가_hwpml을_판별한다():
    """두 라우터는 의도적으로 중복 유지 중이라 양쪽 다 검증한다."""
    from backfill_lambda import router as bf
    assert bf._detect_format(DOC, "x.hwp") == "hwpml"
    assert bf._detect_format(b"\xef\xbb\xbf" + DOC, "x.hwp") == "hwpml"
    assert bf._detect_format(b"%PDF-1.7", "x.hwp") == "pdf"
    assert bf._detect_format(b"<?xml version='1.0'?><rss/>", "x.hwp") == "hwp"


def test_backfill_router가_hwpml_추출기로_보낸다():
    from backfill_lambda import router as bf
    out = bf.extract_document(DOC, "x.hwp")
    assert out["source_type"] == "hwpml"
    assert "제1조(총칙)" in out["text"]
