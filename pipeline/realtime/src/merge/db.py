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


# --- 첨부 없는 공고 스윕(no_attachment) ------------------------------------
# 첨부가 하나도 없는 공고는 추출 산출물이 영원히 안 생긴다. 그런데 병합 배치는
# qualifications/daily/ 인벤토리에 있는 공고만 처리 대상으로 남기므로(handler의
# #80 필터), 이런 공고는 대상에서 매번 조용히 빠지고 qual_status='pending'에
# 영구히 고인다. 실제로 2026-08-06 daily에서 28건 중 7건이 이 상태였다.
#
# 이 행들은 "아직 안 왔다"가 아니라 "올 것이 없다"이므로 별도 종결 상태
# no_attachment로 빼낸다. fetch_merge_targets가 pending/partial/failed만 보고,
# 조회 API는 merged만 노출하므로(app/infra/db/repositories/bid_repository.py)
# 새 값이 어디로도 새지 않는다.

# daily 전용 가드. 백필 행(raw/raw/backfill/...)은 조원 소유라 이 배치가 절대
# 건드리지 않는다(db/backfill_expected_file_count.py와 같은 원칙). LIKE 대신
# strpos를 쓰는 이유는 psycopg2가 파라미터를 받는 순간 쿼리 안의 리터럴 %를
# 플레이스홀더로 해석하기 때문이다 — 나중에 이 쿼리에 파라미터가 붙어도 조용히
# 깨지지 않도록 %가 아예 없는 형태로 고정한다.
DAILY_ONLY_SQL = "strpos(raw_s3_key, 'raw/raw/daily/') = 1"

# expected_file_count = 0 만으로는 부족하다. 압축(zip) 첨부만 달린 공고도 수집
# 시점엔 0으로 잡히는데, 다운로드 단계가 zip을 풀고 나서 값을 올려준다
# (ingestion/json_file_download_daily.correct_expected_counts). 그 창을 그대로
# 종결시키면 멀쩡한 공고가 사라진다. bid_attachments 행이 아예 없다는 조건을
# 같이 걸어 "첨부 목록 자체가 빈 공고"로 좁힌다 — 이 경우엔 풀릴 zip도, 정정될
# 개수도 없다. 적재 로더가 bid_table/bid_attachments를 한 트랜잭션에서 커밋하므로
# (db/load_curated_backfill_to_rds.write_rows) 첨부 행이 아직 안 들어온 중간
# 상태를 잘못 집을 위험도 없다.
NO_ATTACHMENT_WHERE_SQL = f"""qual_status = 'pending'
          AND is_human_verified = FALSE
          AND expected_file_count = 0
          AND {DAILY_ONLY_SQL}
          AND NOT EXISTS (
              SELECT 1 FROM bid_attachments a
              WHERE a.bid_ntce_no = bid_table.bid_ntce_no
                AND a.bid_ntce_ord = bid_table.bid_ntce_ord
          )"""


def _assert_daily_only(where_sql: str) -> None:
    """daily 한정 조건이 실제로 조건절에 박혀 있는지 실행 직전 재확인한다.

    이 스윕은 WHERE 하나에 전 테이블의 상태가 걸린 UPDATE라, 조건이 빠지면
    백필 행까지 한 번에 갈아엎는다. 조용히 넘어가지 않고 즉시 실패시킨다.
    """
    if DAILY_ONLY_SQL not in where_sql:
        raise RuntimeError(
            "안전 가드 실패: no_attachment 스윕 조건절에 daily 한정 필터가 없다 — "
            "백필 행까지 건드릴 위험이 있어 실행을 중단한다."
        )


