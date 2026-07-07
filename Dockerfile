# pyhwp × distutils 이슈로 3.12 금지 — 3.11 고정
FROM public.ecr.aws/lambda/python:3.11

COPY requirements-lambda.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-lambda.txt

COPY parsing/ ${LAMBDA_TASK_ROOT}/parsing/
COPY pipeline/ ${LAMBDA_TASK_ROOT}/pipeline/

CMD ["pipeline.lambda_handler.handler"]
