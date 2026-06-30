#!/usr/bin/env python3
"""나라장터 입찰공고 수집 (조달청_나라장터 입찰공고정보서비스, data.go.kr 15129394).
지정 기간(--start ~ --end)을 하루 단위·업무구분(물품/공사/용역/외자 4종)별로 조회해
'입찰마감 전' 공고만 골라 원본(raw)과 정제본(curated)으로 저장한다.
  raw/     : API 원본 113필드 그대로
  curated/ : 필요한 필드만 정제 (schema.py)
실행 예:
  export G2B_SERVICE_KEY="디코딩키"
  python collect_g2b_bids.py --start 2026-06-01 --end 2026-06-30
  python collect_g2b_bids.py --start 2026-06-25          # 하루만(끝일 생략 시 시작일과 동일)
  python collect_g2b_bids.py                             # 인자 없으면 오늘 하루
※ 같은 폴더에 schema.py 가 있어야 한다.
"""
import os, json, time, logging, argparse
from datetime import datetime, timedelta
from pathlib import Path
import requests
from schema import to_curated, parse_dt

SERVICE_KEY = os.environ.get("G2B_SERVICE_KEY", "")                  # Decoding(디코딩) 키
BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"  # '/ad/' 주의
BASE_DIR = Path("/Users/oloqlq/Desktop/bidding")                    # 저장 루트
RAW_DIR, CURATED_DIR = BASE_DIR / "raw", BASE_DIR / "curated"
OPERATIONS = {                                                       # 업무구분 → 오퍼레이션
    "thng": "getBidPblancListInfoThng", "cnstwk": "getBidPblancListInfoCnstwk",
    "servc": "getBidPblancListInfoServc", "frgcpt": "getBidPblancListInfoFrgcpt",
}
NUM_OF_ROWS, TIMEOUT, MAX_RETRY = 100, 30, 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("g2b")


def fetch(operation, bgn_dt, end_dt, page_no):
    """한 페이지 호출 후 (items, total)까지 파싱. 네트워크 오류는 재시도, 키 오류는 즉시 중단."""
    params = {"serviceKey": SERVICE_KEY, "pageNo": page_no, "numOfRows": NUM_OF_ROWS,
              "inqryDiv": 1, "inqryBgnDt": bgn_dt, "inqryEndDt": end_dt, "type": "json"}
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.get(f"{BASE_URL}/{operation}", params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()                # 키 오류 시 JSON이 아님 → JSONDecodeError
            break
        except json.JSONDecodeError:
            raise SystemExit(f"JSON 파싱 실패 — 디코딩 키/파라미터 확인: {resp.text[:200]}")
        except requests.RequestException as e:
            if attempt == MAX_RETRY:
                raise RuntimeError(f"{operation} p{page_no} 재시도 초과: {e}")
            log.warning(f"{operation} p{page_no} 재시도 {attempt}/{MAX_RETRY}: {e}")
            time.sleep(2 ** attempt)             # 지수 백오프

    body = payload.get("response", {}).get("body", {})
    total = int(body.get("totalCount", 0) or 0)
    items = body.get("items") or []
    if isinstance(items, dict):                  # 단건이면 {"item": ...} 형태
        items = items.get("item") or []
    if not isinstance(items, list):
        items = [items]
    return items, total


def fetch_all(operation, bgn_dt, end_dt):
    """페이지네이션 전체 수집."""
    records, total = fetch(operation, bgn_dt, end_dt, 1)
    last_page = (total + NUM_OF_ROWS - 1) // NUM_OF_ROWS   # 올림 나눗셈
    for page in range(2, last_page + 1):
        time.sleep(0.2)
        records += fetch(operation, bgn_dt, end_dt, page)[0]
    return records


def is_open(rec, now):
    """입찰마감일시(bidClseDt)가 아직 안 지났거나, 마감일시가 없으면 True."""
    clse = parse_dt(rec.get("bidClseDt"))
    return clse is None or clse > now


def save(root, cat, day, records):
    """year/month/day 파티션 경로로 JSON 저장."""
    out_dir = root / f"year={day:%Y}/month={day:%m}/day={day:%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"bid_{cat}_{day:%Y%m%d}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def collect_day(day, categories, now):
    """하루치를 업무구분별로 수집 → 입찰마감 전만 필터 → raw + curated 저장."""
    bgn, end = f"{day:%Y%m%d}0000", f"{day:%Y%m%d}2359"
    for cat in categories:
        records = fetch_all(OPERATIONS[cat], bgn, end)
        fetched = len(records)
        records = [r for r in records if is_open(r, now)]           # 입찰마감 전만
        if not records:
            log.info(f"[{day:%Y-%m-%d}] {cat}: 조회 {fetched} / 마감전 0건")
            continue
        save(RAW_DIR, cat, day, records)                            # ① 원본 보존
        curated = [to_curated(r, cat, now) for r in records]        # ② 정제 투영
        save(CURATED_DIR, cat, day, curated)
        log.info(f"[{day:%Y-%m-%d}] {cat}: 조회 {fetched} / 마감전 {len(records)}건 저장")


def to_day(s):
    """'YYYY-MM-DD' 또는 'YYYYMMDD' → datetime."""
    s = s.replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit(f"날짜 형식 오류: {s} (예: 2026-06-01)")


def parse_args():
    today = datetime.now().strftime("%Y-%m-%d")
    p = argparse.ArgumentParser(description="나라장터 입찰공고 수집 (입찰마감 전 공고만)")
    p.add_argument("--start", default=today, help="수집 시작일 YYYY-MM-DD (기본: 오늘)")
    p.add_argument("--end", help="수집 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    return p.parse_args()


if __name__ == "__main__":
    if not SERVICE_KEY:
        raise SystemExit("환경변수 G2B_SERVICE_KEY(디코딩 키)를 먼저 설정하세요.")

    args = parse_args()
    start_day = to_day(args.start)
    end_day = to_day(args.end) if args.end else start_day
    if start_day > end_day:
        raise SystemExit(f"--start({args.start})가 --end({args.end})보다 늦습니다.")

    now = datetime.now()
    categories = list(OPERATIONS)        # 물품·공사·용역·외자 4종 전부
    log.info(f"수집 범위 {start_day:%Y-%m-%d} ~ {end_day:%Y-%m-%d} / 업무구분 {categories}")

    for i in range((end_day - start_day).days + 1):
        collect_day(start_day + timedelta(days=i), categories, now)
    log.info("수집 완료.")
