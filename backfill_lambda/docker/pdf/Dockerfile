FROM public.ecr.aws/lambda/python:3.11

COPY backfill_lambda/docker/pdf/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY parsing/ ${LAMBDA_TASK_ROOT}/parsing/
COPY backfill_lambda/ ${LAMBDA_TASK_ROOT}/backfill_lambda/

CMD ["backfill_lambda.handler.handler"]
