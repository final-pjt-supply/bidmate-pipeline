# -*- coding: utf-8 -*-
"""API 서버 설정. 환경변수만 읽는다(코드에 접속정보 하드코딩 금지).

로컬(db/docker-compose.yml)은 POSTGRES_* 기본값과 맞춰 무설정으로 뜨고,
배포(Lambda)는 환경변수/Secrets Manager로 주입한다. 파이프라인 배치가 쓰는
MERGE_DB_* 와는 이름을 분리한다 — 같은 RDS를 가리키더라도 API 서버와 배치는
독립 배포 단위라 설정 통로를 섞지 않는다.
"""
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env(리포 루트, gitignore됨)에서 로컬/터널 접속정보를 읽는다. 배포(Lambda)는
    # 환경변수/Secrets Manager가 .env보다 우선한다(env가 파일값을 덮어씀).
    model_config = SettingsConfigDict(
        env_prefix="", extra="ignore", env_file=".env", env_file_encoding="utf-8"
    )

    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="bidding_agent")
    postgres_user: str = Field(default="bidding_agent")
    postgres_password: str = Field(default="bidding_agent")
    # RDS는 보통 SSL 필수(sslmode=require). private RDS를 SSH 터널로 붙을 때는
    # 터널 자체가 암호화되지만 libpq SSL도 켜두는 게 안전(RDS 파라미터가 강제 가능).
    # 로컬 docker(무SSL)에선 None으로 둔다.
    postgres_sslmode: str | None = Field(default=None)

    # SQLAlchemy 커넥션 풀. Lambda(stateless, 컨테이너당 요청 1개)에선 큰 풀이
    # 무의미하고 RDS 커넥션만 소진하므로 작게 잡는다.
    db_pool_size: int = Field(default=1)
    db_max_overflow: int = Field(default=2)

    @property
    def database_url(self) -> str:
        # RDS 자격증명엔 특수문자(@:/ 등)가 흔해 URL-encode 하지 않으면 파싱이 깨진다.
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        url = (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
        if self.postgres_sslmode:
            url += f"?sslmode={self.postgres_sslmode}"
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
