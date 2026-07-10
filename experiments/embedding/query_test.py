# -*- coding: utf-8 -*-
"""쿼리 문장을 Cloudflare BGE-M3로 임베딩해 로컬 OpenSearch knn 인덱스에서 top-5를 검색한다.

3종 knn 쿼리로 검증:
  1. 코퍼스에 확실히 있는 내용(eLoran 수신기 보정지도 지원 시스템 구축, R26BK01624671_000
     — all_chunks.json에서 "자격" 텍스트가 실제 존재함을 확인한 문서)
  2. 일반적인 탐색형 질문
  3. 코퍼스에 없을 법한 엉뚱한 질문(김치찌개 만드는 법) — 스코어가 1/2번 대비
     확 떨어지는지가 인덱스/임베딩이 의미 있게 구분하고 있다는 신호

추가로 "eLoran" 단독 키워드 BM25 검색도 확인한다 — 청킹이 표를 절단하던 시절
(v1 인덱스)에는 이 단어 자체가 인덱스에 없어 0건이었던 실패 케이스(RESULTS.md
6절 참고). 절단 대신 분할로 바꾼 뒤(v2) 이 단어가 잡히는지가 그 수정의 직접
검증이다.

실행(리포 루트에서, 사전에 index_to_opensearch.py 실행 필요):
    cd experiments/embedding && python query_test.py [--index-name NAME]
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

DEFAULT_INDEX_NAME = "embedding-experiment-chunks"
TOP_K = 5

QUERIES = {
    "1_코퍼스에_있음": "eLoran 수신기 보정지도 지원 시스템 구축 공고의 입찰 참가자격은 무엇인가?",
    "2_일반_탐색형": "소프트웨어 개발 용역 공고",
    "3_엉뚱한_질문": "김치찌개 만드는 법",
}


def get_client() -> OpenSearch:
    return OpenSearch(hosts=[{"host": "localhost", "port": 9200}], use_ssl=False, verify_certs=False)


def knn_search(client: OpenSearch, index_name: str, query_vector: list[float]) -> list[dict]:
    body = {
        "size": TOP_K,
        "query": {"knn": {"vector": {"vector": query_vector, "k": TOP_K}}},
    }
    resp = client.search(index=index_name, body=body)
    return resp["hits"]["hits"]


def bm25_search(client: OpenSearch, index_name: str, query_text: str) -> list[dict]:
    body = {"size": TOP_K, "query": {"match": {"text": query_text}}}
    resp = client.search(index=index_name, body=body)
    return resp["hits"]["hits"]


def _print_hits(hits: list[dict]) -> None:
    if not hits:
        print("  (결과 없음)")
        return
    for rank, hit in enumerate(hits, start=1):
        src = hit["_source"]
        preview = src["text"][:80].replace("\n", " ")
        print(
            f"  #{rank} score={hit['_score']:.4f} "
            f"bid_id={src['bid_id']} document_id={src['document_id']} "
            f"chunk_idx={src['chunk_idx']}"
        )
        print(f"      {preview}")


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenSearch knn/BM25 검색 테스트")
    parser.add_argument("--index-name", default=DEFAULT_INDEX_NAME, help="검색 대상 인덱스명")
    args = parser.parse_args()

    client = get_client()

    print(f"\n{'#' * 70}")
    print(f"# 대상 인덱스: {args.index_name}")
    print("#" * 70)

    print(f"\n{'=' * 70}")
    print("[BM25 단독] 쿼리: 'eLoran'")
    print("=" * 70)
    _print_hits(bm25_search(client, args.index_name, "eLoran"))

    for label, query_text in QUERIES.items():
        print(f"\n{'=' * 70}")
        print(f"[{label}] 쿼리: {query_text!r}")
        print("=" * 70)

        # embed()는 chunk 형태({'text': ...})를 받는 인터페이스이므로 쿼리 하나를 그 형태로 감싼다.
        embedded = embed([{"text": query_text}], batch_size=1)
        query_vector = embedded[0]["vector"]

        _print_hits(knn_search(client, args.index_name, query_vector))


if __name__ == "__main__":
    main()
