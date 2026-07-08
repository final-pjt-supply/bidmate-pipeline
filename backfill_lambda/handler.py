# -*- coding: utf-8 -*-
"""S3 Batch Operations용 다포맷(HWP/HWPX/PDF) 추출 Lambda 핸들러.

기존 parsing/·pipeline/ 를 수정하지 않고 backfill_lambda/ 안에서 독립 동작한다.
task 1건(오브젝트 1개)을 받아 raw/ 다운로드→router.extract_document→extracted/ 미러
경로에 페이지 분할 JSON 업로드. S3 Batch 응답 계약(resultCode 분류)을 반환한다.
"""
import json
import logging
import os
from urllib.parse import unquote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backfill_lambda.router import extract_document
from parsing.hwp_hwpx.json_output import parse_doc_filename, to_json_doc

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

SRC_ROOT = "raw/"
DST_ROOT = "extracted/"
_TEMPORARY_CODES = {"SlowDown", "ServiceUnavailable", "InternalError",
                    "RequestTimeout", "ThrottlingException", "503"}


class PermanentFailure(Exception):
    """재시도해도 실패할 오류(파싱 깨짐·미지원·파일명 미스매치 등)."""


class TemporaryFailure(Exception):
    """일시적 오류(스로틀·5xx·네트워크). S3 Batch가 재시도."""


def _parse_allowed_ext(raw: str) -> set:
    out = set()
    for part in raw.split(","):
        p = part.strip().lower()
        if not p:
            continue
        if not p.startswith("."):
            p = "." + p
        out.add(p)
    return out


def _err_detail(e: Exception) -> str:
    """예외 요약. subprocess 계열이면 stderr 첫 줄만 덧붙여 원인 분류를 돕는다."""
    detail = type(e).__name__
    stderr = getattr(e, "stderr", None)
    if stderr:
        s = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else str(stderr)
        lines = s.strip().splitlines()
        if lines:
            detail += f": {lines[0][:200]}"
    return detail


def output_key(src_key: str) -> str:
    """raw/…x.hwp → extracted/…x.json (파티션 경로 유지, 확장자만 .json)."""
    base, _ext = os.path.splitext(src_key)
    rest = base[len(SRC_ROOT):] if base.startswith(SRC_ROOT) else base
    return f"{DST_ROOT}{rest}.json"


def _raise_s3_failure(e):
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code == "NoSuchKey":
            raise PermanentFailure("NoSuchKey")
        if code in _TEMPORARY_CODES:
            raise TemporaryFailure(code)
        raise PermanentFailure(f"s3 error: {code}")
    raise TemporaryFailure(type(e).__name__)  # BotoCoreError: 전송 계층 → 일시적


def _process_key(bucket: str, key: str) -> str:
    allowed = _parse_allowed_ext(os.getenv("ALLOWED_EXT", ""))
    if not allowed:
        raise PermanentFailure("ALLOWED_EXT not configured")
    ext = os.path.splitext(key)[1].lower()
    if ext not in allowed:
        raise PermanentFailure(f"unsupported extension: {ext or '(none)'}")

    stem = os.path.splitext(os.path.basename(key))[0]
    parsed = parse_doc_filename(stem)
    if parsed is None:
        raise PermanentFailure("filename pattern mismatch")
    bid_id, document_id = parsed

    try:
        data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except (ClientError, BotoCoreError) as e:
        _raise_s3_failure(e)

    try:
        result = extract_document(data, key)
    except ImportError as e:
        raise PermanentFailure(f"packaging error: {e}")
    except Exception as e:
        raise PermanentFailure(f"parse error: {_err_detail(e)}")
    if not result["text"].strip():
        raise PermanentFailure("no extractable text")
    doc = to_json_doc(result, bid_id, document_id)

    body = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    out_key = output_key(key)
    try:
        s3.put_object(Bucket=bucket, Key=out_key, Body=body,
                      ContentType="application/json; charset=utf-8")
    except (ClientError, BotoCoreError) as e:
        _raise_s3_failure(e)
    return out_key


def handler(event: dict, context) -> dict:
    results = []
    for task in event["tasks"]:
        task_id = task["taskId"]
        bucket = task["s3BucketArn"].rsplit(":", 1)[-1]
        key = unquote(task["s3Key"])
        try:
            out_key = _process_key(bucket, key)
            code, msg = "Succeeded", out_key
        except TemporaryFailure as e:
            logger.exception("temporary failure: %s", key)
            code, msg = "TemporaryFailure", str(e)[:1024]
        except PermanentFailure as e:
            logger.exception("permanent failure: %s", key)
            code, msg = "PermanentFailure", str(e)[:1024]
        except Exception as e:
            logger.exception("unexpected failure: %s", key)
            code, msg = "PermanentFailure", f"unexpected: {type(e).__name__}"
        results.append({"taskId": task_id, "resultCode": code, "resultString": msg})

    return {
        "invocationSchemaVersion": event.get("invocationSchemaVersion", "1.0"),
        "treatMissingKeysAs": "PermanentFailure",
        "invocationId": event["invocationId"],
        "results": results,
    }
