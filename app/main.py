# -*- coding: utf-8 -*-
"""FastAPI 앱 + Lambda 진입점(Mangum).

배포는 Lambda + Mangum(stateless)라 세션/전역 가변 상태에 의존하지 않는다. 로컬은
`uvicorn app.main:app --reload`로 띄운다.
"""
from fastapi import FastAPI
from mangum import Mangum

from app.api.v1.router import api_router

app = FastAPI(title="BidMate API", version="0.1.0")
app.include_router(api_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# AWS Lambda 핸들러(template.yaml에서 참조).
handler = Mangum(app)
