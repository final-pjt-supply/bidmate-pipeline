import json
import tempfile
from pathlib import Path

import opendataloader_pdf


def _render_table(table: dict) -> str:
    lines = []
    for row in table.get("rows", []):
        cells = []
        for cell in row.get("cells", []):
            texts = [k.get("content", "") for k in cell.get("kids", []) if k.get("content")]
            cells.append("\n".join(texts).strip())
        lines.append(" | ".join(cells))
    return "[표]\n" + "\n".join(lines) + "\n[/표]"


def _walk(kids: list, pages: dict[int, list[str]]) -> None:
    for node in kids:
        page_num = node.get("page number")

        if node.get("type") == "table":
            if page_num is not None:
                pages.setdefault(page_num, []).append(_render_table(node))
            continue

        content = node.get("content")
        if content and content.strip() and page_num is not None:
            pages.setdefault(page_num, []).append(content.strip())

        if node.get("kids"):
            _walk(node["kids"], pages)
        if node.get("list items"):
            _walk(node["list items"], pages)


def extract_text(pdf_path: str) -> dict:
    """text_extractor.extract_text()와 동일한 반환 구조: {"pages": {page_num: str}, "images": {...}}.

    이미지 레지스트리는 이번 범위에서 빈 딕셔너리로 반환한다. OpenDataLoader는
    PyMuPDF의 xref 개념이 없어 pipeline._describe_all()과 바로 호환되지 않는다.
    """
    # FileNotFoundError(java 없음), subprocess.CalledProcessError 등은 호출부에서 처리할 것
    with tempfile.TemporaryDirectory() as tmp_dir:
        opendataloader_pdf.convert(
            input_path=pdf_path,
            output_dir=tmp_dir,
            format="json",
            quiet=True,
        )
        json_path = Path(tmp_dir) / (Path(pdf_path).stem + ".json")
        with open(json_path, encoding="utf-8") as f:
            doc = json.load(f)

    pages: dict[int, list[str]] = {}
    _walk(doc.get("kids", []), pages)

    return {
        "pages": {p: "\n".join(lines) for p, lines in sorted(pages.items())},
        "images": {},
    }


if __name__ == "__main__":
    import sys

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not pdf_path:
        print("사용법: python -m parsing.opendataloader_extractor <PDF경로>")
        sys.exit(1)

    result = extract_text(pdf_path)
    for page_num, text in result["pages"].items():
        print(f"\n{'='*60}\n{page_num}페이지\n{'='*60}")
        print(text)
