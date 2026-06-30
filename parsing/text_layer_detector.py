import fitz  # PyMuPDF
from pathlib import Path

def detect_text_layers(pdf_path: str) -> dict[int, bool]:
    doc = fitz.open(pdf_path)
    result = {}
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        result[page_num] = len(text) > 0
    doc.close()
    return result


if __name__ == "__main__":
    sample_dir = Path(__file__).parent.parent / "data" / "sample"
    for pdf_file in sample_dir.glob("*.pdf"):
        print(f"\n[{pdf_file.name}]")
        result = detect_text_layers(str(pdf_file))
        for page, has_text in result.items():
            status = "텍스트" if has_text else "스캔(OCR필요)"
            print(f"  {page}페이지: {status}")

