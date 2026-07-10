"""Daily attachment download statistics written to S3.

curated JSON의 첨부 URL을 downloads 폴더의 실제 파일로 저장하는 과정을
서비스(biz_div)별 5분/1시간 JSON 통계로 남긴다.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


BIZ_DIVS = ("cnstwk", "servc", "frgcpt", "thng")
ALL_BIZ_DIV = "all"
DOWNLOAD_PREFIX = "raw/downloads/daily"
DOWNLOAD_METADATA_PREFIX = f"{DOWNLOAD_PREFIX}/_metadata"
STATS_PREFIX = f"{DOWNLOAD_METADATA_PREFIX}/stats"
EXPECTED_RUN_COUNT_PER_HOUR = 12


def s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 통계 저장을 위해 boto3 설치가 필요합니다.") from exc
    return boto3.client("s3")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def iter_hours(start: datetime, end: datetime):
    current = floor_hour(start)
    last = floor_hour(end)
    while current <= last:
        yield current
        current += timedelta(hours=1)


def s3_put_json(s3, bucket: str, key: str, payload: dict[str, Any]) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def s3_get_json(s3, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        payload = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as exc:
        error_code = getattr(getattr(exc, "response", {}), "get", lambda *_: {})("Error", {}).get("Code")
        if error_code in {"NoSuchKey", "404"}:
            return None
        raise
    return json.loads(payload.decode("utf-8"))


def list_objects(s3, bucket: str, prefix: str):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj


def download_manifest_prefixes(start_utc: datetime, end_utc: datetime) -> list[str]:
    prefixes = []
    for hour in iter_hours(start_utc, end_utc):
        prefixes.append(
            f"{DOWNLOAD_METADATA_PREFIX}/year={hour:%Y}/month={hour:%m}/day={hour:%d}/hour={hour:%H}/"
        )
    return prefixes


def find_latest_download_manifest(
    s3,
    bucket: str,
    run_started_at_utc: datetime,
    now_utc: datetime,
) -> tuple[str | None, list[dict[str, Any]]]:
    latest = None
    for prefix in download_manifest_prefixes(run_started_at_utc, now_utc):
        for obj in list_objects(s3, bucket, prefix):
            key = obj["Key"]
            if not key.endswith(".json") or "/bid_files_" not in key:
                continue
            if obj["LastModified"] < run_started_at_utc:
                continue
            if latest is None or obj["LastModified"] > latest["LastModified"]:
                latest = obj

    if latest is None:
        return None, []

    payload = s3.get_object(Bucket=bucket, Key=latest["Key"])["Body"].read()
    records = json.loads(payload.decode("utf-8"))
    if isinstance(records, dict):
        records = [records]
    return latest["Key"], [record for record in records if isinstance(record, dict)]


def normalize_biz_div(value: Any) -> str:
    text = str(value or "미분류").strip()
    return text or "미분류"


def summarize_download(records: list[dict[str, Any]], biz_div: str) -> dict[str, Any]:
    selected = [
        record
        for record in records
        if biz_div == ALL_BIZ_DIV or normalize_biz_div(record.get("업무구분")) == biz_div
    ]
    statuses = Counter(str(record.get("downloadStatus") or "").lower() for record in selected)
    success_file_count = statuses["success"]
    failed_file_count = statuses["failed"]
    target_file_count = success_file_count + failed_file_count
    success_rate = round(success_file_count / target_file_count, 4) if target_file_count else None

    return {
        "expected_file_count": len(selected),
        "target_file_count": target_file_count,
        "success_file_count": success_file_count,
        "failed_file_count": failed_file_count,
        "target_download_success_rate": success_rate,
    }


def empty_download_summary() -> dict[str, Any]:
    return {
        "expected_file_count": 0,
        "target_file_count": 0,
        "success_file_count": 0,
        "failed_file_count": 0,
        "target_download_success_rate": None,
    }


def stats_5min_key(biz_div: str, window_start: datetime) -> str:
    return (
        f"{STATS_PREFIX}/grain=5min/biz_div={biz_div}/year={window_start:%Y}/month={window_start:%m}/"
        f"day={window_start:%d}/hour={window_start:%H}/stats_{window_start:%Y%m%d%H%M}.json"
    )


def stats_hour_key(biz_div: str, hour_start: datetime) -> str:
    return (
        f"{STATS_PREFIX}/grain=hour/biz_div={biz_div}/year={hour_start:%Y}/month={hour_start:%m}/"
        f"day={hour_start:%d}/stats_{hour_start:%Y%m%d%H}.json"
    )


def task_state(context: dict[str, Any], task_id: str) -> str | None:
    dag_run = context.get("dag_run")
    if dag_run is None:
        return None
    task_instance = dag_run.get_task_instance(task_id)
    if task_instance is None:
        return None
    return task_instance.current_state()


def pipeline_status(context: dict[str, Any]) -> str:
    collect_state = task_state(context, "collect_daily_notices")
    download_state = task_state(context, "download_daily_files")
    if collect_state == "success" and download_state == "success":
        return "success"
    if collect_state in {"failed", "upstream_failed"} or download_state in {"failed", "upstream_failed"}:
        return "failed"
    return "partial"


def build_5min_payloads(
    *,
    bucket: str,
    plan: dict[str, Any],
    context: dict[str, Any],
    stats_created_at: datetime,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    s3 = s3_client()
    window_start = parse_iso(plan["cursorIso"])
    window_end = parse_iso(plan["windowEndIso"])
    dag_run = context["dag_run"]
    run_started_at = to_utc(dag_run.start_date or stats_created_at)
    stats_created_at_utc = to_utc(stats_created_at)
    manifest_key, manifest_records = find_latest_download_manifest(s3, bucket, run_started_at, stats_created_at_utc)

    status = pipeline_status(context)
    payloads = {}
    for biz_div in (*BIZ_DIVS, ALL_BIZ_DIV):
        download_summary = summarize_download(manifest_records, biz_div) if manifest_records else empty_download_summary()
        payloads[biz_div] = {
            "pipeline": context["dag"].dag_id,
            "grain": "5min",
            "biz_div": biz_div,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "dag_run_id": context["run_id"],
            "status": status,
            "task_states": {
                "download_daily_files": task_state(context, "download_daily_files"),
            },
            "download": download_summary,
            "s3": {
                "download_manifest_key": manifest_key,
            },
            "created_at": stats_created_at.isoformat(),
        }

    metadata = {
        "window_start": window_start,
        "window_end": window_end,
        "manifest_key": manifest_key,
    }
    return payloads, metadata


def write_5min_stats(bucket: str, plan: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    s3 = s3_client()
    stats_created_at = datetime.now(timezone.utc)
    payloads, metadata = build_5min_payloads(
        bucket=bucket,
        plan=plan,
        context=context,
        stats_created_at=stats_created_at,
    )

    written_keys = {}
    window_start = metadata["window_start"]
    for biz_div, payload in payloads.items():
        key = stats_5min_key(biz_div, window_start)
        s3_put_json(s3, bucket, key, payload)
        written_keys[biz_div] = key

    return {
        "statsKeys": written_keys,
        "windowStartIso": metadata["window_start"].isoformat(),
        "windowEndIso": metadata["window_end"].isoformat(),
        "downloadManifestKey": metadata["manifest_key"],
    }


def add_counter(target: dict[str, Any], source: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        target[field] = int(target.get(field) or 0) + int(source.get(field) or 0)


def merge_5min_into_hourly(hourly: dict[str, Any], five_min: dict[str, Any]) -> dict[str, Any]:
    window_start = five_min["window_start"]
    seen_windows = set(hourly["run"].get("seen_windows") or [])
    if window_start in seen_windows:
        return hourly

    seen_windows.add(window_start)
    hourly["run"]["seen_windows"] = sorted(seen_windows)
    hourly["run"]["actual_run_count"] = len(seen_windows)
    if five_min.get("status") == "success":
        hourly["run"]["success_run_count"] = int(hourly["run"].get("success_run_count") or 0) + 1
    else:
        hourly["run"]["failed_run_count"] = int(hourly["run"].get("failed_run_count") or 0) + 1

    add_counter(
        hourly["download"],
        five_min["download"],
        (
            "expected_file_count",
            "target_file_count",
            "success_file_count",
            "failed_file_count",
        ),
    )

    target_file_count = hourly["download"]["target_file_count"]
    success_file_count = hourly["download"]["success_file_count"]
    hourly["download"]["target_download_success_rate"] = (
        round(success_file_count / target_file_count, 4) if target_file_count else None
    )
    return hourly


def expected_windows_for_hour(hour_start: datetime) -> list[str]:
    return [(hour_start + timedelta(minutes=5 * index)).isoformat() for index in range(EXPECTED_RUN_COUNT_PER_HOUR)]


def empty_hourly_payload(pipeline: str, biz_div: str, hour_start: datetime, created_at: datetime) -> dict[str, Any]:
    hour_end = hour_start + timedelta(hours=1)
    return {
        "pipeline": pipeline,
        "grain": "hour",
        "biz_div": biz_div,
        "hour_start": hour_start.isoformat(),
        "hour_end": hour_end.isoformat(),
        "status": "partial",
        "run": {
            "expected_run_count": EXPECTED_RUN_COUNT_PER_HOUR,
            "actual_run_count": 0,
            "success_run_count": 0,
            "failed_run_count": 0,
            "seen_windows": [],
            "missing_windows": [],
        },
        "download": {
            **empty_download_summary(),
        },
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
    }


def finalize_hourly(hourly: dict[str, Any], hour_start: datetime) -> dict[str, Any]:
    expected = set(expected_windows_for_hour(hour_start))
    seen = set(hourly["run"].get("seen_windows") or [])
    missing = sorted(expected - seen)
    hourly["run"]["missing_windows"] = missing
    hourly["status"] = "final" if not missing else "final_with_missing"
    return hourly


def update_hourly_stats(bucket: str, five_min_result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    s3 = s3_client()
    updated_at = datetime.now(timezone.utc)
    window_start = parse_iso(five_min_result["windowStartIso"])
    window_end = parse_iso(five_min_result["windowEndIso"])
    hour_start = floor_hour(window_start)
    written_keys = {}

    # 현재 5분 통계를 해당 hour 통계에 증분 반영한다. 재시도 중복은 seen_windows로 막는다.
    for biz_div, five_min_key in five_min_result["statsKeys"].items():
        five_min_payload = s3_get_json(s3, bucket, five_min_key)
        if five_min_payload is None:
            continue
        hourly_key = stats_hour_key(biz_div, hour_start)
        hourly = s3_get_json(s3, bucket, hourly_key) or empty_hourly_payload(
            context["dag"].dag_id,
            biz_div,
            hour_start,
            updated_at,
        )
        hourly = merge_5min_into_hourly(hourly, five_min_payload)
        hourly["status"] = "partial"
        hourly["updated_at"] = updated_at.isoformat()
        s3_put_json(s3, bucket, hourly_key, hourly)
        written_keys[biz_div] = hourly_key

    # 정시를 넘긴 실행이면 직전 hour를 final 또는 final_with_missing으로 확정한다.
    if window_end.minute == 0:
        finalize_hour_start = floor_hour(window_end - timedelta(minutes=1))
        for biz_div in (*BIZ_DIVS, ALL_BIZ_DIV):
            hourly_key = stats_hour_key(biz_div, finalize_hour_start)
            hourly = s3_get_json(s3, bucket, hourly_key) or empty_hourly_payload(
                context["dag"].dag_id,
                biz_div,
                finalize_hour_start,
                updated_at,
            )
            hourly = finalize_hourly(hourly, finalize_hour_start)
            hourly["updated_at"] = updated_at.isoformat()
            s3_put_json(s3, bucket, hourly_key, hourly)
            written_keys[biz_div] = hourly_key

    return {"hourlyStatsKeys": written_keys}
