# -*- coding: utf-8 -*-
"""자격요건(qualification) 도메인 모델.

bid_table의 자격 플랫 컬럼들을 중첩 객체로 제시한 '뷰'다. DB에 별도 테이블이나
JSONB 단일 컬럼으로 존재하지 않는다 — services 레이어에서 플랫 ORM 행을 읽어 조립한다.

전 필드 Optional인 이유: 값이 LLM 추출 결과라 문서에 언급이 없으면 null이 정상이다.
qual_status='merged'는 "첨부를 다 처리했다"는 뜻이지 "자격 18필드가 다 찼다"는
뜻이 아니다. 여기서 타입을 조이면 데이터 한 건 때문에 상세 조회가 500 난다.

이 모듈은 추출 파이프라인과 공유하는 도메인 어휘다(스키마 단일화). 프론트 응답
포맷(total/page 등)은 여기가 아니라 api/v1/schemas에 둔다.
"""
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.domain.enums import AwardCutlineType, CompanySizeLimit, RegionLimitType

# Pydantic v2는 Decimal을 JSON '문자열'("85.000")로 직렬화한다 — 프론트 명세는
# 숫자(85)라 타입이 어긋난다. 값은 그대로 두고(가공 X, 정밀도 손실 없음) JSON
# 출력 타입만 숫자로 바꾼다. when_used='json'이라 model_dump()는 Decimal 유지.
DecimalAsNumber = Annotated[
    Decimal, PlainSerializer(float, return_type=float, when_used="json")
]


class _JsonbItem(BaseModel):
    """JSONB 배열 원소 공통 설정.

    extra='ignore': JSONB는 DB가 스키마를 강제하지 않아 과거 파이프라인 버전이 남긴
    여분 키가 섞여 있을 수 있다. 조회 API가 그걸로 실패하면 안 되므로 무시한다.
    """
    model_config = ConfigDict(extra="ignore")


class RequiredLicense(_JsonbItem):
    """필수 면허/등록. or_group이 같으면 OR(하나만 충족), 다르면 AND(전부 필요)."""
    or_group: int | None = None
    name_raw: str | None = None
    code: str | None = None


class PerformanceReq(_JsonbItem):
    category: str | None = None
    basis: str | None = None
    value: float | None = None
    unit: str | None = None
    scope_raw: str | None = None


class PersonnelReq(_JsonbItem):
    field: str | None = None
    grade: str | None = None
    count: int | None = None


class Qualification(BaseModel):
    """상세 응답의 qualification 블록(프론트 명세 15필드).

    DB에는 item_codes/region_basis/capacity_reqs도 있으나 프론트 명세 예시에 없어
    이번 응답에서는 제외한다(필드 추가는 하위호환, 제거는 breaking이므로 빼고 시작).
    필요해지면 아래에 추가한다.
    """
    # from_attributes: 플랫 ORM 행(Bid)에서 자격 컬럼들을 직접 읽어 조립한다.
    model_config = ConfigDict(extra="ignore", from_attributes=True)

    company_size_limit: CompanySizeLimit | None = None
    region_limit_type: RegionLimitType | None = None
    region_limit_names: list[str] | None = None
    required_licenses: list[RequiredLicense] | None = None
    performance_reqs: list[PerformanceReq] | None = None
    required_certs: list[str] | None = None
    personnel_reqs: list[PersonnelReq] | None = None
    direct_production_req: bool | None = None
    credit_rating_req: bool | None = None
    joint_venture_allowed: bool | None = None
    subcontract_allowed: bool | None = None
    # NUMERIC 컬럼 → psycopg가 Decimal로 반환. 내부는 Decimal 유지, JSON 출력만
    # 숫자(프론트 명세 타입)로 직렬화한다(DecimalAsNumber).
    tech_weight: DecimalAsNumber | None = None
    price_weight: DecimalAsNumber | None = None
    award_cutline_type: AwardCutlineType | None = None
    award_cutline_value: DecimalAsNumber | None = None
