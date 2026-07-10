# -*- coding: utf-8 -*-
"""OpenSearch 인덱스 매핑(#64 최종 확정, docs/indexing-design-notes.md 6-3 참고).

index_opensearch.py(인덱스 존재 확인용)와 scripts/setup-opensearch-index.py
(인덱스 생성용) 양쪽에서 이 상수를 그대로 재사용한다 — 매핑 정의가 두 곳에서
갈라지면 "코드는 A인데 실제 인덱스는 B"인 상태가 조용히 생길 수 있어서다.

text/vector 설정(nori 분석기, knn 파라미터)은 experiments/embedding/
index_to_opensearch.py의 _build_index_body("nori")(embedding-experiment-chunks-v3
생성에 쓰인 것과 동일 함수)와 완전히 동일하게 가져옴 — 로컬에서 검증한 하이브리드
검색 품질이 프로덕션에서도 재현되도록. 바뀐 건 number_of_replicas(0→1, AWS
멀티노드 이중화)와 file_id/embedding_model/embedding_version/indexed_at 4개
필드 추가뿐(전부 검색 랭킹과 무관한 메타데이터).
"""

INDEX_MAPPING: dict = {
    "settings": {
        "index": {
            "knn": True,
            "number_of_shards": 1,
            "number_of_replicas": 1,
        },
        "analysis": {
            "analyzer": {
                "korean": {
                    "type": "custom",
                    "tokenizer": "nori_tokenizer",
                    "filter": ["nori_posfilter", "lowercase"],
                }
            },
            "filter": {
                "nori_posfilter": {
                    "type": "nori_part_of_speech",
                    "stoptags": [
                        "E", "IC", "J", "MAG", "MAJ", "MM", "SP", "SSC", "SSO", "SC",
                        "SE", "XPN", "XSA", "XSN", "XSV", "UNA", "NA", "VSV",
                    ],
                }
            },
        },
    },
    "mappings": {
        "properties": {
            "file_id": {"type": "keyword"},
            "bid_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "chunk_idx": {"type": "integer"},
            "type": {"type": "keyword"},
            "text": {"type": "text", "analyzer": "korean"},
            "vector": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "lucene"},
            },
            "indexed_at": {"type": "date"},
            "embedding_model": {"type": "keyword"},
            "embedding_version": {"type": "keyword"},
        }
    },
}
