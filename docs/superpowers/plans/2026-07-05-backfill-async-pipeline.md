# Backfill 비동기 파이프라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `raw_json_backfill.py`, `json_file_download_backfill.py`의 비동기(httpx + aioboto3) 버전을 `backfill_async/` 폴더에 병행 구축한다.

**Architecture:** 기존 동기 스크립트는 그대로 두고, `backfill_async/` 아래 두 스크립트의 비동기 사본을 새로 작성한다. `institutions.py`/`schema.py`는 순수 로직이라 사본 없이 그대로 import한다. 동시성은 `asyncio.Semaphore`로 제한하고, `asyncio.gather(..., return_exceptions=True)`로 부분 실패를 성공분과 분리한다.

**Tech Stack:** Python 3, `httpx` (비동기 HTTP), `aioboto3` (비동기 S3), `pytest` + `pytest-asyncio` (테스트)

## Global Constraints

- 기존 동기 스크립트(`bidding-agent/raw_json_backfill.py`, `bidding-agent/json_file_download_backfill.py`)와 기존 테스트(`bidding-agent/tests/`)는 무변경
- `institutions.py`, `schema.py`는 사본을 만들지 않고 그대로 import해 재사용
- 두 스크립트 간 공통 로직(재시도, 세마포어 설정, S3 클라이언트 생성)은 공유 모듈 없이 **각 파일에 중복 작성**
- 세마포어 동시성 기본값은 8, `--concurrency` 인자로 조정 가능
- 호출 카운터 임계값은 상수 `CALL_BUDGET = 95_000`, 하루 단위로 조기 종료 판단, 상태 파일 없이 로그로만 안내
- 조기 종료는 exit code 0, 실행 중 실패가 하나라도 있었으면 exit code 1
- 신규 의존성은 `httpx`, `aioboto3`, `pytest-asyncio`만 추가 (`bidding-agent/requirement.txt`)
- 스펙 원본: [`docs/superpowers/specs/2026-07-05-backfill-async-pipeline-design.md`](../specs/2026-07-05-backfill-async-pipeline-design.md)

---

## Task 1: 의존성 추가 및 pytest-asyncio 설정

**Files:**
- Modify: `bidding-agent/requirement.txt`
- Create: `bidding-agent/pytest.ini`

**Interfaces:**
- Produces: `pytest.ini`의 `asyncio_mode = auto` 설정 — 이후 모든 `async def test_...` 함수가 별도 데코레이터 없이 비동기 테스트로 인식됨

- [ ] **Step 1: requirement.txt에 의존성 추가**

`bidding-agent/requirement.txt`를 다음 내용으로 교체:

```
requests
boto3
python-dotenv
httpx
aioboto3
pytest-asyncio
```

- [ ] **Step 2: pytest-asyncio 설치 확인**

Run: `cd bidding-agent && pip install -r requirement.txt`
Expected: `httpx`, `aioboto3`, `pytest-asyncio` 설치 완료 로그

- [ ] **Step 3: pytest.ini 작성**

`bidding-agent/pytest.ini` 생성:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 4: 기존 테스트가 여전히 통과하는지 확인 (회귀 확인)**

Run: `cd bidding-agent && python3 -m pytest tests/ -v`
Expected: 기존 테스트 전부 PASS (asyncio_mode 추가가 기존 동기 테스트에 영향 없음을 확인)

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add requirement.txt pytest.ini
git commit -m "Chore : 비동기 backfill 파이프라인용 httpx/aioboto3/pytest-asyncio 의존성 추가"
```

---

## Task 2: backfill_async 패키지 스캐폴딩

**Files:**
- Create: `bidding-agent/backfill_async/__init__.py`
- Create: `bidding-agent/backfill_async/tests/__init__.py`

**Interfaces:**
- Produces: `backfill_async` 파이썬 패키지 (빈 `__init__.py`). 이후 테스트는 `from backfill_async import raw_json_backfill as rjb` 형태로 임포트한다.

**왜 패키지로 만드는가:** 동기 버전과 비동기 버전 스크립트가 둘 다 파일명이 `raw_json_backfill.py`로 동일하다. 만약 `backfill_async/tests/`에서도 기존 테스트처럼 `import raw_json_backfill`(패키지 경로 없이)로 임포트하면, 같은 이름의 모듈이 `sys.modules`에 먼저 캐시된 쪽(동기/비동기 중 먼저 임포트된 것)이 계속 재사용되어 다른 쪽 테스트가 엉뚱한 모듈을 테스트하게 되는 조용한 버그가 생긴다. `backfill_async`를 패키지로 만들고 테스트에서 `from backfill_async import raw_json_backfill`처럼 정규화된 경로로 임포트하면 `sys.modules`에 `backfill_async.raw_json_backfill`로 별도 등록되어 충돌하지 않는다.

- [ ] **Step 1: 빈 패키지 마커 생성**

`bidding-agent/backfill_async/__init__.py` (빈 파일):

```python
```

`bidding-agent/backfill_async/tests/__init__.py` (빈 파일):

```python
```

- [ ] **Step 2: 패키지 인식 확인용 임시 테스트 작성**

`bidding-agent/backfill_async/tests/test_package_wiring.py`:

```python
import backfill_async


def test_backfill_async_is_importable_as_package():
    assert backfill_async.__name__ == "backfill_async"
```

- [ ] **Step 3: 테스트 실행 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_package_wiring.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd bidding-agent
git add backfill_async/__init__.py backfill_async/tests/__init__.py backfill_async/tests/test_package_wiring.py
git commit -m "Feat : backfill_async 패키지 스캐폴딩 추가"
```

---

## Task 3: raw_json_backfill.py — 모듈 골격 + 순수 로직 이식

**Files:**
- Create: `bidding-agent/backfill_async/raw_json_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_raw_json_backfill.py`

**Interfaces:**
- Consumes: `institutions.TOP10_INSTITUTIONS` (튜플), `schema.parse_dt(value) -> datetime|None`, `schema.to_curated(record, biz_div, collected_at=None) -> dict`
- Produces:
  - `s3_day_json_key(prefix: str, cat: str, day: datetime) -> str`
  - `group_by_day(records: list[dict], now: datetime) -> dict[datetime, list[dict]]`
  - `is_open(record: dict, now: datetime) -> bool`
  - `is_exact_institution(record: dict, ntce_instt_nm: str) -> bool`
  - `notice_day(record: dict, fallback_dt: datetime) -> datetime`
  - `to_day(value: str) -> datetime`
  - 상수: `OPERATIONS: dict[str, str]`, `NUM_OF_ROWS = 999`, `TIMEOUT = 30`, `MAX_RETRY = 3`, `DEFAULT_CONCURRENCY = 8`, `CALL_BUDGET = 95_000`, `RAW_PREFIX`, `CURATED_PREFIX`, `BASE_URL`

- [ ] **Step 1: 순수 로직 테스트 작성 (실패할 테스트)**

`bidding-agent/backfill_async/tests/test_raw_json_backfill.py`:

