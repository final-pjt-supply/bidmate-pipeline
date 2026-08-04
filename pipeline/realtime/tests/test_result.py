# -*- coding: utf-8 -*-
"""common/result.py 단위테스트 — 출력 스키마 + partial 표시.

partial은 "본문 일부만 회수된 문서"라는 뜻이다. 소비처(embed 등)는 pages만
읽으므로 동작에는 영향이 없지만, 표시가 없으면 잘린 공고가 완전한 공고와
구분되지 않은 채 인덱스에 들어간다. 그래서 '참일 때만 키가 붙는다'를 고정한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.result import build_result  # noqa: E402

PAGES = [{"page": 1, "text": "공고문 본문입니다. 충분히 긴 텍스트."}]


def test_기본_스키마():
    r = build_result("R26BK00000001_000", "doc01", PAGES)
    assert r == {
        "bid_id": "R26BK00000001_000",
        "document_id": "doc01",
        "pages": [{"page": 1, "text": PAGES[0]["text"]}],
    }


def test_partial_기본값이면_키가_안_붙는다():
    assert "partial" not in build_result("b", "doc01", PAGES)


def test_partial_참이면_키가_붙는다():
    assert build_result("b", "doc01", PAGES, partial=True)["partial"] is True


def test_partial이어도_pages_스키마는_그대로다():
    """소비처는 pages만 읽으므로 partial이 붙어도 기존 계약이 깨지면 안 된다."""
    r = build_result("b", "doc01", PAGES, partial=True)
    assert r["pages"] == [{"page": 1, "text": PAGES[0]["text"]}]


def test_스키마에_없는_키는_걸러진다():
    pages = [{"page": 1, "text": PAGES[0]["text"], "is_scanned": True}]
    assert build_result("b", "doc01", pages)["pages"][0] == {
        "page": 1, "text": PAGES[0]["text"],
    }
