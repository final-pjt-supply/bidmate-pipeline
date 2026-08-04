# -*- coding: utf-8 -*-
"""config.py 상수·env 로딩 단위테스트."""
from embedding.backfill import config


def test_prefix_constants():
    assert config.BUCKET == "bidmate"
    assert config.EXTRACTED_PREFIX == "extracted/downloads/backfill/"
    assert config.CHUNKS_PREFIX == "embeddings/backfill/chunks/"
    assert config.EMBEDDED_PREFIX == "embeddings/backfill/embedded/"


def test_load_env_maps_nonstandard_aws_keys(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("AWS_ACCESS_KEY=AKIA_X\nAWS_SECRET_KEY=secret_x\n# 주석\nFOO=bar\n",
                   encoding="utf-8")
    monkeypatch.setattr(config, "_ENV_PATH", env)
    for k in ("AWS_ACCESS_KEY", "AWS_SECRET_KEY", "AWS_ACCESS_KEY_ID",
              "AWS_SECRET_ACCESS_KEY", "FOO"):
        monkeypatch.delenv(k, raising=False)

    config.load_env()

    assert config.os.environ["FOO"] == "bar"
    # 비표준 이름 → boto3 표준 이름으로 매핑
    assert config.os.environ["AWS_ACCESS_KEY_ID"] == "AKIA_X"
    assert config.os.environ["AWS_SECRET_ACCESS_KEY"] == "secret_x"