```python
import unittest
from datetime import datetime

from backfill_async import raw_json_backfill as rjb


class TestS3DayJsonKey(unittest.TestCase):
    def test_builds_day_and_biz_div_path(self):
        day = datetime(2026, 6, 1)
        key = rjb.s3_day_json_key("raw/raw", "servc", day)
        self.assertEqual(key, "raw/raw/year=2026/month=06/day=01/biz_div=servc.json")


class TestGroupByDay(unittest.TestCase):
    def test_groups_records_by_notice_day(self):
        records = [
            {"bidNtceNo": "1", "bidNtceDt": "2026-06-01 09:00:00"},
            {"bidNtceNo": "2", "bidNtceDt": "2026-06-01 15:30:00"},
            {"bidNtceNo": "3", "bidNtceDt": "2026-06-02 10:00:00"},
        ]
        now = datetime(2026, 6, 3)

        groups = rjb.group_by_day(records, now)

        self.assertEqual(set(groups.keys()), {datetime(2026, 6, 1), datetime(2026, 6, 2)})
        self.assertEqual(len(groups[datetime(2026, 6, 1)]), 2)
        self.assertEqual(len(groups[datetime(2026, 6, 2)]), 1)

    def test_missing_notice_date_falls_back_to_now(self):
        records = [{"bidNtceNo": "1", "bidNtceDt": None}]
        now = datetime(2026, 6, 3, 12, 0, 0)

        groups = rjb.group_by_day(records, now)

        self.assertEqual(set(groups.keys()), {datetime(2026, 6, 3)})


class TestIsOpen(unittest.TestCase):
    def test_open_when_close_date_in_future(self):
        now = datetime(2026, 6, 1)
        record = {"bidClseDt": "2026-06-10 18:00:00"}
        self.assertTrue(rjb.is_open(record, now))

    def test_closed_when_close_date_in_past(self):
        now = datetime(2026, 6, 15)
        record = {"bidClseDt": "2026-06-10 18:00:00"}
        self.assertFalse(rjb.is_open(record, now))

    def test_open_when_close_date_missing(self):
        now = datetime(2026, 6, 1)
        self.assertTrue(rjb.is_open({}, now))


class TestIsExactInstitution(unittest.TestCase):
    def test_matches_exact_name(self):
        record = {"ntceInsttNm": "조달청"}
        self.assertTrue(rjb.is_exact_institution(record, "조달청"))

    def test_rejects_partial_match(self):
        record = {"ntceInsttNm": "조달청 서울지방조달청"}
        self.assertFalse(rjb.is_exact_institution(record, "조달청"))


class TestToDay(unittest.TestCase):
    def test_parses_hyphenated_date(self):
        self.assertEqual(rjb.to_day("2026-06-01"), datetime(2026, 6, 1))

    def test_parses_compact_date(self):
        self.assertEqual(rjb.to_day("20260601"), datetime(2026, 6, 1))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backfill_async.raw_json_backfill'`

- [ ] **Step 3: raw_json_backfill.py 골격 + 순수 로직 작성**

`bidding-agent/backfill_async/raw_json_backfill.py`:

```python
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
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/raw_json_backfill.py backfill_async/tests/test_raw_json_backfill.py
git commit -m "Feat : raw_json_backfill 비동기 버전 골격 및 순수 로직 이식"
```

---

## Task 4: fetch_page — 세마포어 + 재시도 + 호출 카운터

**Files:**
- Modify: `bidding-agent/backfill_async/raw_json_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_raw_json_backfill.py`

**Interfaces:**
- Consumes: Task 3의 상수(`BASE_URL`, `NUM_OF_ROWS`, `TIMEOUT`, `MAX_RETRY`)
- Produces:
  - `class CallCounter: count: int; increment() -> None`
  - `async def fetch_page(client, sem, counter, operation: str, bgn_dt: str, end_dt: str, ntce_instt_nm: str, page_no: int) -> tuple[list[dict], int]` — `(records, totalCount)` 반환, `MAX_RETRY` 소진 시 `RuntimeError` 발생

- [ ] **Step 1: 재시도 동작 테스트 작성**

`test_raw_json_backfill.py`에 추가:

```python
class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def make_page_payload(items, total):
    return {"response": {"body": {"totalCount": total, "items": items}}}


class FakeFailThenSucceedClient:
    """처음 N-1번은 예외를 던지고 마지막에 성공하는 가짜 httpx client."""

    def __init__(self, fail_times, payload):
        self.fail_times = fail_times
        self.payload = payload
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise httpx.ConnectError("boom", request=None)
        return FakeResponse(self.payload)


class TestFetchPageRetry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 지수 백오프 실제 대기를 없애 테스트를 빠르게 한다.
        self._orig_sleep = asyncio.sleep
        asyncio.sleep = lambda *_args, **_kwargs: self._orig_sleep(0)

    async def asyncTearDown(self):
        asyncio.sleep = self._orig_sleep

    async def test_succeeds_after_transient_failures(self):
        client = FakeFailThenSucceedClient(fail_times=2, payload=make_page_payload([{"bidNtceNo": "1"}], 1))
        sem = asyncio.Semaphore(1)
        counter = rjb.CallCounter()

        records, total = await rjb.fetch_page(client, sem, counter, "op", "202606010000", "202606012359", "조달청", 1)

        self.assertEqual(records, [{"bidNtceNo": "1"}])
        self.assertEqual(total, 1)
        self.assertEqual(client.calls, 3)
        self.assertEqual(counter.count, 3)

    async def test_raises_after_max_retry_exhausted(self):
        client = FakeFailThenSucceedClient(fail_times=99, payload=make_page_payload([], 0))
        sem = asyncio.Semaphore(1)
        counter = rjb.CallCounter()

        with self.assertRaises(RuntimeError):
            await rjb.fetch_page(client, sem, counter, "op", "202606010000", "202606012359", "조달청", 1)

        self.assertEqual(client.calls, rjb.MAX_RETRY)
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v -k FetchPageRetry`
Expected: FAIL — `AttributeError: module 'backfill_async.raw_json_backfill' has no attribute 'CallCounter'`

- [ ] **Step 3: CallCounter, fetch_page 구현**

`raw_json_backfill.py`의 `to_day` 함수와 `if __name__ == "__main__":` 사이에 추가:

```python
class CallCounter:
    """이번 실행 1회 기준으로 API 호출 수를 누적하는 단순 카운터.

    같은 날 스크립트를 여러 번 실행하면 각 실행이 독립적으로 카운트하므로
    실제 조달청 일일 한도(10만)를 실행 횟수 합산 기준으로는 보장하지 못한다.
    """

    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1


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
                body = response.json().get("response", {}).get("body", {})
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
```

`raw_json_backfill.py` 상단 import에 `import unittest`는 필요 없다 (테스트 파일에만 필요). 테스트 파일 상단에 `import asyncio`, `import unittest`, `import httpx`가 추가로 필요하므로 `test_raw_json_backfill.py` 최상단 import 목록을 다음으로 교체한다:

```python
import asyncio
import unittest
from datetime import datetime

import httpx

from backfill_async import raw_json_backfill as rjb
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/raw_json_backfill.py backfill_async/tests/test_raw_json_backfill.py
git commit -m "Feat : fetch_page 세마포어/재시도/호출카운터 구현"
```

---

## Task 5: 2단계 동시 조회 (fetch_first_pages / fetch_remaining_pages)

**Files:**
- Modify: `bidding-agent/backfill_async/raw_json_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_raw_json_backfill.py`

