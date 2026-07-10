# -*- coding: utf-8 -*-
"""BM25(nori)+knn 하이브리드 검색 — OpenSearch search pipeline의 normalization
processor로 두 스코어를 정규화한 뒤 가중 평균으로 결합한다.

BM25와 knn은 스코어 스케일이 전혀 다르다(BM25는 문서 길이·용어 빈도에 따라
0~수십, knn은 cosine 기반이라 보통 0~1) — 정규화 없이 그냥 더하면 한쪽이
항상 압도한다. min_max 정규화로 이번 검색 결과 안에서 각 방식의 점수를
0~1로 맞춘 뒤 산술평균(가중치 조정 가능)으로 결합한다.

nori 형태소 분석기가 적용된 인덱스(embedding-experiment-chunks-v3)를
전제로 한다 — BM25 쪽 신호 품질이 형태소 분석 여부에 좌우되므로, 하이브리드
검색은 반드시 nori 인덱스에서 돌려야 의미가 있다.

사전 준비: OpenSearch에 analysis-nori, opensearch-neural-search 플러그인
설치 필요(db/docker-compose.yml 상단 opensearch 서비스 주석 참고).
"""
from opensearchpy import OpenSearch

PIPELINE_ID = "hybrid-search-pipeline"


def create_pipeline(client: OpenSearch, bm25_weight: float, knn_weight: float, pipeline_id: str = PIPELINE_ID) -> None:
    """search pipeline을 (재)생성한다 — 같은 id로 다시 만들면 기존 걸 덮어쓴다.
    가중치 순서는 하이브리드 쿼리의 queries 배열 순서(0=BM25/match, 1=knn)와
    반드시 일치해야 한다(hybrid_search의 queries 배열 순서 참고)."""
    body = {
        "description": "BM25+knn 정규화 후 가중 결합",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        "parameters": {"weights": [bm25_weight, knn_weight]},
                    },
                }
            }
        ],
    }
    client.transport.perform_request("PUT", f"/_search/pipeline/{pipeline_id}", body=body)


def hybrid_search(
    client: OpenSearch,
    index_name: str,
    query_text: str,
    query_vector: list[float],
    top_k: int = 5,
    pipeline_id: str = PIPELINE_ID,
) -> list[dict]:
    body = {
        "size": top_k,
        "query": {
            "hybrid": {
                "queries": [
                    {"match": {"text": query_text}},
                    {"knn": {"vector": {"vector": query_vector, "k": top_k}}},
                ]
            }
        },
    }
    resp = client.search(index=index_name, body=body, params={"search_pipeline": pipeline_id})
    return resp["hits"]["hits"]
