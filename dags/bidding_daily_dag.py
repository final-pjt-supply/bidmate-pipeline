"""Airflow DAG for the 5-minute daily bidding ingestion pipeline."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator


# EC2/Airflow 환경에서 repo 위치나 Python 경로가 다르면 환경변수로 덮어쓴다.
PROJECT_DIR = os.environ.get("BIDDING_AGENT_HOME", str(Path(__file__).resolve().parents[1]))
PYTHON_BIN = os.environ.get("BIDDING_AGENT_PYTHON", "python")

# 수집은 실제 공고 조회 창이므로 5분을 기본으로 둔다.
COLLECT_MINUTES = int(os.environ.get("BIDDING_DAILY_COLLECT_MINUTES", "5"))

# 다운로드는 task 지연을 흡수하기 위해 curated 조회 창을 조금 넓게 잡는다.
DOWNLOAD_MINUTES = int(os.environ.get("BIDDING_DAILY_DOWNLOAD_MINUTES", "15"))


def script_command(script_name: str, minutes: int) -> str:
    return f'cd "{PROJECT_DIR}" && "{PYTHON_BIN}" {script_name} --minutes {minutes}'


default_args = {
    "owner": "bidding-agent",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
}


with DAG(
    dag_id="bidding_daily_pipeline",
    description="Collect recent bid notices and download their attachments every 5 minutes.",
    default_args=default_args,
    schedule_interval="*/5 * * * *",
    start_date=datetime(2026, 1, 1),
    # 실시간성 DAG라 과거 미실행 구간을 한꺼번에 따라잡지 않는다.
    catchup=False,
    # 이전 5분 실행이 끝나기 전에 다음 실행이 겹치지 않게 막는다.
    max_active_runs=1,
    tags=["bidding", "daily", "s3"],
) as dag:
    collect_daily_notices = BashOperator(
        task_id="collect_daily_notices",
        bash_command=script_command("raw_json_daily.py", COLLECT_MINUTES),
    )

    download_daily_files = BashOperator(
        task_id="download_daily_files",
        bash_command=script_command("json_file_download_daily.py", DOWNLOAD_MINUTES),
    )

    collect_daily_notices >> download_daily_files