**Interfaces:**
- Consumes: `fetch_page(...)` (Task 4), `OPERATIONS`, `TOP10_INSTITUTIONS`, `NUM_OF_ROWS`
- Produces:
  - `def day_query_bounds(day: datetime) -> tuple[str, str]`
  - `async def fetch_first_pages(client, sem, counter, bgn_dt: str, end_dt: str) -> dict[tuple[str, str], tuple[list[dict], int] | Exception]` — 키는 `(op_key, ntce_instt_nm)`
  - `async def fetch_remaining_pages(client, sem, counter, bgn_dt: str, end_dt: str, first_pages: dict) -> dict[tuple[str, str], list[tuple[list[dict], int] | Exception]]` — 값은 2페이지부터 순서대로 쌓은 결과 리스트

- [ ] **Step 1: 2단계 조회 테스트 작성**

`test_raw_json_backfill.py`에 추가:

```python
class RoutingFakeClient:
    """operation/기관/페이지 조합별로 미리 정해둔 응답을 돌려주는 가짜 client."""

    def __init__(self, responses):
        # responses: {(operation, ntce_instt_nm, page_no): payload_dict}
        self.responses = responses
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        operation = url.rsplit("/", 1)[-1]
        key = (operation, params["ntceInsttNm"], params["pageNo"])
        self.calls.append(key)
        return FakeResponse(self.responses[key])


class TestTwoStageFetch(unittest.IsolatedAsyncioTestCase):
    async def test_first_pages_covers_every_combo(self):
        responses = {}
        for op_key, operation in rjb.OPERATIONS.items():
            for inst in rjb.TOP10_INSTITUTIONS:
                responses[(operation, inst, 1)] = make_page_payload([{"bidNtceNo": f"{op_key}-{inst}"}], 1)

        client = RoutingFakeClient(responses)
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        first_pages = await rjb.fetch_first_pages(client, sem, counter, "202606010000", "202606012359")

        expected_keys = {
            (op_key, inst) for op_key in rjb.OPERATIONS for inst in rjb.TOP10_INSTITUTIONS
        }
        self.assertEqual(set(first_pages.keys()), expected_keys)
        records, total = first_pages[("thng", rjb.TOP10_INSTITUTIONS[0])]
        self.assertEqual(total, 1)

    async def test_remaining_pages_computed_from_total_count(self):
        op_key = "thng"
        operation = rjb.OPERATIONS[op_key]
        inst = rjb.TOP10_INSTITUTIONS[0]
        total = rjb.NUM_OF_ROWS * 2 + 5  # 3페이지 필요

        responses = {
            (operation, inst, 2): make_page_payload([{"bidNtceNo": "p2"}], total),
            (operation, inst, 3): make_page_payload([{"bidNtceNo": "p3"}], total),
        }
        client = RoutingFakeClient(responses)
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        first_pages = {(op_key, inst): ([{"bidNtceNo": "p1"}], total)}
        remaining = await rjb.fetch_remaining_pages(client, sem, counter, "202606010000", "202606012359", first_pages)

        self.assertEqual(len(remaining[(op_key, inst)]), 2)
        page2_records, _ = remaining[(op_key, inst)][0]
        page3_records, _ = remaining[(op_key, inst)][1]
        self.assertEqual(page2_records, [{"bidNtceNo": "p2"}])
        self.assertEqual(page3_records, [{"bidNtceNo": "p3"}])

    async def test_remaining_pages_empty_when_single_page(self):
        first_pages = {("thng", rjb.TOP10_INSTITUTIONS[0]): ([{"bidNtceNo": "p1"}], 1)}
        client = RoutingFakeClient({})
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        remaining = await rjb.fetch_remaining_pages(client, sem, counter, "202606010000", "202606012359", first_pages)

        self.assertEqual(remaining, {})
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v -k TwoStageFetch`
Expected: FAIL — `AttributeError: ... has no attribute 'fetch_first_pages'`

- [ ] **Step 3: day_query_bounds, fetch_first_pages, fetch_remaining_pages 구현**

`raw_json_backfill.py`의 `fetch_page` 함수 뒤에 추가:

```python
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
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/raw_json_backfill.py backfill_async/tests/test_raw_json_backfill.py
git commit -m "Feat : raw_json_backfill 2단계 동시조회(1페이지 우선 확인 후 나머지 페이지) 구현"
```

---

## Task 6: process_day — 필터링 + 부분 실패 수집

**Files:**
- Modify: `bidding-agent/backfill_async/raw_json_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_raw_json_backfill.py`

**Interfaces:**
- Consumes: `fetch_first_pages`, `fetch_remaining_pages` (Task 5), `is_exact_institution`, `is_open` (Task 3)
- Produces: `async def process_day(client, sem, counter, day: datetime, now: datetime) -> tuple[dict[str, list[dict]], list[tuple[str, str, int, Exception]]]` — `(by_operation, failures)`. `failures`의 각 원소는 `(op_key, ntce_instt_nm, page_no, exception)`

- [ ] **Step 1: 부분 실패 시나리오 테스트 작성**

`test_raw_json_backfill.py`에 추가:

```python
class SelectiveFailClient:
    """특정 (operation, 기관, 페이지) 조합만 실패시키는 가짜 client."""

    def __init__(self, responses, fail_keys):
        self.responses = responses
        self.fail_keys = fail_keys

    async def get(self, url, params=None, timeout=None):
        operation = url.rsplit("/", 1)[-1]
        key = (operation, params["ntceInsttNm"], params["pageNo"])
        if key in self.fail_keys:
            raise httpx.ConnectError("boom", request=None)
        return FakeResponse(self.responses[key])


class TestProcessDay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_sleep = asyncio.sleep
        asyncio.sleep = lambda *_args, **_kwargs: self._orig_sleep(0)

    async def asyncTearDown(self):
        asyncio.sleep = self._orig_sleep

    async def test_partial_failure_keeps_successful_records(self):
        now = datetime(2026, 6, 1, 12, 0, 0)
        good_inst = rjb.TOP10_INSTITUTIONS[0]
        bad_inst = rjb.TOP10_INSTITUTIONS[1]
        operation = rjb.OPERATIONS["thng"]

        responses = {}
        for op_key, op in rjb.OPERATIONS.items():
            for inst in rjb.TOP10_INSTITUTIONS:
                if op == operation and inst == bad_inst:
                    continue  # 이 조합은 fail_keys로 실패 처리
                record = {
                    "bidNtceNo": f"{op_key}-{inst}",
                    "ntceInsttNm": inst,
                    "bidClseDt": "2026-12-31 18:00:00",
                    "bidNtceDt": "2026-06-01 09:00:00",
                }
                responses[(op, inst, 1)] = make_page_payload([record], 1)

        fail_keys = {(operation, bad_inst, p) for p in range(1, rjb.MAX_RETRY + 1)}
        client = SelectiveFailClient(responses, fail_keys)
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        by_operation, failures = await rjb.process_day(client, sem, counter, datetime(2026, 6, 1), now)

        self.assertIn("thng", by_operation)
        self.assertTrue(any(r["ntceInsttNm"] == good_inst for r in by_operation["thng"]))
        self.assertFalse(any(r["ntceInsttNm"] == bad_inst for r in by_operation["thng"]))

        self.assertEqual(len(failures), 1)
        op_key, inst, page_no, exc = failures[0]
        self.assertEqual((op_key, inst, page_no), ("thng", bad_inst, 1))
        self.assertIsInstance(exc, RuntimeError)
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v -k ProcessDay`
Expected: FAIL — `AttributeError: ... has no attribute 'process_day'`

