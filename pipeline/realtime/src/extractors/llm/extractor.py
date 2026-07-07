# -*- coding: utf-8 -*-
"""LLM 자격요건 추출 진입점. 다른 extractors/*.py(pdf/hwp/hwpx)와 동일하게
extract(...) 하나로 handler에 노출한다 — prompt 조립·API 호출·응답 검증은
전부 이 안에서 처리하고 handler는 배선만 한다.
"""
from extractors.llm import client, prompt, schema


def extract(pages: list[dict]) -> dict:
    """text_extracted의 pages([{page,text}])로 참가자격 JSON(18필드+evidence+not_found)을 추출한다."""
    document_text = prompt.build_document_text(pages)
    user_prompt = prompt.build_user_prompt(document_text)
    raw_content = client.chat_completion(prompt.SYSTEM_PROMPT, user_prompt)
    return schema.parse_and_validate(raw_content)
