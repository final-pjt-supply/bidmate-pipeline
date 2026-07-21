# -*- coding: utf-8 -*-
"""baseline — API 서버 소유 테이블 기준점 (파이프라인 테이블 제외)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-21

이 리비전은 의도적으로 비어 있다(no-op). Alembic이 API 서버 마이그레이션을
추적하기 시작하는 기준점(alembic_version)을 세울 뿐이다.

- 이 시점에 API 서버가 소유한 테이블은 아직 없다(bid_table/bid_attachments는
  파이프라인 소유라 env.py의 include_object가 제외).
- 운영 RDS에는 `alembic stamp 0001_baseline`으로 이 리비전을 '적용됨'으로만
  표시한다(실제 DDL 실행 없음). ⚠ 이건 공유 RDS에 alembic_version 테이블을
  만드는 쓰기라 팀 협의 후 수행할 것(alembic/README.md 참조).
- 첫 실제 마이그레이션(예: company 테이블)은 이 리비전을 down_revision으로 갖는다.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
