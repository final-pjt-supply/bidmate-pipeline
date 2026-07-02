import fitz
from pathlib import Path

from parsing.text_extractor import extract_text
from parsing.image_describer import describe_image


def run(pdf_path: str, output_path: str) -> None:
    result = extract_text(pdf_path)
    pages: dict[int, str] = result["pages"]
    registry: dict[str, dict] = result["images"]

    descriptions = _describe_all(pdf_path, registry)
    final_pages = _replace_placeholders(pages, descriptions)
    _save(final_pages, output_path)

    print(f"저장 완료: {output_path}")
    print(f"  이미지 수: {len(registry)}개")


def _describe_all(pdf_path: str, registry: dict) -> dict[str, str]:
    if not registry:
        return {}

    doc = fitz.open(pdf_path)
    descriptions = {}
    for img_id, meta in registry.items():
        img_data = doc.extract_image(meta["xref"])
        desc = describe_image(img_data["image"], img_id, meta["page"])
        descriptions[img_id] = desc
        print(f"  {img_id} 처리 완료 (페이지 {meta['page']})")
    doc.close()
    return descriptions


def _replace_placeholders(pages: dict, descriptions: dict) -> dict[int, str]:
    result = {}
    for page_num, text in pages.items():
        for img_id, desc in descriptions.items():
            text = text.replace(f"[이미지:{img_id}]", f"[이미지 설명: {desc}]")
        result[page_num] = text
    return result


def _save(pages: dict, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for page_num, text in pages.items():
            f.write(f"\n{'='*60}\n{page_num}페이지\n{'='*60}\n")
            f.write(text + "\n")


if __name__ == "__main__":
    sample_dir = Path(__file__).parent.parent / "data" / "sample"
    output_dir = sample_dir / "output"

    for pdf_file in sample_dir.glob("*.pdf"):
        run(
            pdf_path=str(pdf_file),
            output_path=str(output_dir / f"{pdf_file.stem}.txt"),
        )
