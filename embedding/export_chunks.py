"""청킹 결과를 JSON으로 저장 (코랩 임베딩 테스트용)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from embedding.chunker import chunk

txt_dir = Path(__file__).parent.parent / "data" / "sample" / "output" / "txt"
out_path = Path(__file__).parent / "chunks.json"

all_chunks = []
for txt_file in sorted(txt_dir.glob("*.txt")):
    text = txt_file.read_text(encoding="utf-8")
    chunks = chunk(text, source=txt_file.name)
    all_chunks.extend(chunks)
    print(f"{txt_file.name}: {len(chunks)}개 청크")

out_path.write_text(json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n총 {len(all_chunks)}개 청크 → {out_path}")
