# 나라장터 입찰공고 수집 파이프라인 (feat/raw-json-ingestion)

조달청 나라장터 OpenAPI(`BidPublicInfoService`)에서 TOP10 발주기관의 입찰공고를 수집해
S3에 raw/curated JSON으로 적재하고, 공고 첨부문서(HWP/PDF)를 실제로 내려받아 S3에 저장하는
**배치 수집 파이프라인**이다.

## 1. 이 브랜치의 역할과 목표

이 저장소가 최종적으로 지향하는 것은 **입찰공고 기반 조달 인텔리전스 시스템**이다:
신규 공고가 들어오면 과거 수주 실적·제안서와 매칭해 Go/No-Go 판단을 돕고,
제안서 초안과 리스크 코멘트를 생성한다. 그 전체 흐름에서 이 브랜치는 **가장 앞단**,
즉 VectorDB(OpenSearch) 구축으로 이어지는 데이터 파이프라인의 초입을 담당한다.

```text
[이 브랜치]                                          [후속 브랜치/단계]
공고 JSON 수집 ──▶ 첨부파일 적재 ──▶ HWP→PDF 변환 ──▶ 파싱·청킹·임베딩 ──▶ OpenSearch 인덱싱
(raw/curated)      (downloads)      (feat/hwp-to-pdf)  (feat/pdf-parsing-    (하이브리드 검색 +
                                                        embedding)            RAG 서빙)
```

책임 범위는 명확히 두 가지다.

1. **공고 메타데이터 수집** — 조달청 API 응답(원본 113필드)을 S3에 그대로 보존(`raw`)하고,
   후속 단계가 쓰기 좋은 39필드로 정제(`curated`)해 이중 저장한다.
2. **첨부문서 실물 적재** — curated가 가리키는 첨부(과업지시서, 제안요청서, 표준공고서 등)를
   다운로드해 S3에 공고 단위로 정리해 넣는다. 이 파일들이 이후 텍스트 추출·임베딩의 원료가 된다.

> **Bronze 보존 원칙**: `raw`는 재처리를 위한 원본이므로 절대 임의 삭제하지 않는다.
> 스키마가 바뀌어도 raw에서 다시 curated를 만들어낼 수 있어야 한다.

## 2. 폴더 구조와 아키텍처

### 폴더 구조

```text
bidding-agent/
├── institutions.py                    # 조회 대상 TOP10 기관 목록 (공용 상수)
├── schema.py                          # 원본 113필드 → curated 39필드 변환 (공용 순수 로직)
│
├── raw_json_daily.py                  # [daily]    최근 N분 공고 수집 (동기)
├── json_file_download_daily.py        # [daily]    최근 N분 curated의 첨부 다운로드 (동기)
├── raw_json_backfill.py               # [backfill] 기간 지정 공고 수집 (동기, 레거시)
├── json_file_download_backfill.py     # [backfill] 기간 지정 첨부 다운로드 (동기, 레거시)
│
├── backfill_async/                    # [backfill] 비동기 버전 (httpx + aioboto3) — 현행 권장
│   ├── raw_json_backfill.py           #   기간 지정 공고 수집: 날짜 단위 순회 + 동시 조회
│   ├── json_file_download_backfill.py #   첨부 파일 동시 다운로드
│   └── tests/                         #   pytest-asyncio 기반 테스트
│
├── tests/                             # 동기 스크립트 테스트 (unittest 스타일)
├── docs/superpowers/                  # 설계 스펙·구현 계획 문서
├── FIELD_DICTIONARY.md                # curated 39필드 명세 (schema.py와 1:1 동기화)
├── requirement.txt                    # requests, boto3, httpx, aioboto3, pytest-asyncio 등
└── pytest.ini                         # asyncio_mode = auto
```

### 코드 의존성

수집(A)과 다운로드(B)는 S3를 경계로 느슨하게 결합되어 있고, 공용 로직은 두 모듈뿐이다.

