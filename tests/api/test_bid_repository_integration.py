# -*- coding: utf-8 -*-
"""BidRepository 통합 테스트 — 실제 Postgres SQL 검증.

계약 테스트(test_bids_api.py)는 in-memory 대역이라 SQL을 타지 않는다. 여기서는
로컬 docker-compose Postgres(db/docker-compose.yml, POSTGRES_* 기본값)에 붙어
대역이 못 잡는 지점을 검증한다:
  - qual_status='merged' 게이트가 진짜 WHERE에 걸리는지
  - ORDER BY ... NULLS LAST가 Postgres에서 실제로 그렇게 도는지
  - JSONB 왕복(list[dict])·NUMERIC(Decimal)·KST naive TIMESTAMP 타입 보존
  - bid_id(GENERATED STORED) 조회
  - category 필터·페이징 offset

DB가 없으면 전체 스킵(CI/로컬에 Postgres 없어도 스위트가 깨지지 않음).
실행: `cd db && docker compose up -d` 후 `pytest tests/api/test_bid_repository_integration.py`.
모든 삽입은 트랜잭션 안에서만 일어나고 teardown에서 롤백한다(DB를 더럽히지 않음).
"""
import json
import os
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.infra.db.repositories.bid_repository import BidRepository

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PROD_DB_NAMES = {"bidmate"}   # 운영 RDS DB명(template.yaml). 쓰기 테스트 절대 금지.


@pytest.fixture
def session() -> Session:
    """트랜잭션 격리 세션. 붙을 수 없거나 bid_table이 없으면 skip.

    ⚠ 이 테스트는 bid_table에 INSERT한다(끝에 롤백하지만). 운영 DB에 실수로 쓰기가
    나가지 않도록 두 겹으로 막는다:
      1) 대상 호스트가 로컬이 아니면 차단
      2) 대상 DB명이 운영 DB(bidmate)면 차단 — SSH 터널은 RDS를 localhost:5433으로
         보이게 해 host 검사를 통과하므로, DB명 검사가 실질 방어선이다.
    정말 원격/운영을 향해 돌려야 하면(권장 안 함) ALLOW_REMOTE_DB_TESTS=1로 opt-in.
    """
    settings = get_settings()
    opt_in = os.environ.get("ALLOW_REMOTE_DB_TESTS") == "1"
    if not opt_in and settings.postgres_host not in _LOCAL_HOSTS:
        pytest.skip(f"원격 DB({settings.postgres_host}) 쓰기 통합테스트 차단 — 로컬 docker 권장.")
    if not opt_in and settings.postgres_db in _PROD_DB_NAMES:
        pytest.skip(
            f"운영 DB({settings.postgres_db}) 쓰기 통합테스트 차단 — 로컬 docker 전용. "
            "(SSH 터널이라 host는 localhost로 보여도 실제 대상은 운영 RDS)"
        )

    from app.infra.db.session import engine

    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("Postgres 미기동 — `cd db && docker compose up -d` 후 재실행")

    # begin()을 먼저 연다 — execute()를 먼저 하면 SQLAlchemy가 트랜잭션을 자동
    # 시작(autobegin)해 뒤이은 begin()이 충돌하고, 그 예외로 커넥션이 반환되지
    # 않아 pool이 고갈된다. 테이블 존재 체크는 이 트랜잭션 안에서 한다.
    trans = connection.begin()
    try:
        if connection.execute(text("SELECT to_regclass('public.bid_table')")).scalar() is None:
            pytest.skip("bid_table 없음 — db/schema 적용 필요(docker compose up이 초기화)")
        sess = Session(bind=connection)
        try:
            yield sess
        finally:
            sess.close()
    finally:
        trans.rollback()   # 삽입 전부 되돌림(skip 경로 포함)
        connection.close()


_COLUMNS = (
    "bid_ntce_no", "bid_ntce_ord", "bid_category", "qual_status",
    "bid_ntce_nm", "dminstt_nm", "bid_clse_dt", "bid_ntce_dt",
    "bdgt_amt", "bid_prtcpt_lmt_yn", "required_licenses", "required_certs",
    "region_limit_names", "tech_weight", "award_cutline_value",
)
_JSONB_COLS = {"required_licenses", "required_certs", "region_limit_names"}


def _insert(sess: Session, **vals) -> str:
    """bid_table 한 행 삽입. bid_id는 GENERATED라 넣지 않는다. 삽입한 bid_id 반환."""
    row = {c: vals.get(c) for c in _COLUMNS}
    row["bid_ntce_no"] = vals["bid_ntce_no"]
    row["bid_ntce_ord"] = vals.get("bid_ntce_ord", "00")
    row["bid_category"] = vals.get("bid_category", "servc")
    row["qual_status"] = vals.get("qual_status", "merged")
    # JSONB 컬럼은 문자열로 바인딩 후 ::jsonb 캐스팅.
    binds = {}
    cols_sql, vals_sql = [], []
    for c in _COLUMNS:
        cols_sql.append(c)
        if c in _JSONB_COLS and row[c] is not None:
            binds[c] = json.dumps(row[c])
            # `:param::jsonb`는 text()가 `::`를 바인드파라미터로 오해할 수 있어
            # 명시적 CAST를 쓴다.
            vals_sql.append(f"CAST(:{c} AS JSONB)")
        else:
            binds[c] = row[c]
            vals_sql.append(f":{c}")
    sess.execute(
        text(f"INSERT INTO bid_table ({', '.join(cols_sql)}) VALUES ({', '.join(vals_sql)})"),
        binds,
    )
    return f"{row['bid_ntce_no']}_{row['bid_ntce_ord']}"


