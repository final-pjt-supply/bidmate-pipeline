"""Airflow DAG for the 5-minute daily bidding ingestion pipeline."""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
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


def mark_success(**context) -> None:
    plan = context["ti"].xcom_pull(task_ids="plan_window")
    Variable.set(CURSOR_VAR, plan["windowEndIso"])
    log.info("%s=%s 갱신 완료", CURSOR_VAR, plan["windowEndIso"])


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

    mark_daily_success = PythonOperator(
        task_id="mark_success",
        python_callable=mark_success,
    )

    plan_daily_window >> collect_daily_notices_task >> download_daily_files_task >> mark_daily_success
