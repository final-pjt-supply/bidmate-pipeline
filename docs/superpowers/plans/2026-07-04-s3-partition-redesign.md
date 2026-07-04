# S3 폴더 구조 재정의 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `feat/raw-json-ingestion` 브랜치의 4개 수집 스크립트가 사용하는 S3 버킷명과 저장 단위를 재정의하고, README.md를 현재 코드에 맞게 재작성한다.

**Architecture:** 순수 함수(키 생성, day 그룹핑, 파일명 규칙)를 기존 스크립트 안에서 리팩터링하고 `unittest`로 검증한다. 네트워크/S3 호출이 필요한 부분(fetch, put_object, upload_fileobj)은 로직 변경 없이 그대로 두고 호출부만 새 함수로 갈아끼운다.

**Tech Stack:** Python 3 표준 라이브러리(`unittest`), `requests`, `boto3`. 새 의존성 추가 없음.

## Global Constraints

- 4개 대상 파일: `raw_json_backfill.py`, `raw_json_daily.py`, `json_file_download_backfill.py`, `json_file_download_daily.py`. `institutions.py`, `schema.py`는 수정하지 않는다.
- `BUCKET_NAME` 기본값: `"bidding-agent"` → `"bidmate"` (4개 파일 전부, `S3_BUCKET` 환경변수로 오버라이드 가능한 구조는 유지)
- `raw/raw`, `raw/curated`, `raw/downloads` 세 폴더 전부 바로 아래에 `backfill/`, `daily/` 하위 폴더를 둔다.
  각 스크립트의 `RAW_PREFIX`/`CURATED_PREFIX`/`FILES_PREFIX` 상수에 반영한다:
  - `raw_json_daily.py` → `raw/raw/daily`, `raw/curated/daily`
  - `raw_json_backfill.py` → `raw/raw/backfill`, `raw/curated/backfill`
  - `json_file_download_daily.py` → `--curated-prefix` 기본값 `raw/curated/daily`, `FILES_PREFIX` `raw/downloads/daily`
  - `json_file_download_backfill.py` → `--curated-prefix` 기본값 `raw/curated/backfill`, `FILES_PREFIX` `raw/downloads/backfill`
- `raw_json_daily.py`: 저장 단위 동작 변경 없음 (레코드 1건 = JSON 1개 유지, 경로만 `/daily` 하위로 이동)
- `raw_json_backfill.py`: `raw/raw/backfill`, `raw/curated/backfill` 모두 **하루+업무구분 단위로 배열 JSON 1개** 저장. 경로: `{prefix}/year=Y/month=M/day=D/biz_div={cat}.json`
- `json_file_download_backfill.py` / `json_file_download_daily.py`: 첨부파일 키를
  `{prefix}/year=Y/month=M/day=D/biz_div={cat}/bidNtceNo={번호}_ord={2자리 제로패딩}/{stem}_{kind}{확장자}` 형태로 생성
  (`prefix`는 위에서 정의한 `raw/downloads/daily` 또는 `raw/downloads/backfill`).
  `biz_div`는 metadata의 `업무구분` 값을 그대로 쓰고, `stem`은 원본 파일명에서 확장자를 뗀
  부분(없으면 `bidNtceNo`로 대체), `kind`는 `fileKind` 값 그대로.
  완전히 동일한 키가 같은 실행 내에서 재발생하면 `_2`, `_3` 접미사를 붙인다.
- 새 pip 의존성 추가 없음 (`requests`, `boto3`는 기존 스크립트가 이미 필요로 하던 패키지).
- 참고 스펙 문서: `docs/superpowers/specs/2026-07-04-s3-partition-redesign-design.md`

---

## Task 1: 테스트 실행 환경 준비

이 저장소의 `python3`(Homebrew, externally-managed)에는 `requests`/`boto3`가 설치돼 있지 않아
대상 스크립트를 임포트하는 것만으로 `ModuleNotFoundError`가 난다. 이후 태스크의 단위테스트를
돌리려면 로컬 venv가 먼저 있어야 한다. `.venv/`는 이미 `.gitignore`에 포함되어 있다.

**Files:** 없음 (환경 준비만)

- [ ] **Step 1: venv 생성**

```bash
cd /Users/oloqlq/Desktop/final-pjt-supply/bidding-agent
python3 -m venv .venv
```

- [ ] **Step 2: 의존성 설치**

```bash
.venv/bin/pip install requests boto3
```

- [ ] **Step 3: 임포트 확인**

```bash
.venv/bin/python3 -c "import requests, boto3; print('ok')"
```

