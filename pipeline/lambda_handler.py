# -*- coding: utf-8 -*-
"""S3 Batch Operations용 HWP→JSON 변환 Lambda 핸들러.

task 1건(오브젝트 1개)을 받아 raw/ 에서 다운로드→추출→extracted/ 미러 경로에
페이지 분할 JSON 업로드. S3 Batch 응답 계약(resultCode 분류)을 반환한다.
"""
import json
import logging
import os
from urllib.parse import unquote

import boto3
from botocore.exceptions import ClientError

from parsing import extract_bytes
from parsing.json_output import parse_doc_filename, to_json_doc

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

SRC_ROOT = "raw/"
DST_ROOT = "extracted/"
# 일시적(재시도 가치 있음)으로 볼 S3 에러 코드.
_TEMPORARY_CODES = {"SlowDown", "ServiceUnavailable", "InternalError",
                    "RequestTimeout", "ThrottlingException", "503"}


class PermanentFailure(Exception):
    """재시도해도 실패할 오류(파싱 깨짐·미지원·파일명 미스매치 등)."""


class TemporaryFailure(Exception):
    """일시적 오류(스로틀·5xx·네트워크). S3 Batch가 재시도."""


def output_key(src_key: str) -> str:
    """raw/…x.hwp → extracted/…x.json (파티션 경로 유지, 확장자만 .json)."""
    base, _ext = os.path.splitext(src_key)
    rest = base[len(SRC_ROOT):] if base.startswith(SRC_ROOT) else base
    return f"{DST_ROOT}{rest}.json"


def _process_key(bucket: str, key: str) -> str:
    ext = os.path.splitext(key)[1].lower()
    if ext != ".hwp":
        raise PermanentFailure(f"unsupported extension: {ext or '(none)'}")

    stem = os.path.splitext(os.path.basename(key))[0]
    parsed = parse_doc_filename(stem)
    if parsed is None:
        raise PermanentFailure("filename pattern mismatch")
    bid_id, document_id = parsed

    try:
        data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "NoSuchKey":
            raise PermanentFailure("NoSuchKey")
        if code in _TEMPORARY_CODES:
            raise TemporaryFailure(code)
        raise PermanentFailure(f"s3 get error: {code}")

    try:
        result = extract_bytes(data, key)
        doc = to_json_doc(result, bid_id, document_id)
    except Exception as e:  # 추출/변환 실패는 문서 문제 → 영구 실패
        raise PermanentFailure(f"parse error: {type(e).__name__}")

    body = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    out_key = output_key(key)
    try:
        s3.put_object(Bucket=bucket, Key=out_key, Body=body,
                      ContentType="application/json; charset=utf-8")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in _TEMPORARY_CODES:
            raise TemporaryFailure(code)
        raise PermanentFailure(f"s3 put error: {code}")
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
        except Exception as e:  # 예상 못 한 오류도 리포트로 흡수
            logger.exception("unexpected failure: %s", key)
            code, msg = "PermanentFailure", f"unexpected: {type(e).__name__}"
        results.append({"taskId": task_id, "resultCode": code,
                        "resultString": msg})

    return {
        "invocationSchemaVersion": event.get("invocationSchemaVersion", "1.0"),
        "treatMissingKeysAs": "PermanentFailure",
        "invocationId": event["invocationId"],
        "results": results,
    }
