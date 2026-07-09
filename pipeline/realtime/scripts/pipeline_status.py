# -*- coding: utf-8 -*-
"""파이프라인 진행상황 대시보드.

raw/downloads/daily/ 아래 대상 파일(.pdf/.hwp/.hwpx) 하나하나에 대해 extracted/
qualifications 대응 key가 S3에 실제로 있는지 대조해서 상태를 판정한다. 로그/큐/S3가
따로 흩어져 있어 "이 bid_id가 지금 어디쯤 있지"에 답하려면 여러 군데를 열어봐야
했던 문제(#52)의 1차 조치 — 이 스크립트 하나로 그 대조를 대신한다.

멈춤 판정은 raw LastModified 기준 30분 이상 경과한 것만 "멈춤"으로 본다(그 미만은
아직 정상 처리 중일 수 있어 "처리 중"으로 구분 — 파이프라인 정상 처리 시간이
문서당 몇 초~수십 초, LLM 단계가 길어도 3분 타임아웃이라 30분이면 충분히 여유있는
임계치).

대조·판정 핵심 로직(gather_statuses/classify)은 출력(print) 함수와 분리해뒀다 —
추후 이 로직이 주기 실행 모니터 Lambda로 승격되거나 판정 결과를 DB에 적재하는
단계에서 출력 부분 없이 그대로 재사용하기 위함.

실행(리포 루트에서):
    cd pipeline/realtime/scripts && python pipeline_status.py            # 요약 모드
    cd pipeline/realtime/scripts && python pipeline_status.py --detail   # 전체 상태표
"""
import argparse
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from common import paths  # noqa: E402

REGION = "ap-northeast-2"
BUCKET = "bidmate"
RAW_PREFIX = "raw/downloads/daily/"
RAW_EXTENSIONS = (".pdf", ".hwp", ".hwpx")

STALL_THRESHOLD = timedelta(minutes=30)

EXTRACT_LOG_GROUPS = {
    "pdf": "/aws/lambda/realtime-extract-pdf-dev",
    "hwp": "/aws/lambda/realtime-extract-hwp-dev",
    "hwpx": "/aws/lambda/realtime-extract-hwpx-dev",
}
LLM_LOG_GROUP = "/aws/lambda/realtime-llm-extract-dev"

QUEUE_NAMES = {
    "pdf": "realtime-extract-pdf-queue",
    "hwp": "realtime-extract-hwp-queue",
    "hwpx": "realtime-extract-hwpx-queue",
    "llm": "realtime-llm-extract-queue",
}

STATUS_LABELS = {
    "qualified": "qualified(완료)",
    "in_progress": "처리 중(30분 미만)",
    "extracted_stalled": "extracted에서 멈춤",
    "raw_stalled": "raw에서 멈춤",
}


@dataclass
class FileStatus:
    raw_key: str
    bid_id: str
    document_id: str
    ext: str
    raw_last_modified: datetime
    extracted_exists: bool
    qualifications_exists: bool
    status: str  # "qualified" | "in_progress" | "extracted_stalled" | "raw_stalled"
    elapsed: timedelta


# ============================================================
# 대조·판정 core — 출력과 분리(추후 모니터 Lambda/DB 적재 단계 재사용 예정)
# ============================================================

def list_raw_files(s3) -> list[dict]:
    """raw/downloads/daily/ 아래 .pdf/.hwp/.hwpx 대상 파일 목록(S3 Contents 원본)."""
    paginator = s3.get_paginator("list_objects_v2")
    files = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=RAW_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].lower().endswith(RAW_EXTENSIONS):
                files.append(obj)
    return files


def object_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def classify(raw_obj: dict, extracted_exists: bool, qualifications_exists: bool, now: datetime) -> FileStatus | None:
    """raw 객체 하나 + extracted/qualifications 존재 여부로 상태를 판정한다.
    raw key가 파티션 형식이 아니면(예상 밖 객체) None을 반환해 호출부에서 건너뛴다."""
    key = raw_obj["Key"]
    try:
        parts = paths.parse_raw_key(key)
    except ValueError:
        return None

    bid_id = parts["bid_id"]
    document_id = paths.document_id_from_file_id(bid_id, parts["file_id"])
    raw_last_modified = raw_obj["LastModified"]
    elapsed = now - raw_last_modified

    if qualifications_exists:
        status = "qualified"
    elif elapsed < STALL_THRESHOLD:
        status = "in_progress"
    elif extracted_exists:
        status = "extracted_stalled"
    else:
        status = "raw_stalled"

    return FileStatus(
        raw_key=key,
        bid_id=bid_id,
        document_id=document_id,
        ext=parts["ext"].lower(),
        raw_last_modified=raw_last_modified,
        extracted_exists=extracted_exists,
        qualifications_exists=qualifications_exists,
        status=status,
        elapsed=elapsed,
    )


