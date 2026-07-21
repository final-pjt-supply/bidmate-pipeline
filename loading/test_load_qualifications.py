# -*- coding: utf-8 -*-
"""load_qualifications 순수 헬퍼 단위테스트(DB/S3 없음)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import load_qualifications as lq  # noqa: E402
import qual_merge  # noqa: E402


def test_write_columns_cover_18_fields_plus_meta():
    assert set(qual_merge.DOMAIN_FIELDS).issubset(set(lq.WRITE_COLUMNS))
    for c in ("extraction_evidence", "extraction_meta", "merge_conflicts"):
        assert c in lq.WRITE_COLUMNS


def test_build_write_values_maps_merged():
    merged = {
        "values": {f: None for f in qual_merge.DOMAIN_FIELDS},
        "evidence": [{"field": "x", "page": 1, "snippet": "s"}],
        "conflicts": [],
        "meta": {"merged_doc_count": 2},
    }
    merged["values"]["company_size_limit"] = "no_large"
    row = lq.build_write_values(merged)
    assert row["company_size_limit"] == "no_large"
    assert row["extraction_evidence"] == merged["evidence"]
    assert row["extraction_meta"] == merged["meta"]
    assert row["merge_conflicts"] == []
    assert set(row) == set(lq.WRITE_COLUMNS)


def test_build_update_sql_shape():
    sql = lq.build_update_sql()
    assert sql.strip().startswith("UPDATE bid_table SET")
    assert "merged_at = now()" in sql
    assert "qual_status = 'merged'" in sql
    assert "WHERE bid_id = %s" in sql
    assert "is_human_verified IS NOT TRUE" in sql
    # SET 컬럼마다 %s 하나 + WHERE bid_id %s 하나
    assert sql.count("%s") == len(lq.WRITE_COLUMNS) + 1


def test_parse_bid_id_from_path():
    p = ("qualifications/backfill/biz_div=frgcpt/year=2026/month=01/day=29/"
         "R26BK01297003_000/R26BK01297003_000_doc03.json")
    assert lq.parse_bid_id(p) == "R26BK01297003_000"


def test_classify_splits_updatable_protected_unmatched():
    s3 = {"A", "B", "C"}
    db_verified = {"A": False, "B": True}  # C는 bid_table에 없음
    out = lq.classify(s3, db_verified)
    assert out["updatable"] == {"A"}
    assert out["protected"] == {"B"}
    assert out["unmatched"] == {"C"}


def test_load_db_params_strips_dict_residue(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        'POSTGRES_HOST="h.example.com",\n'
        "POSTGRES_PORT=5432,\n"
        'POSTGRES_DBNAME="bidmate",\n'
        'POSTGRES_USER="bidmaster",\n'
        "POSTGRES_PASSWORD=secret\n",
        encoding="utf-8",
    )
    params = lq.load_db_params(env_path=env)
    assert params["host"] == "h.example.com"
    assert params["port"] == 5432
    assert params["dbname"] == "bidmate"
    assert params["user"] == "bidmaster"
    assert params["password"] == "secret"