Expected: `ok` 출력

커밋 없음 (`.venv/`는 gitignore 대상이라 커밋할 변경사항이 없음).

---

## Task 2: 버킷명 + 경로 프리픽스 변경 (`bidmate`, `backfill`/`daily` 하위 폴더)

**Files:**
- Modify: `raw_json_backfill.py:9-10,29,31-32`
- Modify: `raw_json_daily.py:8-9,24,26-27`
- Modify: `json_file_download_backfill.py:4-5,24,25-26`
- Modify: `json_file_download_daily.py:4-5,21,22-23`

**Interfaces:**
- Consumes: 없음
- Produces: 4개 파일의 `BUCKET_NAME="bidmate"`, 그리고 각 파일의 `RAW_PREFIX`/`CURATED_PREFIX`/`FILES_PREFIX`가
  `daily`/`backfill` 하위 경로를 가리키도록 함 — 이후 태스크가 그대로 사용

- [ ] **Step 1: `raw_json_backfill.py` 수정**

`raw_json_backfill.py:8-10`, 현재:
```python
기본 저장 위치:
- s3://bidding-agent/raw/raw/
- s3://bidding-agent/raw/curated/
```
변경 후:
```python
기본 저장 위치:
- s3://bidmate/raw/raw/backfill/
- s3://bidmate/raw/curated/backfill/
```

`raw_json_backfill.py:29`, 현재:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidding-agent")
```
변경 후:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidmate")
```

`raw_json_backfill.py:31-32`, 현재:
```python
RAW_PREFIX = "raw/raw"
CURATED_PREFIX = "raw/curated"
```
변경 후:
```python
RAW_PREFIX = "raw/raw/backfill"
CURATED_PREFIX = "raw/curated/backfill"
```

- [ ] **Step 2: `raw_json_daily.py` 수정**

`raw_json_daily.py:7-9`, 현재:
```python
기본 저장 위치:
- s3://bidding-agent/raw/raw/
- s3://bidding-agent/raw/curated/
```
변경 후:
```python
기본 저장 위치:
- s3://bidmate/raw/raw/daily/
- s3://bidmate/raw/curated/daily/
```

`raw_json_daily.py:24`, 현재:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidding-agent")
```
변경 후:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidmate")
```

`raw_json_daily.py:26-27`, 현재:
```python
RAW_PREFIX = "raw/raw"
CURATED_PREFIX = "raw/curated"
```
변경 후:
```python
RAW_PREFIX = "raw/raw/daily"
CURATED_PREFIX = "raw/curated/daily"
```

- [ ] **Step 3: `json_file_download_backfill.py` 수정**

`json_file_download_backfill.py:3-5`, 현재:
```python
기본 입력/출력:
- s3://bidding-agent/raw/curated/
- s3://bidding-agent/raw/downloads/
```
변경 후:
```python
기본 입력/출력:
- s3://bidmate/raw/curated/backfill/
- s3://bidmate/raw/downloads/backfill/
```

`json_file_download_backfill.py:24`, 현재:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidding-agent")
```
변경 후:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidmate")
```

`json_file_download_backfill.py:25-26`, 현재:
```python
CURATED_PREFIX = "raw/curated"
FILES_PREFIX = "raw/downloads"
```
변경 후:
```python
CURATED_PREFIX = "raw/curated/backfill"
FILES_PREFIX = "raw/downloads/backfill"
```

(`METADATA_PREFIX = f"{FILES_PREFIX}/_metadata"`는 `FILES_PREFIX`를 그대로 참조하므로 자동으로
`raw/downloads/backfill/_metadata`가 된다. 별도 수정 불필요.)

- [ ] **Step 4: `json_file_download_daily.py` 수정**

`json_file_download_daily.py:3-5`, 현재:
```python
기본 입력/출력:
- s3://bidding-agent/raw/curated/
- s3://bidding-agent/raw/downloads/
```
변경 후:
```python
기본 입력/출력:
- s3://bidmate/raw/curated/daily/
- s3://bidmate/raw/downloads/daily/
```

