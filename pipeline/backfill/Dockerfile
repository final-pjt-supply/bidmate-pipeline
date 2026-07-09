# 빌드 컨텍스트는 리포 루트 (realtime src의 llm/common을 재사용 COPY하므로).
# SAM이 template.yaml의 Metadata(Dockerfile/DockerContext)로 자동 빌드한다.
FROM public.ecr.aws/lambda/python:3.11

COPY pipeline/backfill/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 재사용: realtime LLM 추출 로직 + config (read-only, realtime 소스 무수정).
# 세 __init__.py는 비어 있어 pdf/hwp/pymupdf sibling을 import하지 않음.
COPY pipeline/realtime/src/extractors/__init__.py ${LAMBDA_TASK_ROOT}/extractors/__init__.py
COPY pipeline/realtime/src/extractors/llm/ ${LAMBDA_TASK_ROOT}/extractors/llm/
COPY pipeline/realtime/src/common/__init__.py ${LAMBDA_TASK_ROOT}/common/__init__.py
COPY pipeline/realtime/src/common/config.py ${LAMBDA_TASK_ROOT}/common/config.py

# 신규: backfill 전용 코드 (backfill/ 패키지가 TASK_ROOT 최상위에 오도록)
COPY pipeline/backfill/src/ ${LAMBDA_TASK_ROOT}/

CMD ["backfill.handler.lambda_handler"]
