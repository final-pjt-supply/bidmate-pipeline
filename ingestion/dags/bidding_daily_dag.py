"""Airflow DAG for the 5-minute daily bidding ingestion pipeline."""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta
from inspect import signature
from pathlib import Path
from zoneinfo import ZoneInfo

from airflow import DAG
from airflow.models import Variable

try:
    from airflow.providers.standard.operators.python import PythonOperator
except ImportError:
    from airflow.operators.python import PythonOperator


# EC2/Airflow 환경에서 repo 위치나 Python 경로가 다르면 환경변수로 덮어쓴다.
PROJECT_DIR = os.environ.get("BIDDING_AGENT_HOME", str(Path(__file__).resolve().parents[1]))
PYTHON_BIN = os.environ.get("BIDDING_AGENT_PYTHON", "python")
BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")

# RDS 적재 스크립트는 ingestion/이 아니라 db/에 있다. 절대경로로 넘기면 subprocess의 cwd와
# 무관하게 실행되고, 스크립트 파일 자체의 위치 기준으로 sys.path가 잡혀 import도 그대로 된다.
DB_DIR = os.environ.get("BIDDING_AGENT_DB_HOME", str(Path(PROJECT_DIR).parent / "db"))
RDS_LOAD_SCRIPT = str(Path(DB_DIR) / "load_curated_daily_to_rds.py")

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from daily_stats import update_hourly_stats as write_hourly_stats
from daily_stats import write_5min_stats

# 수집은 실제 공고 조회 창이므로 5분을 기본으로 둔다.
COLLECT_MINUTES = int(os.environ.get("BIDDING_DAILY_COLLECT_MINUTES", "5"))

# 다운로드는 task 지연을 흡수하기 위해 curated 조회 창을 조금 넓게 잡는다.
DOWNLOAD_MINUTES = int(os.environ.get("BIDDING_DAILY_DOWNLOAD_MINUTES", "15"))

# gap이 이 값을 넘으면 자동 복구 대신 backfill 대상으로 남기고 최근 5분 흐름으로 복귀한다.
GAP_THRESHOLD_MINUTES = int(os.environ.get("BIDDING_DAILY_GAP_THRESHOLD_MINUTES", "30"))

LOCAL_TZ = ZoneInfo(os.environ.get("BIDDING_DAILY_TIMEZONE", "Asia/Seoul"))
CURSOR_VAR = os.environ.get("BIDDING_DAILY_CURSOR_VAR", "bidding_daily_last_success_at")
GAP_MANIFEST_PREFIX = os.environ.get(
    "BIDDING_DAILY_GAP_MANIFEST_PREFIX",
    "raw/downloads/daily/_metadata/gaps",
)

# 수집 커서(CURSOR_VAR)와 일부러 분리한다. RDS 적재가 실패해도 수집 커서는 그대로 전진하는데,
# 같은 커서를 쓰면 실패한 구간이 "이미 지나간 것"으로 취급되어 다시는 RDS에 안 들어간다.
# 별도 커서를 두면 RDS 적재 실패 시 커서가 안 움직이고, 다음 실행이 그 구간을 자동으로
# 다시 처리한다(self-healing) — 외부 API 호출 예산 문제가 아니라 우리 S3를 다시 읽는
# 것뿐이라 재시도 비용이 낮아서, 수집 단계 같은 gap-threshold 로직도 필요 없다.
RDS_CURSOR_VAR = os.environ.get("BIDDING_DAILY_RDS_CURSOR_VAR", "bidding_daily_rds_last_success_at")

log = logging.getLogger("bidding-daily-dag")


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ).replace(second=0, microsecond=0)


def dag_id_from(context) -> str:
    dag = context.get("dag")
    if dag:
        return dag.dag_id
    return context["dag_run"].dag_id