def gather_statuses(s3) -> list[FileStatus]:
    now = datetime.now(timezone.utc)
    statuses = []
    for obj in list_raw_files(s3):
        key = obj["Key"]
        try:
            extracted_key = paths.raw_key_to_extracted_key(key)
        except ValueError:
            continue
        qualifications_key = paths.extracted_key_to_qualifications_key(extracted_key)

        extracted_exists = object_exists(s3, extracted_key)
        qualifications_exists = object_exists(s3, qualifications_key)

        status = classify(obj, extracted_exists, qualifications_exists, now)
        if status is not None:
            statuses.append(status)
    return statuses


def get_queue_stats(sqs) -> dict[str, dict[str, int]]:
    stats = {}
    for label, name in QUEUE_NAMES.items():
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
        attrs = sqs.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        dlq_url = sqs.get_queue_url(QueueName=f"{name}-dlq")["QueueUrl"]
        dlq_attrs = sqs.get_queue_attributes(
            QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
        )["Attributes"]
        stats[label] = {
            "visible": int(attrs["ApproximateNumberOfMessages"]),
            "in_flight": int(attrs["ApproximateNumberOfMessagesNotVisible"]),
            "dlq": int(dlq_attrs["ApproximateNumberOfMessages"]),
        }
    return stats


def _log_groups_for(status: FileStatus) -> list[str]:
    groups = [EXTRACT_LOG_GROUPS[status.ext]]
    if status.extracted_exists:
        groups.append(LLM_LOG_GROUP)
    return groups


_CW_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
_DURATION_MS_RE = re.compile(r"duration_ms=(\d+)")


def _run_insights_query(logs_client, group: str, query: str, start_time: int, end_time: int) -> list[dict]:
    query_id = logs_client.start_query(
        logGroupNames=[group], startTime=start_time, endTime=end_time, queryString=query, limit=50,
    )["queryId"]

    result = {"status": "Running", "results": []}
    for _ in range(20):
        result = logs_client.get_query_results(queryId=query_id)
        if result["status"] in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(0.5)
    return result.get("results", [])


