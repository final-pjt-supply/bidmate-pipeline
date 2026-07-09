# -*- coding: utf-8 -*-
"""쿼리 문장을 Cloudflare BGE-M3로 임베딩해 로컬 OpenSearch knn 인덱스에서 top-5를 검색한다.

3종 쿼리로 검증:
  1. 코퍼스에 확실히 있는 내용(eLoran 수신기 보정지도 지원 시스템 구축, R26BK01624671_000
     — all_chunks.json에서 "자격" 텍스트가 실제 존재함을 확인한 문서)
  2. 일반적인 탐색형 질문
  3. 코퍼스에 없을 법한 엉뚱한 질문(김치찌개 만드는 법) — 스코어가 1/2번 대비
     확 떨어지는지가 인덱스/임베딩이 의미 있게 구분하고 있다는 신호

실행(리포 루트에서, 사전에 index_to_opensearch.py 실행 필요):
    cd experiments/embedding && python query_test.py
"""
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

INDEX_NAME = "embedding-experiment-chunks"
TOP_K = 5

QUERIES = {
    "1_코퍼스에_있음": "eLoran 수신기 보정지도 지원 시스템 구축 공고의 입찰 참가자격은 무엇인가?",
    "2_일반_탐색형": "소프트웨어 개발 용역 공고",
    "3_엉뚱한_질문": "김치찌개 만드는 법",
}


def get_client() -> OpenSearch:
    return OpenSearch(hosts=[{"host": "localhost", "port": 9200}], use_ssl=False, verify_certs=False)


def search(client: OpenSearch, query_vector: list[float]) -> list[dict]:
    body = {
        "size": TOP_K,
        "query": {"knn": {"vector": {"vector": query_vector, "k": TOP_K}}},
    }
    resp = client.search(index=INDEX_NAME, body=body)
    return resp["hits"]["hits"]


def main() -> None:
    client = get_client()

    for label, query_text in QUERIES.items():
        print(f"\n{'=' * 70}")
        print(f"[{label}] 쿼리: {query_text!r}")
        print("=" * 70)

        # embed()는 chunk 형태({'text': ...})를 받는 인터페이스이므로 쿼리 하나를 그 형태로 감싼다.
        embedded = embed([{"text": query_text}], batch_size=1)
        query_vector = embedded[0]["vector"]

        hits = search(client, query_vector)
        for rank, hit in enumerate(hits, start=1):
            src = hit["_source"]
            preview = src["text"][:80].replace("\n", " ")
            print(
                f"  #{rank} score={hit['_score']:.4f} "
                f"bid_id={src['bid_id']} document_id={src['document_id']} "
                f"chunk_idx={src['chunk_idx']}"
            )
            print(f"      {preview}")


if __name__ == "__main__":
    main()
