# Airflow + 수집/다운로드 스크립트 실행에 필요한 서드파티를 포함한 이미지
FROM apache/airflow:2.10.5

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
