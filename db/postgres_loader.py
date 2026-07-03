import json
import os
from pathlib import Path

import psycopg2

_ENV_KEYS = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

_QUAL_COLUMNS = [
    "company_size_limit", "required_licenses", "required_personnel",
    "perf_min_amt", "perf_max_amt", "perf_period_years", "perf_type",
    "region_limit", "region_min_days", "eval_cutline", "work_period",
    "warranty_rate", "warranty_months", "required_certs", "other_requirements",
]
_JSON_COLUMNS = {"required_licenses", "required_personnel", "required_certs", "other_requirements"}


def _load_config() -> dict:
    env_path = Path(__file__).parent.parent / ".env"
    config = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            for key in _ENV_KEYS:
                if line.startswith(f"{key}="):
                    config[key] = line.split("=", 1)[1].strip()
    for key in _ENV_KEYS:
        if key not in config:
            config[key] = os.environ.get(key, "")
    return config


def _connect():
    config = _load_config()
    return psycopg2.connect(
        host=config["POSTGRES_HOST"],
        port=config["POSTGRES_PORT"],
        dbname=config["POSTGRES_DB"],
        user=config["POSTGRES_USER"],
        password=config["POSTGRES_PASSWORD"],
    )


def upsert_announcement(bid_ntce_no: str, bid_ntce_nm: str = "", ntce_instt_nm: str = "") -> None:
    """bid_qualifications의 FK 제약을 만족시키기 위해 최소 정보로 공고 원본을 upsert."""
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bid_announcements (bid_ntce_no, bid_ntce_nm, ntce_instt_nm)
                VALUES (%s, %s, %s)
                ON CONFLICT (bid_ntce_no) DO NOTHING
                """,
                (bid_ntce_no, bid_ntce_nm, ntce_instt_nm),
            )
    finally:
        conn.close()


def insert_qualifications(bid_ntce_no: str, qualifications: dict) -> None:
    values = []
    for col in _QUAL_COLUMNS:
        v = qualifications.get(col)
        if col in _JSON_COLUMNS and v is not None:
            v = json.dumps(v, ensure_ascii=False)
        values.append(v)

    columns_sql = ", ".join(_QUAL_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_QUAL_COLUMNS))
    set_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in _QUAL_COLUMNS)

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO bid_qualifications (bid_ntce_no, {columns_sql})
                VALUES (%s, {placeholders})
                ON CONFLICT (bid_ntce_no) DO UPDATE SET {set_clause}
                """,
                [bid_ntce_no] + values,
            )
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from data.sample.sample_metadata import SAMPLE_METADATA

    json_dir = Path(__file__).parent.parent / "data" / "sample" / "output" / "json"
    for json_file in sorted(json_dir.glob("*.json")):
        meta = SAMPLE_METADATA[json_file.stem]
        qualifications = json.loads(json_file.read_text(encoding="utf-8"))
        upsert_announcement(meta["bid_ntce_no"], meta["bid_ntce_nm"], meta["ntce_instt_nm"])
        insert_qualifications(meta["bid_ntce_no"], qualifications)
        print(f"[적재] {json_file.name} -> bid_ntce_no={meta['bid_ntce_no']}")
