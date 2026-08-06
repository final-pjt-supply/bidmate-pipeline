# -*- coding: utf-8 -*-
"""merge/db.py 단위테스트 — 실제 Postgres 없이 fake connection/cursor로 검증."""
import sys
from pathlib import Path

import psycopg2.extras
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from merge import db  # noqa: E402


class FakeCursor:
    def __init__(self, fetchall_result=None, fetchone_result=None, rowcount=0):
        self.fetchall_result = fetchall_result or []
        self.fetchone_result = fetchone_result
        self.rowcount = rowcount
        self.executed: list[tuple] = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.fetchall_result

    def fetchone(self):
        return self.fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.closed = 0
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True


@pytest.fixture(autouse=True)
def reset_module_globals():
    db._connection = None
    db._schema_verified = False
    yield
    db._connection = None
    db._schema_verified = False


def test_verify_schema_passes_when_all_columns_present():
    cursor = FakeCursor(fetchall_result=[(c,) for c in db.REQUIRED_COLUMNS])
    conn = FakeConnection(cursor)
    db.verify_schema(conn)  # 예외 없이 통과해야 함


def test_verify_schema_raises_when_columns_missing():
    cursor = FakeCursor(fetchall_result=[("qual_status",), ("merged_at",)])
    conn = FakeConnection(cursor)
    with pytest.raises(RuntimeError) as exc_info:
        db.verify_schema(conn)
    message = str(exc_info.value)
    assert "merge_conflicts" in message
    assert "is_human_verified" in message


def test_get_connection_verifies_schema_only_on_new_connection(monkeypatch):
    cursor = FakeCursor(fetchall_result=[(c,) for c in db.REQUIRED_COLUMNS])
    fake_conn = FakeConnection(cursor)
    monkeypatch.setattr(psycopg2, "connect", lambda **kwargs: fake_conn)

    config = {"db_host": "h", "db_port": 5432, "db_name": "d", "db_user": "u", "db_password": "p"}
    conn1 = db.get_connection(config)
    assert conn1 is fake_conn
    schema_check_calls = sum(1 for q, _ in cursor.executed if "information_schema" in q)
    assert schema_check_calls == 1

    conn2 = db.get_connection(config)
    assert conn2 is fake_conn
    # 두 번째 호출은 커넥션이 재사용되므로 스키마 재검증이 없어야 함
    schema_check_calls_after = sum(1 for q, _ in cursor.executed if "information_schema" in q)
    assert schema_check_calls_after == 1


def test_fetch_merge_targets_filters_pending_partial_failed_and_not_verified():
    cursor = FakeCursor(fetchall_result=[
        {"bid_ntce_no": "R25BK01152374", "bid_ntce_ord": "000", "bid_id": "R25BK01152374_000",
         "bid_category": "cnstwk", "expected_file_count": 1, "qual_status": "pending"},
    ])
    conn = FakeConnection(cursor)
    result = db.fetch_merge_targets(conn)
    query, _ = cursor.executed[0]
    assert "qual_status IN ('pending', 'partial', 'failed')" in query
    assert "is_human_verified = FALSE" in query
    assert result[0]["bid_id"] == "R25BK01152374_000"


def test_has_failed_attachment_returns_cursor_result():
    cursor = FakeCursor(fetchone_result=(True,))
    conn = FakeConnection(cursor)
    assert db.has_failed_attachment(conn, "R25BK01152374", "000") is True
    query, params = cursor.executed[0]
    assert params == ("R25BK01152374", "000")


def test_apply_merge_dry_run_skips_execute_and_commit():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    merged = {"company_size_limit": "sme_only", "merge_conflicts": {"x": []},
              "extraction_evidence": None, "extraction_meta": {}}
    db.apply_merge(conn, "R25BK01152374", "000", merged, "merged", dry_run=True)
    assert cursor.executed == []
    assert conn.committed is False


def test_apply_merge_executes_update_and_commits_when_not_dry_run():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    merged = {"company_size_limit": "sme_only", "required_licenses": [{"name_raw": "x"}],
              "merge_conflicts": None, "extraction_evidence": None, "extraction_meta": {"not_found": []}}
    db.apply_merge(conn, "R25BK01152374", "000", merged, "merged", dry_run=False)
    assert len(cursor.executed) == 1
    query, params = cursor.executed[0]
    assert "UPDATE bid_table SET" in query
    assert "qual_status = %s, merged_at = NOW(), updated_at = NOW()" in query
    assert params[-3:] == ["merged", "R25BK01152374", "000"]
    assert conn.committed is True