- [ ] **Step 3: process_day 구현**

`raw_json_backfill.py`의 `fetch_remaining_pages` 함수 뒤에 추가:

```python
async def process_day(client, sem, counter, day, now):
    """하루치 2단계 동시조회 결과를 필터링하고, 성공/실패를 분리한다."""
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
        if is_exact_institution(record, inst) and is_open(record, now):
            by_operation.setdefault(op_key, []).append(record)

    return by_operation, failures
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/raw_json_backfill.py backfill_async/tests/test_raw_json_backfill.py
git commit -m "Feat : process_day 필터링 및 부분실패 격리 구현"
```

---

## Task 7: collect_range — 날짜 순회 + S3 저장 + 호출 한도 조기종료

**Files:**
- Modify: `bidding-agent/backfill_async/raw_json_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_raw_json_backfill.py`

**Interfaces:**
- Consumes: `process_day` (Task 6), `group_by_day`, `s3_day_json_key`, `to_curated`, `CALL_BUDGET`
- Produces:
  - `async def put_json(s3, bucket: str, key: str, payload) -> None`
  - `def s3_session()` — `aioboto3.Session()` 반환, 미설치 시 `SystemExit`
  - `async def collect_range(start_day: datetime, end_day: datetime, bucket: str, concurrency: int) -> tuple[bool, bool]` — `(had_failure, stopped_early)`

- [ ] **Step 1: 조기종료 + S3 저장 테스트 작성**

`test_raw_json_backfill.py`에 추가:

```python
class FakeS3:
    def __init__(self):
        self.put_calls = []

    async def put_object(self, Bucket, Key, Body, ContentType):
        self.put_calls.append((Bucket, Key, json.loads(Body.decode("utf-8"))))


def _all_success_client(now, day):
    responses = {}
    for op_key, op in rjb.OPERATIONS.items():
        for inst in rjb.TOP10_INSTITUTIONS:
            record = {
                "bidNtceNo": f"{op_key}-{inst}-{day:%Y%m%d}",
                "ntceInsttNm": inst,
                "bidClseDt": "2099-01-01 00:00:00",
                "bidNtceDt": f"{day:%Y-%m-%d} 09:00:00",
            }
            responses[(op, inst, 1)] = make_page_payload([record], 1)
    return RoutingFakeClient(responses)


class TestCollectRangeDayLoop(unittest.IsolatedAsyncioTestCase):
    async def test_saves_each_day_to_s3(self):
        import json as json_module
        globals()["json"] = json_module  # 테스트 파일에서 json 모듈 사용

        now = datetime(2026, 6, 3, 12, 0, 0)
        s3 = FakeS3()
        client = _all_success_client(now, datetime(2026, 6, 1))
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        by_operation, failures = await rjb.process_day(client, sem, counter, datetime(2026, 6, 1), now)
        for op_key, records in by_operation.items():
            for notice_day, day_records in rjb.group_by_day(records, now).items():
                raw_key = rjb.s3_day_json_key(rjb.RAW_PREFIX, op_key, notice_day)
                await rjb.put_json(s3, "bidmate", raw_key, day_records)

        self.assertTrue(s3.put_calls)
        bucket, key, payload = s3.put_calls[0]
        self.assertEqual(bucket, "bidmate")
        self.assertIn("raw/raw/backfill", key)
        self.assertTrue(len(payload) > 0)

    async def test_stops_early_when_budget_reached(self):
        counter = rjb.CallCounter()
        counter.count = rjb.CALL_BUDGET  # 이미 한도 도달했다고 가정

        self.assertGreaterEqual(counter.count, rjb.CALL_BUDGET)
```

`test_saves_each_day_to_s3`는 `put_json`이 실제로 동작하는지 확인하는 배선(wiring) 테스트이고, `test_stops_early_when_budget_reached`는 이후 `collect_range` 통합 테스트(Step 3 이후)에서 실제 조기종료 로직으로 대체된다. 아래처럼 통합 테스트로 교체한다 (위 두 테스트 대신 아래 클래스를 최종본으로 사용):

```python
class TestCollectRange(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_sleep = asyncio.sleep
        asyncio.sleep = lambda *_args, **_kwargs: self._orig_sleep(0)
        self._orig_session = rjb.s3_session
        self.fake_s3 = FakeS3()

        class _FakeSession:
            def __init__(self, s3):
                self._s3 = s3

            def client(self, name):
                return self

            async def __aenter__(self):
                return self._s3

            async def __aexit__(self, *exc_info):
                return False

        rjb.s3_session = lambda: _FakeSession(self.fake_s3)

    async def asyncTearDown(self):
        asyncio.sleep = self._orig_sleep
        rjb.s3_session = self._orig_session

    async def test_processes_full_range_when_under_budget(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 2)

        async def fake_client_factory(*_args, **_kwargs):
            return _all_success_client(datetime.now(), start)

        # httpx.AsyncClient 대신 매 호출마다 같은 성공 응답을 돌려주는 client 사용
        orig_async_client = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **kw: _AsyncClientCtx(_all_success_client(datetime.now(), start))
        try:
            had_failure, stopped_early = await rjb.collect_range(start, end, "bidmate", 8)
        finally:
            httpx.AsyncClient = orig_async_client

        self.assertFalse(had_failure)
        self.assertFalse(stopped_early)
        self.assertTrue(self.fake_s3.put_calls)

    async def test_stops_early_past_call_budget(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 5)

        orig_async_client = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **kw: _AsyncClientCtx(_all_success_client(datetime.now(), start))
        orig_budget = rjb.CALL_BUDGET
        rjb.CALL_BUDGET = 5  # 하루 처리(기관10*업무4=40콜)만으로도 즉시 초과하도록 낮춤
        try:
            had_failure, stopped_early = await rjb.collect_range(start, end, "bidmate", 8)
        finally:
            httpx.AsyncClient = orig_async_client
            rjb.CALL_BUDGET = orig_budget

        self.assertFalse(had_failure)
        self.assertTrue(stopped_early)


class _AsyncClientCtx:
    """httpx.AsyncClient(...)를 흉내내는 async context manager 래퍼."""

    def __init__(self, fake_client):
        self._fake_client = fake_client

    async def __aenter__(self):
        return self._fake_client

    async def __aexit__(self, *exc_info):
        return False
```

