# S3 폴더 구조 재정의 (raw/raw, raw/curated, raw/downloads)

## 배경

`feat/raw-json-ingestion` 브랜치의 수집 파이프라인 4개 스크립트가 사용하는 S3 버킷명과
저장 단위를 재정의한다. 대상 파일:

- `raw_json_backfill.py`
- `raw_json_daily.py`
- `json_file_download_backfill.py`
- `json_file_download_daily.py`

`institutions.py`, `schema.py`(FIELD_MAP/필드 목록)는 이번 변경 범위 밖이며 그대로 유지한다.

## 변경 1 — 버킷명

4개 파일 모두 `BUCKET_NAME` 기본값을 `"bidding-agent"` → `"bidmate"`로 변경한다.
(`S3_BUCKET` 환경변수로 여전히 오버라이드 가능)

## 변경 2 — 세 폴더 전부에 `backfill`/`daily` 하위 폴더 도입

`raw/raw`, `raw/curated`, `raw/downloads` 세 폴더 모두 바로 아래에 `backfill/`과 `daily/`
하위 폴더를 두고, 그 아래로 기존 `year=Y/month=M/day=D/...` 파티션 구조를 그대로 이어간다.
저장 단위(1건=1개 vs 1일=1개)는 기존에 정한 backfill/daily 규칙을 그대로 승계한다.

### `raw/raw`, `raw/curated`

- **daily** (`raw_json_daily.py`): 공고 1건 = JSON 1개 (변경 없음).
  - `raw/raw/daily/year=Y/month=M/day=D/biz_div={cat}/{공고번호-순번}.json`
  - `raw/curated/daily/year=Y/month=M/day=D/biz_div={cat}/{공고번호-순번}.json`
- **backfill** (`raw_json_backfill.py`): 하루+업무구분 단위로 묶어 배열 JSON 1개로 저장.
  - `raw/raw/backfill/year=Y/month=M/day=D/biz_div={cat}.json` — 해당 일자·업무구분의 원본 레코드 배열
  - `raw/curated/backfill/year=Y/month=M/day=D/biz_div={cat}.json` — 해당 일자·업무구분의 curated 레코드 배열
  - 조회 기간 전체를 institutions 루프까지 마친 뒤, 필터링된 레코드를 `notice_day()` 기준으로
    day별 그룹으로 나누고, 그룹당 배열 JSON을 1회 write 한다.
  - 레코드 단위 키를 만들던 `notice_id()` / `s3_json_key()` (레코드별 버전)는 더 이상 필요 없으므로
    제거하고, 그로 인해 미사용이 되는 `safe_key_part()` / `SAFE_KEY` / `re` import도 함께 정리한다.
- 두 스크립트 모두 `RAW_PREFIX`/`CURATED_PREFIX` 상수에 `/daily` 또는 `/backfill`을 반영하는
  것으로 충분하며, 경로를 조립하는 함수 자체는 prefix를 그대로 받아 쓰므로 추가 로직 변경이 없다.

### `raw/downloads`

- **daily** (`json_file_download_daily.py`): `raw/downloads/daily/...`
- **backfill** (`json_file_download_backfill.py`): `raw/downloads/backfill/...`
- 각 다운로드 스크립트는 대응하는 curated 하위 폴더를 읽어야 한다 — 즉
  `json_file_download_daily.py`의 `--curated-prefix` 기본값은 `raw/curated/daily`,
  `json_file_download_backfill.py`는 `raw/curated/backfill`로 바뀐다. (raw_json_daily.py가
  쓴 curated를 download_daily가, raw_json_backfill.py가 쓴 curated를 download_backfill이
  읽는 대응 관계를 유지하기 위함.)
- 다운로드 단계의 `iter_curated_range()` / `iter_recent_curated()`는 이미
  `record if isinstance(record, list) else [record]` 형태로 배열/단건을 모두 지원하므로
  (daily=단건, backfill=배열) 별도 수정이 필요 없다.

## 변경 3 — `raw/downloads` 첨부파일 키 구조

기존:
```
raw/downloads/year=Y/month=M/day=D/biz_div={cat}/notice_id={번호-순번}/{fileSeq}_{원본파일명}
```

변경 후 (변경 2의 `daily`/`backfill` 하위 폴더 포함):
```
raw/downloads/{daily|backfill}/year=Y/month=M/day=D/biz_div={cat}/bidNtceNo={공고번호}_ord={순번}/{stem}_{kind}{확장자}
```

세부 규칙:

- **폴더**: `year=Y/month=M/day=D/` 다음에 `biz_div={cat}` 계층을 유지하고(제거하지 않음),
  그 아래에 `bidNtceNo`와 `ord`를 하나의 경로 세그먼트로 결합한 폴더를 둔다.
  - `biz_div`는 metadata의 `업무구분` 값(=`src_biz_div`, 예: `servc`/`cnstwk`/`frgcpt`/`thng`)을 그대로 사용.
  - `ord`는 `bidNtceOrd` 값에서 숫자만 추출해 `zfill(2)` (예: `"0"` → `"00"`, 없으면 `"00"`).
- **파일명**: `{stem}_{kind}{확장자}`
  - `stem`은 원본 파일명(`fileName`)에서 확장자를 제거한 부분. 원본 파일명이 없는 경우
    (표준공고서 등 `fileName`이 빈 문자열인 첨부) `stem`은 `bidNtceNo` 값으로 대체한다.
  - `kind`는 `fileKind` 값 그대로 사용 (`공고첨부` 또는 `표준공고서`).
  - 확장자는 기존 `guess_ext()` 로직(원본 파일명 → Content-Type → URL 순) 그대로 사용.
- **중복 처리**: 최종 키(`stem_kind확장자`)가 완전히 동일한 첨부파일이 같은 공고 안에 여러 개
  있는 경우에만 `_2`, `_3` ... 접미사를 붙인다. 실행(run) 전체에 걸친 `used_keys` 집합으로
  추적하며, 업로드 시점(실제 Content-Type 확보 후)에 최종 키를 확정해 검사한다.
- `json_file_download_backfill.py`와 `json_file_download_daily.py`는 거의 동일한 코드이므로
  두 파일 모두 동일하게 수정한다.

## 변경 4 — README.md 갱신

현재 `README.md`는 구버전 로컬 파이프라인(`bid_pipeline.py`, `raw_json.py`,
`BASE_DIR=/Users/oloqlq/Desktop/bidding`) 기준으로 작성되어 있어 현재 코드와 맞지 않는다.
`feat/raw-json-ingestion` 파트 전체(4개 스크립트 + schema.py + institutions.py)를 다루도록
전면 재작성한다:

- 파이프라인 개요 (raw_json_daily/backfill → schema.py → json_file_download_daily/backfill)
- S3 버킷/경로 구조 (`s3://bidmate/raw/{raw,curated,downloads}`), 변경된 저장 단위
  (daily=건별, backfill=일별) 및 downloads 키 포맷을 이 문서에 정리된 대로 명시
- 실행 방법과 필요한 환경변수 (`G2B_SERVICE_KEY`, `S3_BUCKET`)
- TOP10 기관 필터링(institutions.py) 개념 설명

## 범위 밖

- `schema.py`의 FIELD_MAP/39필드 구성 변경 없음 → `FIELD_DICTIONARY.md` 갱신 불필요
- `institutions.py` 변경 없음
- OpenSearch 인덱싱, Airflow 전환 등 다음 로드맵 항목은 포함하지 않음
