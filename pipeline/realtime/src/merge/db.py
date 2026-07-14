# -*- coding: utf-8 -*-
"""RDS Postgres 접속 + bid_table 병합 UPDATE(#66).

콜드스타트마다(커넥션을 새로 열 때만) bid_table에 이 배치가 전제하는 6개
컬럼(merge_conflicts, is_human_verified, extraction_evidence, extraction_meta,
qual_status, merged_at)이 실제로 있는지 information_schema로 확인한다
(런타임 가드, fail-loud). db/schema/01_bid_table.sql은 docker-compose
로컬 개발용이라 운영 RDS에 그대로 적용됐다는 보장이 원래 없었다 — 2026-07-12
사용자가 직접 라이브 DB 조회로 6개 컬럼 존재를 확인했지만, 스키마가 이후에
또 바뀔 수 있으니(다른 팀/파이프라인이 같은 테이블을 만짐) 이 가드는 계속
유지한다. 조용히 건너뛰면 qual_status가 원인불명으로 영원히 pending에
머무는 상황을 만들기 때문에, 컬럼이 없으면 즉시 RuntimeError로 실패시킨다.

비밀값(DB 비밀번호 등)은 common/config.load_merge_db_config()를 거쳐
환경변수로만 받는다 — 이 파일에도, 다른 어디에도 하드코딩하지 않는다.
"""
import logging

import psycopg2
import psycopg2.extras

from merge.logic import ALL_QUALIFICATION_FIELDS

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "merge_conflicts",
    "is_human_verified",
    "extraction_evidence",
    "extraction_meta",
    "qual_status",
    "merged_at",
)

_connection = None
_schema_verified = False


def get_connection(config: dict):
    """DB 접속을 얻는다. Lambda 웜스타트 사이에 커넥션을 재사용하고(콜드스타트당
    1번만 psycopg2.connect), 스키마 가드도 커넥션이 새로 열릴 때만 재검증한다."""
    global _connection, _schema_verified
    if _connection is None or _connection.closed:
        _connection = psycopg2.connect(
            host=config["db_host"],
            port=config["db_port"],
            dbname=config["db_name"],
            user=config["db_user"],
            password=config["db_password"],
        )
        _schema_verified = False
    if not _schema_verified:
        verify_schema(_connection)
        _schema_verified = True
    return _connection


def verify_schema(conn) -> None:
    """bid_table에 이 배치가 전제하는 컬럼이 실제로 있는지 확인한다(런타임 가드)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'bid_table' AND column_name = ANY(%s)",
            (list(REQUIRED_COLUMNS),),
        )
        found = {row[0] for row in cur.fetchall()}
    missing = set(REQUIRED_COLUMNS) - found
    if missing:
        raise RuntimeError(
            f"bid_table에 병합 배치가 전제하는 컬럼이 없음: {sorted(missing)} "
            "— 설계 전제가 깨졌다. 코드를 실행하기 전에 실제 스키마를 다시 확인할 것."
        )
    logger.info("bid_table 스키마 가드 통과: 필수 컬럼 %d개 확인됨", len(REQUIRED_COLUMNS))


def fetch_merge_targets(conn) -> list[dict]:
    """병합 대상 bid_table 행을 가져온다.

    필터: qual_status IN ('pending','partial','failed') AND is_human_verified
    = FALSE. is_human_verified=TRUE는 qual_status가 무엇이든 항상 제외한다 —
    사람이 검토/수정한 행을 배치가 재실행 때마다 덮어쓰지 않게 하는 안전장치
    (2단계 설계 결정).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT bid_ntce_no, bid_ntce_ord, bid_id, bid_category, "
            "expected_file_count, qual_status "
            "FROM bid_table "
            "WHERE qual_status IN ('pending', 'partial', 'failed') "
            "AND is_human_verified = FALSE"
        )
        return [dict(row) for row in cur.fetchall()]


def has_failed_attachment(conn, bid_ntce_no: str, bid_ntce_ord: str) -> bool:
    """bid_attachments.status='failed'인 첨부가 있는지 확인한다(merge.logic.
    determine_qual_status의 has_failed_attachment 인자로 바로 넘길 값).

    ⚠ 2026-07-12 기준 이 파이프라인의 어떤 handler도 bid_attachments.status를
    'failed'로 갱신하지 않는다(1~2단계 조사 확인, merge/logic.py의
    determine_qual_status 독스트링과 동일한 갭) — 지금은 이 쿼리가 실질적으로
    항상 False를 반환하지만, 나중에 실패 갱신 로직이 붙으면 자동으로 살아나게
    분기를 미리 만들어둔다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM bid_attachments "
            "WHERE bid_ntce_no = %s AND bid_ntce_ord = %s AND status = 'failed')",
            (bid_ntce_no, bid_ntce_ord),
        )
        return cur.fetchone()[0]


def apply_merge(
    conn,
    bid_ntce_no: str,
    bid_ntce_ord: str,
    merged: dict,
    qual_status: str,
    dry_run: bool,
) -> None:
    """merge.logic.merge_qualification_documents()의 결과를 bid_table에
    UPDATE한다. found_file_count > 0인 경우에만 호출된다는 전제이므로(호출부
    책임 — pending 유지 case는 아예 이 함수를 호출하지 않아야 함) 항상
    merged_at=NOW()를 갱신한다.

    DRY_RUN=true면 실제 UPDATE를 실행하지 않고 대상 bid_id + qual_status
    전이 + merge_conflicts 유무만 로그로 남긴다 — #66 10건 사전 검증 계획
    (2단계)의 dry-run 단계용.
    """
    columns = list(ALL_QUALIFICATION_FIELDS) + ["merge_conflicts", "extraction_evidence", "extraction_meta"]
    values = [_to_db_value(merged.get(col)) for col in columns]

    if dry_run:
        logger.info(
            "DRY_RUN — 실제 UPDATE 생략: bid_ntce_no=%s bid_ntce_ord=%s qual_status=%s "
            "merge_conflicts_있음=%s",
            bid_ntce_no, bid_ntce_ord, qual_status, bool(merged.get("merge_conflicts")),
        )
        return

    set_clause = ", ".join(f"{col} = %s" for col in columns)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE bid_table SET {set_clause}, qual_status = %s, merged_at = NOW(), "
            "updated_at = NOW() WHERE bid_ntce_no = %s AND bid_ntce_ord = %s",
            values + [qual_status, bid_ntce_no, bid_ntce_ord],
        )
    conn.commit()
    logger.info(
        "bid_table 병합 UPDATE 완료: bid_ntce_no=%s bid_ntce_ord=%s qual_status=%s",
        bid_ntce_no, bid_ntce_ord, qual_status,
    )


def _to_db_value(value):
    """JSONB 컬럼(리스트/딕셔너리 값)은 psycopg2.extras.Json으로 감싸고, 스칼라는
    그대로 넘긴다 — merge.logic이 이미 컬럼별로 올바른 파이썬 타입(list/dict/
    스칼라/None)을 만들어주므로 타입만 보고 판단해도 충분하다."""
    if isinstance(value, (list, dict)):
        return psycopg2.extras.Json(value)
    return value
