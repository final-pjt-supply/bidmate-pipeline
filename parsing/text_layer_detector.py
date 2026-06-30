import fitz  # PyMuPDF

MIN_TEXT_LENGTH = 50


def detect_text_layers(pdf_path: str) -> dict[int, bool]:
    doc = fitz.open(pdf_path)
    result = {}
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        result[page_num] = len(text) >= MIN_TEXT_LENGTH
    doc.close()
    return result
