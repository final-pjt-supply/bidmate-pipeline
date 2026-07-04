# Backfill 비동기 파이프라인 설계

날짜: 2026-07-05
상태: 승인 대기

## 배경 및 목적

기존 `raw_json_backfill.py`, `json_file_download_backfill.py`는 `requests`/`boto3` 기반 완전 동기 스크립트로,
기관×업무구분×페이지(공고 조회) 및 첨부파일 단위(다운로드)를 하나씩 순차 처리한다. 두 스크립트 모두
네트워크 I/O 대기가 병목이라, `asyncio` 기반 동시 처리로 전환해 백필 소요 시간을 단축한다.

기존 동기 스크립트는 그대로 두고, 별도 폴더에 비동기 전용 버전을 병행 구축한다.

## 범위

- 대상: `raw_json_backfill.py`, `json_file_download_backfill.py`의 비동기 버전만 신규 작성
- 기존 동기 스크립트(`bidding-agent/raw_json_backfill.py`, `bidding-agent/json_file_download_backfill.py`)와
  기존 테스트(`bidding-agent/tests/`)는 수정하지 않는다
- `institutions.py`, `schema.py`는 I/O 없는 순수 로직이라 사본을 만들지 않고 그대로 import해 재사용한다
- daily 파이프라인(`raw_json_daily.py`, `json_file_download_daily.py`)의 비동기 전환은 이번 범위 밖

## 폴더 구조

```
bidding-agent/
  backfill_async/
    raw_json_backfill.py            # 비동기 버전 (신규)
    json_file_download_backfill.py  # 비동기 버전 (신규)
    tests/
      test_raw_json_backfill.py
      test_json_file_download_backfill.py
  raw_json_backfill.py               # 기존 동기 버전, 무변경
  json_file_download_backfill.py     # 기존 동기 버전, 무변경
  institutions.py / schema.py        # 무변경, backfill_async에서 그대로 import
  requirement.txt                    # httpx, aioboto3, pytest-asyncio 추가
```

## 의존성

- HTTP 클라이언트: `requests` → `httpx.AsyncClient` (API 조회 + 첨부파일 다운로드)
- S3 클라이언트: `boto3` → `aioboto3` (S3 이벤트 루프 블로킹 방지)
- 테스트: `pytest-asyncio` 추가
- 두 스크립트 간 공통 로직(재시도, 세마포어 설정, S3 클라이언트 생성)은 공용 모듈로 분리하지 않고
  **각 파일에 중복 작성**한다 (파일 하나만 열어도 전체 로직이 보이도록)

## `raw_json_backfill.py` (비동기) 설계

### 동시성 제어

- `asyncio.Semaphore(concurrency)` 로 동시 요청 수 제한, 기본값 8, `--concurrency` 인자로 조정 가능

### 조회 흐름 (날짜 단위 순회)

전체 `--start ~ --end` 범위를 한 번에 조회하지 않고 **하루 단위로 순회**한다 (호출 카운터 조기 종료를 날짜
경계에서 판단하기 위함). 각 날짜에 대해:

1. **1단계 — 1페이지 동시 조회**: TOP10 기관 × 4개 업무구분(operation) 조합(최대 40개) 각각의 1페이지를
   세마포어로 동시 조회해 `totalCount` 확보
2. **2단계 — 나머지 페이지 동시 조회**: 1단계에서 계산한 남은 페이지 번호를 모두 모아 하나의 태스크
   리스트로 만들고, 같은 세마포어로 동시 조회 (기관/업무구분 구분 없이 평평하게 묶음)
3. 필터링(`is_exact_institution`, `is_open`, `group_by_day`)은 순수 함수라 기존 로직 그대로 재사용
4. S3 저장은 `aioboto3`의 `put_object`로 교체

재시도(`MAX_RETRY`, 지수 백오프)는 기존 값을 유지하되 `time.sleep` → `await asyncio.sleep`으로 전환한다.

### 부분 실패 처리

모든 `asyncio.gather`는 `return_exceptions=True`로 실행한다.

- 성공한 결과는 무조건 S3에 저장한다 (일부 실패가 이미 성공한 나머지 결과의 저장을 막지 않음)
- 실패한 (기관, 업무구분, 페이지) 조합은 로그로 남긴다
- 실행 중 실패가 하나라도 있었으면 처리는 계속하되, 스크립트는 **exit code 1**로 종료해 운영자가 인지하게 한다

### API 호출 카운터 및 조기 종료

