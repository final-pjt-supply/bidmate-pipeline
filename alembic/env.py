# -*- coding: utf-8 -*-
"""Alembic 마이그레이션 환경 — BidMate API 서버 전용.

핵심 규약(alembic/README.md 참조):
- 관리 대상 = API 서버가 소유한 테이블(company, match_results 등, 앞으로 추가)
- 제외 = 파이프라인 소유 테이블(bid_table, bid_attachments) — db/schema/*.sql이 SSOT.
  우리 Bid ORM은 컬럼 일부만 매핑했으므로, 관리 대상에 넣으면 autogenerate가
  매핑 안 한 컬럼을 DROP하려 든다. include_object로 시야에서 완전히 제외한다.
- DB URL은 app.config(.env/환경변수)에서 읽는다(ini에 하드코딩 안 함).
"""
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

from app.config import get_settings
from app.infra.db.session import Base

# 모델을 import해야 Base.metadata에 테이블이 등록된다.
# (앞으로 company/match_results 모델을 추가하면 여기서 함께 import한다.)
import app.infra.db.models.bid  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 파이프라인 팀 소유 — Alembic이 절대 건드리지 않는다.
_PIPELINE_OWNED_TABLES = {"bid_table", "bid_attachments"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """파이프라인 소유 테이블(과 그 하위 객체)을 autogenerate/비교에서 제외."""
    if type_ == "table" and name in _PIPELINE_OWNED_TABLES:
        return False
    # 인덱스/제약 등 테이블에 딸린 객체도, 소속 테이블이 제외 대상이면 함께 제외.
    parent = getattr(obj, "table", None)
    if parent is not None and parent.name in _PIPELINE_OWNED_TABLES:
        return False
    return True


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
