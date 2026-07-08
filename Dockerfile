# Airflow + 수집/다운로드 스크립트 실행에 필요한 서드파티를 포함한 이미지
# (repo에 requirements 파일이 없어 스크립트 의존성을 직접 명시)
FROM apache/airflow:2.10.5

RUN pip install --no-cache-dir \
    "httpx>=0.28" \
    "requests>=2.32" \
    "boto3>=1.40" \
    "aioboto3>=15.5" \
    "python-dotenv>=1.0"
