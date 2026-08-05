"""수집 DAG의 "정상 동작 증거"를 CloudWatch 커스텀 메트릭으로 남긴다.

왜 필요한가
    기존 감시는 두 겹이다 - DLQ 알람(메시지가 흐르다 실패하는 경우)과 Airflow
    자체 감지(Airflow가 살아 있을 때의 문제). 구멍은 Airflow 자체가 죽는
    경우다(EC2 장애, 디스크 풀, docker 데몬 사망, 스케줄러 행). 이때는 DAG이
    아예 안 돌아 S3에 새 파일이 없고 -> SQS가 비고 -> DLQ도 비어서, 모든
    대시보드가 평화롭다. "정상"과 "수집 사망"이 모니터링상 구분되지 않는다.

    그래서 장애 유형별 감지를 쌓는 대신, 정상 동작의 증거가 사라지는 것을
    잡는다(dead man's switch). 이 메트릭이 15분간 끊기면 CloudWatch 알람이
    기존 SNS 토픽(realtime-pipeline-alerts)으로 알린다 - 원인이 무엇이든
    결과가 같기 때문에 원인 목록이 필요 없다.
    알람 정의는 ingestion/monitoring/template.yaml.

발신 실패를 삼키는 이유
    (1) fail-safe 방향이 맞다. 메트릭이 안 올라가면 알람이 울리므로 손실이 없다.
    (2) 태스크를 실패시키면 DAG의 on_failure_callback(record_failed_window)이
        발동해 실재하지 않는 gap을 manifest에 기록하고 backfill 대상 목록을
        오염시킨다.

Timestamp를 명시하지 않는 이유
    생략하면 CloudWatch 수신 시각이 쓰인다. 만약 수집 창의 끝(windowEndIso)을
    타임스탬프로 넣으면, 재시도로 10분 늦게 성공한 run이 10분 전 버킷을 채워
    정지 구간을 소급해서 메워버린다. "언제 신호가 도착했는가"가 생존 감시의
    질문이므로 수신 시각이 정답이다.

권한
    EC2 인스턴스 프로파일에 cloudwatch:PutMetricData 가 필요하다. PutMetricData는
    리소스 단위 권한을 지원하지 않으므로 Resource "*" 에 cloudwatch:namespace
    조건을 걸어 Bidmate/Ingestion 밖에는 쓰지 못하게 좁힌다.
"""

from __future__ import annotations

import logging
import os

# 에이전트 쪽이 Bidmate/Agent 를 쓰고 있어 같은 관례를 따른다.
NAMESPACE = os.environ.get("INGESTION_METRIC_NAMESPACE", "Bidmate/Ingestion")
METRIC_NAME = "PipelineHeartbeat"
ALARM_NAME = "ingestion-daily-pipeline-heartbeat-missing"

log = logging.getLogger("ingestion-heartbeat")


def emit_heartbeat(dag_id: str) -> bool:
    """수집 사이클 1회 성공을 알린다.

    성공 여부를 bool로 돌려주되 예외는 삼킨다(위 모듈 주석의 fail-safe 참고).
    호출부는 반환값을 무시해도 된다 - 로그로 충분하고, 진짜 감지는 알람이 한다.
    """
    try:
        import boto3

        boto3.client("cloudwatch").put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[
                {
                    "MetricName": METRIC_NAME,
                    "Dimensions": [{"Name": "DagId", "Value": dag_id}],
                    "Value": 1,
                    "Unit": "Count",
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - 발신 실패로 DAG을 죽이지 않는다
        log.warning("하트비트 발신 실패 - 생존 감시 알람(%s)이 울릴 수 있다: %s", ALARM_NAME, exc)
        return False

    log.info("하트비트 발신: %s/%s DagId=%s", NAMESPACE, METRIC_NAME, dag_id)
    return True
