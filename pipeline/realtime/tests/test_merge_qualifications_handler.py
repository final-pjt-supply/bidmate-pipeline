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
    "source_bucket": "bidmate", "dry_run": True,
}


def _target(bid_id="R25BK01152374_000", expected_file_count=1):
    return {
        "bid_ntce_no": bid_id.split("_")[0], "bid_ntce_ord": bid_id.split("_")[1],
        "bid_id": bid_id, "bid_category": "cnstwk",
        "expected_file_count": expected_file_count, "qual_status": "pending",
    }


@pytest.fixture(autouse=True)
def patch_config_and_connection(monkeypatch):
    monkeypatch.setattr(handler, "load_merge_db_config", lambda: FAKE_CONFIG)
    monkeypatch.setattr(db, "get_connection", lambda config: "FAKE_CONN")


def test_no_targets_short_circuits_without_building_inventory(monkeypatch):
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [])
    called = []
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: called.append(bucket))

    result = handler.lambda_handler({}, None)

    assert result["total"] == 0
    assert called == []  # 대상 0건이면 인벤토리 리스팅 자체를 하지 않아야 함


def test_target_with_no_files_found_is_skipped(monkeypatch):
    monkeypatch.setattr(db, "fetch_merge_targets", lambda conn: [_target()])
    monkeypatch.setattr(inventory, "build_inventory", lambda bucket: {})  # 인벤토리에 없음
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
