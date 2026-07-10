# -*- coding: utf-8 -*-
"""인덱싱 큐를 트리거로 하는 Lambda 진입점(#64). embed_chunks.py가 저장한
vectors/....json(청크+벡터)을 읽어 OpenSearch에 bulk 색인한다. 파이프라인의
마지막 단계라 다음 큐로 넘기는 게 없다(extract_llm.py와 동일한 성격).

VPC 내부 배치 전제(실제 VpcConfig는 template.yaml, Step 4 소관) — 관리형
OpenSearch가 Private VPC 안에 있어 이 Lambda도 같이 들어가야 붙을 수 있다.
추출/임베딩/LLM Lambda는 반대로 VPC 밖에 남는다(Cloudflare/NVIDIA 외부 API
호출 때문 — VPC 안에 넣으면 인터넷이 막힌다).
"""
import json
import logging
import time
from datetime import datetime, timezone

from opensearchpy import helpers

from common import paths, s3, sqs
from common.config import load_indexing_config
from common.logs import log_process_done, log_process_start
from indexing.opensearch_client import get_client

logger = logging.getLogger(__name__)

# 다른 handler와 동일한 이유(루트 로거 기본 레벨 WARNING에 INFO가 걸러지는 문제).
logging.getLogger().setLevel(logging.INFO)

_client = None
_index_name = None
_index_checked = False


def _get_checked_client():
    """콜드스타트당 한 번만 클라이언트를 만들고 인덱스 존재를 확인한다.

    OpenSearch bulk API는 대상 인덱스가 없으면 동적 매핑으로 자동 생성해버린다
    — 그러면 vector가 knn_vector가 아니게 되는 등 매핑이 조용히 틀어진다.
    인덱스 생성은 이 Lambda의 책임이 아니라 scripts/setup-opensearch-index.py가
    미리 해둬야 하는 일이므로, 없으면 여기서 즉시 실패시켜(재시도→DLQ→알람)
    "배포 순서가 꼬였다"는 신호를 조용히 삼키지 않고 드러낸다.
    """
    global _client, _index_name, _index_checked
    if _client is None:
        config = load_indexing_config()
        _client = get_client(
            mode=config["opensearch_mode"],
            host=config["opensearch_host"],
            port=config["opensearch_port"],
            user=config["opensearch_user"],
            password=config["opensearch_password"],
        )
        _index_name = config["opensearch_index_name"]
    if not _index_checked:
        if not _client.indices.exists(index=_index_name):
            raise RuntimeError(
                f"OpenSearch 인덱스가 없음: {_index_name} — "
                "scripts/setup-opensearch-index.py를 먼저 실행할 것(auto-create로 "
                "매핑이 틀어지는 걸 막기 위해 여기서 자동 생성하지 않음)"
            )
        _index_checked = True
    return _client, _index_name


def lambda_handler(event, _context):
    for bucket, key in sqs.iter_direct_messages(event):
        _process(bucket, key)


def _process(bucket: str, key: str) -> None:
    parts = paths.parse_vectors_key(key)
    bid_id = parts["bid_id"]
    file_id = parts["file_id"]
    document_id = paths.document_id_from_file_id(bid_id, file_id)

    log_process_start(bid_id, document_id, key)
    start = time.monotonic()
    try:
        client, index_name = _get_checked_client()

        body = json.loads(s3.get_object(bucket, key))
        chunks = body["chunks"]
        indexed_at = datetime.now(timezone.utc).isoformat()

        actions = (
            {
                "_index": index_name,
                "_id": f"{c['file_id']}::{c['chunk_idx']}",
                "_source": {**c, "indexed_at": indexed_at},
            }
            for c in chunks
        )
        # raise_on_error=True(기본값) — 배치 안 문서 하나라도 실패하면 예외를 던져
        # 메시지 전체를 재시도시킨다. BatchSize=1 관례(template.yaml 주석 참고)와
        # 동일한 원칙: 부분 성공을 조용히 넘기지 않고, 실패한 메시지만 정확히
        # 재시도/DLQ 되게 한다. experiments/embedding/index_to_opensearch.py는
        # 실험 스크립트라 raise_on_error=False로 느슨하게 돌렸지만, 여기선 그대로
        # 가져오지 않는다.
        success, _ = helpers.bulk(client, actions)

        log_process_done(bid_id, document_id, int((time.monotonic() - start) * 1000), f"{index_name}({success}건)")
    except Exception:
        logger.exception(
            "인덱싱 실패: bucket=%s key=%s bid_id=%s document_id=%s",
            bucket, key, bid_id, document_id,
        )
        raise
