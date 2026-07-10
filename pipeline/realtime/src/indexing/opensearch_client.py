# -*- coding: utf-8 -*-
"""OpenSearch 클라이언트 생성. OPENSEARCH_MODE로 local/aws 분기(#64).

local: db/docker-compose.yml의 로컬 OpenSearch(무인증, http) — 로직 테스트용.
       experiments/embedding/index_to_opensearch.py의 get_client()와 동일 설정.
aws:   VPC 내부 관리형 OpenSearch. basic auth(OPENSEARCH_USER/PASSWORD) — SigV4
       아님(#64 최종 확정, 실제 도메인이 basic auth로 운영 중). 인덱싱 Lambda가
       VPC 안에 배치돼야 붙을 수 있다(VpcConfig는 template.yaml, Step 4 소관 —
       이 모듈은 접속 자체만 다룬다).
"""
from opensearchpy import OpenSearch


def get_client(mode: str, host: str, port: int, user: str | None = None, password: str | None = None) -> OpenSearch:
    if mode == "local":
        return OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_compress=True,
            use_ssl=False,
            verify_certs=False,
        )
    if mode == "aws":
        if not user or not password:
            raise ValueError("OPENSEARCH_MODE=aws는 OPENSEARCH_USER/OPENSEARCH_PASSWORD가 필요함")
        return OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=(user, password),
            use_ssl=True,
            verify_certs=True,
        )
    raise ValueError(f"알 수 없는 OPENSEARCH_MODE: {mode!r} (local 또는 aws만 지원)")