`json_file_download_daily.py:21`, 현재:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidding-agent")
```
변경 후:
```python
BUCKET_NAME = os.environ.get("S3_BUCKET", "bidmate")
```

`json_file_download_daily.py:22-23`, 현재:
```python
CURATED_PREFIX = "raw/curated"
FILES_PREFIX = "raw/downloads"
```
변경 후:
```python
CURATED_PREFIX = "raw/curated/daily"
FILES_PREFIX = "raw/downloads/daily"
```

(`METADATA_PREFIX`는 `FILES_PREFIX` 참조라 자동으로 `raw/downloads/daily/_metadata`가 된다.)

- [ ] **Step 5: 상수 값 확인**

```bash
cd /Users/oloqlq/Desktop/final-pjt-supply/bidding-agent
.venv/bin/python3 -c "
import raw_json_backfill as a, raw_json_daily as b, json_file_download_backfill as c, json_file_download_daily as d
print(a.BUCKET_NAME, a.RAW_PREFIX, a.CURATED_PREFIX)
print(b.BUCKET_NAME, b.RAW_PREFIX, b.CURATED_PREFIX)
print(c.BUCKET_NAME, c.CURATED_PREFIX, c.FILES_PREFIX, c.METADATA_PREFIX)
print(d.BUCKET_NAME, d.CURATED_PREFIX, d.FILES_PREFIX, d.METADATA_PREFIX)
"
```

Expected:
```text
bidmate raw/raw/backfill raw/curated/backfill
bidmate raw/raw/daily raw/curated/daily
bidmate raw/curated/backfill raw/downloads/backfill raw/downloads/backfill/_metadata
bidmate raw/curated/daily raw/downloads/daily raw/downloads/daily/_metadata
```

또한 옛 이름이 남아있지 않은지 확인:

```bash
grep -n "bidding-agent" raw_json_backfill.py raw_json_daily.py json_file_download_backfill.py json_file_download_daily.py
```

Expected: 출력 없음

- [ ] **Step 6: 커밋**

```bash
git add raw_json_backfill.py raw_json_daily.py json_file_download_backfill.py json_file_download_daily.py
git commit -m "Refactor : S3 버킷명 bidmate 및 backfill/daily 하위 경로 반영"
```

---

## Task 3: `raw_json_backfill.py` — 일자별 배열 저장

**Files:**
- Modify: `raw_json_backfill.py:17-24` (import 정리), `raw_json_backfill.py:43-46` (SAFE_KEY 제거), `raw_json_backfill.py:110-175` (핵심 로직)
- Test: `tests/test_raw_json_backfill.py`

**Interfaces:**
- Consumes: 기존 `notice_day(record, fallback_dt)`, `is_open(record, now)`, `is_exact_institution(record, ntce_instt_nm)`, `to_curated(record, biz_div, collected_at)`, `put_json(s3, bucket, key, payload)`, `fetch_all(...)`, `TOP10_INSTITUTIONS`
- Produces:
  - `group_by_day(records: list[dict], now: datetime) -> dict[datetime, list[dict]]`
  - `s3_day_json_key(prefix: str, cat: str, day: datetime) -> str`
  - (제거됨: `safe_key_part`, `notice_id`, 레코드 단위 `s3_json_key`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_raw_json_backfill.py` 새로 생성:

```python
import unittest
from datetime import datetime

import raw_json_backfill as rjb


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
        self.assertEqual(len(groups[datetime(2026, 6, 3)]), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인 (함수가 아직 없음)**

```bash
cd /Users/oloqlq/Desktop/final-pjt-supply/bidding-agent
.venv/bin/python3 -m unittest discover -s tests -p "test_raw_json_backfill.py" -t . -v
```

Expected: `AttributeError: module 'raw_json_backfill' has no attribute 's3_day_json_key'` (또는 `group_by_day`)로 FAIL

- [ ] **Step 3: import 정리 — `re` 제거**

`raw_json_backfill.py:17-24`, 현재:
```python
import argparse
import json
import logging
import os
import re
import time
from datetime import datetime
import requests
```
변경 후:
```python
import argparse
import json
import logging
import os
import time
from datetime import datetime
import requests
```

- [ ] **Step 4: `SAFE_KEY` 상수 제거**

`raw_json_backfill.py:44`, 현재:
```python
MAX_RETRY = 3
SAFE_KEY = re.compile(r"[^0-9A-Za-z가-힣._=-]+")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
```
변경 후:
```python
MAX_RETRY = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
```

- [ ] **Step 5: 핵심 로직 교체**

`raw_json_backfill.py:110-176`, 현재:
```python
def safe_key_part(value, fallback):
    cleaned = SAFE_KEY.sub("_", str(value or "").strip())
    return cleaned[:180] or fallback


def notice_id(record, index):
    bid_no = safe_key_part(record.get("bidNtceNo"), f"no-bid-no-{index}")
    bid_ord = safe_key_part(record.get("bidNtceOrd"), "000")
    return f"{bid_no}-{bid_ord}"


