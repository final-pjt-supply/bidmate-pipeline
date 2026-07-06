# -*- coding: utf-8 -*-
"""PDF 큐를 트리거로 하는 Lambda 진입점. 얇게 유지하고 실제 로직은 extractors/common에 위임."""
from extractors import pdf


def lambda_handler(event, context):
    raise NotImplementedError
