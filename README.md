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
   후속 단계가 쓰기 좋은 47필드로 정제(`curated`)해 이중 저장한다.
2. **첨부문서 실물 적재** — curated가 가리키는 첨부(과업지시서, 제안요청서, 표준공고서 등)를
   다운로드해 S3에 공고 단위로 정리해 넣는다. 이 파일들이 이후 텍스트 추출·임베딩의 원료가 된다.

> **Bronze 보존 원칙**: `raw`는 재처리를 위한 원본이므로 절대 임의 삭제하지 않는다.
> 스키마가 바뀌어도 raw에서 다시 curated를 만들어낼 수 있어야 한다.

## 2. 폴더 구조와 아키텍처

### 폴더 구조

```text
bidding-agent/
├── institutions.py                    # 조회 대상 TOP10 기관 목록 (공용 상수)
├── schema.py                          # 원본 113필드 → curated 47필드 변환 (공용 순수 로직)
│
├── raw_json_daily.py                  # [daily]    최근 N분 공고 수집 (동기)
├── json_file_download_daily.py        # [daily]    최근 N분 curated의 첨부 다운로드 (동기)
├── raw_json_backfill.py               # [backfill] 기간 지정 공고 수집 (비동기: httpx + aioboto3)
├── json_file_download_backfill.py     # [backfill] 기간 지정 첨부 다운로드 (비동기)
│
├── .github/                           # Gemini PR 자동 리뷰 워크플로우
├── FIELD_DICTIONARY.md                # curated 47필드 명세 (schema.py와 1:1 동기화)
└── requirement.txt                    # requests, boto3, httpx, aioboto3 등
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
          │                                            ▲
          ▼                                            │
   s3://…/raw/raw/        s3://…/raw/curated/ ─────────┘        s3://…/raw/downloads/
   (원본 113필드)          (정제 47필드 + attachments 배열)       (HWP/PDF 실물 + _metadata)
```

**데이터 계약**: 다운로드 단계(B)는 raw가 아니라 **curated를 소비**한다.
첨부 URL 파싱 로직(`ntceSpecDocUrl1~10` + `stdNtceDocUrl` → `attachments` 배열)은
`schema.py._attachments()` **한 곳에만** 존재한다. B는 curated의 `attachments`를 읽기만 한다.

### S3 레이아웃

버킷: `s3://bidmate/` (환경변수 `S3_BUCKET_NAME`으로 오버라이드, 기본값 `bidmate`)

```text
s3://bidmate/raw/raw/{backfill,daily}/        # API 원본 그대로 (113필드)
s3://bidmate/raw/curated/{backfill,daily}/    # schema.py 변환 결과 (47필드)
s3://bidmate/raw/downloads/{backfill,daily}/  # 첨부파일 실물 + _metadata/ (다운로드 manifest)
```

`backfill/`은 `year=YYYY/month=MM/day=DD`, `daily/`는
`year=YYYY/month=MM/day=DD/hour=HH` Hive 파티션을 따른다. `backfill/`과 `daily/`는
쓰는 스크립트와 읽는 스크립트가 항상 짝을 이룬다 (backfill 수집분은 backfill 다운로더만 소비).

저장 단위와 파일명 규칙은 daily/backfill 모두 공고 단위를 기준으로 맞춘다:

| | daily | backfill |
|---|---|---|
| raw/curated 저장 단위 | 공고 1건 = JSON 1개 (`biz_div={cat}/{bidNtceNo}-{ord}.json`) | 동일 |
| 다운로드 폴더 | `{공고번호}_{차수}` 공고 단위 폴더 | 동일 |
| 파일명 규칙 | `{공고번호}_{차수}_doc{NN}{확장자}` — 원본 파일명 미사용 | 동일 |

첨부 적재 시 두 가지 규칙이 적용된다:

- **확장자 중복 제거**: 같은 이름의 문서가 hwpx/hwp/pdf 여러 확장자로 함께 게시된 경우
  우선순위 **hwpx > hwp > pdf**로 하나만 내려받는다 (pdf는 hwpx/hwp가 없을 때만).
  zip 등 그 외 확장자와 파일명 없는 첨부(표준공고서)는 대상이 아니다.
- **파일명 익명화**: 남은 첨부에 공고 내 순번 `doc01`, `doc02`…를 부여해
  `{공고번호}_{차수 2자리}_doc{NN}{확장자}`로 저장한다 (전부 언더바 연결 snake 형식,
  예: `20260700001_00/20260700001_00_doc01.hwpx`). 원본 파일명·종류(공고첨부/표준공고서)와
  제외 사유는 manifest(`fileName`, `fileKind`, `downloadError`)에서 추적한다.

