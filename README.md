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
