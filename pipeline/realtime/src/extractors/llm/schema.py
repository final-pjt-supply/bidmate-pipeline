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
        "type은 코드 체계 이름(예: '세부품명번호', '업종코드' 같은 분류 체계 이름), code는 문서에 실제로 적힌 코드 값."
    ),
    "region_limit_type": "str|null — 지역 제한 종류. 'hq_location'(본점 소재지 제한) 또는 'none'(제한 없음).",
    "region_limit_names": "list[str]|null — 제한 지역명. 문서에 실제로 적힌 지역명 그대로(예: [\"<지역명>\"] 형식).",
    "region_basis": "str|null — 지역 제한 판단 기준. 문서에 실제로 적힌 기준일/기간 설명 그대로(예: '<기준일/기간 설명>' 형식).",
    "performance_reqs": (
        "list[{category:str, basis:str, value:number, unit:str, scope_raw:str}]|null — 실적 요건. "
        "category=실적 유형, basis=인정 기준(예: '<기간>'), "
        "value/unit=문서에 실제로 적힌 최소 실적 수치와 단위(예: <금액>/'<단위>'), scope_raw=원문 그대로의 범위 설명."
    ),
    "capacity_reqs": "list[{name:str, value:number, unit:str}]|null — 보유 설비·시설·생산능력 요건.",
    "personnel_reqs": (
        "list[{field:str, grade:str, count:int}]|null — 필수 기술인력. "
        "field=전문분야, grade=등급(고급/중급/초급 등), count=인원수."
    ),
    "required_certs": "list[str]|null — 필수 인증. 문서에 실제로 적힌 인증명 그대로(예: [\"<인증명>\"] 형식).",
    "award_cutline_type": (
        "str|null — 낙찰 커트라인 방식. "
        "'score'=적격심사 등 점수제 통과 점수(예: '종합평점 95점 이상'), "
        "'rate'=낙찰하한율 등 비율제(예: '낙찰하한율 87.745% 이상'), "
        "'lowest_price'=최저가낙찰(예: '예정가격 이하 최저가 입찰자', '규격적격자 중 최저가 입찰자' — "
        "점수·비율 커트라인 자체가 없는 방식). 셋 중 하나."
    ),
    "award_cutline_value": (
        "number|null — award_cutline_type에 대응하는 수치(점수 또는 %). "
        "award_cutline_type이 'lowest_price'이면 정해진 수치가 없는 방식이므로 award_cutline_value는 null이 정상이다."
    ),
    "tech_weight": "number|null — 기술(또는 이행능력) 평가 배점 비중.",
    "price_weight": "number|null — 가격 평가 배점 비중.",
    "joint_venture_allowed": (
        "bool|null — 공동수급(컨소시엄) 허용 여부. "
        "'공동계약: 불가/불허' 또는 '공동수급을 허용하지 않음' -> false. "
        "'공동계약 가능' 또는 '공동수급 가능(공동이행/분담이행 등)' -> true. "
        "표 형태나 짧은 항목으로만 적혀 있어도(예: '공동계약: 불허' 한 줄) 반드시 반영한다."
    ),
    "subcontract_allowed": (
        "bool|null — 하도급 허용 여부. "
        "'하도급 금지/불허/승인 없이 금지' -> false, '하도급 가능/허용' -> true. "
        "명시적 언급이 없으면 null(임의로 추측하지 말 것)."
    ),
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
    "award_cutline_type": {"score", "rate", "lowest_price", None},
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


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """공백 차이를 무시하고 비교하기 위한 정규화(줄바꿈/띄어쓰기 전부 제거)."""
    return _WHITESPACE_RE.sub("", text)


def _drop_ungrounded_evidence(data: dict, document_text: str) -> int:
    """evidence.snippet이 document_text에 실제로 존재하지 않으면 그 항목을 드랍한다.

    프롬프트가 아무리 잘 짜여도 모델이 few-shot/스키마 설명문을 베껴 evidence를
    지어내는 걸 완전히 막을 수 없어서, 구조적으로 걸러내는 마지막 안전망이다.
    """
    evidence = data.get("evidence")
    if not evidence:
        return 0
    normalized_doc = _normalize(document_text)
    kept, dropped = [], 0
    for item in evidence:
        snippet = item.get("snippet", "") if isinstance(item, dict) else ""
        if snippet and _normalize(snippet) in normalized_doc:
            kept.append(item)
        else:
            dropped += 1
    data["evidence"] = kept
    return dropped


_DOMAIN_FIELDS = tuple(f for f in REQUIRED_FIELDS if f not in ("evidence", "not_found"))


def _demote_ungrounded_values(data: dict) -> int:
    """evidence가 하나도 안 남은 non-null/non-empty 필드는 값을 null로 강등한다.

    evidence 그라운딩 필터(_drop_ungrounded_evidence)는 evidence 배열만 청소하지
    최상위 값 자체는 건드리지 않는다 — 그래서 "evidence 없이 값만 우겨넣는" 유형의
    오염(예: 실제 근거 없이 award_cutline_type/value를 채우는 경우)이 그대로 남는다.
    반드시 document_text 그라운딩 검증 뒤에 호출해야 한다(드랍 후 살아남은
    evidence만 "그라운딩됨"으로 인정하기 위함).
    """
    grounded_fields = {
        item["field"]
        for item in data.get("evidence") or []
        if isinstance(item, dict) and "field" in item
    }
    demoted = 0
    for field in _DOMAIN_FIELDS:
        value = data.get(field)
        is_empty = value is None or (isinstance(value, list) and len(value) == 0)
        if not is_empty and field not in grounded_fields:
            data[field] = None
            demoted += 1
    return demoted


def parse_and_validate(raw_content: str, document_text: str | None = None) -> dict:
    """LLM 응답 문자열을 JSON으로 파싱하고 스키마(필드 존재/타입/enum)를 검증한다.

    document_text가 주어지면:
    1) evidence.snippet이 실제로 그 문서에 있는지 대조해서 없는 항목은 드랍
    2) 드랍 후에도 evidence가 하나도 안 남은 non-null 필드는 값을 null로 강등
    (값 자체가 evidence 없이 지어진 경우까지 걸러내는 이중 안전망)
    개수는 각각 data["_meta"]["dropped_evidence"] / ["demoted_values"]에 남긴다
    (document_text 없이 호출하면 이 검증들은 건너뛴다 — 기존 호출부/테스트 호환용).
    """
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

    if document_text is not None:
        dropped = _drop_ungrounded_evidence(data, document_text)
        demoted = _demote_ungrounded_values(data)
        data["_meta"] = {"dropped_evidence": dropped, "demoted_values": demoted}

    return data
