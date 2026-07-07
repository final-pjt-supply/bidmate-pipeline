# -*- coding: utf-8 -*-
"""raw/text_extracted S3 key 파티션 구조 파싱 및 변환.

형식: raw/downloads/biz_div=TYPE/{stage}/year=Y/month=M/day=D/{bid_id}/{file_id}.{ext}
biz_div가 stage보다 앞에 온다(품목별로 먼저 나뉘고, 그 아래에서 backfill/realtime이 갈림).
stage는 backfill/realtime 둘 다 올 수 있다(백필 배치와 실시간 파이프라인이 같은 버킷을 공유).
"""
import re

_RAW_KEY_RE = re.compile(
    r"^raw/downloads/biz_div=(?P<biz_div>[^/]+)"
    r"/(?P<stage>[^/]+)"
    r"/year=(?P<year>[^/]+)/month=(?P<month>[^/]+)/day=(?P<day>[^/]+)"
    r"/(?P<bid_id>[^/]+)/(?P<file_id>[^/]+)\.(?P<ext>[^./]+)$"
)


def parse_raw_key(key: str) -> dict:
    """raw key에서 biz_div/stage/year/month/day/bid_id/file_id/ext를 꺼낸다. 형식이 다르면 ValueError."""
    m = _RAW_KEY_RE.match(key)
    if m is None:
        raise ValueError(f"raw key 형식이 아님: {key}")
    return m.groupdict()


def raw_key_to_text_key(key: str) -> str:
    """raw key와 같은 파티션 구조로 text_extracted 결과 key를 조립한다(확장자만 .json)."""
    parts = parse_raw_key(key)
    return (
        "text_extracted/downloads/biz_div={biz_div}/{stage}/year={year}/month={month}/day={day}"
        "/{bid_id}/{file_id}.json"
    ).format(**parts)


def document_id_from_file_id(bid_id: str, file_id: str) -> str:
    """file_id에서 bid_id 접두어(뒤따르는 "_" 포함)를 뗀 나머지를 document_id로 반환한다.

    예: bid_id="R25BK01152374_000", file_id="R25BK01152374_000_doc01" -> "doc01"
    """
    prefix = f"{bid_id}_"
    if not file_id.startswith(prefix):
        raise ValueError(f"file_id가 bid_id로 시작하지 않음: bid_id={bid_id} file_id={file_id}")
    return file_id[len(prefix):]
