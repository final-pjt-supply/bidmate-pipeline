# -*- coding: utf-8 -*-
"""검색 품질 평가셋 생성 — 하이브리드 가중치 튜닝 + recall 측정용.

experiments/embedding/data/의 문서를 공고(bid_id) 단위로 묶어(파일 개수와
공고 개수가 다르다 — 69개 파일이지만 고유 bid_id는 42개, 첨부문서가 여러 개인
공고가 15건 있음), 공고당 2~3개의 쿼리를 LLM(qwen3-next-80b, NVIDIA Build)으로
생성한다. 이미 parsing/pdf/qualification_extractor.py가 쓰는 것과 같은
LLM_BASE_URL/LLM_API_KEY/LLM_MODEL(.env, 공급자 교체 가능한 범용 이름) 패턴을
재사용한다 — 이 실험 스크립트는 그 방식을 그대로 따르고, 실시간 파이프라인의
extractors/llm/client.py(NVIDIA_API_KEY, OpenAI SDK)와는 별개다.

쿼리 유형 3종(공고 하나에서 가능하면 각각 최소 1개, 애매하면 2종류만):
  - 탐색형: "OO 분야 공고" 같은 넓은 도메인 탐색
  - 조건매칭형: "△△ 업종/실적이면 참여 가능한 공고" 같은 입찰 담당자의
    자격 매칭 관점 질문
  - 공고내부형: 이미 특정 공고를 어느 정도 알고 있는 상태에서 자격요건/
    마감일/금액 등 세부사항을 묻는 질문

핵심 제약: 원문 문장·사업명을 그대로 베끼지 않는다. LLM 프롬프트에서 동의어/
일반 표현으로 바꿔쓰고 구어체·업종 용어를 섞도록 명시적으로 지시한다 —
원문 그대로면 BM25가 사실상 100% 맞히는 비현실적인 평가셋이 된다.

실행(리포 루트에서):
    cd experiments/embedding && python generate_eval_set.py
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(__file__).parent / "data"
OUT_PATH = Path(__file__).parent / "eval_set.json"

_ENV_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
_CONTEXT_CHAR_BUDGET = 6000  # 공고당 LLM에 넘길 원문 길이 상한(비용/속도 절충, 앞부분 우선)
_QUERIES_PER_BID = "2~3"
_MAX_RETRIES = 2

_TYPES = ("탐색형", "조건매칭형", "공고내부형")

_SYSTEM_PROMPT = """당신은 조달 입찰 검색 시스템의 평가셋을 만드는 어시스턴트입니다.
주어진 입찰공고 원문을 보고, 실제 입찰 담당자가 이 공고를 찾으려고 검색창에
입력할 법한 쿼리를 만드세요.

쿼리 유형 3가지(가능하면 최소 1개씩 섞어서 총 2~3개 작성, 문서 내용상 애매한
유형이 있으면 생략 가능):
1. 탐색형 — "OO 분야 공고 찾아줘" 같은 넓은 도메인/키워드 탐색 질문
2. 조건매칭형 — "△△업종 등록/실적 있으면 참여 가능한 공고 있나" 같은,
   입찰 담당자가 자기 회사 자격을 기준으로 참여 가능 여부를 묻는 질문
3. 공고내부형 — 이미 이 공고를 어느 정도 아는 상태에서 자격요건/마감일/
   사업금액 등 세부사항을 묻는 질문

절대 규칙 — 원문을 그대로 베끼지 마세요:
- 공고명이나 원문 문장을 토씨 하나 안 틀리고 그대로 쓰면 안 됩니다.
- 핵심어(사업명, 품명 등)는 동의어나 더 일반적인 표현으로 바꿔 쓰세요.
  예: "eLoran 수신기 보정지도 지원 시스템 구축" → "전자항법 관련 시스템
  구축 사업" 또는 "위치정보 보정 시스템 사업" 처럼 풀어 쓰기.
- 실제 담당자가 쓸 법한 구어체·업종 용어·약어를 섞으세요(완벽한 문장체가
  아니어도 됩니다). 예: "이번에 뜬 소프트웨어 용역 중에 중소기업만 참여
  가능한 거 뭐있지" 같은 톤도 괜찮습니다.
