# -*- coding: utf-8 -*-
"""RDS 병합 배치 Lambda 진입점(#66) — EventBridge 주기 트리거.

qualifications/ S3 문서를 병합해 bid_table의 자격요건 18개 컬럼을 UPDATE한다.
SQS 트리거 handler들과 달리 이벤트 페이로드를 쓰지 않는다(event 인자는 무시) —
매 실행마다 bid_table의 미완결 행 전체를 스윕하는 배치이기 때문이다.

콜드스타트의 DB 연결/스키마 가드(db.get_connection)가 실패하면 배치 전체를
즉시 실패시킨다(fail-loud, try/except로 감싸지 않음) — 재시도해도 똑같이
실패할 구조적 문제이기 때문이다. 반면 bid_id 하나의 병합 실패(_process_target
내부)는 나머지 대상 처리를 막지 않는다 — 그 bid는 다음 회차에도 여전히
pending/partial로 남아있어 자연스럽게 재시도된다.
"""
import logging
import time

from common.config import load_merge_db_config
from common.logs import (
    log_batch_summary,
    log_merge_done,
    log_merge_failed,
    log_merge_skip,
    log_merge_start,
)
from merge import db, inventory
from merge.logic import determine_qual_status, merge_qualification_documents

logger = logging.getLogger(__name__)
# 다른 handler와 동일한 이유(README 참고): 로거 레벨을 명시하지 않으면 루트 로거
# 기본값(WARNING)에 INFO가 걸러져 MERGE_START/DONE/SKIP·BATCH_SUMMARY가
# CloudWatch에 안 남는다.
logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, _context):
    config = load_merge_db_config()
    conn = db.get_connection(config)

    targets = db.fetch_merge_targets(conn)
    if not targets:
        logger.info("처리 대상 없음 — 배치 종료")
        return {"total": 0, "merged": 0, "partial": 0, "failed": 0, "skipped": 0,
                "conflict_bid_ids": [], "error_bid_ids": []}

    inv = inventory.build_inventory(config["source_bucket"])

    counts = {"merged": 0, "partial": 0, "failed": 0, "skipped": 0}
    conflict_bid_ids: list[str] = []
    error_bid_ids: list[str] = []

    for target in targets:
        bid_id = target["bid_id"]
        log_merge_start(bid_id)
        start = time.monotonic()
        try:
            outcome = _process_target(conn, target, inv, config)
        except Exception:
            logger.exception("bid_id 처리 중 예외 발생 — 건너뛰고 다음 대상 계속: bid_id=%s", bid_id)
            error_bid_ids.append(bid_id)
            log_merge_failed(bid_id, "unexpected_exception")
            continue

        if outcome is None:
            counts["skipped"] += 1
            log_merge_skip(bid_id, "no_files_found_yet")
            continue

        qual_status, has_conflicts = outcome
        counts[qual_status] += 1
        if has_conflicts:
            conflict_bid_ids.append(bid_id)
        log_merge_done(bid_id, qual_status, int((time.monotonic() - start) * 1000))

    log_batch_summary(
        total=len(targets),
        merged=counts["merged"],
        partial=counts["partial"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        conflict_bid_ids=conflict_bid_ids,
        error_bid_ids=error_bid_ids,
    )
    return {"total": len(targets), **counts,
            "conflict_bid_ids": conflict_bid_ids, "error_bid_ids": error_bid_ids}


def _process_target(conn, target: dict, inv: dict, config: dict) -> tuple[str, bool] | None:
    """대상 bid 하나를 병합한다. S3에서 찾은 파일이 0건이면 None을 반환해
    호출부가 skip으로 집계하게 한다 — 아직 아무 파일도 안 왔다는 뜻이라
    qual_status를 pending 그대로 두고 UPDATE 자체를 하지 않는다(2단계 설계).
    """
    bid_id = target["bid_id"]
    refs = inv.get(bid_id, [])
    found_file_count = len(refs)
    if found_file_count <= 0:
        return None

    documents = inventory.fetch_documents(config["source_bucket"], refs)
    merged = merge_qualification_documents(bid_id, documents)

    has_failed = db.has_failed_attachment(conn, target["bid_ntce_no"], target["bid_ntce_ord"])
    qual_status = determine_qual_status(found_file_count, target["expected_file_count"], has_failed)

    db.apply_merge(
        conn, target["bid_ntce_no"], target["bid_ntce_ord"],
        merged, qual_status, config["dry_run"],
    )

    return qual_status, bool(merged.get("merge_conflicts"))
