# -*- coding: utf-8 -*-
"""tests/samples/의 샘플을 추출기에 넣어 결과를 JSON으로 저장 (AWS 비용 0원)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from extractors import hwp, hwpx, pdf  # noqa: E402

_EXTRACTORS = {".pdf": pdf.extract, ".hwp": hwp.extract, ".hwpx": hwpx.extract}

OUT_DIR = Path(__file__).parent.parent / "tests" / "output"


def run(sample_path: str) -> None:
    path = Path(sample_path)
    extract = _EXTRACTORS[path.suffix.lower()]
    result = extract(path.read_bytes())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # file_id는 실제 파이프라인에선 규약대로 생성되지만, 로컬 확인용이라 파일명 stem 사용
    out_path = OUT_DIR / f"{path.stem}.extracted.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),  # 한글 안 깨지게
        encoding="utf-8",
    )
    # 콘솔엔 요약만
    pages = result.get("pages", [])
    total = sum(len(p.get("text", "")) for p in pages)
    print(f"  → {out_path.name}  (pages={len(pages)}, chars={total})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        samples_dir = Path(__file__).parent.parent / "tests" / "samples"
        for sample in sorted(samples_dir.iterdir()):
            if sample.suffix.lower() in _EXTRACTORS:
                print(f"=== {sample.name} ===")
                run(str(sample))
    else:
        run(sys.argv[1])
