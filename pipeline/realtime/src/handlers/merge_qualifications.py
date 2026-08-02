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

단, 그 실패 뒤의 conn.rollback() 자체가 또 실패하면 예외적으로 배치를 즉시
중단한다(2026-07-14, #75 사고 대응) — rollback 실패는 커넥션 손상 가능성이
높다는 신호라 그 커넥션으로 남은 대상을 계속 돌리면 전부 같은 이유로 실패해
무의미한 실패 로그만 도배한다(사고 당일 실제로 겪은 증상). 처리 못 한 남은
대상은 UPDATE 자체를 안 했으니 pending/partial 그대로이고, 다음 주기가 새
커넥션으로 자연스럽게 다시 집는다.

DB 대상 선정(db.fetch_merge_targets)은 여전히 pending/partial/failed 전부를
뽑는다 — partial 재처리는 이 기존 조건이 그대로 담당하므로 여기서 따로 안
바꾼다(#80 설계 결정). 대신 daily 인벤토리(merge.inventory, #80부터
qualifications/daily/만 봄)에 실제로 파일이 있는 대상만 남긴 "다음"에 회당
상한(200건)을 적용한다 — 순서를 이렇게 두는 이유는 #80에서 실측 확인된 문제
때문이다: 예전엔 DB 대상(정렬 없음, 백필 포함 시 2만 건대)을 그대로 앞에서
잘라 상한을 적용해서, 백필 소관 행이 정렬 없는 반환 순서상 상한을 전부
점유하면 daily 대상이 순번 자체를 못 받는 구조였다. 인벤토리 필터를 먼저
거치면 상한은 오직 "daily 파일이 실제로 있는" 대상 안에서만 적용된다.
"""
import logging
import time

from common.config import load_merge_db_config
from common.logs import (
    log_batch_summary,
    log_merge_done,
    log_merge_failed,
    log_merge_no_targets,
    log_merge_skip,
    log_merge_start,
    log_merge_target_funnel,
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

    tagged = _sweep_tags(conn, config)

    targets = db.fetch_merge_targets(conn)
    if not targets:
        logger.info("처리 대상 없음 — 배치 종료")
        return {"total": 0, "merged": 0, "partial": 0, "failed": 0, "skipped": 0,
                "tagged": tagged, "conflict_bid_ids": [], "error_bid_ids": []}

    db_total = len(targets)

    inv = inventory.build_inventory(config["source_bucket"])

    # #80: 상한 적용보다 먼저 daily 인벤토리로 걸러낸다 — 그래야 백필 소관 대상이
    # (daily 파일이 없어 여기서 이미 빠지므로) 상한 슬롯을 점유하지 못한다.
    targets = [t for t in targets if t["bid_id"] in inv]
    daily_matched = len(targets)

    if daily_matched == 0:
        log_merge_no_targets(db_total)
        return {"total": 0, "merged": 0, "partial": 0, "failed": 0, "skipped": 0,
                "tagged": tagged, "conflict_bid_ids": [], "error_bid_ids": []}

    max_targets = config["max_targets_per_run"]
    if len(targets) > max_targets:
        logger.info(
            "이번 회차 %d건 처리, %d건 다음 주기로 이월",
            max_targets, len(targets) - max_targets,
        )
        targets = targets[:max_targets]

    log_merge_target_funnel(db_total, daily_matched, len(targets))

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
            try:
                conn.rollback()
            except Exception:
                logger.error(
                    "conn.rollback() 자체가 실패함 — 커넥션 손상 가능성 높음, 배치 중단"
                    "(남은 대상은 pending/partial 유지 — 다음 주기가 새 커넥션으로 처리): "
                    "bid_id=%s", bid_id, exc_info=True,
                )
                break
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
    return {"total": len(targets), **counts, "tagged": tagged,
            "conflict_bid_ids": conflict_bid_ids, "error_bid_ids": error_bid_ids}


def _sweep_tags(conn, config: dict) -> int:
    """태그가 없는 공고에 품목 태그를 매긴다(#97).

    자격요건 병합 대상과 따로 뽑는다 — 병합은 daily 첨부가 온 공고만 처리하는데
    태깅에 필요한 건 제목·업종·코드뿐이라 첨부와 무관하다. 같은 목록을 쓰면
    첨부가 영영 안 오는 공고는 태그도 못 받는다.

    태깅 실패가 자격요건 병합을 막지 않게 통째로 감싼다 — 태깅은 나중에 얹은
    기능이고, 이게 죽는다고 이미 돌던 배치까지 세울 이유가 없다. 실패해도
    해당 공고는 여전히 태그가 없으니 다음 회차가 자연스럽게 다시 집는다.
    """
    try:
        if not db.bid_tags_available(conn):
            return 0
        targets = db.fetch_untagged_targets(conn, config["tag_max_per_run"])
        if not targets:
            return 0

        from tagging.rules import decide_tag       # numpy 로드를 필요할 때만

        rows = []
        for row in targets:
            tag, source, confidence = decide_tag(row)
            rows.append((row["bid_id"], tag, source, confidence))

        written = db.upsert_tags(conn, rows, config["dry_run"])
        by_source: dict[str, int] = {}
        for _, _, source, _ in rows:
            by_source[source] = by_source.get(source, 0) + 1
        logger.info("TAG_SWEEP 대상=%d 기록=%d 출처별=%s", len(targets), written, by_source)
        return written
    except Exception:
        logger.exception("태깅 스윕 실패 — 자격요건 병합은 그대로 진행")
        try:
            conn.rollback()
        except Exception:
            logger.error("태깅 실패 후 rollback도 실패 — 커넥션 손상 가능성", exc_info=True)
        return 0


def _process_target(conn, target: dict, inv: dict, config: dict) -> tuple[str, bool] | None:
    """대상 bid 하나를 병합한다. S3에서 찾은 파일이 0건이면 None을 반환해
    호출부가 skip으로 집계하게 한다 — 아직 아무 파일도 안 왔다는 뜻이라
    qual_status를 pending 그대로 두고 UPDATE 자체를 하지 않는다(2단계 설계).

    #80부터 lambda_handler가 daily 인벤토리에 있는 대상만 걸러서 넘기므로
    이 함수가 실제로 호출되는 시점엔 found_file_count가 0인 경우가 이론상
    없어야 한다 — 그래도 방어적으로 남겨둔다(예: 인벤토리 구축과 처리 사이
    S3 삭제 같은 경합 상황 대비).
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
