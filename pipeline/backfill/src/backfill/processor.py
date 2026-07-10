# -*- coding: utf-8 -*-
"""backfill 건당 처리: extracted JSON → LLM 자격요건 추출 → qualifications 적재.

realtime handlers/extract_llm.py의 _process와 같은 일을 하되:
- 트리거가 S3 Batch(건당)라 경로/DB 배선이 다르다
- bid_id/document_id를 경로가 아니라 입력 JSON 필드에서 읽는다
- 실패를 Permanent/Temporary로 분류해 S3 Batch 재시도에 위임한다(DB 미연동)
- 출력 키가 이미 있으면 skip(멱등)

LLM 추출은 backfill.llm_bedrock(Bedrock Converse) 경로를 쓴다. prompt/schema/preprocess는
realtime extractors/llm를 Dockerfile로 read-only COPY해 재사용한다.
"""
import json
import logging

from botocore.exceptions import BotoCoreError, ClientError

from backfill import s3
from backfill.llm_bedrock import extract
from backfill.paths import extracted_backfill_key_to_qualifications_key

logger = logging.getLogger(__name__)

_S3_TEMPORARY_CODES = {"SlowDown", "ServiceUnavailable", "InternalError",
                       "RequestTimeout", "ThrottlingException", "503"}

# Bedrock converse의 일시적 오류(스로틀·용량·서버측). 나머지 ClientError는 영구.
_LLM_TEMPORARY_CODES = {"ThrottlingException", "ServiceUnavailableException",
                        "ModelTimeoutException", "InternalServerException",
                        "ModelNotReadyException", "ServiceQuotaExceededException"}


class PermanentFailure(Exception):
    """재시도해도 실패할 오류(파싱·형식·스키마·미지원)."""


class TemporaryFailure(Exception):
    """일시적 오류(S3 스로틀·5xx·네트워크, LLM API 429/5xx/타임아웃). S3 Batch가 재시도."""


def _raise_s3_failure(e: Exception):
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code in _S3_TEMPORARY_CODES:
            raise TemporaryFailure(f"s3 temporary: {code}")
        raise PermanentFailure(f"s3 error: {code}")
    raise TemporaryFailure(type(e).__name__)  # BotoCoreError: 전송 계층 → 일시적


def _raise_llm_failure(e: Exception):
    """Bedrock 호출 오류 분류. 스로틀·용량·서버측 5xx는 일시적, 나머지(검증·스키마 등)는 영구."""
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code in _LLM_TEMPORARY_CODES:
            raise TemporaryFailure(f"llm transient: {code}")
        raise PermanentFailure(f"llm error: {code}")
    if isinstance(e, BotoCoreError):
        raise TemporaryFailure(f"llm transient: {type(e).__name__}")  # 전송 계층 → 일시적
    raise PermanentFailure(f"llm error: {type(e).__name__}")  # 스키마 파싱 등 → 영구


def process_task(bucket: str, key: str) -> str:
    """extracted backfill 오브젝트 1건을 처리하고 출력 qualifications 키를 반환한다."""
    try:
        out_key = extracted_backfill_key_to_qualifications_key(key)
    except ValueError as e:
        raise PermanentFailure(str(e))

    try:
        exists = s3.object_exists(bucket, out_key)
    except (ClientError, BotoCoreError) as e:
        _raise_s3_failure(e)
    if exists:
        logger.info("skip (이미 존재): %s", out_key)
        return out_key  # 멱등 skip

    try:
        raw = s3.get_object(bucket, key)
    except (ClientError, BotoCoreError) as e:
        _raise_s3_failure(e)

    try:
        doc = json.loads(raw)
        pages = doc["pages"]
        bid_id = doc["bid_id"]
        document_id = doc["document_id"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise PermanentFailure(f"bad extracted json: {type(e).__name__}")

    try:
        qualifications = extract(pages)
    except Exception as e:
        _raise_llm_failure(e)

    result = {"bid_id": bid_id, "document_id": document_id, **qualifications}
    body = json.dumps(result, ensure_ascii=False).encode("utf-8")

    try:
        s3.put_object(bucket, out_key, body)
    except (ClientError, BotoCoreError) as e:
        _raise_s3_failure(e)

    logger.info("완료: %s (pages=%d)", out_key, len(pages))
    return out_key
