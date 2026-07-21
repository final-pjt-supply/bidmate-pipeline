# -*- coding: utf-8 -*-
"""bid_table ORM 매핑 (읽기 전용).

db/schema/01_bid_table.sql(SSOT)을 미러링한다. 조회 API가 실제로 서빙/필터/정렬에
쓰는 컬럼만 매핑했다(전 컬럼을 옮기지 않는다 — 파이프라인 운영 컬럼은 이 앱의 관심사가
아니다). 스키마가 바뀌면 여기와 SSOT가 갈라지므로, 컬럼 주석에 SSOT 근거를 남긴다.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class Bid(Base):
    __tablename__ = "bid_table"

    # --- 식별 (PK: 복합키. bid_id는 GENERATED STORED 파생 컬럼) ---
    bid_ntce_no: Mapped[str] = mapped_column(String(40), primary_key=True)
    bid_ntce_ord: Mapped[str] = mapped_column(String(10), primary_key=True)
    # bid_id는 DB가 생성하는 값이라 앱은 읽기만 한다. split 금지(불투명 식별자).
    bid_id: Mapped[str] = mapped_column(String(60))
    bid_category: Mapped[str] = mapped_column(String(10))   # NOT NULL

    # --- 목록/상세 공용 기본 필드 (전부 nullable) ---
    bid_ntce_nm: Mapped[str | None] = mapped_column(Text)
    ntce_instt_nm: Mapped[str | None] = mapped_column(String(200))
    dminstt_nm: Mapped[str | None] = mapped_column(String(200))

    # --- 일정 (KST naive TIMESTAMP — 타임존 붙이지 말 것) ---
    bid_ntce_dt: Mapped[datetime | None] = mapped_column()        # today 필터 기준
    bid_clse_dt: Mapped[datetime | None] = mapped_column()        # deadline 정렬 기준
    openg_dt: Mapped[datetime | None] = mapped_column()
    bid_qlfct_rgst_dt: Mapped[datetime | None] = mapped_column()

    # --- 금액 ---
    presmpt_prce: Mapped[int | None] = mapped_column(BigInteger)
    bdgt_amt: Mapped[int | None] = mapped_column(BigInteger)

    # --- 방식 ---
    cntrct_cncls_mthd_nm: Mapped[str | None] = mapped_column(String(50))
    sucsfbid_mthd_nm: Mapped[str | None] = mapped_column(String(200))
    bid_methd_nm: Mapped[str | None] = mapped_column(String(50))
    bid_prtcpt_lmt_yn: Mapped[bool | None] = mapped_column(Boolean)

    # --- 링크 ---
    bid_ntce_dtl_url: Mapped[str | None] = mapped_column(Text)

    # --- 자격요건 (상세 응답 qualification 조립 원천) ---
    company_size_limit: Mapped[str | None] = mapped_column(String(20))
    direct_production_req: Mapped[bool | None] = mapped_column(Boolean)
    credit_rating_req: Mapped[bool | None] = mapped_column(Boolean)
    required_licenses: Mapped[list | None] = mapped_column(JSONB)
    region_limit_type: Mapped[str | None] = mapped_column(String(20))
    region_limit_names: Mapped[list | None] = mapped_column(JSONB)
    performance_reqs: Mapped[list | None] = mapped_column(JSONB)
    personnel_reqs: Mapped[list | None] = mapped_column(JSONB)
    required_certs: Mapped[list | None] = mapped_column(JSONB)
    award_cutline_type: Mapped[str | None] = mapped_column(String(20))
    award_cutline_value: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    tech_weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    price_weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    joint_venture_allowed: Mapped[bool | None] = mapped_column(Boolean)
    subcontract_allowed: Mapped[bool | None] = mapped_column(Boolean)

    # --- 노출 게이트 ---
    qual_status: Mapped[str | None] = mapped_column(String(20))