- 나쁜 예(원문 그대로): "전기차 주행 시뮬레이터"
  좋은 예(paraphrase): "전기자동차 운전 훈련용 시뮬레이션 장비 사는 공고 있나"

반드시 아래 JSON 형식으로만 답하세요(다른 텍스트 없이):
{"queries": [{"query": "...", "type": "탐색형|조건매칭형|공고내부형"}, ...]}
"""


def _load_config() -> dict:
    env_path = Path(__file__).parent.parent.parent / ".env"
    config = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            for key in _ENV_KEYS:
                if line.startswith(f"{key}="):
                    config[key] = line.split("=", 1)[1].strip()
    for key in _ENV_KEYS:
        config.setdefault(key, os.environ.get(key, ""))
    return config


def _group_by_bid() -> dict[str, list[dict]]:
    files = sorted(DATA_DIR.rglob("*.json"))
    by_bid: dict[str, list[dict]] = {}
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        by_bid.setdefault(doc["bid_id"], []).append(doc)
    for bid_id in by_bid:
        by_bid[bid_id].sort(key=lambda d: d["document_id"])
    return by_bid


def _build_context(docs: list[dict]) -> tuple[str, str]:
    """공고에 속한 문서들의 텍스트를 이어붙여 앞부분 위주로 잘라 반환한다.
    (본문, source_doc용 document_id 목록 문자열)을 반환."""
    parts = []
    for doc in docs:
        text = "\n\n".join(p["text"] for p in doc.get("pages", []))
        parts.append(text)
    combined = "\n\n".join(parts)
    source_doc = ",".join(d["document_id"] for d in docs)
    return combined[:_CONTEXT_CHAR_BUDGET], source_doc


def _parse_json(content: str) -> dict:
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass
    if "```" in content:
        for part in content.split("```"):
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue
    match = re.search(r'\{[\s\S]*\}', content)
    if match:
        return json.loads(match.group())
    raise ValueError(f"JSON 파싱 실패: {content[:200]!r}")


def _call_llm(config: dict, context: str) -> list[dict]:
    payload = {
        "model": config["LLM_MODEL"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"입찰공고 원문(일부):\n\n{context}"},
        ],
        "max_tokens": 1024,
        "temperature": 0.7,  # 파라프레이즈 다양성을 위해 0보다 높게(자격요건 추출과 달리 창의성 필요)
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {config['LLM_API_KEY']}",
        "Content-Type": "application/json",
    }
    resp = requests.post(f"{config['LLM_BASE_URL']}/chat/completions", headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    parsed = _parse_json(content)
    queries = parsed.get("queries", [])
    return [q for q in queries if q.get("query") and q.get("type") in _TYPES]


def main() -> None:
    config = _load_config()
    if not config.get("LLM_API_KEY"):
        raise ValueError("LLM_API_KEY가 설정되지 않았습니다(.env 확인).")

    by_bid = _group_by_bid()
    print(f"고유 공고(bid_id) 수: {len(by_bid)}")

    eval_set = []
    failed = []

    for i, (bid_id, docs) in enumerate(sorted(by_bid.items()), start=1):
        context, source_doc = _build_context(docs)
        print(f"[{i}/{len(by_bid)}] {bid_id} (문서 {len(docs)}개, source_doc={source_doc}) 생성 중...")

        queries = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                queries = _call_llm(config, context)
                break
            except Exception as e:
                print(f"  시도 {attempt}/{_MAX_RETRIES} 실패: {e}")
                if attempt < _MAX_RETRIES:
                    time.sleep(3)

        if not queries:
            failed.append(bid_id)
            continue

        for q in queries:
            eval_set.append({
                "query": q["query"],
                "answer_bid_id": bid_id,
                "type": q["type"],
                "source_doc": source_doc,
            })

    OUT_PATH.write_text(json.dumps(eval_set, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n생성 완료: {len(eval_set)}개 쿼리 (공고 {len(by_bid) - len(failed)}/{len(by_bid)}건 성공)")
    print(f"저장 위치: {OUT_PATH}")
    if failed:
        print(f"실패한 bid_id({len(failed)}건): {failed}")


if __name__ == "__main__":
    main()
