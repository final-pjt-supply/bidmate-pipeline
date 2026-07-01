#!/usr/bin/env python3
"""
나라장터 입찰공고 Json 수집기.

실행 방법
- 환경변수 G2B_SERVICE_KEY에 디코딩 키를 설정
- python3 raw_json.py --start 2026-06-01 --end 2026-06-30
"""

#----------------------------------------------
# 패키지 호출
#----------------------------------------------
import argparse
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import requests
from schema import parse_dt, to_curated

#----------------------------------------------
# 환경변수
#----------------------------------------------
SERVICE_KEY = os.environ.get("G2B_SERVICE_KEY", "")
BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"
BASE_DIR = Path("/Users/oloqlq/Desktop/bidding")
RAW_DIR = BASE_DIR / "raw"
CURATED_DIR = BASE_DIR / "curated"

OPERATIONS = {
    "thng": "getBidPblancListInfoThng",
    "cnstwk": "getBidPblancListInfoCnstwk",
    "servc": "getBidPblancListInfoServc",
    "frgcpt": "getBidPblancListInfoFrgcpt",
}

NUM_OF_ROWS = 999
TIMEOUT = 30
MAX_RETRY = 3


#----------------------------------------------
# 함수 정의
#----------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("g2b")

def fetch(session, operation, bgn_dt, end_dt, page_no):
    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": NUM_OF_ROWS,
        "inqryDiv": 1,
        "inqryBgnDt": bgn_dt,
        "inqryEndDt": end_dt,
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
                raise RuntimeError(f"{operation} p{page_no} 재시도 초과: {exc}") from exc
            log.warning("%s p%s 재시도 %s/%s: %s", operation, page_no, attempt, MAX_RETRY, exc)
            time.sleep(2 ** attempt)

    return [], 0


def fetch_all(session, operation, bgn_dt, end_dt):
    records, total = fetch(session, operation, bgn_dt, end_dt, 1)
    last_page = (total + NUM_OF_ROWS - 1) // NUM_OF_ROWS
    for page_no in range(2, last_page + 1):
        time.sleep(0.1)
        records.extend(fetch(session, operation, bgn_dt, end_dt, page_no)[0])
    return records


def group_open_records(records, start_day, end_day, now):
    grouped = defaultdict(list)
    for record in records:
        close_dt = parse_dt(record.get("bidClseDt"))
        if close_dt is not None and close_dt <= now:
            continue

        notice_dt = parse_dt(record.get("bidNtceDt")) or start_day
        day = datetime(notice_dt.year, notice_dt.month, notice_dt.day)
        if start_day <= day <= end_day:
            grouped[day].append(record)
    return grouped


def save_json(root, cat, day, records):
    out_dir = root / f"year={day:%Y}/month={day:%m}/day={day:%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"bid_{cat}_{day:%Y%m%d}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def collect_range(start_day, end_day):
    bgn_dt = f"{start_day:%Y%m%d}0000"
    end_dt = f"{end_day:%Y%m%d}2359"
    now = datetime.now()

    with requests.Session() as session:
        for cat, operation in OPERATIONS.items():
            records = fetch_all(session, operation, bgn_dt, end_dt)
            grouped = group_open_records(records, start_day, end_day, now)
            saved_count = 0

            for day, day_records in sorted(grouped.items()):
                save_json(RAW_DIR, cat, day, day_records)
                save_json(CURATED_DIR, cat, day, [to_curated(record, cat, now) for record in day_records])
                saved_count += len(day_records)

            log.info(
                "[%s ~ %s] %s: 조회 %s / 마감전 %s건 저장",
                f"{start_day:%Y-%m-%d}",
                f"{end_day:%Y-%m-%d}",
                cat,
                len(records),
                saved_count,
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
    parser = argparse.ArgumentParser(description="나라장터 입찰공고 수집 (입찰마감 전 공고만)")
    parser.add_argument("--start", default=today, help="수집 시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--end", help="수집 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    return parser.parse_args()


def main():
    if not SERVICE_KEY:
        raise SystemExit("환경변수 G2B_SERVICE_KEY(디코딩 키)를 먼저 설정하세요.")

    args = parse_args()
    start_day = to_day(args.start)
    end_day = to_day(args.end) if args.end else start_day
    if start_day > end_day:
        raise SystemExit(f"--start({args.start})가 --end({args.end})보다 늦습니다.")

    log.info("수집 범위 %s ~ %s", f"{start_day:%Y-%m-%d}", f"{end_day:%Y-%m-%d}")
    collect_range(start_day, end_day)
    log.info("수집 완료.")



#----------------------------------------------
# 메인 실행
#----------------------------------------------
if __name__ == "__main__":
    main()
