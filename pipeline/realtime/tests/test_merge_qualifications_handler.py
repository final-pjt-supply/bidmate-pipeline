# -*- coding: utf-8 -*-
"""handlers/merge_qualifications.py 단위테스트 — 조립(흐름)만 검증.

logic/inventory/db는 각자 own 테스트(test_merge_logic.py/test_inventory.py/
test_db.py)로 이미 검증됐으므로, 여기서는 그 모듈들을 전부 monkeypatch로
대체하고 handler가 이들을 올바른 순서/조건으로 호출·집계하는지만 본다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from common.config import load_merge_db_config  # noqa: E402
from handlers import merge_qualifications as handler  # noqa: E402
from merge import db, inventory  # noqa: E402

FAKE_CONFIG = {
    "db_host": "h", "db_port": 5432, "db_name": "d", "db_user": "u", "db_password": "p",
    "source_bucket": "bidmate", "dry_run": True, "max_targets_per_run": 200,
    "tag_max_per_run": 500,
}


def _target(bid_id="R25BK01152374_000", expected_file_count=1):
    return {
        "bid_ntce_no": bid_id.split("_")[0], "bid_ntce_ord": bid_id.split("_")[1],
        "bid_id": bid_id, "bid_category": "cnstwk",
        "expected_file_count": expected_file_count, "qual_status": "pending",
    }


class FakeConn:
    """rollback 호출 여부/횟수를 추적하는 가짜 커넥션. rollback_raises=True면
    rollback() 자체가 예외를 던져 방어 로직(try/except)을 시험한다."""

    def __init__(self, rollback_raises: bool = False):
        self.rollback_calls = 0
        self._rollback_raises = rollback_raises

    def rollback(self):
        self.rollback_calls += 1
        if self._rollback_raises:
            raise RuntimeError("rollback 자체 실패(시뮬레이션)")


@pytest.fixture(autouse=True)
def patch_config_and_connection(monkeypatch):
    monkeypatch.setattr(handler, "load_merge_db_config", lambda: FAKE_CONFIG)
    monkeypatch.setattr(db, "get_connection", lambda config: FakeConn())
    # 이 파일은 자격요건 병합만 검증한다. 태깅 스윕(#97)은 tests/test_tagging.py
    # 소관이라 여기서는 꺼둔다 — 켜두면 FakeConn에 cursor가 없어 스윕이 예외로
    # 빠지고, 그 안의 conn.rollback()이 rollback_calls를 오염시킨다.
    monkeypatch.setattr(db, "bid_tags_available", lambda conn: False)


def test_no_targets_short_circuits_without_building_inventory(monkeypatch):
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [])
    called = []
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: called.append(bucket))

    result = handler.lambda_handler({}, None)

    assert result["total"] == 0
    assert called == []  # 대상 0건이면 인벤토리 리스팅 자체를 하지 않아야 함


def test_target_with_no_files_found_is_skipped(monkeypatch):
    """#80부터 인벤토리에 아예 없는 bid_id는 핸들러 단계에서 걸러져 여기까지
    오지 않는다(아래 test_target_absent_from_inventory_excluded_before_cap 참고).
    이 테스트는 인벤토리엔 있지만(멤버십 통과) refs가 빈 방어적 경합 상황만
    남겨 _process_target의 found_file_count<=0 분기를 검증한다."""
    target = _target()
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [target])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {target["bid_id"]: []})
    apply_merge_calls = []
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: apply_merge_calls.append((a, k)))

    result = handler.lambda_handler({}, None)

    assert result["skipped"] == 1
    assert result["merged"] == 0
    assert apply_merge_calls == []  # 파일이 없으면 UPDATE 시도 자체를 안 함


def test_target_merged_successfully_is_counted_and_applied(monkeypatch):
    target = _target(expected_file_count=1)
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [target])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {target["bid_id"]: ["ref1"]})
    monkeypatch.setattr(inventory, "fetch_documents", lambda bucket, refs: [{"bid_id": target["bid_id"]}])
    monkeypatch.setattr(handler, "merge_qualification_documents",
                         lambda bid_id, docs: {"merge_conflicts": None})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")

    apply_merge_calls = []
    monkeypatch.setattr(db, "apply_merge", lambda conn, no, ord_, merged, status, dry_run:
                         apply_merge_calls.append((no, ord_, status, dry_run)))

    result = handler.lambda_handler({}, None)

    assert result["merged"] == 1
    assert result["conflict_bid_ids"] == []
    assert apply_merge_calls == [("R25BK01152374", "000", "merged", True)]  # dry_run=True 전달 확인


def test_target_with_conflicts_recorded_in_conflict_bid_ids(monkeypatch):
    target = _target()
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [target])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {target["bid_id"]: ["ref1"]})
    monkeypatch.setattr(inventory, "fetch_documents", lambda bucket, refs: [{"bid_id": target["bid_id"]}])
    monkeypatch.setattr(handler, "merge_qualification_documents",
                         lambda bid_id, docs: {"merge_conflicts": {"credit_rating_req": [...]}})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "partial")
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: None)

    result = handler.lambda_handler({}, None)

    assert result["conflict_bid_ids"] == [target["bid_id"]]
    assert result["partial"] == 1


def test_one_target_failure_does_not_stop_batch(monkeypatch):
    ok_target = _target(bid_id="R25BK01000000_000")
    bad_target = _target(bid_id="R25BK02000000_000")
    fake_conn = FakeConn()
    monkeypatch.setattr(db, "get_connection", lambda config: fake_conn)
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [bad_target, ok_target])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {
        ok_target["bid_id"]: ["ref1"], bad_target["bid_id"]: ["ref1"],
    })

    def fake_fetch_documents(bucket, refs):
        raise RuntimeError("S3 장애 시뮬레이션")

    def fake_merge(bid_id, docs):
        return {"merge_conflicts": None}

    call_count = {"n": 0}

    def fetch_documents_side_effect(bucket, refs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("첫 번째 대상에서만 실패")
        return [{"bid_id": ok_target["bid_id"]}]

    monkeypatch.setattr(inventory, "fetch_documents", fetch_documents_side_effect)
    monkeypatch.setattr(handler, "merge_qualification_documents", fake_merge)
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: None)

    result = handler.lambda_handler({}, None)

    assert result["error_bid_ids"] == [bad_target["bid_id"]]
    assert result["merged"] == 1  # 두 번째 대상은 정상 처리됨
    assert fake_conn.rollback_calls == 1  # 실패한 대상 처리 후 rollback 호출됨


def test_rollback_failure_stops_batch_and_leaves_remaining_targets_untouched(monkeypatch, caplog):
    """conn.rollback() 자체가 실패하면 커넥션 손상 가능성이 높으므로 배치를 즉시
    중단해야 함 — 뒤 대상은 시도조차 하지 않고 다음 주기(새 커넥션)로 넘긴다."""
    bad_target = _target(bid_id="R25BK02000000_000")
    ok_target = _target(bid_id="R25BK01000000_000")
    fake_conn = FakeConn(rollback_raises=True)
    monkeypatch.setattr(db, "get_connection", lambda config: fake_conn)
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [bad_target, ok_target])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {
        ok_target["bid_id"]: ["ref1"], bad_target["bid_id"]: ["ref1"],
    })
    monkeypatch.setattr(
        inventory, "fetch_documents",
        lambda bucket, refs: (_ for _ in ()).throw(RuntimeError("첫 번째 대상 처리 중 실패")),
    )
    monkeypatch.setattr(handler, "merge_qualification_documents", lambda bid_id, docs: {"merge_conflicts": None})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")
    apply_merge_calls = []
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: apply_merge_calls.append(a))

    with caplog.at_level("ERROR"):
        result = handler.lambda_handler({}, None)

    assert result["error_bid_ids"] == [bad_target["bid_id"]]
    assert result["merged"] == 0  # ok_target은 시도조차 되지 못하고 루프가 중단됨
    assert apply_merge_calls == []
    assert fake_conn.rollback_calls == 1  # rollback 시도는 했음(실패했지만)
    assert any("배치 중단" in r.message for r in caplog.records)


def test_targets_capped_per_run_and_excess_deferred_with_info_log(monkeypatch, caplog):
    capped_config = {**FAKE_CONFIG, "max_targets_per_run": 1}
    monkeypatch.setattr(handler, "load_merge_db_config", lambda: capped_config)
    t1 = _target(bid_id="R25BK01000000_000")
    t2 = _target(bid_id="R25BK02000000_000")
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [t1, t2])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {
        t1["bid_id"]: ["ref1"], t2["bid_id"]: ["ref1"],
    })
    monkeypatch.setattr(inventory, "fetch_documents", lambda bucket, refs: [{"bid_id": t1["bid_id"]}])
    monkeypatch.setattr(handler, "merge_qualification_documents",
                         lambda bid_id, docs: {"merge_conflicts": None})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: None)

    with caplog.at_level("INFO"):
        result = handler.lambda_handler({}, None)

    assert result["total"] == 1  # 상한(1건) 적용 후 실제 처리된 건수만
    assert result["merged"] == 1
    assert any("이번 회차 1건 처리, 1건 다음 주기로 이월" in r.message for r in caplog.records)


def test_targets_within_cap_are_not_deferred(monkeypatch, caplog):
    t1 = _target(bid_id="R25BK01000000_000")
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [t1])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {t1["bid_id"]: ["ref1"]})
    monkeypatch.setattr(inventory, "fetch_documents", lambda bucket, refs: [{"bid_id": t1["bid_id"]}])
    monkeypatch.setattr(handler, "merge_qualification_documents",
                         lambda bid_id, docs: {"merge_conflicts": None})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: None)

    with caplog.at_level("INFO"):
        result = handler.lambda_handler({}, None)

    assert result["total"] == 1
    assert not any("다음 주기로 이월" in r.message for r in caplog.records)


def test_target_absent_from_inventory_excluded_before_cap(monkeypatch, caplog):
    """#80 — daily 인벤토리에 없는 대상(=backfill 소관 행 시뮬레이션)은 상한
    적용 전에 제외돼야 한다. 상한을 1로 좁혀도 daily 인벤토리에 있는 대상만
    남아 상한을 다 안 써도 처리돼야 함을 함께 확인한다."""
    capped_config = {**FAKE_CONFIG, "max_targets_per_run": 1}
    monkeypatch.setattr(handler, "load_merge_db_config", lambda: capped_config)
    backfill_only_target = _target(bid_id="R25BK09000000_000")  # 인벤토리에 없음
    daily_target = _target(bid_id="R25BK01000000_000")
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [backfill_only_target, daily_target])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {daily_target["bid_id"]: ["ref1"]})
    monkeypatch.setattr(inventory, "fetch_documents", lambda bucket, refs: [{"bid_id": daily_target["bid_id"]}])
    monkeypatch.setattr(handler, "merge_qualification_documents",
                         lambda bid_id, docs: {"merge_conflicts": None})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: None)

    with caplog.at_level("INFO"):
        result = handler.lambda_handler({}, None)

    assert result["total"] == 1
    assert result["merged"] == 1
    # 상한(1건) 때문이 아니라 인벤토리 필터로 daily_matched가 이미 1건이었어야 함
    assert any("MERGE_TARGET_FUNNEL db_total=2 daily_matched=1 after_cap=1" in r.message
               for r in caplog.records)
    assert not any("다음 주기로 이월" in r.message for r in caplog.records)


def test_partial_status_target_present_in_inventory_is_included(monkeypatch):
    """partial 재처리는 fetch_merge_targets의 기존 조건이 담당하므로(#80 설계
    결정) 핸들러는 qual_status를 따로 안 가린다 — 인벤토리에만 있으면 처리
    대상에 포함돼야 한다."""
    target = _target(bid_id="R25BK01000000_000")
    target["qual_status"] = "partial"
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [target])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {target["bid_id"]: ["ref1"]})
    monkeypatch.setattr(inventory, "fetch_documents", lambda bucket, refs: [{"bid_id": target["bid_id"]}])
    monkeypatch.setattr(handler, "merge_qualification_documents",
                         lambda bid_id, docs: {"merge_conflicts": None})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: None)

    result = handler.lambda_handler({}, None)

    assert result["total"] == 1
    assert result["merged"] == 1


def test_daily_matched_over_cap_truncated_to_max(monkeypatch, caplog):
    """daily 인벤토리 매칭 건수가 상한(200)을 넘으면 그 매칭된 목록 안에서만
    잘라야 한다(백필은 애초에 인벤토리 필터에서 이미 빠진 뒤의 잘림)."""
    targets = [_target(bid_id=f"R25BK{i:08d}_000") for i in range(210)]
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: targets)
    monkeypatch.setattr(inventory, "build_inventory",
                         lambda bucket: {t["bid_id"]: ["ref1"] for t in targets})
    monkeypatch.setattr(inventory, "fetch_documents", lambda bucket, refs: [{"bid_id": "x"}])
    monkeypatch.setattr(handler, "merge_qualification_documents",
                         lambda bid_id, docs: {"merge_conflicts": None})
    monkeypatch.setattr(db, "has_failed_attachment", lambda conn, no, ord_: False)
    monkeypatch.setattr(handler, "determine_qual_status", lambda found, expected, failed: "merged")
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: None)

    with caplog.at_level("INFO"):
        result = handler.lambda_handler({}, None)

    assert result["total"] == 200
    assert result["merged"] == 200
    assert any("MERGE_TARGET_FUNNEL db_total=210 daily_matched=210 after_cap=200" in r.message
               for r in caplog.records)


def test_zero_daily_matched_logs_no_targets_and_returns_zero_summary(monkeypatch, caplog):
    """DB 대상은 있었지만(전부 백필 소관이라 가정) daily 인벤토리에 하나도
    안 걸리면 MERGE_NO_TARGETS를 찍고 기존 '대상 0건' 조기 반환과 같은 모양의
    summary를 반환해야 한다."""
    backfill_only = [_target(bid_id="R25BK09000000_000"), _target(bid_id="R25BK09000001_000")]
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: backfill_only)
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {})  # daily 파일 전혀 없음
    apply_merge_calls = []
    monkeypatch.setattr(db, "apply_merge", lambda *a, **k: apply_merge_calls.append(a))

    with caplog.at_level("INFO"):
        result = handler.lambda_handler({}, None)

    assert result == {"total": 0, "merged": 0, "partial": 0, "failed": 0, "skipped": 0,
                       "tagged": 0, "conflict_bid_ids": [], "error_bid_ids": []}
    assert apply_merge_calls == []
    assert any("MERGE_NO_TARGETS db_total=2" in r.message for r in caplog.records)


def test_cold_start_connection_failure_propagates_without_catching(monkeypatch):
    def raise_on_connect(config):
        raise RuntimeError("bid_table에 병합 배치가 전제하는 컬럼이 없음")

    monkeypatch.setattr(db, "get_connection", raise_on_connect)

    with pytest.raises(RuntimeError):
        handler.lambda_handler({}, None)


def test_dry_run_defaults_to_true_when_env_unset(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("MERGE_DB_HOST", "h")
    monkeypatch.setenv("MERGE_DB_NAME", "d")
    monkeypatch.setenv("MERGE_DB_USER", "u")
    monkeypatch.setenv("MERGE_DB_PASSWORD", "p")
    assert load_merge_db_config()["dry_run"] is True
