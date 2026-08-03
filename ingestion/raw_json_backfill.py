#!/usr/bin/env python3
"""

나라장터 입찰공고 백필 수집기 (httpx + aioboto3 비동기 동시 조회).
공고 1건당 raw/curated JSON 1개씩 이중 저장, year=YYYY/month=MM/day=DD 파티션 구조.
실행 방법
- 환경변수 G2B_SERVICE_KEY에 디코딩 키를 설정
- python3 raw_json_backfill.py --start 2026-06-01 --end 2026-06-30

"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
import httpx
from institutions import TOP10_INSTITUTIONS
from schema import parse_dt, to_curated

try:  # .env가 있으면 로드, 없으면(IAM 역할·export 등) 무시
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

SERVICE_KEY = os.environ.get("G2B_SERVICE_KEY", "")
BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")
# https 필수 - raw_json_daily.py와 같은 이유(80 포트가 2026-08-02부터 무응답).
BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
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
DEFAULT_CONCURRENCY = 12
CALL_BUDGET = 95_000
SAFE_KEY = re.compile(r"[^0-9A-Za-z가-힣._=-]+")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("g2b-backfill-async")


def is_institution_match(record, ntce_instt_nm):
    """ntceInsttNm 파라미터는 부분일치라, 응답에서 조회 기관명으로 시작하는 레코드만 남긴다.

    완전일치가 아니라 접두일치를 쓰는 이유: 한국전력공사/한국수력원자력 같은 기관은
    실제 공고가 본사 명의가 아니라 "한국전력공사 강원지역본부"처럼 지역본부·사업본부
    명의로 올라온다. 완전일치로는 이런 하위 조직 공고가 전부 걸러져 거의 0건으로
    수렴했다 (실측: 한국전력공사 완전일치 시 6개월치 4건). 접두일치로 바꾸면 조회
    기관명의 하위 조직까지 자동으로 포함된다.
    """
    return (record.get("ntceInsttNm") or "").strip().startswith(ntce_instt_nm)


def notice_day(record, fallback_dt):
    notice_dt = parse_dt(record.get("bidNtceDt")) or fallback_dt
    return datetime(notice_dt.year, notice_dt.month, notice_dt.day)


def safe_key_part(value, fallback):
    cleaned = SAFE_KEY.sub("_", str(value or "").strip())
    return cleaned[:180] or fallback


def notice_id(record, index):
    bid_no = safe_key_part(record.get("bidNtceNo"), f"no-bid-no-{index}")
    bid_ord = safe_key_part(record.get("bidNtceOrd"), "000")
    return f"{bid_no}-{bid_ord}"


def s3_notice_json_key(prefix, cat, day, record, index):
    return (
        f"{prefix}/biz_div={cat}/year={day:%Y}/month={day:%m}/day={day:%d}/"
        f"{notice_id(record, index)}.json"
    )


def to_day(value):
    value = value.replace("/", "-")
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    raise SystemExit(f"날짜 형식 오류: {value} (예: 2026-06-01)")


class CallCounter:
    """이번 실행 1회 기준으로 API 호출 수를 누적하는 단순 카운터.

    같은 날 스크립트를 여러 번 실행하면 각 실행이 독립적으로 카운트하므로
    실제 조달청 일일 한도(10만)를 실행 횟수 합산 기준으로는 보장하지 못한다.
    """

    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1


class G2BApiError(RuntimeError):
    """조달청 API가 정상 response 대신 에러 응답(예: 입력범위값 초과)을 반환했을 때 발생.

    HTTP 상태코드는 200으로 오고 JSON 파싱도 성공하기 때문에, 이 검사가 없으면
    fetch_page가 조용히 (빈 리스트, 0)을 반환해 실제로는 실패한 조회가 "0건"으로
    둔갑한다. 이 에러는 같은 파라미터로 재시도해도 결과가 달라지지 않으므로
    MAX_RETRY 루프 밖에서 즉시 발생시킨다.
    """


async def fetch_page(client, sem, counter, operation, bgn_dt, end_dt, ntce_instt_nm, page_no):
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

    async with sem:
        for attempt in range(1, MAX_RETRY + 1):
            try:
                # 재시도도 실제 API 호출이므로 실패한 시도도 카운트에 포함한다.
                counter.increment()
                response = await client.get(f"{BASE_URL}/{operation}", params=params, timeout=TIMEOUT)
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
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == MAX_RETRY:
                    raise RuntimeError(f"{operation}/{ntce_instt_nm} p{page_no} 재시도 초과: {exc}") from exc
                log.warning(
                    "%s/%s p%s 재시도 %s/%s: %s", operation, ntce_instt_nm, page_no, attempt, MAX_RETRY, exc
                )
                await asyncio.sleep(2 ** attempt)

    return [], 0


def day_query_bounds(day):
    return f"{day:%Y%m%d}0000", f"{day:%Y%m%d}2359"


async def fetch_first_pages(client, sem, counter, bgn_dt, end_dt):
    """기관×업무구분 조합(최대 40개)의 1페이지를 동시 조회해 totalCount를 확보한다."""
    combos = [(op_key, operation, inst) for op_key, operation in OPERATIONS.items() for inst in TOP10_INSTITUTIONS]
    tasks = [
        fetch_page(client, sem, counter, operation, bgn_dt, end_dt, inst, 1) for _, operation, inst in combos
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {(op_key, inst): result for (op_key, _, inst), result in zip(combos, results)}


async def fetch_remaining_pages(client, sem, counter, bgn_dt, end_dt, first_pages):
    """1단계에서 확보한 totalCount로 남은 페이지 번호를 계산해 동시 조회한다."""
    remaining_tasks = []
    remaining_keys = []
    for (op_key, inst), result in first_pages.items():
        if isinstance(result, Exception):
            continue
        _, total = result
        last_page = (total + NUM_OF_ROWS - 1) // NUM_OF_ROWS
        operation = OPERATIONS[op_key]
        for page_no in range(2, last_page + 1):
            remaining_tasks.append(fetch_page(client, sem, counter, operation, bgn_dt, end_dt, inst, page_no))
            remaining_keys.append((op_key, inst))

    if not remaining_tasks:
        return {}

    results = await asyncio.gather(*remaining_tasks, return_exceptions=True)
    grouped = {}
    for key, result in zip(remaining_keys, results):
        grouped.setdefault(key, []).append(result)
    return grouped


async def process_day(client, sem, counter, day):
    """하루치 2단계 동시조회 결과를 필터링하고, 성공/실패를 분리한다.

    backfill은 과거 이력 수집이 목적이라 "지금 기준 아직 마감 안 된 공고"만 남기는
    필터(daily 수집용 is_open)는 적용하지 않는다 — 이미 마감된 과거 공고도 그대로 보존한다.
    """
    bgn_dt, end_dt = day_query_bounds(day)

    first_pages = await fetch_first_pages(client, sem, counter, bgn_dt, end_dt)
    remaining = await fetch_remaining_pages(client, sem, counter, bgn_dt, end_dt, first_pages)

    raw_hits = []  # (op_key, ntce_instt_nm, record)
    failures = []  # (op_key, ntce_instt_nm, page_no, exception)

    for (op_key, inst), result in first_pages.items():
        if isinstance(result, Exception):
            failures.append((op_key, inst, 1, result))
            continue
        records, _ = result
        raw_hits.extend((op_key, inst, record) for record in records)

    for (op_key, inst), page_results in remaining.items():
        for offset, result in enumerate(page_results):
            page_no = offset + 2
            if isinstance(result, Exception):
                failures.append((op_key, inst, page_no, result))
                continue
            records, _ = result
            raw_hits.extend((op_key, inst, record) for record in records)

    by_operation = {}
    for op_key, inst, record in raw_hits:
        if is_institution_match(record, inst):
            by_operation.setdefault(op_key, []).append(record)

    return by_operation, failures


def s3_session():
    try:
        import aioboto3
    except ImportError as exc:
        raise SystemExit("S3 저장을 위해 aioboto3 설치가 필요합니다. 예: pip install aioboto3") from exc
    return aioboto3.Session()


async def put_json(s3, bucket, key, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    await s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )


async def collect_range(start_day, end_day, bucket, concurrency):
    sem = asyncio.Semaphore(concurrency)
    counter = CallCounter()
    fallback_dt = datetime.now()
    had_failure = False

    session = s3_session()
    async with session.client("s3") as s3, httpx.AsyncClient() as client:
        day = start_day
        while day <= end_day:
            by_operation, failures = await process_day(client, sem, counter, day)
            saved_count = 0

            for op_key, records in by_operation.items():
                for index, record in enumerate(records, start=1):
                    day_part = notice_day(record, fallback_dt)
                    raw_key = s3_notice_json_key(RAW_PREFIX, op_key, day_part, record, index)
                    curated_key = s3_notice_json_key(CURATED_PREFIX, op_key, day_part, record, index)
                    await put_json(s3, bucket, raw_key, record)
                    await put_json(s3, bucket, curated_key, to_curated(record, op_key, raw_key))
                    saved_count += 1

            if failures:
                had_failure = True
                for op_key, inst, page_no, exc in failures:
                    log.error("[%s] %s/%s p%s 실패: %s", f"{day:%Y-%m-%d}", op_key, inst, page_no, exc)

            log.info(
                "[%s] 처리 완료 (S3 저장 %s건, 누적 API 호출 %s회)",
                f"{day:%Y-%m-%d}",
                saved_count,
                counter.count,
            )

            if counter.count >= CALL_BUDGET:
                log.warning(
                    "%s일까지 처리 완료. 호출 한도(%s회) 근접으로 조기 종료. 이후 범위는 --start %s 로 재실행하세요.",
                    f"{day:%Y-%m-%d}",
                    CALL_BUDGET,
                    f"{day + timedelta(days=1):%Y-%m-%d}",
                )
                return had_failure, True

            day += timedelta(days=1)

    return had_failure, False


def parse_args():
    today = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(
        description="나라장터 입찰공고 백필 수집 (비동기, TOP10 기관, 마감 공고 포함 전체 보존)"
    )
    parser.add_argument("--start", default=today, help="수집 시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--end", help="수집 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="동시 요청 수 제한 (기본: 12)"
    )
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
        "S3 bucket=%s / 수집 범위 %s ~ %s / 동시성=%s",
        args.bucket,
        f"{start_day:%Y-%m-%d}",
        f"{end_day:%Y-%m-%d}",
        args.concurrency,
    )

    had_failure, stopped_early = asyncio.run(collect_range(start_day, end_day, args.bucket, args.concurrency))

    if had_failure:
        log.error("일부 조합에서 실패가 발생했습니다. 로그를 확인하세요.")
        sys.exit(1)

    if stopped_early:
        sys.exit(0)

    log.info("수집 완료.")


if __name__ == "__main__":
    main()