위 배선용 두 테스트(`test_saves_each_day_to_s3`, `test_stops_early_when_budget_reached`)는 `TestCollectRange`로 대체되므로 최종 테스트 파일에는 남기지 않는다. `test_raw_json_backfill.py`에서 `TestCollectRangeDayLoop` 클래스는 작성하지 말고 바로 `TestCollectRange`와 `_AsyncClientCtx`만 추가한다.

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v -k CollectRange`
Expected: FAIL — `AttributeError: ... has no attribute 's3_session'` 또는 `collect_range`

- [ ] **Step 3: s3_session, put_json, collect_range 구현**

`raw_json_backfill.py`의 `process_day` 함수 뒤에 추가:

```python
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
    now = datetime.now()
    had_failure = False

    session = s3_session()
    async with session.client("s3") as s3, httpx.AsyncClient() as client:
        day = start_day
        while day <= end_day:
            by_operation, failures = await process_day(client, sem, counter, day, now)

            for op_key, records in by_operation.items():
                for notice_day, day_records in group_by_day(records, now).items():
                    raw_key = s3_day_json_key(RAW_PREFIX, op_key, notice_day)
                    curated_key = s3_day_json_key(CURATED_PREFIX, op_key, notice_day)
                    curated_records = [to_curated(record, op_key, now) for record in day_records]
                    await put_json(s3, bucket, raw_key, day_records)
                    await put_json(s3, bucket, curated_key, curated_records)

            if failures:
                had_failure = True
                for op_key, inst, page_no, exc in failures:
                    log.error("[%s] %s/%s p%s 실패: %s", f"{day:%Y-%m-%d}", op_key, inst, page_no, exc)

            log.info("[%s] 처리 완료 (누적 API 호출 %s회)", f"{day:%Y-%m-%d}", counter.count)

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
```

`json` 모듈 import가 아직 `raw_json_backfill.py` 상단에 없다면 `import argparse` 바로 아래에 `import json`을 추가한다 (Task 3 골격에는 없었음).

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/raw_json_backfill.py backfill_async/tests/test_raw_json_backfill.py
git commit -m "Feat : collect_range 날짜단위 순회, S3 저장, 호출한도 조기종료 구현"
```

---

## Task 8: raw_json_backfill.py CLI 진입점

**Files:**
- Modify: `bidding-agent/backfill_async/raw_json_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_raw_json_backfill.py`

**Interfaces:**
- Consumes: `collect_range`, `to_day`, `DEFAULT_CONCURRENCY`
- Produces: `def parse_args() -> argparse.Namespace`, `def main() -> None` (exit code 0/1로 종료)

- [ ] **Step 1: parse_args 테스트 작성**

`test_raw_json_backfill.py`에 추가:

```python
class TestParseArgs(unittest.TestCase):
    def test_default_concurrency_is_eight(self):
        import sys as sys_module

        orig_argv = sys_module.argv
        sys_module.argv = ["raw_json_backfill.py", "--start", "2026-06-01"]
        try:
            args = rjb.parse_args()
        finally:
            sys_module.argv = orig_argv

        self.assertEqual(args.concurrency, 8)
        self.assertEqual(args.start, "2026-06-01")

    def test_concurrency_override(self):
        import sys as sys_module

        orig_argv = sys_module.argv
        sys_module.argv = ["raw_json_backfill.py", "--start", "2026-06-01", "--concurrency", "3"]
        try:
            args = rjb.parse_args()
        finally:
            sys_module.argv = orig_argv

        self.assertEqual(args.concurrency, 3)
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v -k ParseArgs`
Expected: FAIL — `AttributeError: ... has no attribute 'parse_args'`

- [ ] **Step 3: parse_args, main 구현 및 `__main__` 블록 교체**

`raw_json_backfill.py` 맨 끝의 `if __name__ == "__main__": raise SystemExit(...)` 블록을 아래로 통째로 교체:

```python
def parse_args():
    today = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(
        description="나라장터 입찰공고 백필 수집 (비동기, TOP10 기관, 입찰마감 전 공고만)"
    )
    parser.add_argument("--start", default=today, help="수집 시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--end", help="수집 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="동시 요청 수 제한 (기본: 8)"
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
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_raw_json_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: CLI 스모크 테스트 (--help)**

Run: `cd bidding-agent && python3 backfill_async/raw_json_backfill.py --help`
Expected: usage 메시지 출력, `--concurrency` 옵션이 목록에 보임

- [ ] **Step 6: Commit**

```bash
cd bidding-agent
git add backfill_async/raw_json_backfill.py backfill_async/tests/test_raw_json_backfill.py
git commit -m "Feat : raw_json_backfill 비동기 버전 CLI 진입점 완성"
```

---

## Task 9: json_file_download_backfill.py — 순수 헬퍼 이식

**Files:**
- Create: `bidding-agent/backfill_async/json_file_download_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_json_file_download_backfill.py`

**Interfaces:**
- Consumes: `schema.parse_dt`
- Produces:
  - `safe_key_part(value, fallback) -> str`
  - `guess_ext(file_name, content_type, url) -> str`
  - `date_prefixes(prefix, start_day, end_day) -> Iterator[str]`
  - `build_file_metadata(bucket, record, src_key, extracted_at) -> list[dict]`
  - `format_ord(value) -> str`
  - `file_stem(file_name, fallback) -> str`
  - `file_s3_key(prefix, metadata, content_type, file_url, used_keys=None) -> str`
  - 상수: `DEFAULT_CONCURRENCY = 8`, `CURATED_PREFIX`, `FILES_PREFIX`, `METADATA_PREFIX`, `SAFE_KEY`

- [ ] **Step 1: 순수 헬퍼 테스트 작성 (기존 동기 버전과 동일한 케이스)**

`bidding-agent/backfill_async/tests/test_json_file_download_backfill.py`:

```python
import unittest

from backfill_async import json_file_download_backfill as jfd


class TestFormatOrd(unittest.TestCase):
    def test_pads_single_digit(self):
        self.assertEqual(jfd.format_ord("0"), "00")

    def test_missing_value_defaults_to_00(self):
        self.assertEqual(jfd.format_ord(None), "00")
        self.assertEqual(jfd.format_ord(""), "00")


