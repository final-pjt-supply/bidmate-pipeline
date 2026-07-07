# -*- coding: utf-8 -*-
"""환경변수 로딩. 버킷/큐 이름은 코드에 하드코딩하지 않고 여기서만 읽는다."""
import os


def load_config() -> dict:
    """pdf/hwp/hwpx 추출 handler가 쓰는 설정: 다음(LLM 추출) 큐 URL."""
    return {
        "next_queue_url": os.environ["LLM_EXTRACT_QUEUE_URL"],
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
