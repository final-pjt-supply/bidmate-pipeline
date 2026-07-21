# -*- coding: utf-8 -*-
"""공고 조회 유스케이스. 필터·정렬 정책과 페이지 계산, 그리고 플랫 ORM 행 →
중첩 응답 조립을 담당한다(쿼리 실행은 repository, HTTP 변환은 router).
"""
from datetime import datetime, timedelta, timezone

from app.api.v1.schemas.bid import BidDetail, BidListItem, BidListResponse
from app.domain.enums import BidCategory, BidSortKey
from app.domain.qualification import Qualification
from app.infra.db.models.bid import Bid
from app.infra.db.repositories.bid_repository import BidRepository

PAGE_SIZE = 20   # 명세상 고정
_KST = timezone(timedelta(hours=9))   # 한국 표준시(DST 없음). DB는 KST naive.


class BidNotFoundError(Exception):
    """존재하지 않거나 merged가 아닌 bid_id. 라우터가 404로 통일한다."""


class BidService:
    def __init__(self, repository: BidRepository):
        self._repo = repository

    def list_bids(
        self,
        *,
        category: BidCategory | None,
        sort: BidSortKey,
        today: bool,
        page: int,
        company_id: str | None = None,
    ) -> BidListResponse:
        """목록.

        정렬 폴백: sort=score는 회사별 match_score 기준이지만 지금은 match_score가
        전부 null이라 deadline과 동일하게 처리한다. 회사별 점수 정렬이 실제로 붙으면
        company_id로 분기하는 자리를 아래에 표시해 둔다.
        """
        # TODO(company-scoped sort): sort=score이고 company_id가 있으면
        #   repo.list_page_by_score(company_id=...)로 회사별 점수순 정렬 경로를 탄다.
        #   지금은 score/deadline 모두 deadline(마감 임박순)으로 수렴.
        _ = sort  # 현재는 정렬 키가 결과를 바꾸지 않음(폴백). 시그니처는 계약상 유지.

        ntce_from, ntce_to = self._today_range() if today else (None, None)
        category_value = category.value if category is not None else None
        # 추천 페이지는 마감 지난 공고를 노출하지 않는다(결정 E, 2026-07-21 확정).
        # 마감 없는(NULL) 공고는 남긴다(repository._apply_filters 참조).
        now_kst = datetime.now(_KST).replace(tzinfo=None)

        total = self._repo.count(
            category=category_value,
            ntce_dt_from=ntce_from,
            ntce_dt_to=ntce_to,
            clse_after=now_kst,
        )
        offset = (page - 1) * PAGE_SIZE
        rows = self._repo.list_page(
            category=category_value,
            ntce_dt_from=ntce_from,
            ntce_dt_to=ntce_to,
            limit=PAGE_SIZE,
            offset=offset,
            clse_after=now_kst,
        )
        # 범위 밖 page는 rows=[]가 자연스럽게 나온다(200 + 빈 배열, 에러 아님).
        items = [BidListItem.model_validate(r) for r in rows]
        return BidListResponse(total=total, page=page, page_size=PAGE_SIZE, items=items)

    def get_bid(self, bid_id: str, *, company_id: str | None = None) -> BidDetail:
        row = self._repo.get_by_bid_id(bid_id)
        if row is None:
            raise BidNotFoundError(bid_id)
        detail = BidDetail.model_validate(row)
        # 플랫 컬럼 → 중첩 qualification 조립(DB엔 별도 테이블/JSONB단일컬럼으로 없음).
        detail.qualification = Qualification.model_validate(row)
        return detail

    @staticmethod
    def _today_range() -> tuple[datetime, datetime]:
        """KST 오늘의 [00:00, 내일 00:00) naive 반개구간(bid_ntce_dt 비교용)."""
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        start = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
