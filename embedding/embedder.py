import time

_model = None


def _get_model():
    global _model
    if _model is None:
        import torch
        from FlagEmbedding import BGEM3FlagModel

        device = "cuda" if torch.cuda.is_available() else "cpu"
        use_fp16 = device == "cuda"
        print(f"[임베딩] 디바이스: {device.upper()}")
        print("[임베딩] BGE-M3 모델 로딩 중...")
        t = time.time()
        _model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=use_fp16)
        print(f"[임베딩] 모델 로딩 완료 ({time.time() - t:.1f}초)")
    return _model


def embed(chunks: list[dict], batch_size: int = 12) -> list[dict]:
    """청크 리스트를 받아 각 청크에 'vector' 필드를 추가해 반환."""
    if not chunks:
        return []

    model = _get_model()
    texts = [c["text"] for c in chunks]

    t = time.time()
    result = model.encode(texts, batch_size=batch_size, max_length=512)
    vectors = result["dense_vecs"].tolist()
    elapsed = time.time() - t

    print(
        f"[임베딩] {len(chunks)}개 청크 완료 "
        f"({elapsed:.1f}초, 청크당 {elapsed/len(chunks)*1000:.0f}ms)"
    )

    return [{**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors)]


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from embedding.chunker import chunk

    txt_dir = Path(__file__).parent.parent / "data" / "sample" / "output" / "txt"
    txt_files = sorted(txt_dir.glob("*.txt"))
    if not txt_files:
        print("샘플 txt 파일이 없습니다.")
        sys.exit(1)

    # 첫 번째 샘플 파일로 테스트
    txt_file = txt_files[0]
    text = txt_file.read_text(encoding="utf-8")
    chunks = chunk(text, source=txt_file.name)
    print(f"\n파일: {txt_file.name} ({len(chunks)}개 청크)\n")

    embedded = embed(chunks)

    print(f"\n벡터 차원: {len(embedded[0]['vector'])}")
    print(f"첫 번째 청크 미리보기: {embedded[0]['text'][:60]}")
    print(f"벡터 앞 5개: {embedded[0]['vector'][:5]}")