def parse_cursor(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        log.warning("Airflow Variable %s 값이 잘못되어 최근 %s분 기준으로 대체합니다: %s", CURSOR_VAR, COLLECT_MINUTES, value)
        return fallback

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def put_gap_manifest(payload: dict) -> None:
    """자동 복구하지 않은 구간을 S3 manifest로 남긴다. S3 실패 시 Airflow 로그에는 반드시 남긴다."""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    log.warning("daily gap manifest: %s", body)

    try:
        import boto3
    except ImportError:
        log.warning("boto3가 없어 gap manifest는 Airflow 로그에만 남겼습니다.")
        return

    run_dt = now_local()
    key = (
        f"{GAP_MANIFEST_PREFIX}/year={run_dt:%Y}/month={run_dt:%m}/day={run_dt:%d}/hour={run_dt:%H}/"
        f"gap_{run_dt:%Y%m%d%H%M%S}_{payload['reason']}.json"
    )
    try:
        boto3.client("s3").put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
    except Exception as exc:
        log.warning("S3 gap manifest 저장 실패. Airflow 로그 기록만 유지합니다: %s", exc)
        return

    log.warning("daily gap manifest 저장: s3://%s/%s", BUCKET_NAME, key)


def plan_window(**context) -> dict:
    window_end = now_local()
    fallback_cursor = window_end - timedelta(minutes=COLLECT_MINUTES)
    cursor = parse_cursor(Variable.get(CURSOR_VAR, default_var=None), fallback_cursor)
    gap_minutes = max(0, (window_end - cursor).total_seconds() / 60)
    gap_exceeded = gap_minutes > GAP_THRESHOLD_MINUTES

    if gap_exceeded:
        skipped_end = window_end - timedelta(minutes=COLLECT_MINUTES)
        if skipped_end > cursor:
            put_gap_manifest(
                {
                    "reason": "gap_threshold_exceeded",
                    "dagId": dag_id_from(context),
                    "runId": context["run_id"],
                    "gapStart": cursor.isoformat(),
                    "gapEnd": skipped_end.isoformat(),
                    "gapMinutes": math.ceil((skipped_end - cursor).total_seconds() / 60),
                    "thresholdMinutes": GAP_THRESHOLD_MINUTES,
                    "action": "mark_for_backfill_and_continue_recent_5_minutes",
                }
            )

        # 긴 gap은 backfill 대상으로 넘겼으므로 다음 실행이 계속 과거를 물고 늘어지지 않게 기준점을 이동한다.
        Variable.set(CURSOR_VAR, skipped_end.isoformat())
        cursor = skipped_end
        collect_minutes = COLLECT_MINUTES
    else:
        collect_minutes = max(COLLECT_MINUTES, math.ceil(gap_minutes))

    download_minutes = max(DOWNLOAD_MINUTES, collect_minutes)
    plan = {
        "cursorIso": cursor.isoformat(),
        "windowEndIso": window_end.isoformat(),
        "windowEndCli": f"{window_end:%Y-%m-%d %H:%M}",
        "collectMinutes": collect_minutes,
        "downloadMinutes": download_minutes,
        "gapMinutes": math.ceil(gap_minutes),
        "gapThresholdExceeded": gap_exceeded,
    }
    log.info("daily window plan: %s", json.dumps(plan, ensure_ascii=False))
    return plan


def run_script(script_name: str, *args: str) -> None:
    command = [PYTHON_BIN, script_name, *args]
    log.info("실행: cwd=%s command=%s", PROJECT_DIR, command)
    subprocess.run(command, cwd=PROJECT_DIR, check=True)


def collect_daily_notices(**context) -> None:
    plan = context["ti"].xcom_pull(task_ids="plan_window")
    run_script(
        "raw_json_daily.py",
        "--minutes",
        str(plan["collectMinutes"]),
        "--end-dt",
        plan["windowEndCli"],
    )


def download_daily_files(**context) -> None:
    plan = context["ti"].xcom_pull(task_ids="plan_window")
    run_script("json_file_download_daily.py", "--minutes", str(plan["downloadMinutes"]))


def update_5min_stats(**context) -> dict:
    """이번 DAG run의 5분 단위 첨부파일 다운로드 통계를 S3에 저장한다."""
    plan = context["ti"].xcom_pull(task_ids="plan_window")
    result = write_5min_stats(BUCKET_NAME, plan, context)
    log.info("5분 통계 저장 완료: %s", json.dumps(result, ensure_ascii=False))
    return result


def update_hourly_stats(**context) -> dict:
    """5분 다운로드 통계를 hour 통계에 증분 반영하고, 정시에는 직전 hour를 확정한다."""
    five_min_result = context["ti"].xcom_pull(task_ids="update_5min_stats")
    result = write_hourly_stats(BUCKET_NAME, five_min_result, context)
    log.info("1시간 통계 갱신 완료: %s", json.dumps(result, ensure_ascii=False))
    return result


def mark_success(**context) -> None:
    plan = context["ti"].xcom_pull(task_ids="plan_window")
    Variable.set(CURSOR_VAR, plan["windowEndIso"])
    log.info("%s=%s 갱신 완료", CURSOR_VAR, plan["windowEndIso"])


def load_daily_to_rds(**context) -> dict:
    """curated daily JSON 중 RDS 커서 이후 갱신된 것만 골라 bid_table/bid_attachments에 적재한다.

    until은 이번 실행의 수집 창 끝(plan.windowEndIso)으로 고정한다 — RDS 적재가 수집보다
    앞서 나가서 "아직 이번 DAG run이 수집하지 않은" 미래 구간을 걸러버리는 걸 막기 위함이다.
    """
    plan = context["ti"].xcom_pull(task_ids="plan_window")
    since_iso = Variable.get(RDS_CURSOR_VAR, default_var=None) or plan["cursorIso"]
    until_iso = plan["windowEndIso"]
    run_script(RDS_LOAD_SCRIPT, "--since", since_iso, "--until", until_iso, "--yes")
    return {"sinceIso": since_iso, "untilIso": until_iso}


def mark_rds_success(**context) -> None:
    result = context["ti"].xcom_pull(task_ids="load_daily_to_rds")
    Variable.set(RDS_CURSOR_VAR, result["untilIso"])
    log.info("%s=%s 갱신 완료", RDS_CURSOR_VAR, result["untilIso"])


def record_rds_load_failure(context) -> None:
    # 수집 실패(record_failed_window)와 달리 manifest는 안 남긴다: RDS 커서가 전진하지 않은
    # 채로 남아있는 것 자체가 "다시 처리해야 할 구간" 표시이고, 우리 S3를 다시 읽는 재시도라
    # 비용도 낮다. Airflow 로그/재시도 이력만으로 추적하기에 충분하다.
    log.error("load_daily_to_rds 실패 — RDS 커서(%s) 미전진, 다음 실행이 자동 재시도함", RDS_CURSOR_VAR)


def record_failed_window(context) -> None:
    ti = context["ti"]
    plan = ti.xcom_pull(task_ids="plan_window") or {}
    window_end = plan.get("windowEndIso") or now_local().isoformat()
    window_start = plan.get("cursorIso") or (now_local() - timedelta(minutes=COLLECT_MINUTES)).isoformat()
    put_gap_manifest(
        {
            "reason": "task_failed_after_retries",
            "dagId": dag_id_from(context),
            "runId": context["run_id"],
            "taskId": ti.task_id,
            "windowStart": window_start,
            "windowEnd": window_end,
            "collectMinutes": plan.get("collectMinutes"),
            "downloadMinutes": plan.get("downloadMinutes"),
            "action": "mark_for_backfill",
        }
    )


default_args = {
    "owner": "bidding-agent",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
    "on_failure_callback": record_failed_window,
}

dag_kwargs = {
    "dag_id": "bidding_daily_pipeline",
    "description": "Collect recent bid notices and download their attachments every 5 minutes.",
    "default_args": default_args,
    "start_date": datetime(2026, 1, 1),
    # 실시간성 DAG라 과거 미실행 구간을 한꺼번에 따라잡지 않는다.
    "catchup": False,
    # 이전 5분 실행이 끝나기 전에 다음 실행이 겹치지 않게 막는다.
    "max_active_runs": 1,
    "tags": ["bidding", "daily", "s3"],
}

# Airflow 2는 schedule_interval, Airflow 3은 schedule을 사용한다.
if "schedule" in signature(DAG).parameters:
    dag_kwargs["schedule"] = "*/5 * * * *"
else:
    dag_kwargs["schedule_interval"] = "*/5 * * * *"

with DAG(**dag_kwargs) as dag:
    plan_daily_window = PythonOperator(
        task_id="plan_window",
        python_callable=plan_window,
    )

    collect_daily_notices_task = PythonOperator(
        task_id="collect_daily_notices",
        python_callable=collect_daily_notices,
    )

    download_daily_files_task = PythonOperator(
        task_id="download_daily_files",
        python_callable=download_daily_files,
    )

    update_5min_stats_task = PythonOperator(
        task_id="update_5min_stats",
        python_callable=update_5min_stats,
        # 다운로드 실패도 통계 JSON에 남겨야 하므로 upstream 완료 상태만 기다린다.
        trigger_rule="all_done",
    )

    update_hourly_stats_task = PythonOperator(
        task_id="update_hourly_stats",
        python_callable=update_hourly_stats,
    )

    mark_daily_success = PythonOperator(
        task_id="mark_success",
        python_callable=mark_success,
    )

    load_daily_to_rds_task = PythonOperator(
        task_id="load_daily_to_rds",
        python_callable=load_daily_to_rds,
        on_failure_callback=record_rds_load_failure,
    )

    mark_rds_success_task = PythonOperator(
        task_id="mark_rds_success",
        python_callable=mark_rds_success,
    )

    plan_daily_window >> collect_daily_notices_task >> download_daily_files_task
    download_daily_files_task >> mark_daily_success
    download_daily_files_task >> update_5min_stats_task >> update_hourly_stats_task
    # RDS 적재는 수집 커서(mark_success)와 독립된 별도 흐름이라 병렬로 둔다.
    download_daily_files_task >> load_daily_to_rds_task >> mark_rds_success_task
