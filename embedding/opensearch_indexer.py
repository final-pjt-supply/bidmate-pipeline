import os
from pathlib import Path

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

_ENV_KEYS = ("OPENSEARCH_HOST", "OPENSEARCH_PORT", "OPENSEARCH_USER", "OPENSEARCH_PASSWORD")

INDEX_NAME = "bid_chunks"
VECTOR_DIM = 1024  # BGE-M3 dense_vecs 차원


def _load_config() -> dict:
    env_path = Path(__file__).parent.parent / ".env"
    config = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            for key in _ENV_KEYS:
                if line.startswith(f"{key}="):
                    config[key] = line.split("=", 1)[1].strip()
    for key in _ENV_KEYS:
        if key not in config:
            config[key] = os.environ.get(key, "")
    return config


def _get_client() -> OpenSearch:
    """local dev(docker-compose.local.yml)는 보안 플러그인이 꺼져 있어 평문 HTTP·인증 없이 접속.
    운영 방향(docker-compose.yml 단독)으로 띄운 경우엔 OPENSEARCH_USER가 채워져 있어야 HTTPS로 접속."""
    config = _load_config()
    is_secured = bool(config["OPENSEARCH_USER"])
    return OpenSearch(
        hosts=[{
            "host": config["OPENSEARCH_HOST"],
            "port": int(config["OPENSEARCH_PORT"]),
            "scheme": "https" if is_secured else "http",
        }],
        http_auth=(config["OPENSEARCH_USER"], config["OPENSEARCH_PASSWORD"]) if is_secured else None,
        use_ssl=is_secured,
        verify_certs=False,
        ssl_show_warn=False,
    )


def ensure_index() -> None:
    client = _get_client()
    if client.indices.exists(index=INDEX_NAME):
        return
    client.indices.create(
        index=INDEX_NAME,
        body={
            "settings": {"index.knn": True},
            "mappings": {
                "properties": {
                    "bid_ntce_no": {"type": "keyword"},
                    "chunk_idx": {"type": "integer"},
                    "type": {"type": "keyword"},
                    "source": {"type": "keyword"},
                    "text": {"type": "text"},
                    "vector": {
                        "type": "knn_vector",
                        "dimension": VECTOR_DIM,
                        "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
                    },
                }
            },
        },
    )


def index_chunks(bid_ntce_no: str, embedded_chunks: list[dict]) -> None:
    """embedder.embed()가 반환한(각 dict에 'vector' 포함) 청크 리스트를 색인."""
    ensure_index()
    client = _get_client()
    actions = [
        {
            "_index": INDEX_NAME,
            "_id": f"{bid_ntce_no}::{c['chunk_idx']}",
            "_source": {
                "bid_ntce_no": bid_ntce_no,
                "chunk_idx": c["chunk_idx"],
                "type": c["type"],
                "source": c["source"],
                "text": c["text"],
                "vector": c["vector"],
            },
        }
        for c in embedded_chunks
    ]
    bulk(client, actions)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from embedding.embedder import embed
    from parsing.chunker import chunk

    txt_dir = Path(__file__).parent.parent / "data" / "sample" / "output" / "txt"
    for txt_file in sorted(txt_dir.glob("*.txt")):
        text = txt_file.read_text(encoding="utf-8")
        chunks = chunk(text, source=txt_file.name)
        embedded = embed(chunks)
        bid_ntce_no = f"SAMPLE-{txt_file.stem}"
        index_chunks(bid_ntce_no, embedded)
        print(f"[색인] {txt_file.name} -> {len(embedded)}개 청크, bid_ntce_no={bid_ntce_no}")
