# -*- coding: utf-8 -*-
"""FastAPI 의존성. 인증 진입점과 계층 조립(session→repository→service)을 여기 모은다.

★ 인증 자리 확보(지금 로직 미구현, 자리만):
  나중에 JWT(Cognito) 인증이 모든 엔드포인트에 붙는다. 그때 뜯어고치지 않도록,
  엔드포인트가 '현재 사용자(company_id)'를 받는 통로를 지금 만들어 두고 통과시킨다.
  회사별 점수 정렬(sort=score)도 같은 통로(CurrentUser.company_id)로 들어온다.
"""
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.orm import Session

from app.infra.db.repositories.bid_repository import BidRepository
from app.infra.db.session import get_session
from app.services.bid_service import BidService


@dataclass(frozen=True)
class CurrentUser:
    """현재 요청 주체. 멀티테넌시 격리(WHERE company_id=?)와 회사별 정렬의 키.

    company_id가 None인 건 '아직 인증 미구현'을 뜻한다 — 인증이 붙으면 토큰에서
    채워지고, None을 흘려보내는 분기는 그때 제거한다.
    """
    company_id: str | None = None


def get_current_user() -> CurrentUser:
    """★ 인증 진입점(스텁). 지금은 무조건 통과시키고 익명 사용자를 돌려준다.

    나중엔 여기서 Authorization 헤더의 JWT를 검증(Cognito JWKS)하고 company_id를
    꺼내 CurrentUser로 채운다. 엔드포인트 시그니처는 그대로 두고 이 함수 본문만
    교체하면 되도록, 반환 타입을 지금 고정해 둔다.
    """
    return CurrentUser(company_id=None)


def get_db() -> Iterator[Session]:
    yield from get_session()


def get_bid_service(db: Session = Depends(get_db)) -> BidService:
    return BidService(BidRepository(db))
