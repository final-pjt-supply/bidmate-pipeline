# -*- coding: utf-8 -*-
"""experiments/embedding/chunks/all_chunks.json(run_chunking.py 산출물)을 읽어
Cloudflare Workers AI(BGE-M3)로 임베딩하고 chunks/embedded_chunks.json에 저장한다.

실행(리포 루트에서):
    cd experiments/embedding && python run_embedding.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        if _k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
            os.environ.setdefault(_k, _v)

from embedding.cloudflare_embedder import embed  # noqa: E402

CHUNKS_PATH = Path(__file__).parent / "chunks" / "all_chunks.json"
OUT_PATH = Path(__file__).parent / "chunks" / "embedded_chunks.json"


def main() -> None:
    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    print(f"입력 청크 수: {len(chunks)}")

    embedded = embed(chunks)

    OUT_PATH.write_text(json.dumps(embedded, ensure_ascii=False), encoding="utf-8")
    print(f"저장 완료: {OUT_PATH}")
    print(f"벡터 차원: {len(embedded[0]['vector'])}")


if __name__ == "__main__":
    main()