```text
                 institutions.py ─── schema.py
                (TOP10 기관 목록)   (to_curated / parse_dt / _attachments)
                        │               │
          ┌─────────────┴───────────────┴──────────────┐
          ▼                                            ▼
  A. 공고 수집 스크립트                        B. 첨부 다운로드 스크립트
  raw_json_{daily,backfill}.py                json_file_download_{daily,backfill}.py
  backfill_async/raw_json_backfill.py         backfill_async/json_file_download_backfill.py
          │                                            ▲
          ▼                                            │
   s3://…/raw/raw/        s3://…/raw/curated/ ─────────┘        s3://…/raw/downloads/
   (원본 113필드)          (정제 39필드 + attachments 배열)       (HWP/PDF 실물 + _metadata)
```

**데이터 계약**: 다운로드 단계(B)는 raw가 아니라 **curated를 소비**한다.
첨부 URL 파싱 로직(`ntceSpecDocUrl1~10` + `stdNtceDocUrl` → `attachments` 배열)은
`schema.py._attachments()` **한 곳에만** 존재한다. B는 curated의 `attachments`를 읽기만 한다.

### S3 레이아웃

버킷: `s3://bidmate/` (환경변수 `S3_BUCKET_NAME`으로 오버라이드, 기본값 `bidmate`)

```text
s3://bidmate/raw/raw/{backfill,daily}/        # API 원본 그대로 (113필드)
s3://bidmate/raw/curated/{backfill,daily}/    # schema.py 변환 결과 (39필드)
s3://bidmate/raw/downloads/{backfill,daily}/  # 첨부파일 실물 + _metadata/ (다운로드 manifest)
```

세 prefix 모두 `year=YYYY/month=MM/day=DD` Hive 파티션을 따르며, `backfill/`과 `daily/`는
쓰는 스크립트와 읽는 스크립트가 항상 짝을 이룬다 (backfill 수집분은 backfill 다운로더만 소비).

저장 단위는 파이프라인별로 다르다:

| | daily | backfill |
|---|---|---|
| raw/curated 저장 단위 | 공고 1건 = JSON 1개 (`{bidNtceNo}-{ord}.json`) | 하루+업무구분 = 배열 JSON 1개 (`biz_div={cat}.json`) |
| 다운로드 폴더 | `bidNtceNo={공고번호}_ord={2자리}` 공고 단위 폴더 | 동일 |
| 파일명 규칙 | `{원본stem}_{공고첨부\|표준공고서}{확장자}`, 중복 시 `_2`, `_3` 접미사 | 동일 |

다운로드 단계는 curated를 읽을 때 단건 dict와 배열을 모두 지원하므로
daily 산출물(단건)과 backfill 산출물(배열)을 같은 코드로 처리한다.

## 3. backfill 파이프라인 vs daily 파이프라인

같은 API, 같은 스키마를 쓰지만 **목적이 달라 설계가 갈라진다.**

| 구분 | daily (준실시간) | backfill (과거 이력) |
|---|---|---|
| 목적 | "지금 입찰 가능한 공고"를 빠르게 반영 | 과거 기간의 공고 이력을 통째로 적재 |
| 조회 창 | 최근 N분 (`--minutes`, 기본 5) | `--start`~`--end` 날짜 범위 |
| 실행 주체 | 스케줄러(Airflow 전환 예정)가 5분마다 | 사람이 필요할 때 수동 실행 |
| **마감 공고 필터** | **적용** (`is_open`: 마감 지난 공고 제외) | **미적용** — 이력 수집이 목적이므로 마감된 공고도 전부 보존 |
| 시간창 필터 | `in_window`: 게시시각이 조회창 안인 것만 | 없음 (날짜 범위 자체가 조건) |
| 저장 단위 | 공고 1건 = 1 JSON | 하루+업무구분 = 배열 1 JSON |
| 호출량 | 회당 40콜 내외로 미미 | 기간에 비례해 커짐 → **호출 예산 관리 필요** |

> 이 차이는 실제 버그로 검증됐다: 초기 비동기 backfill이 daily용 `is_open` 필터를
> 물려받은 탓에, 2026년 1월 데이터를 백필했더니 "7월 기준 아직 안 마감된" 극소수
> 공고만 남는 문제가 있었고, backfill에서는 필터를 제거하는 것으로 확정했다.

### backfill: 동기(레거시) vs 비동기(현행)

backfill은 두 구현이 공존한다. **신규 백필 작업은 `backfill_async/`를 사용한다.**

