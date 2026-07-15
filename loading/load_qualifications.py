# -*- coding: utf-8 -*-
"""S3 자격요건 JSON을 bid_id로 병합해 bid_table에 적재하는 로더(Bastion 실행).

이 파일 상단은 순수 헬퍼(컬럼 매핑·SQL 생성)만 정의한다. CLI 오케스트레이션은
Task 5에서 추가된다. 병합 로직은 같은 폴더의 qual_merge에 위임한다.
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import qual_merge  # noqa: E402

logger = logging.getLogger(__name__)

# UPDATE 대상 컬럼: 18개 도메인 필드 + 메타 3개
WRITE_COLUMNS = qual_merge.DOMAIN_FIELDS + (
    "extraction_evidence", "extraction_meta", "merge_conflicts",
)

# jsonb로 저장할 컬럼(psycopg2 Json으로 감쌈)
JSONB_COLUMNS = frozenset({
    "required_licenses", "item_codes", "region_limit_names",
    "performance_reqs", "capacity_reqs", "personnel_reqs", "required_certs",
    "extraction_evidence", "extraction_meta", "merge_conflicts",
})


def build_write_values(merged: dict) -> dict:
    """merge_docs 결과를 {컬럼: 값}으로 매핑한다."""
    row = dict(merged["values"])  # 18개 도메인 필드
    row["extraction_evidence"] = merged["evidence"]
    row["extraction_meta"] = merged["meta"]
    row["merge_conflicts"] = merged["conflicts"]
    return row


def build_update_sql() -> str:
    """bid_id 기준 파라미터라이즈드 UPDATE 문. 컬럼 순서는 WRITE_COLUMNS."""
    set_clause = ", ".join(f"{col} = %s" for col in WRITE_COLUMNS)
    return (
        f"UPDATE bid_table SET {set_clause}, "
        f"merged_at = now(), qual_status = 'merged' "
        f"WHERE bid_id = %s AND (is_human_verified IS NOT TRUE)"
    )


def parse_bid_id(path) -> str:
    """.../<bid_id>/<bid_id>_docNN.json 에서 bid_id(부모 디렉터리명) 추출."""
    return Path(str(path)).parent.name


def classify(s3_bid_ids: set, db_verified: dict) -> dict:
    """S3 bid_id를 UPDATE 대상/보호 스킵/매칭 실패로 분류.

    db_verified: bid_table에 존재하는 bid_id -> is_human_verified(bool) 매핑.
    """
    updatable, protected, unmatched = set(), set(), set()
    for bid_id in s3_bid_ids:
        if bid_id not in db_verified:
            unmatched.add(bid_id)
        elif db_verified[bid_id]:
            protected.add(bid_id)
        else:
            updatable.add(bid_id)
    return {"updatable": updatable, "protected": protected, "unmatched": unmatched}


def load_db_params(env_path=None) -> dict:
    """.env의 POSTGRES_*를 psycopg2 connect용 dict로. dict 잔재(따옴표·쉼표) 제거.

    주의(스펙 §11): strip 방식은 값 자체에 쉼표가 들어가면 깨진다 — .env 원본 정리가 근본책.
    """
    env_path = Path(env_path) if env_path else Path(__file__).resolve().parents[1] / ".env"
    raw = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        raw[k.strip()] = v.strip().rstrip(",").strip().strip('"').strip("'")
    return {
        "host": raw["POSTGRES_HOST"],
        "port": int(raw["POSTGRES_PORT"]),
        "dbname": raw["POSTGRES_DBNAME"],
        "user": raw["POSTGRES_USER"],
        "password": raw["POSTGRES_PASSWORD"],
    }


def _setup_logging(out_dir: Path, verbose: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = logging.FileHandler(out_dir / f"load_qualifications_{ts}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    root.handlers[:] = [fh, ch]


def _scan_local_tree(local_dir: Path) -> tuple[dict, list]:
    """로컬 트리의 *.json을 bid_id별로 그룹핑. (bid_id -> [doc dict], 파싱실패 키 목록)."""
    by_bid: dict = {}
    skipped: list = []
    for path in local_dir.rglob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("JSON 파싱 실패 doc 스킵: %s (%s)", path, e)
            skipped.append(str(path))
            continue
        # 추적성: 로컬 경로에서 원본 S3 키 복원해 doc에 주입(merge_docs meta가 수집)
        doc["_s3_key"] = "qualifications/backfill/" + path.relative_to(local_dir).as_posix()
        by_bid.setdefault(parse_bid_id(path), []).append(doc)
    return by_bid, skipped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="S3 자격요건 → bid_table 적재 로더")
    parser.add_argument("--local-dir", required=True, help="aws s3 sync로 받은 로컬 디렉터리")
    parser.add_argument("--dry-run", action="store_true", help="DB 미변경, 리포트만")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out-dir", default="logs", help="로그·부속 산출물 디렉터리")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    _setup_logging(out_dir, args.verbose)
    local_dir = Path(args.local_dir)

    # psycopg2는 Bastion 실행 시에만 필요 → 여기서 import(단위테스트는 main 미호출)
    import psycopg2
    from psycopg2.extras import Json, execute_batch

    db = load_db_params()
    logger.info("시작: dry_run=%s local_dir=%s host=%s db=%s batch=%d",
                args.dry_run, local_dir, db["host"], db["dbname"], args.batch_size)

    by_bid, skipped_docs = _scan_local_tree(local_dir)
    logger.info("스캔 완료: S3 bid_id %d개, 파싱실패 doc %d개", len(by_bid), len(skipped_docs))

    conn = psycopg2.connect(**db)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT bid_id, coalesce(is_human_verified, false) FROM bid_table")
            db_verified = {bid: verified for bid, verified in cur.fetchall()}
        # 연결은 UPDATE에서 재사용

        groups = classify(set(by_bid), db_verified)
        logger.info("분류: UPDATE 대상 %d / 보호 스킵 %d / 매칭 실패 %d",
                    len(groups["updatable"]), len(groups["protected"]), len(groups["unmatched"]))

        # 부속 산출물
        (out_dir / "unmatched_bidids.txt").write_text(
            "\n".join(sorted(groups["unmatched"])), encoding="utf-8")
        (out_dir / "skipped_docs.txt").write_text("\n".join(skipped_docs), encoding="utf-8")

        sql = build_update_sql()
        conflict_bids = 0
        conflict_fields = 0
        processed = 0
        start = time.time()
        batch: list = []

        for bid_id in sorted(groups["updatable"]):
            merged = qual_merge.merge_docs(by_bid[bid_id])
            if merged["conflicts"]:
                conflict_bids += 1
                conflict_fields += len(merged["conflicts"])
                logger.debug("충돌 bid_id=%s: %s", bid_id, merged["conflicts"])
            row = build_write_values(merged)
            params = [Json(row[c]) if c in JSONB_COLUMNS else row[c] for c in WRITE_COLUMNS]
            params.append(bid_id)
            batch.append(params)

            if not args.dry_run and len(batch) >= args.batch_size:
                with conn.cursor() as cur:
                    execute_batch(cur, sql, batch)
                conn.commit()
                processed += len(batch)
                logger.info("[%d/%d] 커밋, elapsed %.0fs", processed, len(groups["updatable"]), time.time() - start)
                batch = []

        if not args.dry_run and batch:
            with conn.cursor() as cur:
                execute_batch(cur, sql, batch)
            conn.commit()
            processed += len(batch)
    except Exception:
        # 배치/DB 실패는 사람이 봐야 하는 것 → 스택트레이스와 함께 ERROR로 파일 로그에 남긴다(스펙 §8.2)
        logger.exception("적재 중 오류 — 배치 커밋/DB 실패로 중단")
        raise
    finally:
        conn.close()

    logger.info(
        "==== load_qualifications report (%s) ====\n"
        "S3 bid_id total:   %d\nUPDATE 대상:       %d\n매칭 실패:         %d -> unmatched_bidids.txt\n"
        "보호로 스킵:       %d\n충돌 발생 bid_id:  %d (필드 충돌 %d)\n파싱 실패 doc:     %d -> skipped_docs.txt\n"
        "적용 UPDATE:       %d\n총 소요:           %.0fs\n"
        "==========================================",
        "dry-run" if args.dry_run else "apply",
        len(by_bid), len(groups["updatable"]), len(groups["unmatched"]),
        len(groups["protected"]), conflict_bids, conflict_fields, len(skipped_docs),
        0 if args.dry_run else processed, time.time() - start,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
