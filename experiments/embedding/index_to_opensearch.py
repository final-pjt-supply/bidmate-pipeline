# -*- coding: utf-8 -*-
"""chunks/embedded_chunks.json(run_embedding.py 산출물)을 로컬 OpenSearch(knn 인덱스)에 적재한다.

로컬 전용 — db/docker-compose.yml의 opensearch 서비스(인증 없음, localhost:9200)를
대상으로 한다. .env의 OPENSEARCH_*(AWS 관리형 bidmate-search 클러스터, 다른 팀
소유)와는 무관하다.

실행(리포 루트에서, 사전에 db/에서 `docker compose up -d opensearch` 필요):
    cd experiments/embedding && python index_to_opensearch.py [--index-name NAME]

--index-name을 지정하지 않으면 기본 인덱스(embedding-experiment-chunks)를
지우고 다시 만든다. 이전 결과와 나란히 비교하려면(예: 청킹 로직 변경 전/후)
버전이 다른 이름(예: embedding-experiment-chunks-v2)을 지정해 기존 인덱스는
그대로 두고 새 인덱스를 만든다.
"""
import argparse
import json
from pathlib import Path

from opensearchpy import OpenSearch, helpers

HOST = "localhost"
PORT = 9200
DEFAULT_INDEX_NAME = "embedding-experiment-chunks"
VECTOR_DIM = 1024

CHUNKS_PATH = Path(__file__).parent / "chunks" / "embedded_chunks.json"

_INDEX_BODY = {
    "settings": {
        "index": {
            "knn": True,
            "number_of_shards": 1,
            "number_of_replicas": 0,  # 로컬 단일 노드 실험 — 레플리카 불필요(status: yellow 방지)
        }
    },
    "mappings": {
        "properties": {
            "vector": {
                "type": "knn_vector",
                "dimension": VECTOR_DIM,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                },
            },
            "text": {"type": "text"},
            "type": {"type": "keyword"},
            "source": {"type": "keyword"},
            "bid_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "chunk_idx": {"type": "integer"},
        }
    },
}


def get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": HOST, "port": PORT}],
        http_compress=True,
        use_ssl=False,
        verify_certs=False,
    )


def create_index(client: OpenSearch, index_name: str) -> None:
    if client.indices.exists(index=index_name):
        print(f"기존 인덱스 삭제: {index_name}")
        client.indices.delete(index=index_name)
    client.indices.create(index=index_name, body=_INDEX_BODY)
    print(f"인덱스 생성 완료: {index_name} (dimension={VECTOR_DIM})")


def bulk_load(client: OpenSearch, chunks: list[dict], index_name: str) -> None:
    def _actions():
        for i, c in enumerate(chunks):
            yield {"_index": index_name, "_id": i, "_source": c}

    success, errors = helpers.bulk(client, _actions(), chunk_size=500, raise_on_error=False)
    print(f"적재 완료: 성공 {success}건, 실패 {len(errors)}건")
    if errors:
        print("실패 샘플:", errors[:3])


def main() -> None:
    parser = argparse.ArgumentParser(description="임베딩 청크를 로컬 OpenSearch에 적재")
    parser.add_argument("--index-name", default=DEFAULT_INDEX_NAME, help="적재할 인덱스명(기존 결과 보존하려면 버전 구분된 이름 사용)")
    args = parser.parse_args()

    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    print(f"입력 청크 수: {len(chunks)}")

    client = get_client()
    create_index(client, args.index_name)
    bulk_load(client, chunks, args.index_name)

    client.indices.refresh(index=args.index_name)
    count = client.count(index=args.index_name)["count"]
    print(f"인덱스 내 문서 수(refresh 후): {count}")


if __name__ == "__main__":
    main()