다운로드 단계는 curated를 읽을 때 단건 dict를 기본으로 처리한다.
기존 배열 JSON도 방어적으로 지원하므로 과거 산출물이 남아 있어도 같은 코드로 처리한다.

## 3. backfill 파이프라인 vs daily 파이프라인

같은 API, 같은 스키마를 쓰지만 **목적이 달라 설계가 갈라진다.**

| 구분 | daily (준실시간) | backfill (과거 이력) |
|---|---|---|
| 목적 | "지금 입찰 가능한 공고"를 빠르게 반영 | 과거 기간의 공고 이력을 통째로 적재 |
| 조회 창 | 최근 N분 (`--minutes`, 기본 5) | `--start`~`--end` 날짜 범위 |
| 실행 주체 | 스케줄러(Airflow 전환 예정)가 5분마다 | 사람이 필요할 때 수동 실행 |
| **마감 공고 필터** | **적용** (`is_open`: 마감 지난 공고 제외) | **미적용** — 이력 수집이 목적이므로 마감된 공고도 전부 보존 |
| 시간창 필터 | `in_window`: 게시시각이 조회창 안인 것만 | 없음 (날짜 범위 자체가 조건) |
| 저장 단위 | 공고 1건 = 1 JSON | 공고 1건 = 1 JSON |
| 호출량 | 회당 40콜 내외로 미미 | 기간에 비례해 커짐 → **호출 예산 관리 필요** |

> 이 차이는 실제 버그로 검증됐다: 초기 비동기 backfill이 daily용 `is_open` 필터를
> 물려받은 탓에, 2026년 1월 데이터를 백필했더니 "7월 기준 아직 안 마감된" 극소수
> 공고만 남는 문제가 있었고, backfill에서는 필터를 제거하는 것으로 확정했다.

### backfill 구현 특성 (비동기)

backfill 스크립트 2개는 `httpx.AsyncClient` + `aioboto3` 기반 비동기 구현이다.

- **동시성 제어**: `asyncio.Semaphore`(기본 8, `--concurrency`)로 동시 요청 수 제한
- **날짜 단위 순회**: `--start`~`--end`를 하루씩 쪼개 조회 → API의 조회기간 제한(약 1개월)에 원천적으로 안 걸림
- **2단계 페이지네이션**: ① 기관 10 × 업무구분 4 = 40개 조합의 1페이지를 동시 조회해 totalCount 확보 → ② 남은 페이지 전부를 하나의 동시 배치로 조회
- **부분 실패 격리**: 성공분은 무조건 S3 저장, 실패 조합만 로그 + exit code 1
- **API 에러 응답 감지**: `G2BApiError` — 에러 구조 응답을 즉시 실패 처리(0건 오인·재시도 낭비 없음)
- **호출 예산**: 실행 단위 카운터, 95,000콜 도달 시 날짜 경계에서 조기 종료(exit 0) + 재개 안내 로그

backfill의 하루 처리 흐름:

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

`.env` 파일 또는 환경변수 (`cp env.example .env` 후 값을 채우면 된다):

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

# ── backfill (비동기, 기간 지정) ──
python3 raw_json_backfill.py --start 2026-01-01 --end 2026-06-30
python3 json_file_download_backfill.py --start 2026-01-01 --end 2026-06-30