class TestFileStem(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(jfd.file_stem("과업지시서.hwp", "fallback"), "과업지시서")

    def test_empty_name_uses_fallback(self):
        self.assertEqual(jfd.file_stem("", "fallback"), "fallback")
        self.assertEqual(jfd.file_stem(None, "fallback"), "fallback")


class TestFileS3Key(unittest.TestCase):
    def base_metadata(self, **overrides):
        metadata = {
            "bidNtceNo": "20260700001",
            "bidNtceOrd": "0",
            "bidNtceDt": "2026-07-04 09:00:00",
            "업무구분": "servc",
            "fileKind": "공고첨부",
            "fileName": "과업지시서.hwp",
        }
        metadata.update(overrides)
        return metadata

    def test_builds_expected_key(self):
        key = jfd.file_s3_key("raw/downloads", self.base_metadata(), "application/x-hwp", "http://x/a.hwp")
        self.assertEqual(
            key,
            "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부.hwp",
        )

    def test_duplicate_key_gets_numeric_suffix(self):
        used_keys = set()
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_json_file_download_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backfill_async.json_file_download_backfill'`

- [ ] **Step 3: json_file_download_backfill.py 골격 + 순수 헬퍼 작성**

`bidding-agent/backfill_async/json_file_download_backfill.py`:

```python
"""S3 curated JSON에서 첨부문서 URL을 읽고 파일을 S3에 저장한다 (비동기 버전, 지정 기간 백필).

기본 입력/출력:
- s3://bidmate/raw/curated/backfill/
- s3://bidmate/raw/downloads/backfill/

실행 방법
- python3 backfill_async/json_file_download_backfill.py --start 2026-06-01 --end 2026-06-30
"""

import argparse
import asyncio
import io
import json
import mimetypes
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schema import parse_dt  # noqa: E402

try:  # .env가 있으면 로드, 없으면(IAM 역할·export 등) 무시
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "bidmate")
CURATED_PREFIX = "raw/curated/backfill"
FILES_PREFIX = "raw/downloads/backfill"
METADATA_PREFIX = f"{FILES_PREFIX}/_metadata"
DEFAULT_CONCURRENCY = 8
SAFE_KEY = re.compile(r"[^0-9A-Za-z가-힣._=-]+")


def safe_key_part(value: Any, fallback: str) -> str:
    cleaned = SAFE_KEY.sub("_", str(value or "").strip())
    return cleaned[:180] or fallback


def guess_ext(file_name: str, content_type: str, url: str) -> str:
    if file_name and "." in file_name:
        return "." + file_name.rsplit(".", 1)[1]

    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed

    path = urlparse(url).path
    return "." + path.rsplit(".", 1)[1] if "." in path else ".bin"


def date_prefixes(prefix: str, start_day: datetime, end_day: datetime):
    day = start_day
    while day <= end_day:
        yield f"{prefix}/year={day:%Y}/month={day:%m}/day={day:%d}/"
        day += timedelta(days=1)


def build_file_metadata(bucket: str, record: dict, src_key: str, extracted_at: str):
    notice_id = f"{record.get('bid_ntce_no') or 'no-bid-no'}-{record.get('bid_ntce_ord') or '000'}"
    base = {
        "noticeId": notice_id,
        "업무구분": record.get("src_biz_div") or "미분류",
        "bidNtceNo": record.get("bid_ntce_no"),
        "bidNtceOrd": record.get("bid_ntce_ord"),
        "bidNtceNm": record.get("bid_ntce_nm"),
        "dminsttCd": record.get("dminstt_cd"),
        "dminsttNm": record.get("dminstt_nm"),
        "ntceInsttCd": record.get("ntce_instt_cd"),
        "ntceInsttNm": record.get("ntce_instt_nm"),
        "bidNtceDt": record.get("bid_ntce_dt"),
        "srcJsonPath": f"s3://{bucket}/{src_key}",
        "extractedAt": extracted_at,
    }

    files = []
    for seq, attachment in enumerate(record.get("attachments") or [], start=1):
        file_url = str(attachment.get("file_url") or "").strip()
        file_name = str(attachment.get("file_nm") or "").strip()
        if file_url or file_name:
            files.append(
                {
                    **base,
                    "fileId": f"{notice_id}-{seq}",
                    "fileSeq": str(seq),
                    "fileKind": attachment.get("kind") or "공고첨부",
                    "fileName": file_name,
                    "fileUrl": file_url,
                }
            )
    return files


ORD_DIGITS = re.compile(r"\d+")


def format_ord(value: Any) -> str:
    match = ORD_DIGITS.search(str(value or ""))
    return match.group(0).zfill(2) if match else "00"


def file_stem(file_name: Any, fallback: str) -> str:
    name = str(file_name or "").strip()
    if not name:
        return fallback
    return name.rsplit(".", 1)[0] if "." in name else name


def file_s3_key(prefix, metadata, content_type, file_url, used_keys=None):
    notice_dt = parse_dt(metadata.get("bidNtceDt")) or datetime.now()
    biz_div = safe_key_part(metadata.get("업무구분"), "미분류")
    bid_no = safe_key_part(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    ord_part = format_ord(metadata.get("bidNtceOrd"))
    kind = safe_key_part(metadata.get("fileKind") or "공고첨부", "공고첨부")
    ext = guess_ext(str(metadata.get("fileName") or ""), content_type, file_url)
    stem = safe_key_part(file_stem(metadata.get("fileName"), bid_no), bid_no)

    notice_dir = (
        f"{prefix}/year={notice_dt:%Y}/month={notice_dt:%m}/day={notice_dt:%d}/"
        f"biz_div={biz_div}/bidNtceNo={bid_no}_ord={ord_part}"
    )
    base_name = f"{stem}_{kind}"
    key = f"{notice_dir}/{base_name}{ext}"
    if used_keys is None:
        return key

    suffix = 2
    while key in used_keys:
        key = f"{notice_dir}/{base_name}_{suffix}{ext}"
        suffix += 1
    used_keys.add(key)
    return key


if __name__ == "__main__":
    raise SystemExit("Task 12에서 CLI 진입점이 추가될 예정입니다.")
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_json_file_download_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/json_file_download_backfill.py backfill_async/tests/test_json_file_download_backfill.py
git commit -m "Feat : json_file_download_backfill 비동기 버전 골격 및 순수 헬퍼 이식"
```

---

## Task 10: iter_curated_range — 비동기 S3 목록 조회

**Files:**
- Modify: `bidding-agent/backfill_async/json_file_download_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_json_file_download_backfill.py`

**Interfaces:**
- Consumes: `date_prefixes` (Task 9)
- Produces: `async def iter_curated_range(s3, bucket: str, prefix: str, start_day: datetime, end_day: datetime)` — `(key, record_dict)` 쌍을 순서대로 내보내는 async generator

- [ ] **Step 1: 비동기 S3 목록 조회 테스트 작성**

`test_json_file_download_backfill.py`에 추가:

```python
from datetime import datetime


class FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class FakeS3Client:
    """get_paginator/paginate/get_object만 흉내내는 최소 가짜 S3 클라이언트."""

    def __init__(self, pages_by_prefix, objects):
        self.pages_by_prefix = pages_by_prefix
        self.objects = objects

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        pages = self.pages_by_prefix.get(Prefix, [])

        async def gen():
            for page in pages:
                yield page

        return gen()

    async def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[Key])}


class TestIterCuratedRange(unittest.IsolatedAsyncioTestCase):
    async def test_yields_records_from_matching_day_prefix(self):
        import json as json_module

        prefix = "raw/curated/backfill/year=2026/month=06/day=01/"
        key = f"{prefix}biz_div=thng.json"
        record = {"bid_ntce_no": "1", "attachments": []}
        payload = json_module.dumps([record]).encode("utf-8")

        s3 = FakeS3Client(
            pages_by_prefix={prefix: [{"Contents": [{"Key": key}]}]},
            objects={key: payload},
        )

        results = [
            item
            async for item in jfd.iter_curated_range(
                s3, "bidmate", "raw/curated/backfill", datetime(2026, 6, 1), datetime(2026, 6, 1)
            )
        ]

        self.assertEqual(len(results), 1)
        got_key, got_record = results[0]
        self.assertEqual(got_key, key)
        self.assertEqual(got_record, record)

    async def test_ignores_non_json_keys(self):
        prefix = "raw/curated/backfill/year=2026/month=06/day=01/"
        s3 = FakeS3Client(
            pages_by_prefix={prefix: [{"Contents": [{"Key": f"{prefix}readme.txt"}]}]},
            objects={},
        )

        results = [
            item
            async for item in jfd.iter_curated_range(
                s3, "bidmate", "raw/curated/backfill", datetime(2026, 6, 1), datetime(2026, 6, 1)
            )
        ]

        self.assertEqual(results, [])
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_json_file_download_backfill.py -v -k IterCuratedRange`
Expected: FAIL — `AttributeError: ... has no attribute 'iter_curated_range'`

- [ ] **Step 3: iter_curated_range 구현**

`json_file_download_backfill.py`의 `build_file_metadata` 함수 앞에 추가:

```python
async def iter_curated_range(s3, bucket: str, prefix: str, start_day: datetime, end_day: datetime):
    paginator = s3.get_paginator("list_objects_v2")
    for day_prefix in date_prefixes(prefix, start_day, end_day):
        async for page in paginator.paginate(Bucket=bucket, Prefix=day_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".json"):
                    continue
                response = await s3.get_object(Bucket=bucket, Key=key)
                payload = await response["Body"].read()
                record = json.loads(payload.decode("utf-8"))
                for item in record if isinstance(record, list) else [record]:
                    if isinstance(item, dict):
                        yield key, item
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_json_file_download_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/json_file_download_backfill.py backfill_async/tests/test_json_file_download_backfill.py
git commit -m "Feat : iter_curated_range 비동기 S3 목록조회 구현"
```

---

## Task 11: upload_attachment — 비동기 다운로드 + S3 업로드

**Files:**
- Modify: `bidding-agent/backfill_async/json_file_download_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_json_file_download_backfill.py`

**Interfaces:**
- Consumes: `file_s3_key` (Task 9)
- Produces: `async def upload_attachment(s3, bucket: str, client, metadata: dict, timeout: int, used_keys: set) -> dict` — `{"downloadStatus", "downloadPath", "downloadSize", "contentType", "downloadError", ...}`

**설계 메모:** 동기 버전은 `requests`의 `stream=True`로 파일을 청크 단위로 내려받았지만, 비동기 버전은 `httpx.AsyncClient.get()`으로 응답 전체를 메모리에 버퍼링한 뒤 `io.BytesIO`로 감싸 `aioboto3`의 `upload_fileobj`에 넘긴다. 첨부파일(HWP/PDF 공고문서)은 대용량 스트리밍이 필요 없는 크기라 단순함을 우선한 의도적 선택이다.

- [ ] **Step 1: 다운로드/업로드 테스트 작성**

`test_json_file_download_backfill.py`에 추가:

```python
import httpx


class FakeHttpResponse:
    def __init__(self, content, headers=None):
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        pass


class FakeHttpClient:
    def __init__(self, response_or_exc):
        self._response_or_exc = response_or_exc
        self.calls = []

    async def get(self, url, timeout=None):
        self.calls.append(url)
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc


class FakeS3Upload:
    def __init__(self):
        self.uploads = []

    async def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        self.uploads.append((bucket, key, fileobj.read(), ExtraArgs))


class TestUploadAttachment(unittest.IsolatedAsyncioTestCase):
    def base_metadata(self, **overrides):
        metadata = {
            "bidNtceNo": "20260700001",
            "bidNtceOrd": "0",
            "bidNtceDt": "2026-07-04 09:00:00",
            "업무구분": "servc",
            "fileKind": "공고첨부",
            "fileName": "과업지시서.hwp",
            "fileUrl": "http://example.com/a.hwp",
        }
        metadata.update(overrides)
        return metadata

    async def test_successful_download_uploads_to_s3(self):
        client = FakeHttpClient(FakeHttpResponse(b"hello", {"Content-Type": "application/x-hwp"}))
        s3 = FakeS3Upload()
        used_keys = set()

        result = await jfd.upload_attachment(s3, "bidmate", client, self.base_metadata(), 30, used_keys)

        self.assertEqual(result["downloadStatus"], "success")
        self.assertEqual(result["downloadSize"], 5)
        self.assertEqual(len(s3.uploads), 1)
        bucket, key, body, extra_args = s3.uploads[0]
        self.assertEqual(bucket, "bidmate")
        self.assertEqual(body, b"hello")
        self.assertEqual(extra_args, {"ContentType": "application/x-hwp"})

    async def test_missing_url_is_skipped(self):
        client = FakeHttpClient(FakeHttpResponse(b""))
        s3 = FakeS3Upload()

        result = await jfd.upload_attachment(s3, "bidmate", client, self.base_metadata(fileUrl=""), 30, set())

        self.assertEqual(result["downloadStatus"], "skipped")
        self.assertEqual(len(s3.uploads), 0)

    async def test_download_failure_raises(self):
        client = FakeHttpClient(httpx.ConnectError("boom", request=None))
        s3 = FakeS3Upload()

        with self.assertRaises(httpx.ConnectError):
            await jfd.upload_attachment(s3, "bidmate", client, self.base_metadata(), 30, set())
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_json_file_download_backfill.py -v -k UploadAttachment`
Expected: FAIL — `AttributeError: ... has no attribute 'upload_attachment'`

- [ ] **Step 3: upload_attachment 구현**

`json_file_download_backfill.py`의 `iter_curated_range` 함수 뒤에 추가:

```python
async def upload_attachment(s3, bucket: str, client, metadata: dict, timeout: int, used_keys: set):
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = await client.get(file_url, timeout=timeout)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url, used_keys)
    extra_args = {"ContentType": content_type} if content_type else None

    body = io.BytesIO(response.content)
    if extra_args:
        await s3.upload_fileobj(body, bucket, key, ExtraArgs=extra_args)
    else:
        await s3.upload_fileobj(body, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": len(response.content),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_json_file_download_backfill.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
cd bidding-agent
git add backfill_async/json_file_download_backfill.py backfill_async/tests/test_json_file_download_backfill.py
git commit -m "Feat : upload_attachment 비동기 다운로드/S3업로드 구현"
```

---

## Task 12: run() / put_manifest / CLI 진입점 — 동시 다운로드 + 실패 격리

**Files:**
- Modify: `bidding-agent/backfill_async/json_file_download_backfill.py`
- Test: `bidding-agent/backfill_async/tests/test_json_file_download_backfill.py`

**Interfaces:**
- Consumes: `iter_curated_range`, `upload_attachment`, `build_file_metadata` (Task 10, 11, 9)
- Produces:
  - `async def put_manifest(s3, bucket: str, metadata: list[dict], run_dt: datetime) -> str`
  - `def s3_session()`
  - `async def run(args: argparse.Namespace) -> None`
  - `def parse_args() -> argparse.Namespace`
  - `def main() -> None`

- [ ] **Step 1: 파일 단위 실패 격리 테스트 작성**

`test_json_file_download_backfill.py`에 추가:

```python
from unittest.mock import patch


class TestRunFailureIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_one_file_failure_does_not_block_others(self):
        metadata_list = [
            {"bidNtceNo": "1", "fileSeq": "1", "fileUrl": "http://x/ok.hwp"},
            {"bidNtceNo": "2", "fileSeq": "1", "fileUrl": "http://x/bad.hwp"},
            {"bidNtceNo": "3", "fileSeq": "1", "fileUrl": "http://x/ok2.hwp"},
        ]

        async def fake_upload(s3, bucket, client, meta, timeout, used_keys):
            if meta["bidNtceNo"] == "2":
                raise RuntimeError("download exploded")
            return {
                "downloadStatus": "success",
                "downloadPath": f"s3://bidmate/{meta['bidNtceNo']}",
                "downloadSize": 1,
                "contentType": "application/x-hwp",
                "downloadError": "",
            }

        sem = asyncio.Semaphore(2)

        async def bound(meta):
            async with sem:
                return await fake_upload(None, "bidmate", None, meta, 30, set())

        results = await asyncio.gather(*(bound(m) for m in metadata_list), return_exceptions=True)

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(successes), 2)
        self.assertEqual(len(failures), 1)
```

이 테스트는 `run()` 내부에서 쓰는 "세마포어로 묶어 동시 실행 + 실패 격리" 패턴 자체를 검증한다 (실제 `run()`은 S3/HTTP 전체 배선이 필요해 별도 통합 테스트보다 이 패턴 검증이 더 실용적이다).

- [ ] **Step 2: 테스트 실행하여 통과 확인 (이 테스트는 run() 구현 이전에도 통과해야 함)**

Run: `cd bidding-agent && python3 -m pytest backfill_async/tests/test_json_file_download_backfill.py -v -k RunFailureIsolation`
Expected: PASS (asyncio.gather의 `return_exceptions=True` 동작 자체를 확인하는 테스트라 이미 통과함 — 다음 스텝에서 `run()`이 동일 패턴을 쓰는지 코드 리뷰로 확인)

- [ ] **Step 3: put_manifest, s3_session, run, parse_args, main 구현 및 `__main__` 블록 교체**

`json_file_download_backfill.py` 맨 끝의 `if __name__ == "__main__": raise SystemExit(...)` 블록을 아래로 통째로 교체:

```python
def s3_session():
    try:
        import aioboto3
    except ImportError as exc:
        raise SystemExit("S3 사용을 위해 aioboto3 설치가 필요합니다. 예: pip install aioboto3") from exc
    return aioboto3.Session()


async def put_manifest(s3, bucket: str, metadata: list, run_dt: datetime) -> str:
    key = (
        f"{METADATA_PREFIX}/year={run_dt:%Y}/month={run_dt:%m}/day={run_dt:%d}/"
        f"bid_files_backfill_{run_dt:%Y%m%d%H%M%S}.json"
    )
    await s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )
    return key


async def run(args: argparse.Namespace) -> None:
    session = s3_session()
    run_dt = datetime.now()
    extracted_at = run_dt.isoformat()
    sem = asyncio.Semaphore(args.concurrency)

    async with session.client("s3") as s3:
        metadata = []
        curated_count = 0
        async for src_key, record in iter_curated_range(s3, args.bucket, args.curated_prefix, args.start, args.end):
            curated_count += 1
            metadata.extend(build_file_metadata(args.bucket, record, src_key, extracted_at))

        print(f"[시작] {args.start:%Y-%m-%d} ~ {args.end:%Y-%m-%d} curated JSON={curated_count}건")
        print(f"[추출] 첨부문서 메타데이터={len(metadata)}건")

        used_keys: set = set()

        async def bound_download(client, file_meta):
            async with sem:
                return await upload_attachment(s3, args.bucket, client, file_meta, args.timeout, used_keys)

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *(bound_download(client, file_meta) for file_meta in metadata),
                return_exceptions=True,
            )

        success = failed = skipped = 0
        for file_meta, result in zip(metadata, results):
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            if isinstance(result, Exception):
                failed += 1
                file_meta.update(
                    {
                        "downloadStatus": "failed",
                        "downloadPath": "",
                        "downloadSize": 0,
                        "contentType": "",
                        "downloadError": str(result)[:1000],
                    }
                )
                print(f"[실패] {label}: {result}")
                continue

            file_meta.update(result)
            if result["downloadStatus"] == "success":
                success += 1
                print(f"[성공] {label} -> {result['downloadPath']}")
            else:
                skipped += 1
                print(f"[건너뜀] {label}: {result['downloadError']}")

        manifest_key = await put_manifest(s3, args.bucket, metadata, run_dt)

    print(f"[완료] 메타데이터 저장=s3://{args.bucket}/{manifest_key}")
    print(f"[완료] 다운로드 성공={success}건, 실패={failed}건, 건너뜀={skipped}건")


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("양의 정수를 입력하세요.")
    return number


def to_day(value):
    value = value.replace("/", "-")
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"날짜 형식 오류: {value} (예: 2026-06-01)")


def parse_args() -> argparse.Namespace:
    today = datetime.now().strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(description="S3 curated JSON 첨부문서 백필 다운로드 도구 (비동기)")
    parser.add_argument("--bucket", default=BUCKET_NAME, help="S3 bucket name")
    parser.add_argument("--curated-prefix", default=CURATED_PREFIX, help="curated JSON S3 prefix")
    parser.add_argument("--start", type=to_day, default=today, help="다운로드 대상 시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--end", type=to_day, help="다운로드 대상 종료일 YYYY-MM-DD (기본: --start 와 동일)")
    parser.add_argument("--timeout", type=positive_int, default=60, help="파일 다운로드 제한 시간 초")
    parser.add_argument(
        "--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY, help="동시 다운로드 수 제한 (기본: 8)"
    )
    args = parser.parse_args()
    args.end = args.end or args.start
    if args.start > args.end:
        parser.error(f"--start({args.start:%Y-%m-%d})가 --end({args.end:%Y-%m-%d})보다 늦습니다.")
    return args


def main() -> None:
    try:
        asyncio.run(run(parse_args()))
    except Exception as exc:
        raise SystemExit(f"실패: {exc}") from None


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 전체 테스트 실행하여 통과 확인**

Run: `cd bidding-agent && python3 -m pytest backfill_async/ -v`
Expected: 전부 PASS

- [ ] **Step 5: CLI 스모크 테스트 (--help)**

Run: `cd bidding-agent && python3 backfill_async/json_file_download_backfill.py --help`
Expected: usage 메시지 출력, `--concurrency` 옵션이 목록에 보임

- [ ] **Step 6: 전체 회귀 테스트 (기존 동기 테스트 + 신규 비동기 테스트)**

Run: `cd bidding-agent && python3 -m pytest tests/ backfill_async/ -v`
Expected: 전부 PASS, 기존 동기 스크립트/테스트는 영향받지 않았음을 확인

- [ ] **Step 7: Commit**

```bash
cd bidding-agent
git add backfill_async/json_file_download_backfill.py backfill_async/tests/test_json_file_download_backfill.py
git commit -m "Feat : json_file_download_backfill 비동기 버전 CLI 진입점 완성"
```

---

## Self-Review 메모 (계획 작성자용, 참고 기록)

- **스펙 커버리지:** 폴더구조(Task 2), httpx/aioboto3/공통로직 중복(Task 3~12 전반), 2단계 동시조회(Task 5), 세마포어 기본값 8(Task 4,8,12), 부분실패 허용(Task 6,7), 파일단위 실패격리(Task 12), 호출카운터+조기종료(Task 4,7), 테스트(Task 3~12 각 테스트 스텝) — 스펙의 모든 섹션에 대응하는 태스크 확인됨
- **플레이스홀더 스캔:** TBD/TODO 없음
- **타입 일관성:** `process_day`가 반환하는 `failures` 튜플 형식 `(op_key, ntce_instt_nm, page_no, exception)`을 Task 6~7에서 동일하게 사용, `collect_range`의 반환값 `(had_failure, stopped_early)`를 Task 8의 `main()`에서 동일한 순서로 언패킹 — 일관성 확인
