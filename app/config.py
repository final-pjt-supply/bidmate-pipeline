# -*- coding: utf-8 -*-
"""API 서버 설정. 환경변수만 읽는다(코드에 접속정보 하드코딩 금지).

로컬(db/docker-compose.yml)은 POSTGRES_* 기본값과 맞춰 무설정으로 뜨고,
배포(Lambda)는 환경변수/Secrets Manager로 주입한다. 파이프라인 배치가 쓰는
MERGE_DB_* 와는 이름을 분리한다 — 같은 RDS를 가리키더라도 API 서버와 배치는
독립 배포 단위라 설정 통로를 섞지 않는다.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="bidding_agent")
    postgres_user: str = Field(default="bidding_agent")
    postgres_password: str = Field(default="bidding_agent")

    # SQLAlchemy 커넥션 풀. Lambda(stateless, 컨테이너당 요청 1개)에선 큰 풀이
    # 무의미하고 RDS 커넥션만 소진하므로 작게 잡는다.
    db_pool_size: int = Field(default=1)
    db_max_overflow: int = Field(default=2)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
