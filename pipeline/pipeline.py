import json
import sys
import time
from pathlib import Path

import fitz

from pipeline.qualification_extractor import extract_qualifications
from parsing.pdf.pdf_image_describer import describe_image
from parsing.pdf.pdf_extractor import extract_text


def run(pdf_path: str) -> dict:
    path = Path(pdf_path)
    txt_dir = path.parent / "output" / "txt"
    json_dir = path.parent / "output" / "json"
    txt_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    txt_path = txt_dir / (path.stem + ".txt")

    # 1단계: 텍스트 추출
    t0 = time.time()
    result = extract_text(pdf_path)
    descriptions = _describe_all(pdf_path, result["images"])
    final_pages = _replace_placeholders(result["pages"], descriptions)
    _save(final_pages, str(txt_path))
    t1 = time.time()
    print(f"[1] 텍스트 추출 완료 ({t1 - t0:.1f}초) → {txt_path.name}")

    # 2단계: 자격요건 추출
    full_text = txt_path.read_text(encoding="utf-8")
    qualifications = extract_qualifications(full_text)
    t2 = time.time()
    print(f"[2] 자격요건 추출 완료 ({t2 - t1:.1f}초)")

    json_path = json_dir / (path.stem + ".json")
    json_path.write_text(json.dumps(qualifications, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[저장] {json_path.name}")

    return qualifications


def _describe_all(pdf_path: str, registry: dict) -> dict[str, str]:
    if not registry:
        return {}
    doc = fitz.open(pdf_path)
    descriptions = {}
    for img_id, meta in registry.items():
        img_data = doc.extract_image(meta["xref"])
        descriptions[img_id] = describe_image(img_data["image"], img_id, meta["page"])
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
    with open(output_path, "w", encoding="utf-8") as f:
        for page_num, text in pages.items():
            f.write(f"\n{'='*60}\n{page_num}페이지\n{'='*60}\n")
            f.write(text + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sample_dir = Path(__file__).parent.parent / "data" / "sample"
        for pdf_file in sample_dir.glob("*.pdf"):
            print(f"\n=== {pdf_file.name} ===")
            run(str(pdf_file))
    else:
        run(sys.argv[1])