| | 동기 (루트의 `raw_json_backfill.py`) | 비동기 (`backfill_async/raw_json_backfill.py`) |
|---|---|---|
| HTTP / S3 | `requests` / `boto3` | `httpx.AsyncClient` / `aioboto3` |
| 조회 방식 | 기관×업무구분×페이지 순차 처리 | `asyncio.Semaphore`(기본 8, `--concurrency`)로 동시 조회 |
| 범위 처리 | `--start`~`--end`를 **한 번의 API 조건**으로 조회 → API의 조회기간 제한(약 1개월)에 걸리면 조용히 0건 (알려진 이슈) | **날짜 단위로 쪼개 순회** → 범위 제한에 원천적으로 안 걸림 |
| 페이지네이션 | totalCount 확인 후 순차 | 2단계: ① 40개 조합의 1페이지 동시 조회로 totalCount 확보 → ② 남은 페이지 전부를 하나의 동시 배치로 조회 |
| 실패 처리 | 한 페이지 재시도 소진 시 전체 중단 | **부분 실패 격리**: 성공분은 무조건 S3 저장, 실패 조합만 로그 + exit code 1 |
| API 에러 응답 감지 | 없음 (0건으로 오인) | `G2BApiError`: 에러 구조 응답을 즉시 실패 처리(재시도 낭비 없음) |
| 호출 예산 | 없음 | 실행 단위 카운터, 95,000콜 도달 시 날짜 경계에서 조기 종료(exit 0) + 재개 안내 로그 |
| 마감 공고 필터 | `is_open` 적용 (미수정 레거시) | 제거됨 |

비동기 버전의 하루 처리 흐름:

```text
collect_range (날짜 루프)
  └─ process_day (하루치)
       ├─ fetch_first_pages   : 기관 10 × 업무구분 4 = 40조합의 1페이지 동시 조회 (semaphore=8)
       ├─ fetch_remaining_pages: totalCount로 계산된 나머지 페이지 전부 동시 조회
       ├─ 필터링              : is_exact_institution (기관명 완전일치만)
       └─ 성공/실패 분리       : 실패 조합은 (op, 기관, 페이지, 예외)로 수집
  └─ S3 저장 (raw + curated) → 호출 예산 체크 → 다음 날짜
```

## 4. 조달청 API 노하우 (운영하며 확인된 사실)

코드를 수정하거나 디버깅할 때 반드시 알아야 하는 API 특성들이다.

- **서비스 URL에 `/ad/`가 들어간다**: `http://apis.data.go.kr/1230000/ad/BidPublicInfoService`.
  블로그 예제 대부분은 `/ad/` 없는 구버전이라 그대로 쓰면 응답이 안 온다.
- **serviceKey는 Decoding 키**를 쓴다. requests/httpx의 params 딕셔너리가 자동 URL 인코딩하므로
  Encoding 키를 넣으면 이중 인코딩으로 인증 에러가 난다.
- **조회기간 제한 ≈ 1개월**: 나라장터검색조건 오퍼레이션(`…PPSSrch`)은 `inqryBgnDt`~`inqryEndDt`가
  31일이면 정상, 35일 이상이면 `resultCode=07 입력범위값 초과 에러`를 반환한다 (실측).
- **에러 응답은 정상 응답과 JSON 구조가 다르다**:
  ```json
  정상: {"response": {"header": {...}, "body": {"items": [...], "totalCount": N}}}
  에러: {"nkoneps.com.response.ResponseError": {"header": {"resultCode": "07", ...}}}
  ```
  HTTP 상태는 둘 다 200이라, `"response"` 키 존재를 확인하지 않으면 에러가 조용히
  "0건 조회"로 둔갑한다. 비동기 버전은 `G2BApiError`로 감지한다.
- **`ntceInsttNm` 파라미터는 부분일치**다. "조달청"으로 조회하면 "조달청 서울지방조달청"도
  섞여 오므로, 응답에서 완전일치(`is_exact_institution`)로 다시 거른다.
