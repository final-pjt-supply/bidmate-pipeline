#!/usr/bin/env python3
"""
나라장터 입찰공고 백필 수집기 (비동기 버전).

동기 버전(../raw_json_backfill.py)과 동일한 대상·저장 구조를 사용하되,
httpx + aioboto3 기반 비동기 동시 조회로 처리 속도를 높인다.
raw/curated 이중 저장, year=YYYY/month=MM/day=DD 파티션 구조는 동일하다.

실행 방법
- 환경변수 G2B_SERVICE_KEY에 디코딩 키를 설정
- python3 backfill_async/raw_json_backfill.py --start 2026-06-01 --end 2026-06-30
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

# 동기 버전과 파일명이 같아 sys.modules 충돌을 피하려고 backfill_async를 패키지로
# 두었지만(Task 2), 직접 스크립트 실행 시(`python3 backfill_async/raw_json_backfill.py`)에는
# institutions.py/schema.py가 있는 상위 폴더가 sys.path에 없어 아래 삽입이 필요하다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from institutions import TOP10_INSTITUTIONS  # noqa: E402
from schema import parse_dt, to_curated  # noqa: E402

try:  # .env가 있으면 로드, 없으면(IAM 역할·export 등) 무시
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

SERVICE_KEY = os.environ.get("G2B_SERVICE_KEY", "")
BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")
BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"
RAW_PREFIX = "raw/raw/backfill"
CURATED_PREFIX = "raw/curated/backfill"

OPERATIONS = {
    "cnstwk": "getBidPblancListInfoCnstwkPPSSrch",
    "servc": "getBidPblancListInfoServcPPSSrch",
    "frgcpt": "getBidPblancListInfoFrgcptPPSSrch",
    "thng": "getBidPblancListInfoThngPPSSrch",
}

NUM_OF_ROWS = 999
TIMEOUT = 30
MAX_RETRY = 3
DEFAULT_CONCURRENCY = 8
CALL_BUDGET = 95_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("g2b-backfill-async")


def is_open(record, now):
    close_dt = parse_dt(record.get("bidClseDt"))
    return close_dt is None or close_dt > now


def is_exact_institution(record, ntce_instt_nm):
    """ntceInsttNm 파라미터는 부분일치라 조회 대상 기관명과 완전일치하는 레코드만 남긴다."""
    return (record.get("ntceInsttNm") or "").strip() == ntce_instt_nm


def notice_day(record, fallback_dt):
    notice_dt = parse_dt(record.get("bidNtceDt")) or fallback_dt
    return datetime(notice_dt.year, notice_dt.month, notice_dt.day)


def group_by_day(records, now):
    """필터링된 레코드를 공고일(notice_day) 기준으로 묶는다."""
    groups = {}
    for record in records:
        day = notice_day(record, now)
        groups.setdefault(day, []).append(record)
    return groups


def s3_day_json_key(prefix, cat, day):
    return f"{prefix}/year={day:%Y}/month={day:%m}/day={day:%d}/biz_div={cat}.json"


def to_day(value):
    value = value.replace("/", "-")
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    raise SystemExit(f"날짜 형식 오류: {value} (예: 2026-06-01)")


if __name__ == "__main__":
    raise SystemExit("Task 8에서 CLI 진입점이 추가될 예정입니다.")
