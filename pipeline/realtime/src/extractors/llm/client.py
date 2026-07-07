# -*- coding: utf-8 -*-
"""NVIDIA Build API(OpenAI 호환) 호출.

base_url을 env(LLM_BASE_URL)로 빼서 나중에 다른 OpenAI 호환 API(AWS Bedrock 등)로
교체 가능하게 한다. parsing/hwp_hwpx/hwp_image_describer.py의 NVIDIA 호출 방식을
참고했지만 거기는 비전 모델이라 requests로 직접 호출하고, 여기는 텍스트 전용이라
OpenAI SDK의 chat.completions만으로 충분하다.
"""
from openai import OpenAI

from common.config import load_llm_config

_client: OpenAI | None = None
_model: str | None = None


def _get_client() -> OpenAI:
    global _client, _model
    if _client is None:
        config = load_llm_config()
        _client = OpenAI(base_url=config["llm_base_url"], api_key=config["nvidia_api_key"])
        _model = config["llm_model"]
    return _client


def chat_completion(system_prompt: str, user_prompt: str) -> str:
    """system/user 메시지를 보내고 응답 텍스트(content)를 그대로 반환한다."""
    client = _get_client()
    response = client.chat.completions.create(
        model=_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=8192,
        temperature=0,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    # 수동 연결 확인용(실제 API 호출, 과금 대상):
    #   cd pipeline/realtime/scripts && python -c "from dotenv import load_dotenv; load_dotenv('../../../.env')"
    # 처럼 .env를 먼저 로드한 뒤 `python -m extractors.llm.client`로 실행(src가 sys.path에 있어야 함).
    # 전체 파이프라인(프롬프트+스키마 검증까지) 테스트는 scripts/test_llm_extract.py 참고.
    print(chat_completion(
        "당신은 JSON만 답하는 테스트 어시스턴트입니다.",
        '{"ping": "pong"} 형태로만 답하세요.',
    ))