def test_merged_gate_in_real_sql(session):
    _insert(session, bid_ntce_no="mg_a", qual_status="merged")
    _insert(session, bid_ntce_no="mg_b", qual_status="pending")
    _insert(session, bid_ntce_no="mg_c", qual_status="partial")
    repo = BidRepository(session)

    assert repo.count(category=None, ntce_dt_from=None, ntce_dt_to=None) == 1
    rows = repo.list_page(category=None, ntce_dt_from=None, ntce_dt_to=None, limit=20, offset=0)
    assert [r.bid_id for r in rows] == ["mg_a_00"]
    assert repo.get_by_bid_id("mg_b_00") is None   # 비-merged는 None → 라우터 404


def test_order_by_nulls_last_in_postgres(session):
    _insert(session, bid_ntce_no="late", bid_clse_dt=datetime(2026, 8, 1))
    _insert(session, bid_ntce_no="null", bid_clse_dt=None)
    _insert(session, bid_ntce_no="soon", bid_clse_dt=datetime(2026, 7, 22))
    repo = BidRepository(session)
    rows = repo.list_page(category=None, ntce_dt_from=None, ntce_dt_to=None, limit=20, offset=0)
    assert [r.bid_id for r in rows] == ["soon_00", "late_00", "null_00"]


def test_jsonb_numeric_timestamp_roundtrip(session):
    _insert(
        session,
        bid_ntce_no="rt",
        required_licenses=[{"or_group": 1, "name_raw": "소프트웨어사업자", "code": "SW01"}],
        required_certs=["ISO 27001", "GS인증"],
        region_limit_names=["경기도"],
        tech_weight=80,
        award_cutline_value=85,
        bid_clse_dt=datetime(2026, 7, 26, 18, 0, 0),
    )
    row = BidRepository(session).get_by_bid_id("rt_00")
    assert row is not None
    assert row.required_licenses == [{"or_group": 1, "name_raw": "소프트웨어사업자", "code": "SW01"}]
    assert row.required_certs == ["ISO 27001", "GS인증"]
    assert isinstance(row.tech_weight, Decimal) and row.tech_weight == Decimal("80")
    assert isinstance(row.award_cutline_value, Decimal)
    # KST naive — tzinfo가 붙어 오면 안 된다.
    assert row.bid_clse_dt == datetime(2026, 7, 26, 18, 0, 0)
    assert row.bid_clse_dt.tzinfo is None


def test_category_filter_and_paging(session):
    for i in range(3):
        _insert(session, bid_ntce_no=f"srv{i}", bid_category="servc",
                bid_clse_dt=datetime(2026, 7, 20 + i))
    _insert(session, bid_ntce_no="cst", bid_category="cnstwk",
            bid_clse_dt=datetime(2026, 7, 19))
    repo = BidRepository(session)

    assert repo.count(category="servc", ntce_dt_from=None, ntce_dt_to=None) == 3
    page1 = repo.list_page(category="servc", ntce_dt_from=None, ntce_dt_to=None, limit=2, offset=0)
    page2 = repo.list_page(category="servc", ntce_dt_from=None, ntce_dt_to=None, limit=2, offset=2)
    assert [r.bid_id for r in page1] == ["srv0_00", "srv1_00"]
    assert [r.bid_id for r in page2] == ["srv2_00"]


def test_clse_after_excludes_expired_keeps_null(session):
    _insert(session, bid_ntce_no="past", bid_clse_dt=datetime(2020, 1, 1))
    _insert(session, bid_ntce_no="future", bid_clse_dt=datetime(2099, 1, 1))
    _insert(session, bid_ntce_no="nodl", bid_clse_dt=None)
    repo = BidRepository(session)
    cutoff = datetime(2026, 7, 21, 12, 0)
    rows = repo.list_page(
        category=None, ntce_dt_from=None, ntce_dt_to=None,
        clse_after=cutoff, limit=20, offset=0,
    )
    ids = {r.bid_id for r in rows}
    assert "past_00" not in ids                 # 마감 지남 → 제외
    assert ids == {"future_00", "nodl_00"}      # 미래 + NULL(마감없음) → 노출
    assert repo.count(category=None, ntce_dt_from=None, ntce_dt_to=None, clse_after=cutoff) == 2


def test_today_range_filter_on_ntce_dt(session):
    _insert(session, bid_ntce_no="in", bid_ntce_dt=datetime(2026, 7, 21, 9, 0))
    _insert(session, bid_ntce_no="yday", bid_ntce_dt=datetime(2026, 7, 20, 9, 0))
    _insert(session, bid_ntce_no="tmrw", bid_ntce_dt=datetime(2026, 7, 22, 9, 0))
    repo = BidRepository(session)
    rows = repo.list_page(
        category=None,
        ntce_dt_from=datetime(2026, 7, 21, 0, 0),
        ntce_dt_to=datetime(2026, 7, 22, 0, 0),
        limit=20, offset=0,
    )
    assert [r.bid_id for r in rows] == ["in_00"]
