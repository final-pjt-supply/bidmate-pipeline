FROM public.ecr.aws/lambda/python:3.11

COPY docker/hwp/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY parsing/ ${LAMBDA_TASK_ROOT}/parsing/
COPY lambda_app/ ${LAMBDA_TASK_ROOT}/lambda_app/

CMD ["lambda_app.handler.handler"]
