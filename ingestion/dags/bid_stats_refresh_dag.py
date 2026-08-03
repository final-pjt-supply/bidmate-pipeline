"""공고 통계 matview 일간 갱신 DAG.

`/stats` 화면이 읽는 집계를 다시 계산한다(bidmate-backend #125의 alembic 0006).
집계 로직은 전부 matview 정의에 있으므로 여기서 하는 일은 REFRESH 두 번뿐이다.

왜 일 1회인가
    집계가 `bid_ntce_dt < date_trunc('month', CURRENT_DATE)`로 진행 중인 달을
    통째로 뺀다. 그래서 숫자가 실제로 바뀌는 건 매월 1일이다. 일간 갱신은 backfill이
    과거 달 행을 고쳤을 때를 위한 것이고, 전체 재계산이 7초 남짓이라 비용이 없다.

왜 기존 5분 DAG(bidding_daily_pipeline)에 붙이지 않나
    5분마다 45개 조건을 재계산하는 건 순수한 낭비다. 한 달에 한 번 바뀌는 값이다.

왜 API 서버의 크론이 아닌가
    백엔드가 blue-green 배포라 전환 구간에 컨테이너가 둘 뜬다. 컨테이너 내부 크론은
    그때 REFRESH를 중복 발사한다. 막으려면 advisory lock을 직접 짜야 하는데 없는
    문제를 만드는 것이다. Airflow는 스케줄러가 하나라 이 문제가 없다.

접속 정보 (STATS_DB_*)
    이 DAG은 이 compose가 처음으로 서비스 RDS에 붙는 태스크다. 기존 수집·다운로드
    DAG은 결과를 전부 S3에 쓰기 때문에 RDS 설정이 아예 없었다(compose 주석의
    "외부 RDS 없음"). 그래서 STATS_DB_*를 새로 추가했다 — POSTGRES_*는 이미
    Airflow 메타DB가 쓰고 있어 재사용할 수 없다.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from inspect import signature
from zoneinfo import ZoneInfo

import psycopg
from airflow import DAG

try:
    from airflow.providers.standard.operators.python import PythonOperator
except ImportError:
    from airflow.operators.python import PythonOperator

LOCAL_TZ = ZoneInfo(os.environ.get("BIDDING_DAILY_TIMEZONE", "Asia/Seoul"))

# 순서가 중요하다 — bid_stats가 institution_parent를 조인한다.
# CONCURRENTLY는 unique 인덱스가 있어야 하고(0006이 만든다), 갱신 중에도 조회를
# 막지 않는다. 45행짜리라 락 시간 자체는 짧지만, 새벽 4시에도 화면은 열려 있다.
MATVIEWS = ("institution_parent", "bid_stats")

log = logging.getLogger("bid-stats-refresh")


def _dsn() -> str:
    """서비스 RDS 접속 문자열. STATS_DB_* 환경변수에서 읽는다.

    앱과 같은 POSTGRES_*를 쓰면 안 된다 — 이 compose에서 그 이름은 Airflow 자신의
    메타DB(같은 박스의 postgres 컨테이너)가 이미 쓰고 있다. 그대로 쓰면 조용히
    메타DB에 붙어 "relation bid_stats does not exist"로 끝난다.

    기본값도 두지 않는다. host를 localhost로 폴백시키면 설정 누락이 '접속 실패'가
    아니라 '엉뚱한 DB 접속'으로 나타나 원인 파악이 늦어진다.
    """
    missing = [k for k in ("STATS_DB_HOST", "STATS_DB_NAME", "STATS_DB_USER",
                           "STATS_DB_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"서비스 DB 접속 정보가 없다: {', '.join(missing)} — "
            "ingestion/.env에 STATS_DB_*를 넣고 compose를 다시 올릴 것"
        )
    params = {
        "host": os.environ["STATS_DB_HOST"],
        "port": os.environ.get("STATS_DB_PORT", "5432"),
        "dbname": os.environ["STATS_DB_NAME"],
        "user": os.environ["STATS_DB_USER"],
        "password": os.environ["STATS_DB_PASSWORD"],
        # RDS는 보통 SSL 필수다. 로컬 docker(무SSL)에선 비워 둔다.
        "sslmode": os.environ.get("STATS_DB_SSLMODE", "require"),
    }
    return " ".join(f"{k}={v}" for k, v in params.items())


def refresh_stats_matviews(**_context) -> dict:
    """matview 두 개를 순서대로 갱신하고 결과를 확인한다.

    autocommit이 필수다 — REFRESH MATERIALIZED VIEW CONCURRENTLY는 트랜잭션 블록
    안에서 실행할 수 없고, psycopg는 기본으로 트랜잭션을 연다.
    """
    elapsed: dict[str, float] = {}
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        for name in MATVIEWS:
            started = time.monotonic()
            with conn.cursor() as cur:
                cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name}")
            elapsed[name] = round(time.monotonic() - started, 2)
            log.info("%s 갱신 완료 (%.2fs)", name, elapsed[name])

        # 갱신이 조용히 빈 결과를 만들면 화면이 "공고 0건"이라는 거짓을 그린다.
        # 조건 조합은 45개(5업종 x (전체 + 상위 8품목))라 그보다 적으면 이상하다.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), min(computed_at) FROM bid_stats")
            rows, computed_at = cur.fetchone()

    if not rows:
        raise ValueError("bid_stats가 비었다 — 갱신이 실패했거나 원본이 사라졌다")

    log.info("bid_stats %d행, computed_at=%s", rows, computed_at)
    return {"rows": rows, "elapsed": elapsed}


default_args = {
    "owner": "bidding-agent",
    "depends_on_past": False,
    # 일 1회라 재시도 간격을 넉넉히 둔다. RDS가 잠깐 안 붙어도 다음 날까지
    # 기다릴 이유는 없지만, 급하게 몰아칠 이유도 없다.
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

dag_kwargs = {
    "dag_id": "bid_stats_refresh",
    "description": "Refresh the bid statistics materialized views once a day.",
    "default_args": default_args,
    # cron을 KST로 해석시키려면 start_date가 tz-aware여야 한다.
    "start_date": datetime(2026, 1, 1, tzinfo=LOCAL_TZ),
    # 며칠 꺼져 있었어도 과거 날짜를 하나씩 따라잡을 이유가 없다 — 어차피 매번
    # 전체 재계산이라 마지막 한 번이면 같은 결과다.
    "catchup": False,
    "max_active_runs": 1,
    "tags": ["bidding", "stats", "matview"],
}

# Airflow 2는 schedule_interval, Airflow 3은 schedule을 쓴다(기존 DAG과 같은 처리).
if "schedule" in signature(DAG).parameters:
    dag_kwargs["schedule"] = "0 4 * * *"
else:
    dag_kwargs["schedule_interval"] = "0 4 * * *"

with DAG(**dag_kwargs) as dag:
    PythonOperator(
        task_id="refresh_matviews",
        python_callable=refresh_stats_matviews,
    )
