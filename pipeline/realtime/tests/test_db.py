# -*- coding: utf-8 -*-
"""merge/db.py 단위테스트 — 실제 Postgres 없이 fake connection/cursor로 검증."""
import sys
from pathlib import Path

import psycopg2.extras
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from merge import db  # noqa: E402


class FakeCursor:
    def __init__(self, fetchall_result=None, fetchone_result=None):
        self.fetchall_result = fetchall_result or []
        self.fetchone_result = fetchone_result
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


def test_to_db_value_wraps_list_and_dict_as_json():
    assert isinstance(db._to_db_value([1, 2]), psycopg2.extras.Json)
    assert isinstance(db._to_db_value({"a": 1}), psycopg2.extras.Json)


def test_to_db_value_passes_through_scalars():
    assert db._to_db_value("sme_only") == "sme_only"
    assert db._to_db_value(None) is None
    assert db._to_db_value(True) is True