def sweep_no_attachment(conn, dry_run: bool) -> int:
    """첨부 목록이 빈 daily 공고를 pending에서 no_attachment로 확정하고 건수를 반환한다.

    DRY_RUN=true면 UPDATE 대신 같은 조건으로 COUNT만 세어 "이번에 몇 건이
    빠졌을지"를 로그로 남긴다(apply_merge의 dry-run 관례와 동일).
    """
    _assert_daily_only(NO_ATTACHMENT_WHERE_SQL)

    if dry_run:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM bid_table WHERE {NO_ATTACHMENT_WHERE_SQL}")
            count = cur.fetchone()[0]
        logger.info("DRY_RUN — no_attachment 스윕 생략: 대상 %d건", count)
        return count

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bid_table SET qual_status = 'no_attachment', updated_at = NOW() "
            f"WHERE {NO_ATTACHMENT_WHERE_SQL}"
        )
        count = cur.rowcount
    conn.commit()
    return count


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


# --- 품목 태그(#97) --------------------------------------------------------
# bid_table은 건드리지 않고 bid_tags에 따로 쌓는다. 이 테이블은 파이프라인 팀
# 소유이고 매칭 로직이 어떤 컬럼을 참조하는지 확정할 수 없어, 컬럼 추가는
# 영향 범위를 예측하기 어렵다.


def bid_tags_available(conn) -> bool:
    """bid_tags 테이블이 있는지 확인한다. 없으면 태깅만 건너뛴다.

    verify_schema와 달리 여기서는 fail-loud로 가지 않는다. 태깅은 나중에 얹은
    기능이라, 마이그레이션 전에 코드가 먼저 배포돼도 기존 자격요건 병합은
    그대로 돌아야 한다. 없다는 사실은 로그로 남겨 조용히 묻히지 않게 한다.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.bid_tags') IS NOT NULL")
        exists = cur.fetchone()[0]
    if not exists:
        logger.warning(
            "bid_tags 테이블이 없어 태깅을 건너뛴다 — "
            "db/schema/03_bid_tags.sql을 적용할 것(자격요건 병합은 정상 진행)"
        )
    return bool(exists)


def fetch_untagged_targets(conn, limit: int) -> list[dict]:
    """아직 태그가 없는 공고를 가져온다.

    자격요건 병합 대상과 별개로 뽑는다. 병합은 daily 첨부가 도착한 공고만
    처리하는데, 태깅에 필요한 건 제목·업종·코드뿐이라 첨부와 무관하다.
    같은 목록을 쓰면 첨부가 영영 안 오는 공고는 태그도 못 받는다.

    ⚠ 이미 태그가 있는 행은 다시 뽑지 않는다. 모델을 재학습했거나 태그 체계를
      바꿔서 전체를 다시 매기려면 scripts/retag_all.py로 따로 돌려야 한다.
      매 회차 재판정하게 두면, 코드가 있어도 매핑에 없는 공고가 매번 다시
      뽑혀 무한히 순환한다.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT b.bid_id, b.bid_ntce_nm, b.bid_category, b.item_codes, "
            "b.main_cnstty_nm "
            "FROM bid_table b LEFT JOIN bid_tags t ON t.bid_id = b.bid_id "
            "WHERE t.bid_id IS NULL AND b.bid_id IS NOT NULL "
            "ORDER BY b.bid_id DESC LIMIT %s",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def upsert_tags(conn, rows: list[tuple], dry_run: bool) -> int:
    """(bid_id, tag, source, confidence) 목록을 bid_tags에 UPSERT한다.

    이력은 남기지 않는다. 태그가 바뀌는 건 대부분 추정 -> 정답 방향이고,
    "작년에 우리가 뭐라고 분류했었나"를 추적할 요건이 없다. 필요해지면
    bid_tags_history를 따로 붙이면 되고, 지금 안 만든다고 막히지 않는다.
    """
    if not rows:
        return 0
    if dry_run:
        logger.info("DRY_RUN — 태그 UPSERT 생략: %d건 (예: %s)", len(rows), rows[:3])
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO bid_tags (bid_id, tag, source, confidence) VALUES %s "
            "ON CONFLICT (bid_id) DO UPDATE SET "
            "tag = EXCLUDED.tag, source = EXCLUDED.source, "
            "confidence = EXCLUDED.confidence, updated_at = NOW()",
            rows,
        )
    conn.commit()
    return len(rows)
