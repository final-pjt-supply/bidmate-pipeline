# -*- coding: utf-8 -*-
"""LLM 프롬프트 조립: 시스템 프롬프트 + 물품/용역/공사/외자 few-shot 4종 + 본문.

필터링 없이 문서 전문을 통짜로 넣는 1차 버전이라(요청 스펙), 여기서 하는 일은
페이지 마커를 붙여 이어붙이고 few-shot·스키마와 함께 하나의 user 메시지로
조립하는 것뿐이다. 실제 판단 로직은 전부 LLM에 위임한다.
"""
import json

from extractors.llm.schema import SCHEMA

SYSTEM_PROMPT = """당신은 대한민국 공공조달(나라장터) 입찰공고 분석 전문가입니다.
입찰공고 문서 전문을 읽고 주어진 JSON 스키마에 맞춰 참가자격·낙찰기준 정보를 정확히 추출합니다.

규칙:
- 반드시 스키마에 정의된 키만 사용해 JSON 객체 하나만 출력합니다. 설명, 코드블록 표시, 그 외 텍스트는 넣지 않습니다.
- 문서에 아예 언급이 없는 항목은 null로 둡니다.
- 문서가 "해당 없음", "제한 없음" 등으로 특정 항목의 부재를 명시적으로 확인해준 경우, 그 필드 값은 null(또는 enum의 'none')로 두되 not_found 배열에 그 필드명을 추가합니다. 단순히 언급이 없을 뿐인 항목은 not_found에 넣지 않습니다.
- evidence는 null이 아닌 값을 채운 필드에 대해서만 작성합니다. null로 남긴 필드의 evidence는 만들지 않습니다.
- evidence.page는 문서에 표시된 "[페이지 N]" 마커의 N을 그대로 사용합니다.
- 추측하지 말고 문서에 명시된 내용만 반영합니다."""

_FEW_SHOT_GOODS = """[예시 1: 물품]
[입력 공고문]
[페이지 2]
2. 입찰참가자격
가. 중소기업제품 구매촉진 및 판로지원에 관한 법률에 따른 중소기업자로서 직접생산확인증명서를 보유한 업체
나. 소프트웨어산업 진흥법에 따른 소프트웨어사업자 신고필증을 보유한 업체(세부품명번호: 4321000001)

[페이지 5]
4. 물품 납품 및 검사
납품기한: 계약체결일로부터 30일 이내

5. 낙찰자 결정
적격심사 낙찰하한율 88% 이상인 자 중 최저가 입찰자

[출력 JSON]
{
  "company_size_limit": "sme_only",
  "direct_production_req": true,
  "credit_rating_req": null,
  "required_licenses": [{"or_group": 1, "name_raw": "소프트웨어사업자 신고필증", "code": null}],
  "item_codes": [{"type": "세부품명번호", "code": "4321000001"}],
  "region_limit_type": null,
  "region_limit_names": null,
  "region_basis": null,
  "performance_reqs": null,
  "capacity_reqs": null,
  "personnel_reqs": null,
  "required_certs": null,
  "award_cutline_type": "rate",
  "award_cutline_value": 88.0,
  "tech_weight": null,
  "price_weight": null,
  "joint_venture_allowed": null,
  "subcontract_allowed": null,
  "evidence": [
    {"field": "company_size_limit", "page": 2, "snippet": "중소기업자로서 직접생산확인증명서를 보유한 업체"},
    {"field": "direct_production_req", "page": 2, "snippet": "직접생산확인증명서를 보유한 업체"},
    {"field": "required_licenses", "page": 2, "snippet": "소프트웨어사업자 신고필증을 보유한 업체"},
    {"field": "item_codes", "page": 2, "snippet": "세부품명번호: 4321000001"},
    {"field": "award_cutline_type", "page": 5, "snippet": "낙찰하한율 88% 이상인 자 중 최저가 입찰자"},
    {"field": "award_cutline_value", "page": 5, "snippet": "낙찰하한율 88% 이상"}
  ],
  "not_found": []
}"""