def fetch_related_logs(logs_client, status: FileStatus) -> list[dict]:
    """멈춘 파일의 bid_id/document_id로 관련 Lambda 로그그룹(단계별로 따로)에서
    PROCESS_*/ERROR/WARNING 로그를 raw 유입 시각 이후 범위로 조회한다. 그룹별로
    쿼리를 따로 날려서 각 이벤트가 어느 단계(추출/LLM) 소속인지 태그를 붙인다
    (단계 소요시간 계산에 필요 — summarize_stage_timings 참고).

    결과가 없으면 빈 리스트를 반환하고, 호출부(print_summary)가 "로그 무흔적"으로
    표시한다."""
    start_time = int(status.raw_last_modified.timestamp())
    end_time = int(time.time())
    query = (
        "fields @timestamp, @message"
        f" | filter @message like /bid_id={status.bid_id}/"
        f" and @message like /document_id={status.document_id}/"
        " | filter @message like /PROCESS_/ or @message like /ERROR/ or @message like /WARNING/"
        " | sort @timestamp asc"
    )

    events = []
    for group in _log_groups_for(status):
        for row in _run_insights_query(logs_client, group, query, start_time, end_time):
            fields = {f["field"]: f["value"] for f in row}
            raw_ts = fields.get("@timestamp", "")
            try:
                ts = datetime.strptime(raw_ts, _CW_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
            except ValueError:
                ts = status.raw_last_modified
            events.append({"group": group, "timestamp": ts, "message": fields.get("@message", "").strip()})

    events.sort(key=lambda e: e["timestamp"])
    return events


def summarize_stage_timings(status: FileStatus, events: list[dict]) -> list[str]:
    """이미 조회해온 로그 이벤트(fetch_related_logs 결과)에서 PROCESS_START/DONE의
    timestamp와 duration_ms를 뽑아 단계 간 소요시간을 계산한다. 새 API 호출은
    없다 — 이미 있는 timestamp/duration_ms를 파싱만 한다.

    - raw 도착 → 추출 시작: 큐 대기시간(추출 Lambda가 메시지를 집어들기까지)
    - 추출 처리시간: PROCESS_DONE의 duration_ms(추출 Lambda 안에서 걸린 시간)
    - 추출 완료 → LLM 시작: 큐 대기시간(다음 단계로 넘어가는 데 걸린 시간)
    - LLM 처리시간: PROCESS_DONE의 duration_ms
    실패해서 해당 단계 로그 자체가 없으면 그 줄은 그냥 생략한다(부분 결과 허용).
    """
    extract_group = EXTRACT_LOG_GROUPS[status.ext]

    def _find(group: str, event_type: str) -> dict | None:
        return next((e for e in events if e["group"] == group and event_type in e["message"]), None)

    extract_start = _find(extract_group, "PROCESS_START")
    extract_done = _find(extract_group, "PROCESS_DONE")
    llm_start = _find(LLM_LOG_GROUP, "PROCESS_START")
    llm_done = _find(LLM_LOG_GROUP, "PROCESS_DONE")

    lines = []
    if extract_start:
        wait = (extract_start["timestamp"] - status.raw_last_modified).total_seconds()
        lines.append(f"raw 도착 → 추출 시작: {wait:.1f}초 대기")
    if extract_done:
        m = _DURATION_MS_RE.search(extract_done["message"])
        if m:
            lines.append(f"추출 처리시간: {int(m.group(1)) / 1000:.1f}초")
    if extract_done and llm_start:
        wait = (llm_start["timestamp"] - extract_done["timestamp"]).total_seconds()
        lines.append(f"추출 완료 → LLM 시작: {wait:.1f}초 대기")
    if llm_done:
        m = _DURATION_MS_RE.search(llm_done["message"])
        if m:
            lines.append(f"LLM 처리시간: {int(m.group(1)) / 1000:.1f}초")
    return lines


# ============================================================
# 출력
# ============================================================

def _format_elapsed(td: timedelta) -> str:
    minutes = int(td.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}분"
    return f"{minutes // 60}시간 {minutes % 60}분"


def print_summary(statuses: list[FileStatus], queue_stats: dict, logs_client) -> None:
    counts = Counter(s.status for s in statuses)
    print(f"대상 파일: {len(statuses)}건 (raw/downloads/daily/, .pdf/.hwp/.hwpx)")
    for key in ("qualified", "in_progress", "extracted_stalled", "raw_stalled"):
        print(f"  {STATUS_LABELS[key]}: {counts.get(key, 0)}")
    print()

    stalled = [s for s in statuses if s.status in ("extracted_stalled", "raw_stalled")]
    print(f"=== 멈춘 파일 ({len(stalled)}건) ===")
    if not stalled:
        print("없음")
    for s in stalled:
        stage = "extracted" if s.status == "extracted_stalled" else "raw"
        print(f"- {s.raw_key}")
        print(f"    bid_id={s.bid_id} document_id={s.document_id} 단계={stage} 경과={_format_elapsed(s.elapsed)}")
        events = fetch_related_logs(logs_client, s)
        if not events:
            print("    로그 무흔적 — 이벤트/큐 단계 미도달 의심")
        else:
            for e in events:
                print(f'    | {e["timestamp"]}  {e["message"]}')
            timings = summarize_stage_timings(s, events)
            if timings:
                print("    단계별 소요:")
                for t in timings:
                    print(f"      - {t}")
    print()

    print("=== 큐 상태 ===")
    for label, stat in queue_stats.items():
        print(f"  {label:5} visible={stat['visible']:4} in_flight={stat['in_flight']:3} dlq={stat['dlq']:3}")


def print_detail(statuses: list[FileStatus]) -> None:
    for s in sorted(statuses, key=lambda s: s.raw_key):
        print(
            f"{STATUS_LABELS[s.status]:20} {s.raw_key}"
            f"  bid_id={s.bid_id} document_id={s.document_id}"
            f"  extracted={s.extracted_exists} qualifications={s.qualifications_exists}"
            f"  경과={_format_elapsed(s.elapsed)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="파이프라인 진행상황 대시보드")
    parser.add_argument("--detail", action="store_true", help="파일별 전체 상태표 출력(요약 대신)")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=REGION)
    sqs = boto3.client("sqs", region_name=REGION)
    logs_client = boto3.client("logs", region_name=REGION)

    statuses = gather_statuses(s3)

    if args.detail:
        print_detail(statuses)
    else:
        queue_stats = get_queue_stats(sqs)
        print_summary(statuses, queue_stats, logs_client)


if __name__ == "__main__":
    main()
