# -*- coding: utf-8 -*-
"""bid_table 조회 쿼리. 쿼리 실행만 담당하고 비즈니스 판단(정렬 폴백/페이지 계산)은
services에 둔다.

노출 게이트(qual_status='merged')는 모든 조회 메서드에 고정으로 박는다 — API가
pending/partial/failed를 흘리면 안 되는 건 도메인 불변식이라, 호출부 실수로 빠질 수
없게 여기서 강제한다.
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import QualStatus
from app.infra.db.models.bid import Bid

_MERGED = QualStatus.MERGED.value


class BidRepository:
    def __init__(self, session: Session):
        self._session = session

    def _merged_base(self):
        """merged 게이트가 걸린 공통 베이스 select."""
        return select(Bid).where(Bid.qual_status == _MERGED)

    def count(
        self,
        *,
        category: str | None,
        ntce_dt_from: datetime | None,
        ntce_dt_to: datetime | None,
    ) -> int:
        """필터 적용 후 총 건수(응답 total)."""
        stmt = select(func.count()).select_from(Bid).where(Bid.qual_status == _MERGED)
        stmt = self._apply_filters(stmt, category, ntce_dt_from, ntce_dt_to)
        return self._session.execute(stmt).scalar_one()

    def list_page(
        self,
        *,
        category: str | None,
        ntce_dt_from: datetime | None,
        ntce_dt_to: datetime | None,
        limit: int,
        offset: int,
    ) -> list[Bid]:
        """마감 임박순(deadline) 한 페이지.

        정렬은 bid_clse_dt ASC + NULLS LAST — 마감일 없는 공고가 메인 홈 상단을
        먹지 않게 한다. 안정 정렬을 위해 bid_id로 tie-break.

        # TODO(company-scoped sort): sort=score가 실제 동작하게 되면 여기가 아니라
        #   호출부(service)에서 current_user.company_id로 match_results를 조인해
        #   회사별 점수순으로 정렬하는 별도 경로가 붙는다. 그 조인 쿼리를 이 클래스에
        #   list_page_by_score(company_id, ...)로 추가하고, service가 분기한다.
        """
        stmt = self._merged_base()
        stmt = self._apply_filters(stmt, category, ntce_dt_from, ntce_dt_to)
        stmt = (
            stmt.order_by(Bid.bid_clse_dt.asc().nulls_last(), Bid.bid_id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(stmt).scalars().all())

    def get_by_bid_id(self, bid_id: str) -> Bid | None:
        """merged인 단건. 존재하지 않거나 merged가 아니면 None(호출부가 404로 통일)."""
        stmt = self._merged_base().where(Bid.bid_id == bid_id)
        return self._session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def _apply_filters(stmt, category, ntce_dt_from, ntce_dt_to):
        if category is not None:
            stmt = stmt.where(Bid.bid_category == category)
        # today: 공고게시일(bid_ntce_dt) 기준 KST 오늘 [from, to) 반개구간.
        if ntce_dt_from is not None:
            stmt = stmt.where(Bid.bid_ntce_dt >= ntce_dt_from)
        if ntce_dt_to is not None:
            stmt = stmt.where(Bid.bid_ntce_dt < ntce_dt_to)
        return stmt
