# -*- coding: utf-8 -*-
"""추출 결과를 페이지 분할 JSON으로 변환하는 출력 포맷 로직.

- paginate: 본문을 10000자 단위로 분할(줄 경계, [표]…[/표]는 미분할).
"""
from parsing.contract import TABLE_OPEN, TABLE_CLOSE, ExtractResult


def paginate(text: str, max_chars: int = 10000) -> list[str]:
    """본문을 max_chars 글자 단위로 페이지 분할한다.

    - 줄 경계에서만 자른다(줄 중간 분할 없음).
    - [표]가 열린 동안에는 [/표]가 닫힐 때까지 자르지 않는다(표 미분할).
    - 표 하나가 max_chars를 넘으면 해당 페이지는 초과하되 표는 온전히 유지.
    - 빈/공백 본문은 빈 리스트를 반환.
    """
    if not text.strip():
        return []

    pages: list[str] = []
    buf: list[str] = []
    buf_len = 0
    in_table = False

    for line in text.split("\n"):
        buf.append(line)
        buf_len += len(line) + 1  # 줄 사이 "\n" 1자 포함(근사)
        if TABLE_OPEN in line:
            in_table = True
        if TABLE_CLOSE in line:
            in_table = False
        if buf_len >= max_chars and not in_table:
            pages.append("\n".join(buf))
            buf = []
            buf_len = 0

    if buf:
        pages.append("\n".join(buf))
    return pages


def to_json_doc(
    result: ExtractResult,
    bid_ntce_no: str,
    document_id: str,
    max_chars: int = 10000,
) -> dict:
    """추출 결과를 페이지 분할 JSON 스키마 dict로 변환한다.

    {"bid_ntce_no", "document_id", "pages": [{"page"(1부터), "text"}]}
    """
    pages = paginate(result["text"], max_chars)
    return {
        "bid_ntce_no": bid_ntce_no,
        "document_id": document_id,
        "pages": [
            {"page": i, "text": page}
            for i, page in enumerate(pages, start=1)
        ],
    }
