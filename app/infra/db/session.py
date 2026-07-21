# -*- coding: utf-8 -*-
"""SQLAlchemy engine/session. 동기 엔진(조회·자격필터는 동기 규약).

엔진은 모듈 로드 시 1회 생성해 Lambda 웜스타트 사이에 재사용한다(콜드스타트당
1번만 커넥션 풀 초기화). pool_pre_ping으로 RDS가 끊은 죽은 커넥션을 걸러낸다.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """ORM 베이스. bid_table 등 기존 스키마의 읽기 전용 매핑이 여기 붙는다.

    ⚠ 이 ORM은 db/schema/*.sql(SSOT)의 '반영'이지 원본이 아니다. 테이블 생성/변경은
    파이프라인 팀의 DDL + Alembic으로만 하고, 이 앱은 create_all을 호출하지 않는다.
    """


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """요청 스코프 세션. FastAPI 의존성(api/deps.py)이 감싼다."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
