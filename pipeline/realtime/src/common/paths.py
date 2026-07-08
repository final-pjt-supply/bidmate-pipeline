# -*- coding: utf-8 -*-
"""raw/text_extracted/llm_extracted S3 key 파티션 구조 파싱 및 변환.

형식(bidmate 실버킷, 2026-07-08 전수 조사로 확정):
  raw/downloads/{stage}/biz_div=TYPE/year=Y/month=M/day=D/hour=H/{bid_id}/{file_id}.{ext}

stage가 biz_div보다 앞에 온다(테스트 버킷 realtime-dev-ds에서 쓰던 순서와 반대 —
실버킷 조사 결과 이 순서가 맞아서 그쪽에 맞춤. 테스트 버킷 호환은 포기).
hour=은 daily(우리 realtime 파이프라인이 다루는 stage) 전용이다 — backfill은
hour= 없이 year/month/day까지만 쓰고, 별도 파이프라인(backfill_lambda)이 처리하므로
이 정규식은 hour=이 없는 키(=backfill)를 의도적으로 거부한다.
"""
import re

# text_extracted/llm_extracted라는 이름 자체가 팀 내 다른 파이프라인(backfill_lambda의
# "extracted/") 명명 규칙과 통일할지 논의 중이라, 나중에 바뀌어도 여기 한 줄만 고치면
# 되게 상수로 뺀다.
TEXT_EXTRACTED_PREFIX = "text_extracted"
LLM_EXTRACTED_PREFIX = "llm_extracted"

_RAW_KEY_RE = re.compile(
    r"^raw/downloads/(?P<stage>[^/]+)"
    r"/biz_div=(?P<biz_div>[^/]+)"
    r"/year=(?P<year>[^/]+)/month=(?P<month>[^/]+)/day=(?P<day>[^/]+)/hour=(?P<hour>[^/]+)"
    r"/(?P<bid_id>[^/]+)/(?P<file_id>[^/]+)\.(?P<ext>[^./]+)$"
)


def parse_raw_key(key: str) -> dict:
    """raw key에서 stage/biz_div/year/month/day/hour/bid_id/file_id/ext를 꺼낸다.

    형식이 다르면(예: hour=이 없는 backfill 키) ValueError.
    """
    m = _RAW_KEY_RE.match(key)
    if m is None:
        raise ValueError(f"raw key 형식이 아님: {key}")
    return m.groupdict()


def raw_key_to_text_key(key: str) -> str:
    """raw key와 같은 파티션 구조로 text_extracted 결과 key를 조립한다(확장자만 .json)."""
    parts = parse_raw_key(key)
    return (
        "{prefix}/downloads/{stage}/biz_div={biz_div}"
        "/year={year}/month={month}/day={day}/hour={hour}"
        "/{bid_id}/{file_id}.json"
    ).format(prefix=TEXT_EXTRACTED_PREFIX, **parts)


_TEXT_KEY_RE = re.compile(
    rf"^{re.escape(TEXT_EXTRACTED_PREFIX)}/downloads/(?P<stage>[^/]+)"
    r"/biz_div=(?P<biz_div>[^/]+)"
    r"/year=(?P<year>[^/]+)/month=(?P<month>[^/]+)/day=(?P<day>[^/]+)/hour=(?P<hour>[^/]+)"
    r"/(?P<bid_id>[^/]+)/(?P<file_id>[^/]+)\.json$"
)


def parse_text_key(key: str) -> dict:
    """text_extracted key에서 stage/biz_div/year/month/day/hour/bid_id/file_id를 꺼낸다. 형식이 다르면 ValueError."""
    m = _TEXT_KEY_RE.match(key)
    if m is None:
        raise ValueError(f"{TEXT_EXTRACTED_PREFIX} key 형식이 아님: {key}")
    return m.groupdict()


def text_key_to_llm_key(key: str) -> str:
    """text_extracted key와 같은 파티션 구조로 llm_extracted 결과 key를 조립한다."""
    parts = parse_text_key(key)
    return (
        "{prefix}/downloads/{stage}/biz_div={biz_div}"
        "/year={year}/month={month}/day={day}/hour={hour}"
        "/{bid_id}/{file_id}.json"
    ).format(prefix=LLM_EXTRACTED_PREFIX, **parts)


def document_id_from_file_id(bid_id: str, file_id: str) -> str:
    """file_id에서 bid_id 접두어(뒤따르는 "_" 포함)를 뗀 나머지를 document_id로 반환한다.

    예: bid_id="R25BK01152374_000", file_id="R25BK01152374_000_doc01" -> "doc01"
    """
    prefix = f"{bid_id}_"
    if not file_id.startswith(prefix):
        raise ValueError(f"file_id가 bid_id로 시작하지 않음: bid_id={bid_id} file_id={file_id}")
    return file_id[len(prefix):]