def notice_day(record, fallback_dt):
    notice_dt = parse_dt(record.get("bidNtceDt")) or fallback_dt
    return datetime(notice_dt.year, notice_dt.month, notice_dt.day)


def s3_json_key(prefix, cat, day, record, index):
    return (
        f"{prefix}/year={day:%Y}/month={day:%m}/day={day:%d}/"
        f"biz_div={cat}/{notice_id(record, index)}.json"
    )


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
            fetched_count = saved_count = 0

            for ntce_instt_nm in TOP10_INSTITUTIONS:
                records = fetch_all(session, operation, bgn_dt, end_dt, ntce_instt_nm)
                fetched_count += len(records)

                for index, record in enumerate(records, start=1):
                    if not is_exact_institution(record, ntce_instt_nm) or not is_open(record, now):
                        continue

                    day = notice_day(record, now)
                    raw_key = s3_json_key(RAW_PREFIX, cat, day, record, index)
                    curated_key = s3_json_key(CURATED_PREFIX, cat, day, record, index)
                    put_json(s3, bucket, raw_key, record)
                    put_json(s3, bucket, curated_key, to_curated(record, cat, now))
                    saved_count += 1

            log.info(
                "[%s ~ %s] %s: 조회 %s / S3 저장 %s건",
                f"{start_day:%Y-%m-%d}",
                f"{end_day:%Y-%m-%d}",
                cat,
                fetched_count,
                saved_count,
            )
```
변경 후:
```python
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
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
.venv/bin/python3 -m unittest discover -s tests -p "test_raw_json_backfill.py" -t . -v
```

Expected: 3개 테스트 모두 PASS

- [ ] **Step 7: 구문/임포트 확인**

```bash
.venv/bin/python3 -c "import raw_json_backfill"
```

Expected: 에러 없이 종료

- [ ] **Step 8: 커밋**

```bash
git add raw_json_backfill.py tests/test_raw_json_backfill.py
git commit -m "Refactor : raw_json_backfill 일자별 배열 저장으로 변경"
```

---

## Task 4: 첨부파일 다운로드 S3 키 구조 변경 (backfill + daily)

`json_file_download_backfill.py`와 `json_file_download_daily.py`는 관련 함수 본문이 동일하므로
두 파일에 동일한 변경을 적용한다.

**Files:**
- Modify: `json_file_download_backfill.py:115-163`, `json_file_download_backfill.py:194-199`
- Modify: `json_file_download_daily.py:118-166`, `json_file_download_daily.py:198-203`
- Test: `tests/test_json_file_download_backfill.py`
- Test: `tests/test_json_file_download_daily.py`

**Interfaces:**
- Consumes: 기존 `guess_ext(file_name, content_type, url)`, `safe_key_part(value, fallback)`, `parse_dt(value)`
- Produces (양쪽 파일에 동일하게 존재):
  - `format_ord(value) -> str`
  - `file_stem(file_name, fallback) -> str`
  - `file_s3_key(prefix, metadata, content_type, file_url, used_keys=None) -> str` (시그니처에 `used_keys` 추가)
  - `upload_attachment(s3, bucket, session, metadata, timeout, used_keys) -> dict` (시그니처에 `used_keys` 추가, 필수 인자)

### Part A — `json_file_download_backfill.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_json_file_download_backfill.py` 새로 생성:

```python
import unittest

import json_file_download_backfill as jfd


class TestFormatOrd(unittest.TestCase):
    def test_pads_single_digit(self):
        self.assertEqual(jfd.format_ord("0"), "00")

    def test_keeps_two_digits(self):
        self.assertEqual(jfd.format_ord("12"), "12")

    def test_missing_value_defaults_to_00(self):
        self.assertEqual(jfd.format_ord(None), "00")
        self.assertEqual(jfd.format_ord(""), "00")