def test_sweep_no_attachment_marks_daily_rows_without_attachments_as_merged():
    cursor = FakeCursor(rowcount=7)
    conn = FakeConnection(cursor)

    assert db.sweep_no_attachment(conn, dry_run=False) == 7

    query, params = cursor.executed[0]
    assert params is None
    assert "UPDATE bid_table SET qual_status = 'merged'" in query
    assert "merged_at" not in query                    # 병합한 적 없는 행에 시각을 박지 않음
    assert "qual_status IN ('pending', 'no_attachment')" in query   # partial/failed은 제외
    assert "is_human_verified = FALSE" in query        # 사람이 검토한 행은 제외
    assert "expected_file_count = 0" in query
    assert "NOT EXISTS" in query and "bid_attachments" in query
    assert db.DAILY_ONLY_SQL in query                  # 백필 행 보호
    assert conn.committed is True


def test_sweep_absorbs_legacy_no_attachment_rows():
    """2026-08-06 오전에 no_attachment로 바꿔놓은 362건이 스윕에 흡수돼야 한다 —
    운영 DB가 VPC 안이라 손으로 마이그레이션할 수단이 없다."""
    assert "'no_attachment'" in db.SWEEPABLE_STATUS_SQL
    assert db.SWEEPABLE_STATUS_SQL in db.NO_ATTACHMENT_WHERE_SQL


def test_recover_late_attachment_returns_rows_to_pending():
    cursor = FakeCursor(rowcount=2)
    conn = FakeConnection(cursor)

    assert db.recover_late_attachment(conn, dry_run=False) == 2

    query, _ = cursor.executed[0]
    assert "UPDATE bid_table SET qual_status = 'pending'" in query
    assert "qual_status = 'merged'" in query
    # 실제 병합을 거친 행까지 되돌리면 무한 재처리에 빠진다
    assert "extraction_meta IS NULL" in query
    assert "expected_file_count > 0" in query
    assert "EXISTS" in query and "bid_attachments" in query
    assert db.DAILY_ONLY_SQL in query
    assert conn.committed is True


def test_sweep_and_recover_conditions_are_mutually_exclusive():
    """같은 행이 두 조건에 동시에 걸리면 회차마다 merged↔pending을 오간다."""
    assert "expected_file_count = 0" in db.NO_ATTACHMENT_WHERE_SQL
    assert "expected_file_count > 0" in db.LATE_ATTACHMENT_WHERE_SQL
    assert "NOT EXISTS" in db.NO_ATTACHMENT_WHERE_SQL
    assert "AND EXISTS" in db.LATE_ATTACHMENT_WHERE_SQL


@pytest.mark.parametrize("fn", [db.sweep_no_attachment, db.recover_late_attachment])
def test_status_sweep_dry_run_counts_without_updating(fn):
    cursor = FakeCursor(fetchone_result=(7,))
    conn = FakeConnection(cursor)

    assert fn(conn, dry_run=True) == 7

    query, _ = cursor.executed[0]
    assert query.lstrip().startswith("SELECT COUNT(*)")
    assert "UPDATE" not in query
    assert conn.committed is False


def test_status_sweep_refuses_to_run_without_daily_guard(monkeypatch):
    """daily 한정 조건이 조건절에서 빠지면 백필 행까지 상태를 갈아엎는다 —
    실행 직전에 알아채고 즉시 실패해야 한다."""
    monkeypatch.setattr(db, "NO_ATTACHMENT_WHERE_SQL", "qual_status = 'pending'")
    cursor = FakeCursor(rowcount=1)
    conn = FakeConnection(cursor)

    with pytest.raises(RuntimeError) as exc_info:
        db.sweep_no_attachment(conn, dry_run=False)

    assert "daily" in str(exc_info.value)
    assert cursor.executed == []


def test_sweep_conditions_have_no_percent_placeholder_hazard():
    """psycopg2는 파라미터가 붙는 순간 쿼리 안의 리터럴 %를 플레이스홀더로 읽는다.
    LIKE 대신 strpos를 쓰는 이유이므로 %가 다시 새어들어오지 않게 못 박는다."""
    assert "%" not in db.NO_ATTACHMENT_WHERE_SQL
    assert "%" not in db.LATE_ATTACHMENT_WHERE_SQL


def test_to_db_value_wraps_list_and_dict_as_json():
    assert isinstance(db._to_db_value([1, 2]), psycopg2.extras.Json)
    assert isinstance(db._to_db_value({"a": 1}), psycopg2.extras.Json)


def test_to_db_value_passes_through_scalars():
    assert db._to_db_value("sme_only") == "sme_only"
    assert db._to_db_value(None) is None
    assert db._to_db_value(True) is True
