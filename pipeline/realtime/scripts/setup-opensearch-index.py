# -*- coding: utf-8 -*-
"""OpenSearch 인덱스를 1회성으로 생성한다(#64). index_opensearch.py Lambda가
배포되기 전에 반드시 먼저 실행해야 한다 — 안 해두면 Lambda가 인덱스 없음을
감지하고 즉시 실패하도록 설계돼 있다(handlers/index_opensearch.py 참고, auto-create
로 매핑이 틀어지는 걸 막기 위한 의도적 설계).

experiments/embedding/index_to_opensearch.py의 create_index()와 달리
**비파괴적**이다 — 그쪽은 실험용이라 기존 인덱스를 지우고 다시 만들지만, 이건
운영 인덱스라 이미 있으면 그냥 스킵한다(재실행해도 안전, 데이터 삭제 없음).

실행(리포 루트에서):
    cd pipeline/realtime/scripts && python setup-opensearch-index.py

OPENSEARCH_MODE=aws로 실행하는 경우, 관리형 OpenSearch가 Private VPC 안에 있어
이 스크립트도 VPC 내부(예: 같은 VPC의 bastion/Cloud9, 또는 VPN)에서 실행해야
접속된다 — 로컬 개발 PC에서 곧바로는 못 붙는다(#64 확인 사항).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.config import load_indexing_config  # noqa: E402
from indexing.mapping import INDEX_MAPPING  # noqa: E402
from indexing.opensearch_client import get_client  # noqa: E402


def main() -> None:
    config = load_indexing_config()
    client = get_client(
        mode=config["opensearch_mode"],
        host=config["opensearch_host"],
        port=config["opensearch_port"],
        user=config["opensearch_user"],
        password=config["opensearch_password"],
    )
    index_name = config["opensearch_index_name"]

    if client.indices.exists(index=index_name):
        print(f"이미 존재함 — 스킵(비파괴적, 삭제 안 함): {index_name}")
        return

    client.indices.create(index=index_name, body=INDEX_MAPPING)
    print(f"인덱스 생성 완료: {index_name}")


if __name__ == "__main__":
    main()
