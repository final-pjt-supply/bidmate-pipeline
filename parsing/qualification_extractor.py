import json
import os
import re
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

_FEW_SHOT = """
[예시 공고문]
2. 입찰참가자격
가. 전기공사업법 제4조에 의한 전기공사업 등록업체
나. 본 공사는 10억원 미만으로 대기업(상호출자제한기업집단) 전기공사업자는 참여할 수 없습니다.
다. 철도신호분야(구분:시공) 필수기술인력(중급 1인, 초급 1인 이상)을 보유한 업체
라. 본점 소재지가 전북특별자치도에 입찰공고일 기준 90일 이상 소재한 업체

5. 낙찰자 결정
적격심사하여 종합평점이 95점 이상인 자를 낙찰예정자로 결정합니다.
시공경험: 3억원 미만 2억원 이상 일반철도신호공사 (최근 3년)
공사기간: 착공지정일로부터 40일간
하자보증률/기간: 2% / 2년

[예시 JSON 출력]
{
  "company_size_limit": "대기업제외",
  "required_licenses": ["전기공사업"],
  "required_personnel": [
    {"field": "철도안전전문인력", "grade": "중급", "count": 1},
    {"field": "철도안전전문인력", "grade": "초급", "count": 1}
  ],
  "perf_min_amt": 200000000,
  "perf_max_amt": 300000000,
  "perf_period_years": 3,
  "perf_type": "일반철도신호공사",
  "region_limit": "전북특별자치도",
  "region_min_days": 90,
  "eval_cutline": 95.0,
  "work_period": "착공지정일로부터 40일",
  "warranty_rate": 2.0,
  "warranty_months": 24,
  "required_certs": null,
  "other_requirements": null
}
"""

_PROMPT = """아래는 공공 조달 입찰 공고문 텍스트입니다.
JSON 스키마에 맞게 입찰 자격요건을 추출하세요.
- 항목이 문서에 없으면 null
- JSON만 반환, 설명 없이
- required_personnel의 field는 전문분야, grade는 등급(고급/중급/초급)
- "대기업 참여 불가", "상호출자제한기업 제외" 등의 표현은 company_size_limit: "대기업제외"
- "종합평점 N점 이상", "평점 N점 이상인 자를 낙찰자로" → eval_cutline: N

{few_shot}

스키마:
{schema}

공고문:
{text}

JSON:"""

_MAX_TEXT_CHARS = None  # 전문 전달 (llama-3.1-8b 128k 컨텍스트 내 처리 가능)

_PAGE_SEP = re.compile(r'={10,}\n\d+페이지\n={10,}')
_APPENDIX_START = re.compile(
    r'^\s*(\[붙임|\[별첨|\[부록|\[서식|붙임\s*\d|별첨\s*\d|부록\s*\d|서식\s*\d|【붙임|【별첨|【서식)',
    re.MULTILINE,
)


def _remove_appendices(text: str) -> str:
    pages = _PAGE_SEP.split(text)
    seps = _PAGE_SEP.findall(text)
    result = [pages[0]]
    for sep, page in zip(seps, pages[1:]):
        if _APPENDIX_START.match(page.lstrip('\n')):
            break
        result.append(sep)
        result.append(page)
    removed = len(pages) - len(result)
    if removed > 0:
        print(f"[전처리] 붙임/서식 {removed}페이지 제거 ({len(text):,}자 → {len(''.join(result)):,}자)")
    return ''.join(result)


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
    import re
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass
    # 마크다운 코드블록
    if "```" in content:
        for part in content.split("```"):
            part = part.strip().lstrip("json").strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue
    # 정규식으로 {...} 블록 추출
    match = re.search(r'\{[\s\S]*\}', content)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON 파싱 실패: {content[:200]}")


def extract_qualifications(text: str) -> dict:
    config = _load_config()
    if not config.get("LLM_API_KEY"):
        raise ValueError("LLM_API_KEY가 설정되지 않았습니다.")

    text = _remove_appendices(text)
    trimmed = text[:_MAX_TEXT_CHARS] if _MAX_TEXT_CHARS else text
    prompt = _PROMPT.format(
        few_shot=_FEW_SHOT,
        schema=json.dumps(SCHEMA, ensure_ascii=False, indent=2),
        text=trimmed,
    )

    payload = {
        "model": config["LLM_MODEL"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8192,
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
        print("사용법: python -m parsing.qualification_extractor <텍스트파일경로>")
        sys.exit(1)

    text = Path(txt_path).read_text(encoding="utf-8")
    result = extract_qualifications(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
