# -*- coding: utf-8 -*-
"""환경변수 로딩. 버킷/큐 이름은 코드에 하드코딩하지 않고 여기서만 읽는다."""
import os


def load_config() -> dict:
    """pdf/hwp/hwpx 추출 handler가 쓰는 설정: 다음(LLM 추출) 큐 URL + 임베딩 큐 URL.

    embed_queue_url: 추출 완료 직후 LLM 큐와 병렬로 분기하는 임베딩 큐(#64).
    next_queue_url과 달리 os.environ.get(기본값 None)으로 읽는다 — 이 코드가
    배포된 뒤에도 SAM template(#64 Step 4)에서 EMBED_QUEUE_URL을 실제로 세팅하기
    전까지는 값이 없는 게 정상이고, 그 상태에서 load_config() 자체가 터지면
    기존 LLM 큐 전송(next_queue_url)까지 막혀버린다. handler 쪽에서 None이면
    "아직 안 붙었다"로 보고 임베딩 큐 전송만 건너뛴다.
    """
    return {
        "next_queue_url": os.environ["LLM_EXTRACT_QUEUE_URL"],
        "embed_queue_url": os.environ.get("EMBED_QUEUE_URL"),
    }


def load_llm_config() -> dict:
    """LLM 추출 handler/client가 쓰는 설정: NVIDIA Build(OpenAI 호환) API 접속 정보.

    load_config()와 분리한 이유: pdf/hwp/hwpx Lambda엔 이 env var들이 없어서
    (다음 큐 URL만 필요) 하나로 합치면 그쪽에서 없는 키를 강제로 요구하게 된다.
    """
    return {
        "nvidia_api_key": os.environ["NVIDIA_API_KEY"],
        "llm_base_url": os.environ["LLM_BASE_URL"],
        "llm_model": os.environ["LLM_MODEL"],
    }


def load_embedding_config() -> dict:
    """임베딩 handler(embed_chunks.py)가 쓰는 설정: Cloudflare Workers AI 접속 정보 +
    다음(인덱싱) 큐 URL.

    index_queue_url은 os.environ.get(기본값 None) — #64를 임베딩→S3 벡터 저장
    구간까지만 먼저 배포/검증하고(인덱싱 Lambda·VPC 설정은 후속으로 미룸,
    2026-07-10 결정) 인덱싱 큐가 아직 없는 상태로 이 Lambda를 띄우기 때문이다.
    값이 없으면 handler가 "아직 안 붙었다"로 보고 큐 전송만 건너뛰고 S3 저장까지는
    그대로 완료한다 — extract_pdf/hwp/hwpx.py의 embed_queue_url과 동일한 패턴.
    """
    return {
        "cloudflare_account_id": os.environ["CLOUDFLARE_ACCOUNT_ID"],
        "cloudflare_api_token": os.environ["CLOUDFLARE_API_TOKEN"],
        "index_queue_url": os.environ.get("INDEX_QUEUE_URL"),
    }


def load_indexing_config() -> dict:
    """인덱싱 handler(index_opensearch.py)가 쓰는 설정: OpenSearch 접속 정보.

    opensearch_user/opensearch_password는 os.environ.get(기본값 None) — local
    모드(Docker, 무인증)에선 없는 게 정상이라 필수로 강제하면 안 된다. aws
    모드에서 이 값이 실제로 필요한지는 indexing/opensearch_client.py가 판단한다
    (거기서 없으면 명시적으로 실패).
    """
    return {
        "opensearch_mode": os.environ["OPENSEARCH_MODE"],
        "opensearch_host": os.environ["OPENSEARCH_HOST"],
        "opensearch_port": int(os.environ["OPENSEARCH_PORT"]),
        "opensearch_user": os.environ.get("OPENSEARCH_USER"),
        "opensearch_password": os.environ.get("OPENSEARCH_PASSWORD"),
        "opensearch_index_name": os.environ["OPENSEARCH_INDEX_NAME"],
    }


def load_merge_db_config() -> dict:
    """RDS 병합 배치(merge/db.py, #66)가 쓰는 설정: RDS Postgres 접속 정보 +
    qualifications/ 대상 S3 버킷 + DRY_RUN 플래그.

    db_password는 절대 코드에 하드코딩하지 않고 환경변수(Lambda Environment
    Variables)로만 받는다. 추후 Secrets Manager로 교체할 때도 이 함수 안만
    바꾸면 되게, DB 접속 정보를 읽는 지점을 여기 하나로 모아둔다.
    """
    return {
        "db_host": os.environ["MERGE_DB_HOST"],
        "db_port": int(os.environ.get("MERGE_DB_PORT", "5432")),
        "db_name": os.environ["MERGE_DB_NAME"],
        "db_user": os.environ["MERGE_DB_USER"],
        "db_password": os.environ["MERGE_DB_PASSWORD"],
        "source_bucket": os.environ.get("SOURCE_BUCKET", "bidmate"),
        # 기본값 true — 실제 UPDATE는 명시적으로 DRY_RUN=false를 넣어야만 실행된다
        # (안전 기본값, #66 3단계 지시). "false"(대소문자 무관)일 때만 실제 실행.
        "dry_run": os.environ.get("DRY_RUN", "true").strip().lower() != "false",
        # 회당 처리 대상 상한(2026-07-14 — 실전 timeout 300000ms 실측 대응).
        # 초과분은 드랍하지 않고 다음 주기(5분)로 이월 — qual_status가
        # pending/partial로 남아있어 다음 회차 fetch_merge_targets에 자연스럽게
        # 다시 잡힌다.
        "max_targets_per_run": int(os.environ.get("MERGE_MAX_TARGETS_PER_RUN", "200")),
    }
