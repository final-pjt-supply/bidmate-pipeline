# -*- coding: utf-8 -*-
"""Bedrock(Converse API)로 Qwen을 호출하는 backfill LLM 자격요건 추출.

realtime extractors/llm/client.py(OpenAI/NVIDIA)는 수정하지 않고, backfill은
Bedrock 경로를 자체적으로 둔다. Bedrock은 OpenAI 비호환이라 base_url 교체가 아니라
boto3 bedrock-runtime.converse를 쓴다. prompt/schema/preprocess는 프로바이더
무관이라 그대로 재사용한다(extractor.py는 OpenAI client에 묶여 있어 쓰지 않는다).

인증: AWS_BEARER_TOKEN_BEDROCK(env, Bedrock API 키)를 botocore가 자동 사용.
리전: AWS_BEDROCK_REGION은 boto3 표준 변수가 아니라 명시적으로 region_name에 넘긴다.
JSON 강제: Converse엔 response_format이 없어, 프롬프트 지시 + schema.parse_and_validate의
마크다운/JSON 추출 폴백에 의존한다.
"""
import os

import boto3

from extractors.llm import prompt, schema
from extractors.llm.preprocess import filter_pages

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_BEDROCK_REGION"])
    return _client


def _to_converse(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """OpenAI 스타일 messages를 Converse용 (system, messages)로 분리·변환한다.

    Converse는 system을 별도 파라미터로 받으므로 role=system 메시지를 떼어내고,
    나머지 user/assistant는 content를 [{"text": ...}] 블록으로 감싼다.
    """
    system_blocks = [{"text": m["content"]} for m in messages if m["role"] == "system"]
    conv_messages = [
        {"role": m["role"], "content": [{"text": m["content"]}]}
        for m in messages
        if m["role"] != "system"
    ]
    return system_blocks, conv_messages


def chat_completion(messages: list[dict]) -> str:
    """messages(멀티턴)를 Converse로 보내고 응답 텍스트(content)를 반환한다."""
    system_blocks, conv_messages = _to_converse(messages)
    response = _get_client().converse(
        modelId=os.environ["BEDROCK_MODEL_ID"],
        system=system_blocks,
        messages=conv_messages,
        inferenceConfig={"maxTokens": 8192, "temperature": 0},
    )
    return response["output"]["message"]["content"][0]["text"]


def extract(pages: list[dict]) -> dict:
    """realtime extractor.extract와 동일 계약(pages -> 참가자격 JSON). client만 Bedrock이다."""
    pages = filter_pages(pages)
    document_text = prompt.build_document_text(pages)
    messages = prompt.build_messages(document_text)
    raw_content = chat_completion(messages)
    return schema.parse_and_validate(raw_content, document_text=document_text)
