#!/usr/bin/env python3
"""
나라장터 입찰공고 백필 수집기.

TOP10 기관(institutions.py) 대상으로 나라장터검색조건 오퍼레이션(11~14)을 조회해
지정한 기간에 게시된 공고를 공고 1건당 raw/curated JSON 1개씩 S3에 저장한다.
raw_json_daily.py 와 동일한 S3 파티션 구조를 사용한다.
기본 저장 위치:
- s3://bidmate/raw/raw/backfill/
- s3://bidmate/raw/curated/backfill/

실행 방법
- 환경변수 G2B_SERVICE_KEY에 디코딩 키를 설정
- python3 raw_json_backfill.py --start 2026-06-01 --end 2026-06-30
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime
import requests
from institutions import TOP10_INSTITUTIONS
from schema import parse_dt, to_curated

SERVICE_KEY = os.environ.get("G2B_SERVICE_KEY", "")
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidmate")
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("g2b-backfill")


def s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit("S3 저장을 위해 boto3 설치가 필요합니다. 예: pip install boto3") from exc
    return boto3.client("s3")


def fetch(session, operation, bgn_dt, end_dt, ntce_instt_nm, page_no):
    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS,
        "inqryDiv": 1,
        "inqryBgnDt": bgn_dt,
        "inqryEndDt": end_dt,
        "ntceInsttNm": ntce_instt_nm,
        "type": "json",
    }

    for attempt in range(1, MAX_RETRY + 1):
        try:
            response = session.get(f"{BASE_URL}/{operation}", params=params, timeout=TIMEOUT)
            response.raise_for_status()
            body = response.json().get("response", {}).get("body", {})
            total = int(body.get("totalCount") or 0)
            items = body.get("items") or []
            if isinstance(items, dict):
                items = items.get("item") or []
            return (items if isinstance(items, list) else [items]), total
        except ValueError as exc:
            raise SystemExit(f"JSON 파싱 실패 - 디코딩 키/파라미터 확인: {response.text[:200]}") from exc
        except requests.RequestException as exc:
            if attempt == MAX_RETRY:
                raise RuntimeError(f"{operation}/{ntce_instt_nm} p{page_no} 재시도 초과: {exc}") from exc
            log.warning("%s/%s p%s 재시도 %s/%s: %s", operation, ntce_instt_nm, page_no, attempt, MAX_RETRY, exc)
            time.sleep(2 ** attempt)

    return [], 0


def fetch_all(session, operation, bgn_dt, end_dt, ntce_instt_nm):
    records, total = fetch(session, operation, bgn_dt, end_dt, ntce_instt_nm, 1)
    last_page = (total + NUM_OF_ROWS - 1) // NUM_OF_ROWS
    for page_no in range(2, last_page + 1):
        time.sleep(0.1)
        records.extend(fetch(session, operation, bgn_dt, end_dt, ntce_instt_nm, page_no)[0])
    return records


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


def put_json(s3, bucket, key, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )


def collect_range(start_day, end_day, bucket):
    bgn_dt = f"{start_day:%Y%m%d}0000"
    end_dt = f"{end_day:%Y%m%d}2359"
    now = datetime.now()
    s3 = s3_client()

    with requests.Session() as session:
        for cat, operation in OPERATIONS.items():
            fetched_count = 0
            filtered_records = []

            for ntce_instt_nm in TOP10_INSTITUTIONS:
                records = fetch_all(session, operation, bgn_dt, end_dt, ntce_instt_nm)
                fetched_count += len(records)
                filtered_records.extend(
                    record
                    for record in records
                    if is_exact_institution(record, ntce_instt_nm) and is_open(record, now)
                )

            by_day = group_by_day(filtered_records, now)
            saved_count = 0
            for day, day_records in by_day.items():
                raw_key = s3_day_json_key(RAW_PREFIX, cat, day)
                curated_key = s3_day_json_key(CURATED_PREFIX, cat, day)
                curated_records = [to_curated(record, cat, now) for record in day_records]
                put_json(s3, bucket, raw_key, day_records)
                put_json(s3, bucket, curated_key, curated_records)
                saved_count += len(day_records)

            log.info(
                "[%s ~ %s] %s: 조회 %s / S3 저장 %s건 (%s일 분량)",
                f"{start_day:%Y-%m-%d}",
                f"{end_day:%Y-%m-%d}",
                cat,
                fetched_count,
                saved_count,
                len(by_day),
            )


def to_day(value):
    value = value.replace("/", "-")
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    raise SystemExit(f"날짜 형식 오류: {value} (예: 2026-06-01)")


def parse_args():
    today = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(description="나라장터 입찰공고 백필 수집 (TOP10 기관, 입찰마감 전 공고만)")
    parser.add_argument("--start", default=today, help="수집 시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--end", help="수집 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    return parser.parse_args()


def main():
    if not SERVICE_KEY:
        raise SystemExit("환경변수 G2B_SERVICE_KEY(디코딩 키)를 먼저 설정하세요.")

    args = parse_args()
    start_day = to_day(args.start)
    end_day = to_day(args.end) if args.end else start_day
    if start_day > end_day:
        raise SystemExit(f"--start({args.start})가 --end({args.end})보다 늦습니다.")

    log.info(
        "S3 bucket=%s / 수집 범위 %s ~ %s",
        args.bucket,
        f"{start_day:%Y-%m-%d}",
        f"{end_day:%Y-%m-%d}",
    )
    collect_range(start_day, end_day, args.bucket)
    log.info("수집 완료.")


if __name__ == "__main__":
    main()
