# -*- coding: utf-8 -*-
"""experiments/embedding/data/의 extracted JSON 전체를 embedding/chunker.py로 청킹하고
결과를 experiments/embedding/chunks/all_chunks.json에 저장 + 통계를 출력한다.
드랍된(언어적 내용 없는 잔재) 청크 원문은 experiments/embedding/chunks/dropped.json에
따로 저장한다(눈검사용).

data/의 각 문서는 pages 리스트(문서 원 페이지 단위)로 나뉘어 있는데,
chunker.chunk()는 페이지 경계를 모르는 통짜 텍스트 하나를 받는 함수라
페이지 texts를 그대로 이어붙여서 넘긴다(페이지 구분자를 새로 만들지 않음 —
chunker의 섹션 인식은 원래 페이지 경계와 무관하게 "1. 2. 3." 같은 조항
번호로 하기 때문에 이어붙이기만 해도 된다).

실행(리포 루트에서):
    python experiments/embedding/run_chunking.py
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from embedding.chunker import chunk  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"
OUT_DIR = Path(__file__).parent / "chunks"
OUT_PATH = OUT_DIR / "all_chunks.json"
DROPPED_PATH = OUT_DIR / "dropped.json"


def load_document_text(path: Path) -> tuple[str, str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    bid_id = data.get("bid_id", "")
    document_id = data.get("document_id", "")
    text = "\n\n".join(p["text"] for p in data.get("pages", []))
    return text, bid_id, document_id


def main() -> None:
    files = sorted(DATA_DIR.rglob("*.json"))
    print(f"입력 문서 수: {len(files)}")

    all_chunks = []
    per_doc_counts = []
    all_dropped = []

    for path in files:
        text, bid_id, document_id = load_document_text(path)
        source = f"{bid_id}_{document_id}"
        dropped_out: list[str] = []
        doc_chunks = chunk(text, source=source, dropped_out=dropped_out)
        per_doc_counts.append(len(doc_chunks))
        for c in doc_chunks:
            c["bid_id"] = bid_id
            c["document_id"] = document_id
        all_chunks.extend(doc_chunks)
        for d in dropped_out:
            all_dropped.append({"source": source, "text": d})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    DROPPED_PATH.write_text(json.dumps(all_dropped, ensure_ascii=False, indent=2), encoding="utf-8")

    chunk_lengths = [len(c["text"]) for c in all_chunks]
    truncated = [c for c in all_chunks if c.get("truncated")]

    print(f"\n총 청크 수: {len(all_chunks)}")
    print(f"저장 위치: {OUT_PATH}")
    print(f"드랍된 청크: {len(all_dropped)}건 -> {DROPPED_PATH}")
    print()
    print("=== 문서당 청크 수 ===")
    print(f"  중앙값: {statistics.median(per_doc_counts)}")
    print(f"  최대: {max(per_doc_counts)}")
    print(f"  최소: {min(per_doc_counts)}")
    print()
    print("=== 청크 길이(문자 수) ===")
    print(f"  중앙값: {statistics.median(chunk_lengths):.0f}")
    print(f"  최대: {max(chunk_lengths)}")
    print(f"  최소: {min(chunk_lengths)}")
    print()
    print(f"=== truncated 청크: {len(truncated)}건 ===")
    for c in truncated:
        print(
            f"  {c['bid_id']}_{c['document_id']} chunk_idx={c['chunk_idx']} "
            f"original={c['original_chars']}자 kept={c['kept_chars']}자"
        )


if __name__ == "__main__":
    main()
