# -*- coding: utf-8 -*-
"""backfill/llm_bedrock.py 단위테스트 — bedrock-runtime.converse를 fake로 격리."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "realtime" / "src"))

from backfill import llm_bedrock  # noqa: E402
from extractors.llm.schema import REQUIRED_FIELDS  # noqa: E402


def test_to_converse_splits_system_and_wraps_content():
    messages = [
        {"role": "system", "content": "너는 JSON만 답한다"},
        {"role": "user", "content": "예시 질문"},
        {"role": "assistant", "content": "예시 답변"},
        {"role": "user", "content": "실제 문서"},
    ]
    system_blocks, conv = llm_bedrock._to_converse(messages)
    assert system_blocks == [{"text": "너는 JSON만 답한다"}]
    assert conv == [
        {"role": "user", "content": [{"text": "예시 질문"}]},
        {"role": "assistant", "content": [{"text": "예시 답변"}]},
        {"role": "user", "content": [{"text": "실제 문서"}]},
    ]
    assert all(m["role"] != "system" for m in conv)  # system은 messages에서 빠짐


class _FakeBedrock:
    def __init__(self, text):
        self.text = text
        self.called = {}

    def converse(self, **kwargs):
        self.called = kwargs
        return {"output": {"message": {"content": [{"text": self.text}]}}}


def test_chat_completion_calls_converse_and_returns_text(monkeypatch):
    fake = _FakeBedrock('{"ok": true}')
    monkeypatch.setattr(llm_bedrock, "_get_client", lambda: fake)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "qwen.qwen3-next-80b-a3b")

    out = llm_bedrock.chat_completion([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ])

    assert out == '{"ok": true}'
    assert fake.called["modelId"] == "qwen.qwen3-next-80b-a3b"
    assert fake.called["system"] == [{"text": "S"}]
    assert fake.called["messages"] == [{"role": "user", "content": [{"text": "U"}]}]
    assert fake.called["inferenceConfig"] == {"maxTokens": 8192, "temperature": 0}


def test_extract_reuses_prompt_and_schema(monkeypatch):
    """chat_completion을 스텁해 실제 Bedrock 호출 없이 필터→프롬프트→스키마 검증 파이프라인 확인."""
    captured = {}

    def fake_cc(messages):
        captured["messages"] = messages
        obj = {f: None for f in REQUIRED_FIELDS}
        obj["joint_venture_allowed"] = False
        # grounding 통과용: snippet이 document_text에 실제 존재해야 값이 유지됨
        obj["evidence"] = [{"field": "joint_venture_allowed", "page": 1, "snippet": "공동계약: 불가"}]
        obj["not_found"] = []
        return json.dumps(obj, ensure_ascii=False)

    monkeypatch.setattr(llm_bedrock, "chat_completion", fake_cc)

    result = llm_bedrock.extract([{"page": 1, "text": "2. 입찰참가자격\n가. 공동계약: 불가"}])

    assert result["joint_venture_allowed"] is False  # grounded → 유지
    assert "_meta" in result  # document_text 검증이 돌았다는 증거
    assert captured["messages"][0]["role"] == "system"  # system + few-shot + 문서 조립됨
