# -*- coding: utf-8 -*-
"""LLM 출력 JSON 스키마(18필드 + evidence/not_found) 정의 및 응답 파싱·검증.

LLM 응답은 마크다운 코드블록으로 감싸져 오거나 앞뒤에 설명이 붙는 경우가 있어
parse_and_validate()에서 파싱 폴백(코드블록 -> 정규식 {..} 추출)까지 함께 처리한다.

null과 not_found의 구분(prompt.py의 지시와 짝을 이룸):
- 문서에 아예 언급이 없는 항목 -> 값은 null, not_found엔 안 넣음
- 문서가 "해당 없음"/"제한 없음"처럼 명시적으로 부재를 확인해준 항목
  -> 값은 null(또는 enum의 'none')이지만 not_found에 필드명을 추가
"""
import json
import re

SCHEMA = {
    "company_size_limit": (
        "str|null — 기업 규모 제한. "
        "'sme_only'(중소기업만), 'small_only'(소상공인만), "
        "'no_large'(대기업 참여 불가, 중견기업 이하 가능), "
        "'no_conglomerate'(상호출자제한기업집단만 제외), "
        "'none'(제한 없음) 중 하나. 언급이 아예 없으면 null."
    ),
    "direct_production_req": "bool|null — 직접생산확인증명서 요구 여부.",
    "credit_rating_req": "bool|null — 신용평가등급(회사채/기업어음 등) 요구 여부.",
    "required_licenses": (
        "list[{or_group:int, name_raw:str, code:str|null}]|null — 필수 면허/등록. "
        "or_group이 같은 항목끼리는 그 중 하나만 있으면 됨(OR 조건), 그룹 번호가 다르면 전부 필요(AND). "
        "name_raw는 원문 그대로의 면허명, code는 표준 코드가 문서에 명시된 경우만 채움."
    ),
    "item_codes": (
        "list[{type:str, code:str}]|null — 품목/업종 분류 코드. "
        "type은 코드 체계 이름(예: '세부품명번호', '업종코드'), code는 실제 코드 값."
    ),
    "region_limit_type": "str|null — 지역 제한 종류. 'hq_location'(본점 소재지 제한) 또는 'none'(제한 없음).",
    "region_limit_names": "list[str]|null — 제한 지역명. 예: [\"전북특별자치도\"]",
    "region_basis": "str|null — 지역 제한 판단 기준. 예: '입찰공고일 기준 90일 이상 계속 소재'",
    "performance_reqs": (
        "list[{category:str, basis:str, value:number, unit:str, scope_raw:str}]|null — 실적 요건. "
        "category=실적 유형(예:'일반철도신호공사'), basis=인정 기준(예:'최근 3년'), "
        "value/unit=최소 실적 수치와 단위(예: 300000000/'원'), scope_raw=원문 그대로의 범위 설명."
    ),
    "capacity_reqs": "list[{name:str, value:number, unit:str}]|null — 보유 설비·시설·생산능력 요건.",
    "personnel_reqs": (
        "list[{field:str, grade:str, count:int}]|null — 필수 기술인력. "
        "field=전문분야, grade=등급(고급/중급/초급 등), count=인원수."
    ),
    "required_certs": "list[str]|null — 필수 인증. 예: [\"GS인증\", \"ISO 27001\"]",
    "award_cutline_type": "str|null — 낙찰 커트라인 방식. 'score'(적격심사 등 점수제) 또는 'rate'(낙찰하한율 등 비율제).",
    "award_cutline_value": "number|null — award_cutline_type에 대응하는 수치(점수 또는 %).",
    "tech_weight": "number|null — 기술(또는 이행능력) 평가 배점 비중.",
    "price_weight": "number|null — 가격 평가 배점 비중.",
    "joint_venture_allowed": "bool|null — 공동수급(컨소시엄) 허용 여부.",
    "subcontract_allowed": "bool|null — 하도급 허용 여부.",
    "evidence": (
        "list[{field:str, page:int, snippet:str}] — null이 아닌 값을 채운 필드에 대해서만 근거를 남긴다. "
        "field=근거가 되는 위 스키마 키 이름, page=근거가 있는 [페이지 N] 마커의 N, "
        "snippet=근거가 된 원문 일부(짧게)."
    ),
    "not_found": (
        "list[str] — 문서가 '해당 없음'/'제한 없음' 등으로 명시적으로 부재를 확인해준 필드명 목록. "
        "단순히 언급이 없어 null로 둔 필드는 여기 넣지 않는다."
    ),
}

# TODO: 라이브 테스트(외자물품 샘플)에서 모델이 값이 채워진 필드(performance_reqs,
# subcontract_allowed 등)도 not_found에 같이 넣는 경우가 관찰됨 — "명시적으로 부재가
# 확인된 필드"라는 의도보다 느슨하게(단순히 문서에서 언급된 필드처럼) 해석하는 듯하다.
# 구조 검증(REQUIRED_FIELDS/enum/타입)은 통과하지만 의미가 새고 있어, not_found를
# 하류(SQL 적재·자격 판정 매칭)에서 신뢰도 있게 쓰려면 프롬프트 문구 강화나
# "not_found에 있는 필드는 실제로 null이어야 한다" 같은 교차 검증 추가를 고려할 것.
REQUIRED_FIELDS = tuple(SCHEMA.keys())

_ENUM_FIELDS = {
    "company_size_limit": {"sme_only", "small_only", "no_large", "no_conglomerate", "none", None},
    "region_limit_type": {"hq_location", "none", None},
    "award_cutline_type": {"score", "rate", None},
}

_BOOL_FIELDS = ("direct_production_req", "credit_rating_req", "joint_venture_allowed", "subcontract_allowed")

_LIST_FIELDS = (
    "required_licenses", "item_codes", "region_limit_names",
    "performance_reqs", "capacity_reqs", "personnel_reqs",
    "required_certs", "evidence", "not_found",
)


def _extract_json_text(raw: str) -> str:
    """마크다운 코드블록/설명이 섞여 와도 JSON 본문만 뽑아낸다."""
    raw = raw.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part[:4].lower() == "json":
                part = part[4:].strip()
            if part.startswith("{"):
                return part
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        return match.group()
    return raw


def parse_and_validate(raw_content: str) -> dict:
    """LLM 응답 문자열을 JSON으로 파싱하고 스키마(필드 존재/타입/enum)를 검증한다."""
    try:
        data = json.loads(raw_content.strip())
    except json.JSONDecodeError:
        data = json.loads(_extract_json_text(raw_content))

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(f"LLM 응답에 필드 누락: {missing}")

    for field, allowed in _ENUM_FIELDS.items():
        if data[field] not in allowed:
            raise ValueError(f"{field} 값이 스키마 밖: {data[field]!r} (허용: {allowed})")

    for field in _BOOL_FIELDS:
        if data[field] is not None and not isinstance(data[field], bool):
            raise ValueError(f"{field}는 bool|null이어야 함: {type(data[field])}")

    for field in _LIST_FIELDS:
        if data[field] is not None and not isinstance(data[field], list):
            raise ValueError(f"{field}는 list|null이어야 함: {type(data[field])}")

    return data