class TestFileStem(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(jfd.file_stem("과업지시서.hwp", "fallback"), "과업지시서")

    def test_no_extension_returns_name(self):
        self.assertEqual(jfd.file_stem("과업지시서", "fallback"), "과업지시서")

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

    def test_std_notice_without_filename_uses_bid_no_as_stem(self):
        metadata = self.base_metadata(fileKind="표준공고서", fileName="")
        key = jfd.file_s3_key("raw/downloads", metadata, "application/pdf", "http://x/std.pdf")
        self.assertEqual(
            key,
            "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/20260700001_표준공고서.pdf",
        )

    def test_duplicate_key_gets_numeric_suffix(self):
        used_keys = set()
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        third = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)

        self.assertEqual(first, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부.hwp")
        self.assertEqual(second, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부_2.hwp")
        self.assertEqual(third, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부_3.hwp")

    def test_without_used_keys_no_dedup_applied(self):
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp")
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd /Users/oloqlq/Desktop/final-pjt-supply/bidding-agent
.venv/bin/python3 -m unittest discover -s tests -p "test_json_file_download_backfill.py" -t . -v
```

Expected: `AttributeError: module 'json_file_download_backfill' has no attribute 'format_ord'`로 FAIL

- [ ] **Step 3: `file_s3_key` 및 관련 함수 교체**

`json_file_download_backfill.py:115-163`, 현재:
```python
def file_s3_key(prefix: str, metadata: dict[str, Any], content_type: str, file_url: str):
    notice_dt = parse_dt(metadata.get("bidNtceDt")) or datetime.now()
    biz_div = safe_key_part(metadata.get("업무구분"), "미분류")
    bid_no = safe_key_part(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    file_seq = safe_key_part(metadata.get("fileSeq"), "unknown")
    original_name = str(metadata.get("fileName") or "")
    ext = guess_ext(original_name, content_type, file_url)
    filename = safe_key_part(original_name, f"{bid_no}_{file_seq}{ext}")
    if "." not in filename:
        filename = f"{filename}{ext}"

    return (
        f"{prefix}/year={notice_dt:%Y}/month={notice_dt:%m}/day={notice_dt:%d}/"
        f"biz_div={biz_div}/notice_id={bid_no}/{file_seq}_{filename}"
    )


def upload_attachment(s3, bucket: str, session: requests.Session, metadata: dict[str, Any], timeout: int):
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()
    response.raw.decode_content = True

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3.upload_fileobj(response.raw, bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_fileobj(response.raw, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": int(response.headers.get("Content-Length") or 0),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }
```
변경 후:
```python
ORD_DIGITS = re.compile(r"\d+")


def format_ord(value: Any) -> str:
    match = ORD_DIGITS.search(str(value or ""))
    return match.group(0).zfill(2) if match else "00"


def file_stem(file_name: Any, fallback: str) -> str:
    name = str(file_name or "").strip()
    if not name:
        return fallback
    return name.rsplit(".", 1)[0] if "." in name else name


def file_s3_key(
    prefix: str,
    metadata: dict[str, Any],
    content_type: str,
    file_url: str,
    used_keys: set[str] | None = None,
):
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


def upload_attachment(
    s3,
    bucket: str,
    session: requests.Session,
    metadata: dict[str, Any],
    timeout: int,
    used_keys: set[str],
):
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()
    response.raw.decode_content = True

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url, used_keys)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3.upload_fileobj(response.raw, bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_fileobj(response.raw, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": int(response.headers.get("Content-Length") or 0),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }
```

- [ ] **Step 4: `run()` 호출부 수정**

`json_file_download_backfill.py:194-199`, 현재:
```python
    success = failed = skipped = 0
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                result = upload_attachment(s3, args.bucket, session, file_meta, args.timeout)
```
변경 후:
```python
    success = failed = skipped = 0
    used_keys: set[str] = set()
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                result = upload_attachment(s3, args.bucket, session, file_meta, args.timeout, used_keys)
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
.venv/bin/python3 -m unittest discover -s tests -p "test_json_file_download_backfill.py" -t . -v
```

Expected: 9개 테스트 모두 PASS

- [ ] **Step 6: 구문/임포트 확인**

```bash
.venv/bin/python3 -c "import json_file_download_backfill"
```

Expected: 에러 없이 종료

### Part B — `json_file_download_daily.py` (Part A와 동일한 변경)

- [ ] **Step 7: 실패하는 테스트 작성**

`tests/test_json_file_download_daily.py` 새로 생성 (Part A의 테스트와 동일하되 import만 다름):

```python
import unittest

import json_file_download_daily as jfd


class TestFormatOrd(unittest.TestCase):
    def test_pads_single_digit(self):
        self.assertEqual(jfd.format_ord("0"), "00")

    def test_keeps_two_digits(self):
        self.assertEqual(jfd.format_ord("12"), "12")

    def test_missing_value_defaults_to_00(self):
        self.assertEqual(jfd.format_ord(None), "00")
        self.assertEqual(jfd.format_ord(""), "00")


class TestFileStem(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(jfd.file_stem("과업지시서.hwp", "fallback"), "과업지시서")

    def test_no_extension_returns_name(self):
        self.assertEqual(jfd.file_stem("과업지시서", "fallback"), "과업지시서")

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

    def test_std_notice_without_filename_uses_bid_no_as_stem(self):
        metadata = self.base_metadata(fileKind="표준공고서", fileName="")
        key = jfd.file_s3_key("raw/downloads", metadata, "application/pdf", "http://x/std.pdf")
        self.assertEqual(
            key,
            "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/20260700001_표준공고서.pdf",
        )

    def test_duplicate_key_gets_numeric_suffix(self):
        used_keys = set()
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        third = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)

        self.assertEqual(first, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부.hwp")
        self.assertEqual(second, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부_2.hwp")
        self.assertEqual(third, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부_3.hwp")

    def test_without_used_keys_no_dedup_applied(self):
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp")
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 8: 테스트 실패 확인**

```bash
.venv/bin/python3 -m unittest discover -s tests -p "test_json_file_download_daily.py" -t . -v
```

Expected: `AttributeError: module 'json_file_download_daily' has no attribute 'format_ord'`로 FAIL

- [ ] **Step 9: `file_s3_key` 및 관련 함수 교체**

`json_file_download_daily.py:118-166`, 현재:
```python
def file_s3_key(prefix: str, metadata: dict[str, Any], content_type: str, file_url: str):
    notice_dt = parse_dt(metadata.get("bidNtceDt")) or datetime.now()
    biz_div = safe_key_part(metadata.get("업무구분"), "미분류")
    bid_no = safe_key_part(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    file_seq = safe_key_part(metadata.get("fileSeq"), "unknown")
    original_name = str(metadata.get("fileName") or "")
    ext = guess_ext(original_name, content_type, file_url)
    filename = safe_key_part(original_name, f"{bid_no}_{file_seq}{ext}")
    if "." not in filename:
        filename = f"{filename}{ext}"

    return (
        f"{prefix}/year={notice_dt:%Y}/month={notice_dt:%m}/day={notice_dt:%d}/"
        f"biz_div={biz_div}/notice_id={bid_no}/{file_seq}_{filename}"
    )


def upload_attachment(s3, bucket: str, session: requests.Session, metadata: dict[str, Any], timeout: int):
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()
    response.raw.decode_content = True

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3.upload_fileobj(response.raw, bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_fileobj(response.raw, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": int(response.headers.get("Content-Length") or 0),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }
```
변경 후:
```python
ORD_DIGITS = re.compile(r"\d+")


def format_ord(value: Any) -> str:
    match = ORD_DIGITS.search(str(value or ""))
    return match.group(0).zfill(2) if match else "00"


def file_stem(file_name: Any, fallback: str) -> str:
    name = str(file_name or "").strip()
    if not name:
        return fallback
    return name.rsplit(".", 1)[0] if "." in name else name


def file_s3_key(
    prefix: str,
    metadata: dict[str, Any],
    content_type: str,
    file_url: str,
    used_keys: set[str] | None = None,
):
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


def upload_attachment(
    s3,
    bucket: str,
    session: requests.Session,
    metadata: dict[str, Any],
    timeout: int,
    used_keys: set[str],
):
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()
    response.raw.decode_content = True

    content_type = response.headers.get("Content-Type", "")
    key = file_s3_key(FILES_PREFIX, metadata, content_type, file_url, used_keys)
    extra_args = {"ContentType": content_type} if content_type else None
    if extra_args:
        s3.upload_fileobj(response.raw, bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_fileobj(response.raw, bucket, key)

    return {
        "downloadStatus": "success",
        "downloadPath": f"s3://{bucket}/{key}",
        "downloadSize": int(response.headers.get("Content-Length") or 0),
        "contentType": content_type,
        "downloadError": "",
        "s3Bucket": bucket,
        "s3Key": key,
    }
```

- [ ] **Step 10: `run()` 호출부 수정**

`json_file_download_daily.py:198-203`, 현재:
```python
    success = failed = skipped = 0
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                result = upload_attachment(s3, args.bucket, session, file_meta, args.timeout)
```
변경 후:
```python
    success = failed = skipped = 0
    used_keys: set[str] = set()
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                result = upload_attachment(s3, args.bucket, session, file_meta, args.timeout, used_keys)
```

- [ ] **Step 11: 테스트 통과 확인**

```bash
.venv/bin/python3 -m unittest discover -s tests -p "test_json_file_download_daily.py" -t . -v
```

Expected: 9개 테스트 모두 PASS

- [ ] **Step 12: 구문/임포트 확인**

```bash
.venv/bin/python3 -c "import json_file_download_daily"
```

Expected: 에러 없이 종료

- [ ] **Step 13: 커밋**

```bash
git add json_file_download_backfill.py json_file_download_daily.py tests/test_json_file_download_backfill.py tests/test_json_file_download_daily.py
git commit -m "Refactor : 첨부파일 다운로드 S3 키 구조 변경 (bidNtceNo+ord, stem+kind 파일명)"
```

---

## Task 5: README.md 전면 개정

현재 `README.md`는 구버전 로컬 파이프라인(`bid_pipeline.py`, `raw_json.py`,
`BASE_DIR=/Users/oloqlq/Desktop/bidding`) 기준이라 실제 코드와 맞지 않는다.
`feat/raw-json-ingestion` 파트 전체를 다루도록 전면 교체한다.

**Files:**
- Modify: `README.md` (전체 교체)

**Interfaces:** 없음 (문서 전용 태스크)

- [ ] **Step 1: README.md 전체 교체**

`README.md` 전체 내용을 다음으로 교체:

```markdown
# 나라장터 입찰공고 수집 파이프라인 (raw-json-ingestion)

조달청 나라장터 OpenAPI(`BidPublicInfoService`)에서 TOP10 발주기관의 입찰공고를 수집해
S3에 raw/curated JSON으로 저장하고, curated JSON이 가리키는 첨부문서(HWP/PDF)를 실제로
내려받아 S3에 저장하는 배치 파이프라인이다.

```text
raw_json_daily.py / raw_json_backfill.py  (API 수집)
        │
        ▼  schema.py: to_curated() — 113필드 → 39필드
   raw/raw, raw/curated (S3)
        │
        ▼
json_file_download_daily.py / json_file_download_backfill.py  (첨부 다운로드)
        │
        ▼
   raw/downloads (S3)
```

## 스크립트 구성

| 스크립트 | 역할 | 조회 방식 |
|---|---|---|
| `raw_json_daily.py` | 최근 N분 준실시간 수집 | `--minutes`(기본 5) |
| `raw_json_backfill.py` | 지정 기간 백필 수집 | `--start`/`--end` (YYYY-MM-DD) |
| `json_file_download_daily.py` | 최근 N분 curated의 첨부 다운로드 | `--minutes`(기본 5) |
| `json_file_download_backfill.py` | 지정 기간 curated의 첨부 다운로드 | `--start`/`--end` |
| `schema.py` | 원본 113필드 → curated 39필드 변환 (`to_curated`) | — |
| `institutions.py` | 조회 대상 TOP10 기관 목록 (`TOP10_INSTITUTIONS`) | — |

두 수집 스크립트는 나라장터검색조건 오퍼레이션(업무구분별 `getBidPblancListInfo{Cnstwk,Servc,Frgcpt,Thng}PPSSrch`)을
`TOP10_INSTITUTIONS`에 있는 기관명으로 하나씩 조회한 뒤, 응답의 `ntceInsttNm`이 조회 기관명과
완전히 일치하고(`ntceInsttNm` 파라미터는 부분일치이므로) 입찰마감(`bidClseDt`)이 지나지 않은
공고만 남긴다.

## S3 구조

버킷: `s3://bidmate/` (환경변수 `S3_BUCKET`으로 오버라이드 가능, 기본값 `bidmate`)

```text
s3://bidmate/raw/raw/{backfill,daily}/          # API 원본 그대로의 JSON (113필드)
s3://bidmate/raw/curated/{backfill,daily}/      # schema.py가 변환한 curated JSON (39필드)
s3://bidmate/raw/downloads/{backfill,daily}/    # curated의 attachments가 가리키는 실제 첨부파일(HWP/PDF)
```

### `backfill`/`daily` 하위 폴더

`raw/raw`, `raw/curated`, `raw/downloads` 세 폴더 모두 바로 아래에 `backfill/`과 `daily/`가
있고, 그 아래로 `year=Y/month=M/day=D/...` 파티션이 이어진다. 어느 스크립트가 쓰고 읽는지는
아래 표와 같이 항상 짝을 이룬다.

| 하위 폴더 | 쓰는 스크립트 (raw/curated) | 쓰는 스크립트 (downloads) |
|---|---|---|
| `daily/` | `raw_json_daily.py` | `json_file_download_daily.py` (같은 `daily/` curated를 읽음) |
| `backfill/` | `raw_json_backfill.py` | `json_file_download_backfill.py` (같은 `backfill/` curated를 읽음) |

### raw/raw, raw/curated 저장 단위

daily와 backfill의 저장 단위가 다르다.

- **daily** (`raw_json_daily.py`): 공고 1건 = JSON 1개
  ```text
  raw/raw/daily/year=2026/month=07/day=04/biz_div=servc/20260700001-00.json
  raw/curated/daily/year=2026/month=07/day=04/biz_div=servc/20260700001-00.json
  ```
- **backfill** (`raw_json_backfill.py`): 하루+업무구분 단위로 묶은 배열 JSON 1개
  ```text
  raw/raw/backfill/year=2026/month=07/day=04/biz_div=servc.json       # 그 날 servc 카테고리 원본 레코드 배열
  raw/curated/backfill/year=2026/month=07/day=04/biz_div=servc.json   # 그 날 servc 카테고리 curated 레코드 배열
  ```

다운로드 단계(`json_file_download_*.py`)는 curated JSON을 읽을 때 단건 dict와 배열을 모두
지원하므로, daily 산출물(단건)과 backfill 산출물(배열)을 동일한 코드로 처리한다.

### raw/downloads 저장 구조

```text
raw/downloads/backfill/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/
├── 과업지시서_공고첨부.hwp
└── 20260700001_표준공고서.pdf
```

(`daily/` 아래도 동일한 구조. `daily/`는 `json_file_download_daily.py`가, `backfill/`은
`json_file_download_backfill.py`가 쓴다.)

- 폴더: `{daily|backfill}/year=Y/month=M/day=D/biz_div={cat}/bidNtceNo={공고번호}_ord={공고순번 2자리 제로패딩}`
  - `biz_div`는 metadata의 `업무구분` 값(`servc`/`cnstwk`/`frgcpt`/`thng`)을 그대로 사용한다.
- 파일명: `{stem}_{kind}{확장자}`
  - `stem`: 원본 파일명에서 확장자를 뗀 이름. 원본 파일명이 없는 첨부(표준공고서 등)는
    공고번호(`bidNtceNo`)를 stem으로 사용한다.
  - `kind`: `공고첨부` 또는 `표준공고서` (`schema.py`의 `_attachments()`가 부여)
  - 확장자는 원본 파일명 → HTTP 응답 Content-Type → URL 순으로 추정한다 (`guess_ext`).
- 같은 공고 안에서 `stem`+`kind`+확장자까지 완전히 동일한 첨부가 여러 개 있으면
  `_2`, `_3` ... 접미사를 붙여 덮어쓰기를 막는다.
- 다운로드 메타데이터(추출 시각, 원본 curated JSON 경로, 다운로드 성공/실패 등)는
  `raw/downloads/{daily|backfill}/_metadata/`에 실행 단위로 별도 저장된다.

## 실행 방법

환경변수:

```bash
export G2B_SERVICE_KEY="<data.go.kr 디코딩 서비스키>"
export S3_BUCKET="bidmate"   # 생략 시 기본값 bidmate
```

```bash
# 준실시간 수집 (최근 5분)
python3 raw_json_daily.py

# 백필 수집 (기간 지정)
python3 raw_json_backfill.py --start 2026-06-01 --end 2026-06-30

# 최근 5분 curated의 첨부 다운로드
python3 json_file_download_daily.py

# 기간 지정 curated의 첨부 다운로드
python3 json_file_download_backfill.py --start 2026-06-01 --end 2026-06-30
```

## 필드 명세

`raw/curated`의 39개 필드 정의와 원본 113필드 처분 근거는 [FIELD_DICTIONARY.md](FIELD_DICTIONARY.md) 참고.
`schema.py`의 `FIELD_MAP`을 수정하면 `FIELD_DICTIONARY.md`도 함께 갱신해야 한다.
```

- [ ] **Step 2: 코드와 대조 검토**

다음을 다시 열어 README에 적은 경로/포맷 문자열이 실제 코드와 일치하는지 눈으로 대조한다:
`raw_json_backfill.py`(Task 3 반영 후의 `s3_day_json_key`), `raw_json_daily.py`, `json_file_download_backfill.py`(Task 4 반영 후의 `file_s3_key`), `schema.py`의 `_attachments()`.

- [ ] **Step 3: 커밋**

```bash
git add README.md
git commit -m "Docs : README.md 파이프라인 및 S3 구조 문서 갱신"
```

---

## Self-Review 체크리스트 (참고용, 이미 반영됨)

- 스펙의 4개 변경 항목(버킷명, backfill day-bundling, downloads 키 구조, README) 모두 태스크로 커버됨
- 플레이스홀더 없음 — 모든 스텝에 실제 코드/명령어 포함
- 타입 일관성 — `file_s3_key`/`upload_attachment`의 `used_keys` 파라미터가 Task 4의 Part A/B에서 동일한 시그니처로 정의됨