_FEW_SHOT_SERVICE = """[예시 2: 용역]
[입력 공고문]
[페이지 2]
2. 용역수행능력 평가기준 및 참가자격
가. 「소프트웨어 진흥법」제2조에 따른 소프트웨어사업자
나. 최근 3년간 정보시스템 구축 용역 실적(단일계약 기준 3억원 이상) 보유 업체
다. 정보보호산업진흥법에 따른 정보보호전문서비스기업 인증 보유 업체(가점 아님, 필수)
라. 지역제한 없음

[페이지 5]
5. 낙찰자 결정 방법: 협상에 의한 계약(2단계 경쟁)
기술능력평가 배점: 90점, 입찰가격평가 배점: 10점

[출력 JSON]
{
  "company_size_limit": null,
  "direct_production_req": null,
  "credit_rating_req": null,
  "required_licenses": [{"or_group": 1, "name_raw": "소프트웨어사업자", "code": null}],
  "item_codes": null,
  "region_limit_type": "none",
  "region_limit_names": null,
  "region_basis": null,
  "performance_reqs": [
    {"category": "정보시스템 구축 용역", "basis": "최근 3년, 단일계약 기준", "value": 300000000, "unit": "원", "scope_raw": "단일계약 기준 3억원 이상"}
  ],
  "capacity_reqs": null,
  "personnel_reqs": null,
  "required_certs": ["정보보호전문서비스기업 인증"],
  "award_cutline_type": null,
  "award_cutline_value": null,
  "tech_weight": 90.0,
  "price_weight": 10.0,
  "joint_venture_allowed": null,
  "subcontract_allowed": null,
  "evidence": [
    {"field": "required_licenses", "page": 2, "snippet": "소프트웨어 진흥법 제2조에 따른 소프트웨어사업자"},
    {"field": "performance_reqs", "page": 2, "snippet": "최근 3년간 정보시스템 구축 용역 실적(단일계약 기준 3억원 이상) 보유 업체"},
    {"field": "required_certs", "page": 2, "snippet": "정보보호전문서비스기업 인증 보유 업체(가점 아님, 필수)"},
    {"field": "region_limit_type", "page": 2, "snippet": "지역제한 없음"},
    {"field": "tech_weight", "page": 5, "snippet": "기술능력평가 배점: 90점"},
    {"field": "price_weight", "page": 5, "snippet": "입찰가격평가 배점: 10점"}
  ],
  "not_found": ["region_limit_type"]
}"""

_FEW_SHOT_CONSTRUCTION = """[예시 3: 공사]
[입력 공고문]
[페이지 2]
2. 입찰참가자격
가. 전기공사업법 제4조에 의한 전기공사업 등록업체
나. 본 공사는 10억원 미만으로 대기업(상호출자제한기업집단) 전기공사업자는 참여할 수 없습니다.
다. 철도신호분야(구분:시공) 필수기술인력(중급 1인, 초급 1인 이상)을 보유한 업체
라. 본점 소재지가 전북특별자치도에 입찰공고일 기준 90일 이상 소재한 업체
마. 시공실적 제한: 없음

[페이지 5]
5. 낙찰자 결정
적격심사하여 종합평점이 95점 이상인 자를 낙찰예정자로 결정합니다.
공사기간: 착공지정일로부터 40일간
공동수급 가능(공동이행방식), 하도급은 원칙적으로 금지

[출력 JSON]
{
  "company_size_limit": "no_conglomerate",
  "direct_production_req": null,
  "credit_rating_req": null,
  "required_licenses": [{"or_group": 1, "name_raw": "전기공사업 등록", "code": null}],
  "item_codes": null,
  "region_limit_type": "hq_location",
  "region_limit_names": ["전북특별자치도"],
  "region_basis": "입찰공고일 기준 90일 이상 계속 소재",
  "performance_reqs": null,
  "capacity_reqs": null,
  "personnel_reqs": [
    {"field": "철도신호분야(시공)", "grade": "중급", "count": 1},
    {"field": "철도신호분야(시공)", "grade": "초급", "count": 1}
  ],
  "required_certs": null,
  "award_cutline_type": "score",
  "award_cutline_value": 95.0,
  "tech_weight": null,
  "price_weight": null,
  "joint_venture_allowed": true,
  "subcontract_allowed": false,
  "evidence": [
    {"field": "company_size_limit", "page": 2, "snippet": "대기업(상호출자제한기업집단) 전기공사업자는 참여할 수 없습니다"},
    {"field": "required_licenses", "page": 2, "snippet": "전기공사업법 제4조에 의한 전기공사업 등록업체"},
    {"field": "region_limit_type", "page": 2, "snippet": "본점 소재지가 전북특별자치도에 입찰공고일 기준 90일 이상 소재한 업체"},
    {"field": "region_limit_names", "page": 2, "snippet": "전북특별자치도"},
    {"field": "region_basis", "page": 2, "snippet": "입찰공고일 기준 90일 이상 소재한 업체"},
    {"field": "personnel_reqs", "page": 2, "snippet": "철도신호분야(구분:시공) 필수기술인력(중급 1인, 초급 1인 이상)"},
    {"field": "award_cutline_type", "page": 5, "snippet": "종합평점이 95점 이상인 자를 낙찰예정자로 결정"},
    {"field": "award_cutline_value", "page": 5, "snippet": "종합평점이 95점 이상"},
    {"field": "joint_venture_allowed", "page": 5, "snippet": "공동수급 가능(공동이행방식)"},
    {"field": "subcontract_allowed", "page": 5, "snippet": "하도급은 원칙적으로 금지"}
  ],
  "not_found": ["performance_reqs"]
}"""

