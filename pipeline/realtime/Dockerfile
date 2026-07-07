# 빌드는 리포 루트에서: docker build -f pipeline/realtime/Dockerfile -t bidding-agent-extractor .
# (extractors가 리포 루트의 parsing/ 패키지를 그대로 재사용하므로 컨텍스트가 리포 루트여야 함)
FROM public.ecr.aws/lambda/python:3.11

# 베이스가 Amazon Linux 2라 yum 사용(dnf/microdnf 없음).
# lxml/pyhwp가 소스 빌드로 떨어질 경우를 대비한 컴파일 도구 + libxml2/libxslt 헤더
RUN yum install -y gcc gcc-c++ libxml2-devel libxslt-devel && yum clean all

COPY pipeline/realtime/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# parsing/: 리포 루트 패키지, hwp/hwpx/pdf 추출 로직의 실제 구현체
COPY parsing/ ${LAMBDA_TASK_ROOT}/parsing/
# src/: handlers/extractors/common — Lambda가 바로 import할 수 있도록 TASK_ROOT 최상위에 배치
COPY pipeline/realtime/src/ ${LAMBDA_TASK_ROOT}/

# 기본값은 pdf 핸들러. hwp/hwpx는 같은 이미지에서 Lambda 함수 정의 시 CMD를 오버라이드해서 공유.
CMD ["handlers.extract_pdf.lambda_handler"]
