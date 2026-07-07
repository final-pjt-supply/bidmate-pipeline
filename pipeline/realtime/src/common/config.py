# -*- coding: utf-8 -*-
"""환경변수 로딩. 버킷/큐 이름은 코드에 하드코딩하지 않고 여기서만 읽는다."""
import os


def load_config() -> dict:
    return {
        "next_queue_url": os.environ["LLM_EXTRACT_QUEUE_URL"],
    }