_FEW_SHOT_FOREIGN = """[예시 4: 외자]
[입력 공고문]
[페이지 2]
2. 입찰참가자격 (국제입찰)
가. WTO정부조달협정 등에 따라 외국업체 포함 모든 국내외 업체 참가 가능(외국업체 참가 제한 없음)
나. 대외무역법에 따른 무역업 고유번호를 보유한 업체
다. 신용평가등급 B등급(또는 이에 상응하는 등급) 이상 보유 업체
라. 원산지증명서(FTA 협정관세 적용대상 물품의 경우) 제출 대상

[페이지 5]
5. 낙찰자 결정: 최저가 낙찰제

[출력 JSON]
{
  "company_size_limit": "none",
  "direct_production_req": null,
  "credit_rating_req": true,
  "required_licenses": [{"or_group": 1, "name_raw": "무역업 고유번호", "code": null}],
  "item_codes": null,
  "region_limit_type": null,
  "region_limit_names": null,
  "region_basis": null,
  "performance_reqs": null,
  "capacity_reqs": null,
  "personnel_reqs": null,
  "required_certs": null,
  "award_cutline_type": null,
  "award_cutline_value": null,
  "tech_weight": null,
  "price_weight": null,
  "joint_venture_allowed": null,
  "subcontract_allowed": null,
  "evidence": [
    {"field": "company_size_limit", "page": 2, "snippet": "외국업체 포함 모든 국내외 업체 참가 가능(외국업체 참가 제한 없음)"},
    {"field": "required_licenses", "page": 2, "snippet": "대외무역법에 따른 무역업 고유번호를 보유한 업체"},
    {"field": "credit_rating_req", "page": 2, "snippet": "신용평가등급 B등급(또는 이에 상응하는 등급) 이상 보유 업체"}
  ],
  "not_found": ["company_size_limit"]
}"""

_FEW_SHOTS = (_FEW_SHOT_GOODS, _FEW_SHOT_SERVICE, _FEW_SHOT_CONSTRUCTION, _FEW_SHOT_FOREIGN)


def build_document_text(pages: list[dict]) -> str:
    """text_extracted의 pages([{page,text}])를 [페이지 N] 마커로 이어붙인 통짜 텍스트로 만든다.

    evidence.page가 이 마커 번호를 그대로 참조하도록 SYSTEM_PROMPT에서 지시한다.
    """
    return "\n\n".join(f"[페이지 {p['page']}]\n{p['text']}" for p in pages)


def build_user_prompt(document_text: str) -> str:
    few_shots_block = "\n\n".join(_FEW_SHOTS)
    schema_block = json.dumps(SCHEMA, ensure_ascii=False, indent=2)
    return f"""아래는 참고용 예시 4개(물품/용역/공사/외자)입니다. 형식과 판단 기준을 참고하세요.

{few_shots_block}

스키마:
{schema_block}

이제 아래 실제 입찰공고문에서 위 스키마대로 JSON을 추출하세요.

[분석 대상 공고문]
{document_text}

JSON:"""
