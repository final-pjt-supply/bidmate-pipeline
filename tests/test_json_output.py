# -*- coding: utf-8 -*-
from parsing.json_output import paginate


def test_short_text_is_single_page():
    assert paginate("한 줄", max_chars=10000) == ["한 줄"]


def test_empty_text_returns_no_pages():
    assert paginate("", max_chars=10000) == []


def test_splits_on_char_limit_at_line_boundary():
    # 각 줄 5자("aaaa\n" → 5자로 계산), max_chars=10이면 2줄마다 분할
    text = "aaaa\nbbbb\ncccc\ndddd"
    pages = paginate(text, max_chars=10)
    assert pages == ["aaaa\nbbbb", "cccc\ndddd"]


def test_table_not_split_across_pages():
    # 표가 경계에 걸려도 [/표]까지 한 페이지에 유지
    text = "aaaa\n[표]\nr1\nr2\nr3\n[/표]\nbbbb"
    pages = paginate(text, max_chars=10)
    # 첫 페이지는 표가 닫힐 때까지 이어짐
    assert pages[0] == "aaaa\n[표]\nr1\nr2\nr3\n[/표]"
    assert pages[1] == "bbbb"


def test_table_longer_than_limit_stays_whole():
    rows = "\n".join(f"row{i}" for i in range(50))
    text = f"[표]\n{rows}\n[/표]"
    pages = paginate(text, max_chars=10)
    assert len(pages) == 1
    assert pages[0].startswith("[표]")
    assert pages[0].endswith("[/표]")
