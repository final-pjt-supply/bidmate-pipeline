# -*- coding: utf-8 -*-
"""tests/samples/의 샘플 파일을 추출기에 직접 넣어 결과를 확인 (AWS 비용 0원)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))  # repo 루트 (parsing 패키지용)

from extractors import hwp, hwpx, pdf  # noqa: E402

_EXTRACTORS = {".pdf": pdf.extract, ".hwp": hwp.extract, ".hwpx": hwpx.extract}


def run(sample_path: str) -> None:
    path = Path(sample_path)
    extract = _EXTRACTORS[path.suffix.lower()]
    result = extract(path.read_bytes())
    print(result)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        samples_dir = Path(__file__).parent.parent / "tests" / "samples"
        for sample in sorted(samples_dir.iterdir()):
            if sample.suffix.lower() in _EXTRACTORS:
                print(f"\n=== {sample.name} ===")
                run(str(sample))
    else:
        run(sys.argv[1])
