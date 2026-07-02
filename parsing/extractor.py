import json
import os
from pathlib import Path

import requests

# LLM 공급자 교체 시 .env의 세 줄만 변경하면 됨
# (NVIDIA Build → AWS Bedrock 등)
_ENV_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")

SCHEMA = {
    "company_size_limit":  (
        "str|null  — 기업 규모 제한. "
        "'중소기업'(중소기업만 참여 가능), "
        "'중견기업이하'(중견기업 이하만), "
        "'대기업제외'(대기업·상호출자제한기업 참여 불가 명시), "
        "'제한없음'(규모 제한 없음) 중 하나."
    ),
    "required_licenses":   "list[str]|null  — 필수 면허/업종 목록. 예: ['전기공사업', '소프트웨어사업자']",
    "required_personnel":  (
        "list[{field:str, grade:str, count:int}]|null  — 필수 기술인력. "
        "field=전문 분야(예:'철도안전전문인력'), grade=등급(예:'중급'), count=인원수. "
        "예: [{\"field\":\"철도안전전문인력\",\"grade\":\"중급\",\"count\":1}]"
    ),
    "perf_min_amt":        "int|null  — 최소 실적 금액 (원). 예: 200000000",
    "perf_max_amt":        "int|null  — 최대 실적 금액 (원). 상한이 명시된 경우만.",
    "perf_period_years":   "int|null  — 실적 인정 기간 (년). 예: 3",
    "perf_type":           "str|null  — 실적 유형. 예: '일반철도신호공사'",
    "region_limit":        "str|null  — 지역 제한. 예: '전북특별자치도'",
    "region_min_days":     "int|null  — 본점 소재 최소 기간 (일). 예: 90",
    "eval_cutline":        (
        "float|null  — 낙찰/적격심사 통과 기준 점수. "
        "'종합평점 N점 이상' 또는 '평점 N점 이상인 자를 낙찰자로' 형태로 명시된 점수."
    ),
    "work_period":         "str|null  — 공사/용역 기간. 예: '착공일로부터 40일'",
    "warranty_rate":       "float|null  — 하자보증률 (%). 예: 2.0",
    "warranty_months":     "int|null  — 하자기간 (월). 예: 24",
    "required_certs":      "list[str]|null  — 필수 인증 목록. 예: ['GS인증', 'ISO 27001']",
    "other_requirements":  "dict|null  — 위 항목에 해당하지 않는 기타 자격요건",
}

_PROMPT = """아래는 공공 조달 입찰 공고문 텍스트입니다.
JSON 스키마에 맞게 입찰 자격요건을 추출하세요.
- 항목이 문서에 없으면 null
- JSON만 반환, 설명 없이
- required_personnel의 field는 전문분야, grade는 등급(고급/중급/초급)

스키마:
{schema}

공고문:
{text}

JSON:"""

_MAX_TEXT_CHARS = 6000


def _load_config() -> dict:
    env_path = Path(__file__).parent.parent / ".env"
    config = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            for key in _ENV_KEYS:
                if line.startswith(f"{key}="):
                    config[key] = line.split("=", 1)[1].strip()
    for key in _ENV_KEYS:
        if key not in config:
            config[key] = os.environ.get(key, "")
    return config


def _parse_json(content: str) -> dict:
    # 마크다운 코드블록 제거
    if "```" in content:
        parts = content.split("```")
        content = parts[1] if len(parts) > 1 else parts[0]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())


def extract_qualifications(text: str) -> dict:
    config = _load_config()
    if not config.get("LLM_API_KEY"):
        raise ValueError("LLM_API_KEY가 설정되지 않았습니다.")

    trimmed = text[:_MAX_TEXT_CHARS]
    prompt = _PROMPT.format(
        schema=json.dumps(SCHEMA, ensure_ascii=False, indent=2),
        text=trimmed,
    )

    payload = {
        "model": config["LLM_MODEL"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {config['LLM_API_KEY']}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        f"{config['LLM_BASE_URL']}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_json(content)


if __name__ == "__main__":
    import sys

    txt_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not txt_path:
        print("사용법: python -m parsing.extractor <텍스트파일경로>")
        sys.exit(1)

    text = Path(txt_path).read_text(encoding="utf-8")
    result = extract_qualifications(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
