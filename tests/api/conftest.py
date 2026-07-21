# -*- coding: utf-8 -*-
"""API/서비스 테스트 공용 픽스처.

DB 없이(오프라인) 계약을 고정한다 — repository의 실제 SQL은 별도 통합 테스트(로컬
docker-compose Postgres)의 몫이고, 여기서는 라우터↔서비스의 정책/조립/에러 매핑만
in-memory FakeBidRepository로 검증한다.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_bid_service
from app.infra.db.models.bid import Bid
from app.main import app
from app.services.bid_service import BidService


def make_bid(**overrides) -> Bid:
    """merged 기본값을 가진 bid_table 행. 필요한 필드만 overrides로 덮어쓴다."""
    base = dict(
        bid_ntce_no="20260714123",
        bid_ntce_ord="00",
        bid_id="20260714123_00",
        bid_category="servc",
        bid_ntce_nm="테스트 공고",
        dminstt_nm="성남시청",
        ntce_instt_nm="조달청 경기지방조달청",
        sucsfbid_mthd_nm="협상에 의한 계약",
        # 기본은 확실한 미래(마감 필터 통과) — 벽시계에 의존하지 않게 멀리 둔다.
        bid_clse_dt=datetime(2027, 1, 1, 18, 0, 0),
        bid_ntce_dt=datetime(2026, 7, 14, 10, 0, 0),
        bdgt_amt=819_000_000,
        bid_prtcpt_lmt_yn=True,
        qual_status="merged",
    )
    base.update(overrides)
    return Bid(**base)


class FakeBidRepository:
    """BidRepository의 in-memory 대역. merged 게이트/필터/정렬/페이징을 파이썬으로
    미러링해 서비스 정책을 검증 가능하게 한다(실제 SQL 동등성은 통합 테스트가 담당)."""

    def __init__(self, rows: list[Bid]):
        # 저장 시점에 merged 게이트를 적용 — repository 불변식(비-merged 미노출) 반영.
        self._rows = [r for r in rows if r.qual_status == "merged"]

    def _filtered(self, category, ntce_dt_from, ntce_dt_to, clse_after) -> list[Bid]:
        out = self._rows
        if category is not None:
            out = [r for r in out if r.bid_category == category]
        if ntce_dt_from is not None:
            out = [r for r in out if r.bid_ntce_dt is not None and r.bid_ntce_dt >= ntce_dt_from]
        if ntce_dt_to is not None:
            out = [r for r in out if r.bid_ntce_dt is not None and r.bid_ntce_dt < ntce_dt_to]
        # 마감 지난 것 제외, NULL은 남김(repository와 동일).
        if clse_after is not None:
            out = [r for r in out if r.bid_clse_dt is None or r.bid_clse_dt >= clse_after]
        return out

    def count(self, *, category, ntce_dt_from, ntce_dt_to, clse_after=None) -> int:
        return len(self._filtered(category, ntce_dt_from, ntce_dt_to, clse_after))

    def list_page(self, *, category, ntce_dt_from, ntce_dt_to, limit, offset, clse_after=None) -> list[Bid]:
        rows = self._filtered(category, ntce_dt_from, ntce_dt_to, clse_after)
        # bid_clse_dt ASC NULLS LAST, then bid_id ASC (repository와 동일한 정렬).
        rows = sorted(
            rows,
            key=lambda r: (r.bid_clse_dt is None, r.bid_clse_dt or datetime.max, r.bid_id),
        )
        return rows[offset : offset + limit]

    def get_by_bid_id(self, bid_id: str) -> Bid | None:
        return next((r for r in self._rows if r.bid_id == bid_id), None)


@pytest.fixture
def client_with_rows():
    """rows를 넣으면 그 데이터로 뜬 TestClient를 돌려주는 팩토리."""
    def _make(rows: list[Bid]) -> TestClient:
        service = BidService(FakeBidRepository(rows))
        app.dependency_overrides[get_bid_service] = lambda: service
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()
