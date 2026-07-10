# -*- coding: utf-8 -*-
"""backfill extracted key → qualifications key 변환.

realtime common/paths.py는 backfill 키(extracted/downloads/backfill/..., hour= 없음)를
의도적으로 거부하므로, backfill 전용 변환기를 둔다.

  입력: extracted/downloads/backfill/biz_div=.../year=.../month=.../day=.../{bid_id}/{file_id}.json
  출력: qualifications/backfill/biz_div=.../year=.../month=.../day=.../{bid_id}/{file_id}.json
  (downloads/ 계층만 제거, backfill/ 유지)
"""
import re

_EXTRACTED_BACKFILL_RE = re.compile(
    r"^extracted/downloads/backfill/"
    r"(?P<rest>biz_div=[^/]+/year=[^/]+/month=[^/]+/day=[^/]+/[^/]+/[^/]+\.json)$"
)


def extracted_backfill_key_to_qualifications_key(key: str) -> str:
    """extracted backfill key를 qualifications backfill key로 변환한다. 형식 불일치 시 ValueError."""
    m = _EXTRACTED_BACKFILL_RE.match(key)
    if m is None:
        raise ValueError(f"extracted backfill key 형식이 아님: {key}")
    return f"qualifications/backfill/{m.group('rest')}"
