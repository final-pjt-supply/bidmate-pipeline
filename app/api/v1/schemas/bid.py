# -*- coding: utf-8 -*-
"""프론트 응답 계약(DTO). 이번 프론트 명세 전용이라 domain이 아니라 여기 둔다
(명세가 바뀌어도 파이프라인이 흔들리지 않게 분리).

원본값만 내린다 — D-day 계산·코드 한글변환·금액 포맷은 프론트 담당. 필드명은
snake_case(DB와 동일). match_score/match는 에이전트 완성 전까지 항상 null.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import BidCategory
from app.domain.qualification import Qualification


class BidListItem(BaseModel):
    """목록 아이템. qualification 미포함(상세에만)."""
    model_config = ConfigDict(from_attributes=True)

    bid_id: str
    bid_ntce_nm: str | None = None
    dminstt_nm: str | None = None
    bid_category: BidCategory
    sucsfbid_mthd_nm: str | None = None
    bid_clse_dt: datetime | None = None
    bdgt_amt: int | None = None
    bid_prtcpt_lmt_yn: bool | None = None
    match_score: float | None = None   # 에이전트 완성 전까지 항상 null


class BidListResponse(BaseModel):
    total: int = Field(description="필터 적용 후 전체 건수")
    page: int
    page_size: int
    items: list[BidListItem]
    # 0건 또는 범위 밖 page는 200 + items:[] (에러 아님)


class BidDetail(BaseModel):
    """상세. qualification은 domain 모델을 그대로 재사용(서비스가 조립해 채움)."""
    model_config = ConfigDict(from_attributes=True)

    bid_id: str
    bid_ntce_nm: str | None = None
    dminstt_nm: str | None = None
    ntce_instt_nm: str | None = None
    bid_category: BidCategory
    cntrct_cncls_mthd_nm: str | None = None
    sucsfbid_mthd_nm: str | None = None
    bid_methd_nm: str | None = None
    bid_prtcpt_lmt_yn: bool | None = None
    presmpt_prce: int | None = None
    bdgt_amt: int | None = None
    bid_ntce_dt: datetime | None = None
    bid_clse_dt: datetime | None = None
    bid_qlfct_rgst_dt: datetime | None = None
    openg_dt: datetime | None = None
    bid_ntce_dtl_url: str | None = None
    # 서비스가 플랫 행에서 조립해 채운다. from_attributes로 model_validate(row) 할 때
    # ORM 행엔 'qualification' 속성이 없어 빈 기본값이 들어가고, 직후 서비스가 덮어쓴다.
    qualification: Qualification = Field(default_factory=Qualification)
    match: None = None   # 에이전트 완성 후 다른 팀원이 채움
