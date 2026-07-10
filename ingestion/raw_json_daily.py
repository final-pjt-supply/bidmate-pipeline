#!/usr/bin/env python3
"""
나라장터 입찰공고 5분 단위 준실시간 수집기.

TOP10 기관(institutions.py) 대상으로 나라장터검색조건 오퍼레이션(11~14)을 조회해
최근 N분 동안 등록된 공고를 공고 1건당 raw/curated JSON 1개씩 S3에 저장한다.
기본 저장 위치:
- s3://bidmate/raw/raw/daily/
- s3://bidmate/raw/curated/daily/
"""

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
import requests
from institutions import TOP10_INSTITUTIONS
from schema import parse_dt, to_curated

try:  # .env가 있으면 로드, 없으면(IAM 역할·export 등) 무시
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

SERVICE_KEY = os.environ.get("G2B_SERVICE_KEY", "")
BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")
BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"
RAW_PREFIX = "raw/raw/daily"
CURATED_PREFIX = "raw/curated/daily"

OPERATIONS = {
    "cnstwk": "getBidPblancListInfoCnstwkPPSSrch",
    "servc": "getBidPblancListInfoServcPPSSrch",
    "frgcpt": "getBidPblancListInfoFrgcptPPSSrch",
    "thng": "getBidPblancListInfoThngPPSSrch",
}

NUM_OF_ROWS = 999
TIMEOUT = 30
MAX_RETRY = 3
SAFE_KEY = re.compile(r"[^0-9A-Za-z가-힣._=-]+")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("g2b-daily")


class G2BApiError(RuntimeError):
    """조달청 API가 정상 response 대신 에러 응답을 반환했을 때 발생."""


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
            payload = response.json()
            if "response" not in payload:
                error = payload.get("nkoneps.com.response.ResponseError", {}).get("header", {})
                raise G2BApiError(
                    f"{operation}/{ntce_instt_nm} p{page_no} API 에러 응답: "
                    f"{error.get('resultCode', '?')} {error.get('resultMsg', '알 수 없음')}"
                )
            body = payload["response"].get("body", {})
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


def is_institution_match(record, ntce_instt_nm):
    """조회 기관명과 그 하위 조직 명의 공고를 남긴다."""
    return (record.get("ntceInsttNm") or "").strip().startswith(ntce_instt_nm)


def in_window(record, window_start, window_end):
    notice_dt = parse_dt(record.get("bidNtceDt"))
    return notice_dt is None or window_start <= notice_dt <= window_end


def safe_key_part(value, fallback):
    cleaned = SAFE_KEY.sub("_", str(value or "").strip())
    return cleaned[:180] or fallback


def notice_id(record, index):
    bid_no = safe_key_part(record.get("bidNtceNo"), f"no-bid-no-{index}")
    bid_ord = safe_key_part(record.get("bidNtceOrd"), "000")
    return f"{bid_no}-{bid_ord}"


def notice_partition_dt(record, fallback_dt):
    notice_dt = parse_dt(record.get("bidNtceDt")) or fallback_dt
    return notice_dt


def s3_json_key(prefix, cat, partition_dt, record, index):
    return (
        f"{prefix}/biz_div={cat}/year={partition_dt:%Y}/month={partition_dt:%m}/day={partition_dt:%d}/hour={partition_dt:%H}/"
        f"{notice_id(record, index)}.json"
    )


def put_json(s3, bucket, key, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )


def collect_window(window_start, window_end, bucket):
    bgn_dt = f"{window_start:%Y%m%d%H%M}"
    end_dt = f"{window_end:%Y%m%d%H%M}"
    s3 = s3_client()

    with requests.Session() as session:
        for cat, operation in OPERATIONS.items():
            fetched_count = saved_count = 0

            for ntce_instt_nm in TOP10_INSTITUTIONS:
                records = fetch_all(session, operation, bgn_dt, end_dt, ntce_instt_nm)
                fetched_count += len(records)

                for index, record in enumerate(records, start=1):
                    if (
                        not is_institution_match(record, ntce_instt_nm)
                        or not in_window(record, window_start, window_end)
                        or not is_open(record, window_end)
                    ):
                        continue

                    partition_dt = notice_partition_dt(record, window_end)
                    raw_key = s3_json_key(RAW_PREFIX, cat, partition_dt, record, index)
                    curated_key = s3_json_key(CURATED_PREFIX, cat, partition_dt, record, index)
                    put_json(s3, bucket, raw_key, record)
                    put_json(s3, bucket, curated_key, to_curated(record, cat, raw_key))
                    saved_count += 1

            log.info(
                "[%s ~ %s] %s: 조회 %s / S3 저장 %s건",
                f"{window_start:%Y-%m-%d %H:%M}",
                f"{window_end:%Y-%m-%d %H:%M}",
                cat,
                fetched_count,
                saved_count,
            )


def parse_end_dt(value):
    for date_format in ("%Y-%m-%d %H:%M", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError("종료시각 형식은 'YYYY-MM-DD HH:MM' 또는 'YYYYMMDDHHMM'입니다.")


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("양의 정수를 입력하세요.")
    return number


def parse_args():
    parser = argparse.ArgumentParser(description="나라장터 입찰공고 5분 단위 S3 수집기")
    parser.add_argument("--minutes", type=positive_int, default=5, help="조회할 최근 분 단위 범위")
    parser.add_argument("--end-dt", type=parse_end_dt, help="조회 종료시각. 생략하면 현재 시각")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    return parser.parse_args()


def main():
    if not SERVICE_KEY:
        raise SystemExit("환경변수 G2B_SERVICE_KEY(디코딩 키)를 먼저 설정하세요.")

    args = parse_args()
    window_end = args.end_dt or datetime.now()
    window_start = window_end - timedelta(minutes=args.minutes)
    log.info("S3 bucket=%s / 수집 범위 %s ~ %s", args.bucket, window_start, window_end)
    collect_window(window_start, window_end, args.bucket)
    log.info("수집 완료.")


if __name__ == "__main__":
    main()
    
