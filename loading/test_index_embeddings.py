# -*- coding: utf-8 -*-
"""index_embeddings 순수 헬퍼 단위테스트(S3/OpenSearch 없음)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import index_embeddings as ie  # noqa: E402


def test_load_os_params_maps_reversed_labels(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'OPENSEARCH_HOST="vpc-x.es.amazonaws.com"\n'
        "OPENSEARCH_PORT=443\n"
        "OPENSEARCH_DBNAME=bidmaster\n"
        "OPENSEARCH_USER=SecretPass!\n",
        encoding="utf-8",
    )
    p = ie.load_os_params(env_path=env)
    assert p["host"] == "vpc-x.es.amazonaws.com"
    assert p["port"] == 443
    assert p["user"] == "bidmaster"       # DBNAME이 실제 유저
    assert p["password"] == "SecretPass!"  # USER가 실제 패스워드


def test_checkpoint_roundtrip(tmp_path):
    cp = tmp_path / "done_keys.txt"
    assert ie.load_checkpoint(cp) == set()
    ie.append_checkpoint(cp, "a/b.json")
    ie.append_checkpoint(cp, "c/d.json")
    assert ie.load_checkpoint(cp) == {"a/b.json", "c/d.json"}


def test_estimate_total_chunks():
    assert ie.estimate_total_chunks([40, 50, 30], 27131) == round(40 * 27131)
    assert ie.estimate_total_chunks([], 100) == 0


def test_failure_status_extracts():
    assert ie._failure_status({"index": {"status": 400}}) == 400
    assert ie._failure_status({"index": {"status": "N/A"}}) == "N/A"
    assert ie._failure_status({}) is None
    assert ie._failure_status("boom") is None


def test_is_permanent_classification():
    assert ie._is_permanent(400) is True
    assert ie._is_permanent(404) is True
    assert ie._is_permanent(429) is False
    assert ie._is_permanent(503) is False
    assert ie._is_permanent("N/A") is False
    assert ie._is_permanent(None) is False
