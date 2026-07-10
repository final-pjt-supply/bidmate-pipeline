# -*- coding: utf-8 -*-
"""3종 쿼리를 BM25만/knn만/하이브리드 3방식으로 각각 돌려 top-5를 나란히 비교한다.

대상 인덱스는 nori 분석기가 적용된 embedding-experiment-chunks-v3(BM25 신호
품질이 형태소 분석에 좌우되므로 하이브리드 비교는 반드시 이 인덱스로 한다).

실행(리포 루트에서, 사전에 index_to_opensearch.py --analyzer nori로 v3 적재
및 hybrid_search.create_pipeline 등록 필요):
    cd experiments/embedding && python run_hybrid_comparison.py [--bm25-weight W] [--knn-weight W]
"""
import argparse
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

from opensearchpy import OpenSearch  # noqa: E402

from embedding.cloudflare_embedder import embed  # noqa: E402
from experiments.embedding.hybrid_search import create_pipeline, hybrid_search  # noqa: E402
from experiments.embedding.query_test import QUERIES, bm25_search, knn_search  # noqa: E402

INDEX_NAME = "embedding-experiment-chunks-v3"
TOP_K = 5


def get_client() -> OpenSearch:
    return OpenSearch(hosts=[{"host": "localhost", "port": 9200}], use_ssl=False, verify_certs=False)


def _print_hits(hits: list[dict]) -> None:
    if not hits:
        print("    (결과 없음)")
        return
    for rank, hit in enumerate(hits, start=1):
        src = hit["_source"]
        preview = src["text"][:70].replace("\n", " ")
        print(
            f"    #{rank} score={hit['_score']:.4f} "
            f"bid_id={src['bid_id']} chunk_idx={src['chunk_idx']} — {preview}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="BM25/knn/하이브리드 3방식 비교")
    parser.add_argument("--bm25-weight", type=float, default=0.5)
    parser.add_argument("--knn-weight", type=float, default=0.5)
    args = parser.parse_args()

    client = get_client()
    create_pipeline(client, args.bm25_weight, args.knn_weight)
    print(f"하이브리드 파이프라인 등록: bm25_weight={args.bm25_weight} knn_weight={args.knn_weight}")
    print(f"대상 인덱스: {INDEX_NAME}\n")

    for label, query_text in QUERIES.items():
        print(f"{'=' * 78}")
        print(f"[{label}] 쿼리: {query_text!r}")
        print("=" * 78)

        embedded = embed([{"text": query_text}], batch_size=1)
        query_vector = embedded[0]["vector"]

        print("  --- BM25만 ---")
        _print_hits(bm25_search(client, INDEX_NAME, query_text))

        print("  --- knn만 ---")
        _print_hits(knn_search(client, INDEX_NAME, query_vector))

        print("  --- 하이브리드 ---")
        _print_hits(hybrid_search(client, INDEX_NAME, query_text, query_vector, top_k=TOP_K))

        print()


if __name__ == "__main__":
    main()