- 실행 단위의 단순 카운터로 이번 실행에서 발생한 조달청 API 호출 수를 누적 집계한다
  (일자를 넘어 지속되는 상태는 관리하지 않음 — 아래 "알려진 제약" 참조)
- 하루치 처리가 끝날 때마다 누적 호출 수를 확인해 **95,000회**(안전 마진, 상수로 조정 가능) 이상이면
  다음 날짜로 넘어가지 않고 그 자리에서 종료한다
- 조기 종료 시 "OO일까지 처리 완료, 호출 한도 근접으로 조기 종료. 이후 범위는 `--start OO+1일`로
  재실행하세요" 형태의 로그를 남긴다
- 이 조기 종료는 실패가 아닌 의도된 동작이므로 **exit code 0**으로 종료한다 (실패 시 exit code 1과 구분)
- 상태 파일은 두지 않는다 — 다음 실행 시 시작일은 운영자가 로그를 보고 `--start`로 직접 지정한다 (자동
  이어받기 없음)

### 알려진 제약

호출 카운터는 "이번 실행 1회" 기준으로만 집계하는 단순 카운터다. 같은 날 스크립트를 여러 번 실행하면
각 실행이 독립적으로 95,000부터 다시 세기 때문에, 하루에 여러 번 돌리면 실제 조달청 일일 한도(10만
호출)를 합산 기준으로 넘을 수 있다. 이는 "단순 실행 단위 카운터"를 선택한 데 따른 의도된 트레이드오프다.

## `json_file_download_backfill.py` (비동기) 설계

### 동시성 제어

- `raw_json_backfill.py`와 동일하게 `asyncio.Semaphore(concurrency)` 사용, 기본값 8, `--concurrency` 인자

### 처리 흐름

1. `iter_curated_range`: `aioboto3` 페이지네이터로 날짜별 curated JSON을 조회 (세마포어로 동시성 제한)
2. `build_file_metadata`: 순수 함수, 그대로 재사용
3. `upload_attachment`: `httpx.AsyncClient.stream()`으로 첨부파일을 다운로드하면서 `aioboto3`로 S3 업로드.
   전체 첨부파일 메타데이터 리스트를 세마포어로 묶어 `asyncio.gather(..., return_exceptions=True)`로 동시 실행
4. `put_manifest`: `aioboto3`로 교체

### 실패 처리

파일 단위로 개별 실패를 격리한다 (기존 동기 버전과 동일한 동작): 파일 A 다운로드 실패가 파일 B, C의
처리에 영향을 주지 않으며, 각 파일의 성공/실패 상태가 manifest에 개별 기록된다.

## 테스트 설계

`backfill_async/tests/`에 `pytest-asyncio` 기반으로 작성한다. `httpx.AsyncClient`/`aioboto3` 클라이언트는
mock으로 대체한다.

- **`test_raw_json_backfill.py`**
  - 1단계(1페이지 동시조회) → totalCount 기반 2단계 페이지 계산 로직
  - 재시도(백오프) 동작 — mock이 N번 실패 후 성공하는 시나리오
  - 부분 실패 시나리오 — 일부 조합 성공 + 일부 실패 시 성공분만 S3에 저장되는지, 실패 목록이 올바르게
    로그되는지, exit code가 1인지 검증
  - 호출 카운터 조기 종료 시나리오 — 누적 호출 수가 임계값을 넘었을 때 다음 날짜로 진행하지 않고
    exit code 0으로 종료하는지 검증
  - 순수 함수(`is_exact_institution`, `is_open`, `group_by_day`)는 로직 변경이 없으므로 기존 테스트를
    그대로 이식
- **`test_json_file_download_backfill.py`**
  - 첨부파일 비동기 다운로드 + S3 업로드 mock 검증
  - 파일 단위 실패 격리(한 파일 실패해도 나머지 계속 처리) 검증
  - manifest 저장 검증

## 스펙 외 논의 기록 (Q&A 요약)

- HTTP 클라이언트: `httpx` 채택 (requests와 API 유사, sync/async 동시 지원)
- S3 클라이언트: `aioboto3` 채택 (S3 호출까지 완전 async 통일)
- 공통 로직 분리 여부: 파일별 중복 작성 채택 (공유 모듈 없음)
- 동시성 제한값: 8 (`--concurrency`로 조정 가능)
- 조회 단위: 기관당 1페이지 우선 병렬 확인 후 나머지 페이지 일괄 병렬 조회
- 테스트 범위: 이번 설계에 포함 (pytest-asyncio)
- API 호출 카운터: 실행 단위 단순 카운터 + 95,000회 도달 시 조기 종료, 상태 파일 없이 로그로만 안내
