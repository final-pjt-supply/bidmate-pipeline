# -*- coding: utf-8 -*-
"""도메인 열거형. bid_table의 코드성 컬럼값을 타입으로 고정한다.

CompanySizeLimit/RegionLimitType/AwardCutlineType는 추출 파이프라인
(pipeline/realtime/src/extractors/llm/schema.py의 _ENUM_FIELDS)이 dict 리터럴로
들고 있는 것과 동일한 값 집합이다 — "스키마 단일화"의 첫 접점. 지금은 값만 맞춰두고,
추후 파이프라인이 이 enum을 참조하도록 정리하면 중복이 사라진다.
"""
from enum import Enum


class BidCategory(str, Enum):
    """업무구분. bid_table.bid_category (NOT NULL, 수집 시 결정)."""
    CNSTWK = "cnstwk"   # 공사
    SERVC = "servc"     # 용역
    THNG = "thng"       # 물품
    FRGCPT = "frgcpt"   # 외자


class QualStatus(str, Enum):
    """자격 병합 상태. DB enum이 아니라 VARCHAR + 애플리케이션 규약
    (merge/logic.py determine_qual_status가 SSOT). 조회 API는 merged만 노출한다."""
    PENDING = "pending"
    MERGED = "merged"
    PARTIAL = "partial"
    FAILED = "failed"


class CompanySizeLimit(str, Enum):
    SME_ONLY = "sme_only"
    SMALL_ONLY = "small_only"
    NO_LARGE = "no_large"
    NO_CONGLOMERATE = "no_conglomerate"
    NONE = "none"


class RegionLimitType(str, Enum):
    HQ_LOCATION = "hq_location"
    NONE = "none"


class AwardCutlineType(str, Enum):
    SCORE = "score"
    RATE = "rate"
    LOWEST_PRICE = "lowest_price"   # 이 경우 award_cutline_value=null이 정상


class BidSortKey(str, Enum):
    """목록 정렬 키.

    score는 회사별 match_score 기준이지만 현 단계에선 match_score가 전부 null이라
    deadline으로 폴백한다(services.bid_service 참조). 값 집합 자체는 프론트 계약이라
    지금 확정해둔다.
    """
    SCORE = "score"
    DEADLINE = "deadline"
