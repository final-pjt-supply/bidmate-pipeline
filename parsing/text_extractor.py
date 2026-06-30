import fitz  # PyMuPDF
from pathlib import Path
from parsing.text_layer_detector import detect_text_layers


def extract_text(pdf_path: str, text_layers: dict[int, bool]) -> dict[int, str]:
    doc = fitz.open(pdf_path)
    result = {}
    for page_num, page in enumerate(doc, start=1):
        if text_layers[page_num]:
            result[page_num] = page.get_text().strip()
        else:
            result[page_num] = _ocr_page(page)
    doc.close()
    return result


def _ocr_page(page) -> str:
    # TODO: PaddleOCR 연동 예정
    return ""


if __name__ == "__main__":
    sample_dir = Path(__file__).parent.parent / "data" / "sample"
    output_dir = sample_dir / "output"
    output_dir.mkdir(exist_ok=True)

    for pdf_file in sample_dir.glob("*.pdf"):
        text_layers = detect_text_layers(str(pdf_file))
        result = extract_text(str(pdf_file), text_layers)

        output_path = output_dir / f"{pdf_file.stem}.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            for page_num, text in result.items():
                f.write(f"\n{'='*60}\n")
                f.write(f"{page_num}페이지\n")
                f.write(f"{'='*60}\n")
                f.write(text if text else "(스캔 페이지 - OCR 미구현)")
                f.write("\n")

        print(f"저장 완료: {output_path}")
