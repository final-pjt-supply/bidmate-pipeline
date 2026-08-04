# -*- coding: utf-8 -*-
"""backfill 청킹/임베딩 배치의 설정·경로 단일 출처.

버킷·prefix 상수와 .env 로딩을 담당한다. .env의 AWS 키가 비표준 이름
(AWS_ACCESS_KEY/AWS_SECRET_KEY)이라 boto3가 자동 인식하지 못하므로,
표준 이름(AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)으로 매핑한다.
"""
import os
from pathlib import Path

BUCKET = "bidmate"
EXTRACTED_PREFIX = "extracted/downloads/backfill/"
CHUNKS_PREFIX = "embeddings/backfill/chunks/"
EMBEDDED_PREFIX = "embeddings/backfill/embedded/"

# embedding/backfill/config.py → parents[2] == 리포 루트
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_env() -> None:
    """.env를 os.environ에 로딩(이미 설정된 값은 유지)하고 비표준 AWS 키를 매핑한다."""
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

    if "AWS_ACCESS_KEY" in os.environ:
        os.environ.setdefault("AWS_ACCESS_KEY_ID", os.environ["AWS_ACCESS_KEY"])
    if "AWS_SECRET_KEY" in os.environ:
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.environ["AWS_SECRET_KEY"])