- **날짜 조건은 게시일 기준**이다. 마감일 기준 조회는 불가 → 받아온 뒤 코드에서 판단한다.
- **`items`는 세 가지 형태**로 온다: 다건 리스트 / 단건 `{"item": {...}}` / 빈 문자열. 방어 파싱 필수.
- **기본키는 `(bidNtceNo, bidNtceOrd)` 복합키**다. `bidNtceNo` 단독으로는 유일하지 않다.
- **일일 호출 한도 10만** (운영계정). 비동기 backfill의 호출 예산(95,000)은 이 한도의 안전 마진이다.
  단, 카운터는 실행 1회 기준이므로 같은 날 여러 번 실행하면 합산 한도를 넘을 수 있다.

## 5. 설치와 실행

### 환경 준비

```bash
# 프로젝트 전용 가상환경 (공용 conda/시스템 파이썬에 직접 설치하지 말 것)
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirement.txt
```

`.env` 파일 또는 환경변수:

```bash
G2B_SERVICE_KEY=<data.go.kr 디코딩 서비스키>   # 필수
S3_BUCKET_NAME=bidmate                        # 생략 시 기본값 bidmate
AWS_ACCESS_KEY_ID=...                         # IAM 역할 미사용 시
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=...
```

### 실행

```bash
# ── daily (준실시간, 최근 5분) ──
python3 raw_json_daily.py
python3 json_file_download_daily.py

# ── backfill (비동기, 권장) ──
python3 backfill_async/raw_json_backfill.py --start 2026-01-01 --end 2026-06-30
python3 backfill_async/json_file_download_backfill.py --start 2026-01-01 --end 2026-06-30

# 동시성 조절 (기본 8; API 부하·차단 위험과 속도의 트레이드오프)
python3 backfill_async/raw_json_backfill.py --start 2026-06-01 --concurrency 5
```

비동기 backfill의 종료 코드: 정상 완료 `0` / 호출 예산 도달로 조기 종료 `0`(재개 안내 로그 출력) /
일부 조합 실패 `1`(실패 조합이 로그에 남으므로 해당 범위만 재실행).

### 테스트

```bash
.venv/bin/python3 -m pytest tests/ backfill_async/   # 동기 + 비동기 전체
```

비동기 테스트는 `pytest-asyncio`(`pytest.ini`의 `asyncio_mode = auto`) 기반이며,
httpx/aioboto3를 가짜 클라이언트로 대체해 네트워크 없이 돈다. 재시도, 2단계 조회,
부분 실패 격리, 호출 예산 조기 종료, API 에러 응답 감지가 모두 테스트로 고정되어 있다.

## 6. 알려진 이슈 / 주의사항

- **동기 backfill(루트 `raw_json_backfill.py`)의 레거시 이슈 2건** — 비동기 버전에는 수정 반영됨:
  1. 1개월 초과 범위 요청 시 API 에러를 감지 못 하고 "0건"으로 정상 종료한다.
  2. daily용 `is_open` 필터가 남아 있어 과거 공고 대부분이 걸러진다.
- `schema.py`의 `FIELD_MAP`을 수정하면 **`FIELD_DICTIONARY.md`도 반드시 함께 갱신**한다.
- 이 폴더는 iCloud Drive 동기화 범위 안에 있다. git 오류가 나면 iCloud 간섭을 의심할 것.
- TOP10 기관 제한은 2026-07-01 멘토 미팅 결정이다(전체 기관 수집은 비현실적 판단).
  대상 변경은 `institutions.py` 한 곳만 수정하면 된다.

## 7. 필드 명세와 설계 문서

- curated 39필드 정의와 원본 113필드 처분 근거: [FIELD_DICTIONARY.md](FIELD_DICTIONARY.md)
- S3 파티션 재설계 스펙: [docs/superpowers/specs/2026-07-04-s3-partition-redesign-design.md](docs/superpowers/specs/2026-07-04-s3-partition-redesign-design.md)
- 비동기 backfill 설계 스펙: [docs/superpowers/specs/2026-07-05-backfill-async-pipeline-design.md](docs/superpowers/specs/2026-07-05-backfill-async-pipeline-design.md)

## 8. 다음 로드맵

- 동기 backfill 레거시 이슈 수정 또는 비동기 버전으로 일원화
- daily 파이프라인의 비동기 전환 검토
- Airflow DAG 전환: collect → download → parse → index 자동화
- OpenSearch 인덱스 설계 + Nori 형태소 분석기 적용
- 하이브리드 검색(BM25 + 벡터) + 비즈니스 룰 re-ranking (후속 단계)