# 동시성 조절 (기본 8; API 부하·차단 위험과 속도의 트레이드오프)
python3 raw_json_backfill.py --start 2026-06-01 --concurrency 5
```

backfill의 종료 코드: 정상 완료 `0` / 호출 예산 도달로 조기 종료 `0`(재개 안내 로그 출력) /
일부 조합 실패 `1`(실패 조합이 로그에 남으므로 해당 범위만 재실행).

## 6. 알려진 이슈 / 주의사항

- `schema.py`의 `FIELD_MAP`을 수정하면 **`FIELD_DICTIONARY.md`도 반드시 함께 갱신**한다.
- 이 폴더는 iCloud Drive 동기화 범위 안에 있다. git 오류가 나면 iCloud 간섭을 의심할 것.
- TOP10 기관 제한은 2026-07-01 멘토 미팅 결정이다(전체 기관 수집은 비현실적 판단).
  대상 변경은 `institutions.py` 한 곳만 수정하면 된다.

## 7. 필드 명세

- curated 47필드 정의와 원본 113필드 처분 근거: [FIELD_DICTIONARY.md](FIELD_DICTIONARY.md)

## 8. 설계 결정 기록

별도 문서(`docs/superpowers/`)로 관리하던 설계 스펙·구현 계획의 핵심을 이 절에 통합했다.
"무엇을 하는가"는 위 2~6절에 이미 있으므로, 여기에는 **왜 그렇게 결정했는지**만 남긴다.

### S3 파티션 재설계 (2026-07-04)

- 버킷명 기본값을 `bidding-agent` → `bidmate`로 변경했다 (환경변수 오버라이드 구조는 유지).
- `raw/raw`, `raw/curated`, `raw/downloads` 세 prefix 모두에 `backfill/`·`daily/` 하위 폴더를
  도입했다. 쓰는 스크립트와 읽는 스크립트의 대응 관계(daily 수집분은 daily 다운로더만 소비)를
  경로 구조 자체로 강제하기 위함이다.
- backfill 저장 단위는 한때 "하루+업무구분 배열 JSON 1개"로 단순화했으나, 공고 단위
  재처리와 `raw_s3_key` 추적을 명확하게 하기 위해 다시 "공고 1건 = JSON 1개"로 맞췄다.
  backfill 수집 스크립트도 daily와 같은 `{bidNtceNo}-{ord}.json` 키를 사용한다.
- 첨부파일 키는 `notice_id={번호}/{fileSeq}_{파일명}` → `bidNtceNo={번호}_ord={2자리}/{stem}_{kind}{확장자}`
  구조로 변경했다.
  - `ord`는 `bidNtceOrd`에서 숫자만 추출해 `zfill(2)` (없으면 `00`).
  - `stem`은 원본 파일명에서 확장자를 뗀 부분, 원본 파일명이 없는 첨부(표준공고서 등)는 `bidNtceNo`로 대체.
  - 확장자는 원본 파일명 → HTTP 응답 Content-Type → URL 순으로 추정한다 (`guess_ext`).
  - 완전히 동일한 키가 같은 실행 안에서 재발생하면 `_2`, `_3` 접미사를 붙인다. 실행 전체에 걸친
    `used_keys` 집합으로 추적하며, 실제 Content-Type을 확보한 업로드 시점에 최종 키를 확정한다.
  - (파일명과 공고 폴더명은 아래 "첨부 파일명 익명화" 결정으로 대체됨 — 연월일/biz_div 파티션은 그대로 유효)

### backfill 비동기 파이프라인 (2026-07-05)

- 기존 동기 스크립트와 테스트는 무변경으로 두고, `backfill_async/`에 비동기 버전을 병행 구축했다.
- HTTP 클라이언트는 `httpx`(requests와 API가 유사하고 sync/async 겸용), S3는 `aioboto3`
  (S3 호출까지 완전 async로 통일, 이벤트 루프 블로킹 방지)를 채택했다.
- 두 스크립트 간 공통 로직(재시도, 세마포어, S3 클라이언트 생성)은 공유 모듈로 빼지 않고
  **각 파일에 중복 작성**했다 — 파일 하나만 열어도 전체 로직이 보이도록 한 의도된 선택이다.
- `backfill_async/`를 파이썬 패키지(`__init__.py`)로 만든 이유: 동기 버전과 모듈 파일명이
  같아서(`raw_json_backfill.py`), 패키지 경로 없이 import하면 `sys.modules`에 먼저 캐시된
  쪽이 재사용되어 다른 쪽 테스트가 엉뚱한 모듈을 검증하는 조용한 버그가 생기기 때문이다.
- 호출 예산(95,000)은 **상태 파일 없이 실행 단위 카운터**로만 관리한다. 조기 종료 시 로그에
  안내된 `--start` 날짜로 운영자가 직접 재실행한다(자동 이어받기 없음). 같은 날 여러 번
  실행하면 합산 한도를 보장하지 못하는 것은 단순함을 택한 의도된 트레이드오프다.
- 부분 실패 정책: 모든 `asyncio.gather`는 `return_exceptions=True`로 실행하고, 성공분은
  무조건 S3에 저장한다. 실패가 하나라도 있으면 처리는 계속하되 exit code 1로 종료해
  운영자가 인지하게 한다. 다운로드는 파일 단위로 실패를 격리하고 manifest에 개별 기록한다.

### 첨부 파일명 익명화 + 확장자 중복 제거 (2026-07-06)

- 같은 문서를 hwp와 pdf로 이중 게시하는 공고가 많아, 같은 이름(stem)의 hwpx/hwp/pdf 중
  우선순위(hwpx > hwp > pdf)가 가장 높은 확장자 하나만 적재하도록 했다. 제외분은
  다운로드하지 않되 manifest에 사유를 남긴다.
- 원본 파일명(한글, 특수문자, 길이 편차)을 S3 키에서 제거하고, 공고 폴더와 파일명을
  언더바 연결 snake 형식 `{공고번호}_{차수}/{공고번호}_{차수}_doc{NN}{확장자}`로 통일했다
  (docNN은 2자리 제로패딩). 이름이 결정적이 되면서 재실행 시 같은 키에 덮어써
  멱등해졌고, 기존 `_2`/`_3` 충돌 회피 로직(`used_keys`)은 제거했다.
  원본 이름과의 매핑은 manifest의 `fileName`으로 보존된다.

### 파이프라인 일원화 (2026-07-06)

- 비동기 버전 안정화 후 동기 backfill 2종(레거시 이슈 2건 보유: 1개월 초과 범위를 0건으로
  오인, daily용 `is_open` 필터 잔존)과 테스트 일체(`tests/`, `backfill_async/tests/`,
  `pytest.ini`)를 제거하고, 비동기 스크립트를 저장소 루트로 이동해 backfill 구현을
  일원화했다. 위 "병행 구축"과 "패키지 분리" 항목은 과도기의 결정 기록이다.

### 5분 준실시간 Airflow DAG (2026-07-07)

- EC2 위 Airflow에서 `dags/bidding_daily_dag.py`를 5분마다 실행하도록 구성한다.
  DAG는 orchestration만 담당하고, 실제 로직은 기존 `raw_json_daily.py`와
  `json_file_download_daily.py`를 그대로 호출한다.
- 수집 task는 기본적으로 `raw_json_daily.py --minutes 5`로 실행한다. 다만
  Airflow Variable `bidding_daily_last_success_at` 기준 gap이 30분 이하이면
  `last_success_at ~ 현재 시각`만큼 `--minutes`를 늘려 짧은 지연을 자동 복구한다.
- daily S3 적재 경로는 시간대별 조회와 모니터링이 쉽도록 `hour=HH` 파티션까지 나눈다.
- 다운로드 task는 기본적으로 `json_file_download_daily.py --minutes 15`로 실행한다.
  이는 공고를 15분치 수집한다는 뜻이 아니라, 앞 task 지연이나 Airflow 재시도 지연으로
  S3에 저장된 curated JSON을 놓치지 않기 위한 완충 창이다. gap 복구로 수집 창이
  15분보다 커지면 다운로드 창도 같은 크기로 늘린다.
- DAG 설정은 `catchup=False`, `max_active_runs=1`, `retries=2`를 기본으로 둔다.
  과거 미실행 구간을 몰아서 따라잡지 않고, 이전 실행이 끝나기 전에 다음 5분 실행이
  겹치지 않게 하기 위한 선택이다.
- Airflow 2/3 차이를 흡수하기 위해 `schedule_interval`/`schedule` 인자를 런타임에
  선택하고, `PythonOperator` import도 버전에 맞게 fallback한다.
- Airflow Variable `bidding_daily_last_success_at`에 마지막 처리 기준 시각을 저장한다.
  gap이 30분 이하이면 `last_success_at ~ 현재 시각`까지 수집 창을 넓혀 짧은 지연을 자동 복구한다.
- gap이 30분을 초과하면 긴 구간은 자동 수집하지 않고
  `raw/downloads/daily/_metadata/gaps/` 아래 manifest와 Airflow 로그에 남긴 뒤,
  현재 실행은 다시 최근 5분 기준으로 진행한다. 이 구간은 이후 backfill로 복구한다.
- 초기 운영에서는 daily 다운로드를 순차 처리로 유지한다. 병목이 실제로 관찰되면
  `ThreadPoolExecutor(max_workers=3~5)` 기반 제한 병렬화를 검토한다.
- 15분 다운로드 창은 중복 다운로드 가능성을 감수하고 누락을 줄이는 단기 방안이다.
  중복이나 재처리가 운영상 문제가 되면 DB 상태 컬럼(`pending`/`downloaded`/`failed`)
  기반으로 전환한다.

## 9. 다음 로드맵

- daily 파이프라인의 비동기 전환 검토
- Airflow DAG 전환: collect → download → parse → index 자동화
- OpenSearch 인덱스 설계 + Nori 형태소 분석기 적용
- 하이브리드 검색(BM25 + 벡터) + 비즈니스 룰 re-ranking (후속 단계)
