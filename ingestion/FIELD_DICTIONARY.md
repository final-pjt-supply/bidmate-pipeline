# 나라장터 입찰공고 데이터 사전 (FIELD DICTIONARY)

조달청\_나라장터 입찰공고정보서비스(data.go.kr 15129394, 업무구분 4종)의 입찰공고목록
응답을 정제한 **큐레이션 스키마** 명세서다.<br>
코드의 `schema.py`(`FIELD_MAP` + `to_curated()`)와 1:1로 대응한다.

- **원본 API 응답:** 업무구분당 113개 필드 (필드 선택 옵션 없음 — 호출하면 전부 반환)
- **정제 결과:** **47개 필드** (원본 1:1 매핑 38 + 조립(fallback) 1 + 배열/객체 4 + 생성 1 + 주입 2 + 계산 1)

---

## 1. 처분 요약

| 구분 | 개수 | 비고 |
|---|---:|---|
| 원본 1:1 매핑 (`FIELD_MAP`) | 38 | camelCase 원본 → snake_case 큐레이션 |
| 조립 (원본 키가 업무구분마다 다름) | 1 | `bdgt_amt` — `bdgtAmt`/`asignBdgtAmt` fallback (각주 ³) |
| 배열/객체로 접음 | 4 | `attachments`, `jntcontrct_duty_rgn_nm`, `subsi_cnstty`, `cnstty_accot_shre_rate_list` |
| 생성 (다른 필드 조합) | 1 | `bid_id` = `{공고번호}_{차수}` |
| 주입 (수집기 외부 입력) | 2 | `bid_category`(업무구분), `raw_s3_key`(원본 위치) |
| 계산 | 1 | `expected_file_count` = `len(attachments)` |
| **최종 큐레이션** | **47** | |

**변환 파이프라인:** `raw(113필드)` → `to_curated(record, bid_category, raw_s3_key)` → `curated(47필드)`
원본은 `raw/`에 그대로 보존하고, 정제본은 `curated/`에 저장한다.

---

## 2. 최종 스키마 (47필드)

PK = (`bid_ntce_no`, `bid_ntce_ord`) 복합키. 필드 순서는 `to_curated()` 출력 순서와 동일하다.
`종류` 열: **1:1**=원본 단일필드 매핑 · **조립**=원본 키가 업무구분마다 달라 fallback으로
채택(아래 5절 참고) · **배열/객체**=여러 원본을 접음 · **생성/주입/계산**=아래 5절 참고.

| # | 필드 | 타입 | 변환 | 출처(원본) | 종류 | 설명 |
|---:|---|---|---|---|---|---|
| 1 | `bid_ntce_no` | text | `_txt` | bidNtceNo | 1:1 | 공고번호 **(PK)** |
| 2 | `bid_ntce_ord` | text | `_txt` | bidNtceOrd | 1:1 | 차수 **(PK)** |
| 3 | `bid_id` | text | 생성 | bidNtceNo+bidNtceOrd | 생성 | `{공고번호}_{차수}` (예: `20260700123_01`) |
| 4 | `bid_category` | text | 주입 | (수집기) | 주입 | 출처 API 업무구분(thng/servc/cnstwk/frgcpt) |
| 5 | `bid_ntce_nm` | text | `_txt` | bidNtceNm | 1:1 | 공고명 (임베딩 핵심) |
| 6 | `ntce_instt_cd` | text | `_txt` | ntceInsttCd | 1:1 | 공고기관 코드 |
| 7 | `ntce_instt_nm` | text | `_txt` | ntceInsttNm | 1:1 | 공고기관명 |
| 8 | `dminstt_cd` | text | `_txt` | dminsttCd | 1:1 | 수요기관 코드 |
| 9 | `dminstt_nm` | text | `_txt` | dminsttNm | 1:1 | 수요기관명(발주처) |
| 10 | `re_ntce_yn` | boolean | `_bool` | reNtceYn | 1:1 | 재공고 여부 |
| 11 | `intrbid_yn` | boolean | `_bool` | intrbidYn | 1:1 | 국제입찰 여부 |
| 12 | `bid_ntce_dt` | timestamp | `_dt` | bidNtceDt | 1:1 | 공고일시 |
| 13 | `bid_clse_dt` | timestamp | `_dt` | bidClseDt | 1:1 | 투찰마감 **(마감 필터 기준)** |
| 14 | `openg_dt` | timestamp | `_dt` | opengDt | 1:1 | 개찰일시 |
| 15 | `bid_qlfct_rgst_dt` | timestamp | `_dt` | bidQlfctRgstDt | 1:1 | 자격등록마감 |
| 16 | `rgst_dt` | timestamp | `_dt` | rgstDt | 1:1 | 등록일시 |
| 17 | `chg_dt` | timestamp | `_dt` | chgDt | 1:1 | 변경일시 |
| 18 | `presmpt_prce` | bigint | `_int` | presmptPrce | 1:1 | 추정가격(원) |
| 19 | `bdgt_amt` | bigint | `_int` | bdgtAmt 우선, 없으면 asignBdgtAmt | 조립 | 사업예산(원) ³ |
| 20 | `vat` | bigint | `_int` | VAT | 1:1 | 부가세(원) |
| 21 | `govsply_amt` | bigint | `_int` | govsplyAmt | 1:1 | 관급자재액(원) |
| 22 | `cntrct_cncls_mthd_nm` | text | `_txt` | cntrctCnclsMthdNm | 1:1 | 계약방법(수의/경쟁) |
| 23 | `sucsfbid_mthd_cd` | text | `_txt` | sucsfbidMthdCd | 1:1 | 낙찰방법 코드 |
| 24 | `sucsfbid_mthd_nm` | text | `_txt` | sucsfbidMthdNm | 1:1 | 낙찰방법명 |
| 25 | `sucsfbid_lwlt_rate` | numeric | `_num` | sucsfbidLwltRate | 1:1 | 낙찰하한율(%) |
| 26 | `bid_methd_nm` | text | `_txt` | bidMethdNm | 1:1 | 입찰방식 |
| 27 | `pq_eval_yn` | boolean | `_bool` | pqEvalYn | 1:1 | PQ(사전적격심사) 여부 |
| 28 | `dsgnt_cmpt_yn` | boolean | `_bool` | dsgntCmptYn | 1:1 | 지명경쟁 여부 |
| 29 | `bid_prtcpt_lmt_yn` | boolean | `_bool` | bidPrtcptLmtYn | 1:1 | 참가제한 여부 |
| 30 | `rbid_permsn_yn` | boolean | `_bool` | rbidPermsnYn | 1:1 | 재입찰 허용 여부 |
| 31 | `cnstrtsite_rgn_nm` | text | `_txt` | cnstrtsiteRgnNm | 1:1 | 현장 지역 ¹ |
| 32 | `rgn_duty_jntcontrct_yn` | boolean | `_bool` | rgnDutyJntcontrctYn | 1:1 | 지역의무공동도급 여부 |
| 33 | `rgn_duty_jntcontrct_rt` | numeric | `_num` | rgnDutyJntcontrctRt | 1:1 | 지역의무공동도급 비율(%) |
| 34 | `jntcontrct_duty_rgn_nm` | jsonb(array) | 배열화 | jntcontrctDutyRgnNm1~3 | 배열/객체 | 의무지역 목록 `["지역명", ...]` |
| 35 | `cmmn_spldmd_methd_cd` | text | `_txt` | cmmnSpldmdMethdCd | 1:1 | 공동수급방식 코드 |
| 36 | `cmmn_spldmd_methd_nm` | text | `_txt` | cmmnSpldmdMethdNm | 1:1 | 공동수급방식명 |
| 37 | `cmmn_spldmd_agrmnt_clse_dt` | timestamp | `_dt` | cmmnSpldmdAgrmntClseDt | 1:1 | 공동수급협정 마감 |
| 38 | `main_cnstty_nm` | text | `_txt` | mainCnsttyNm | 1:1 | 주공종 ¹ |
| 39 | `main_cnstty_presmpt_prce` | bigint | `_int` | mainCnsttyPresmptPrce | 1:1 | 주공종 추정가격(원) ¹ |
| 40 | `indstryty_lmt_yn` | boolean | `_bool` | indstrytyLmtYn | 1:1 | 업종제한 여부 |
| 41 | `cnstty_accot_shre_rate_list` | jsonb(array) | 파싱 | cnsttyAccotShreRateList | 배열/객체 | 공종지분 ¹ ² |
| 42 | `subsi_cnstty` | jsonb(array) | 배열화 | subsiCnsttyNm1~9 + subsiCnsttyEvlRt1~9 | 배열/객체 | 부공종 `[{nm, evl_rt}]` ¹ |
| 43 | `bid_ntce_dtl_url` | text | `_txt` | bidNtceDtlUrl | 1:1 | 상세페이지 링크 |
| 44 | `unty_ntce_no` | text | `_txt` | untyNtceNo | 1:1 | 통합공고번호 |
| 45 | `attachments` | jsonb(array) | 배열화 | ntceSpecDocUrl1~10 + ntceSpecFileNm1~10 + stdNtceDocUrl | 배열/객체 | 첨부 목록 `[{file_nm, file_url, kind}]` |
| 46 | `expected_file_count` | int | 계산 | (=len attachments) | 계산 | 기대 첨부 개수 |
| 47 | `raw_s3_key` | text | 주입 | (수집기) | 주입 | 원본 JSON S3 객체 키 |

¹ **주로 공사(cnstwk)에서 채워짐.** 현장·공종·지역의무·공동수급 계열 필드는 용역/물품/외자
공고에서는 원본에 값이 없어 `None`(또는 빈 배열 `[]`)이 된다. 원본에 해당 키 자체가 없어도
`record.get()` → `None`으로 안전하게 처리된다.

² `cnsttyAccotShreRateList`의 원본 형태(문자열/배열)가 표본으로 확정되지 않아, 현재 파서는
**무손실 보존**을 우선한다 (3절 참고). 실데이터 확인 후 `[{nm, rate}]` 구조로 승격 예정.
문자열인 경우 대괄호를 전부 제거한 뒤 구분자로 split한다 — 대괄호를 안 벗기면
`"[토목공사업^100]"` → `["[토목공사업", "100]"]`처럼 토큰에 대괄호가 남는 버그가 있었고,
맨 앞/뒤만 벗기는 방식(removeprefix/removesuffix)도 공종이 여럿이라 대괄호가 여러 그룹으로
나뉘는 값(예: `"[전기^60],[기계^40]"`, 2026-07 cnstwk 표본에서 실제 확인)에서는 가운데
그룹의 대괄호가 그대로 남아 같은 버그가 재발했다. 그래서 위치와 무관하게 대괄호 문자를
전부 제거하도록 수정됨(2026-07).

³ 스펙 명세는 `bdgtAmt`였으나 최초 조사 때 본 실제 응답(thng 표본)에는 그 키가 없고
`asignBdgtAmt`(배정예산금액)만 있어 이를 출처로 채택했었다. 이후 cnstwk 표본으로 재확인한
결과 **업무구분(biz_div)마다 실제 키가 다르다**: cnstwk는 `bdgtAmt`, thng/servc/frgcpt는
`asignBdgtAmt`. cnstwk 전건이 `asignBdgtAmt`만 보다가 늘 null이 되던 버그의 원인이었고,
현재는 `_bdgt_amt()`가 `bdgtAmt` → `asignBdgtAmt` 순서로 시도한다. 원문이 있다는 것만으로
채택하지 않고 `_int()`로 실제 숫자 파싱이 되는지까지 확인한 뒤 채택한다 — 우선순위 키에
"-" 같은 결측 플레이스홀더가 와도 파싱에 실패하면 다음 키로 넘어가고, 빈 문자열/`None`뿐
아니라 정상적인 `0`은 그대로 유효한 값으로 채택한다(2026-07).

---

## 3. 배열/객체 필드 구조

원본의 평면 컬럼들을 하나의 jsonb 배열/객체로 접는다. 값이 없으면 빈 배열 `[]`.

### `attachments` — 첨부 목록

`ntceSpecDocUrl1~10` + `ntceSpecFileNm1~10`(공고첨부)과 표준공고서 `stdNtceDocUrl`을
객체 배열로 접는다. 공고첨부는 URL·파일명이 1:1로 함께 오며 둘 중 하나라도 있으면 추가한다.
표준공고서는 파일명 없이 URL만 온다.

```json
"attachments": [
  { "file_nm": "과업지시서.hwp", "file_url": "https://www.g2b.go.kr/.../downloadFile.do?...", "kind": "공고첨부" },
  { "file_nm": null,           "file_url": "https://www.g2b.go.kr/.../stdNtceDoc...",      "kind": "표준공고서" }
]
```

| 키 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `file_nm` | text | ntceSpecFileNm{i} | 첨부 파일명 (표준공고서는 `null`) |
| `file_url` | text | ntceSpecDocUrl{i} / stdNtceDocUrl | 첨부 다운로드 URL (다운로드 단계에서 소비) |
| `kind` | text | (분류) | `공고첨부` 또는 `표준공고서` |

### `jntcontrct_duty_rgn_nm` — 의무지역

`jntcontrctDutyRgnNm1~3` 중 비어 있지 않은 값만 모은 문자열 배열. 예: `["강원특별자치도"]`.

### `subsi_cnstty` — 부공종

`subsiCnsttyNm{i}`와 `subsiCnsttyEvlRt{i}`(i=1~9)를 쌍으로 묶는다. 이름 또는 평가비율 중
하나라도 있으면 항목으로 추가한다.

```json
"subsi_cnstty": [
  { "nm": "소방공사", "evl_rt": 10.5 },
  { "nm": "전기공사", "evl_rt": 5.0 }
]
```

### `cnstty_accot_shre_rate_list` — 공종지분

`cnsttyAccotShreRateList`를 파싱한다. 원본이 이미 배열/객체면 그대로 보존하고, 문자열이면
대괄호를 전부 제거한 뒤 구분자(`^ | ; ,`)로 나눠 토큰 배열로 만든다.
예(단일 공종): `"[토목공사업^100]"` → `["토목공사업", "100"]`.
예(복수 공종, 실측): `"[전기^60.1],[기계^39.9]"` → `["전기", "60.1", "기계", "39.9"]`.
(원본 형태 확정 전까지 무손실 보존 우선 — 2절 각주 ² 참고.)

---

## 4. 타입 변환 규칙

`to_curated()`가 적용하는 캐스팅 규칙. 빈 문자열·공백·`null`은 모두 `None`으로 정규화한다.

| 변환 함수 | 대상 | 규칙 | 실패 시 |
|---|---|---|---|
| `_txt` | 모든 text 필드 | 앞뒤 공백 제거 | 빈값 → `None` |
| `_int` | 금액 5종(추정가격/사업예산/부가세/관급자재액/주공종추정가격) | 순수 숫자열만 `int` | 숫자 아니면 `None` |
| `_num` | 율(rate) 2종(낙찰하한율/지역의무비율) | `float` 변환 | 변환 불가 시 `None` |
| `_dt` | 일시 7종 | `YYYY-MM-DD HH:MM:SS`로 정규화 (`%Y-%m-%d %H:%M:%S` / `%Y-%m-%d %H:%M` / `%Y-%m-%d` 허용) | 모르는 포맷은 원본 문자열 유지 |
| `_bool` | Yn 8종 | `Y`→`true`, `N`→`false` | 그 외(빈값 등) → `None` |

> 참고: 초 단위 없는 원본(`2026-07-20 18:00`)은 `2026-07-20 18:00:00`으로 보정된다.

---

## 5. 생성·주입·계산 필드

`FIELD_MAP`(원본 1:1)에 없고 `to_curated()`가 별도로 채우는 필드.

| 필드 | 분류 | 산출 방법 |
|---|---|---|
| `bid_id` | 생성 | `f"{bid_ntce_no}_{bid_ntce_ord}"`. 둘 중 하나라도 없으면 `None` |
| `bid_category` | 주입 | 수집기가 조회한 업무구분(오퍼레이션) 키를 `to_curated(record, bid_category, ...)`로 전달 |
| `raw_s3_key` | 주입 | 이 레코드의 원본 JSON이 저장된 S3 객체 키. 수집기가 `to_curated(record, ..., raw_s3_key)`로 전달. daily와 backfill 모두 공고 1건 단위 원본 JSON 키를 넣는다 |
| `expected_file_count` | 계산 | `len(attachments)` — 다운로드 단계가 실제 적재 수와 대조하는 기대치 |
| `bdgt_amt` | 조립(fallback) | `bdgtAmt` 우선, 없으면 `asignBdgtAmt` — 업무구분마다 실제 키가 다름(각주 ³) |

> `bid_category`·`raw_s3_key`는 수집 스크립트(`raw_json_daily.py`, `raw_json_backfill.py`)의
> `to_curated()` 호출부에서 주입한다. 다운로드 스크립트는 `bid_category`를 읽어 S3 키의
> `biz_div` 세그먼트로 사용한다.

---

## 6. 이전 스키마(39필드) 대비 주요 변경

- **부활/신규 채택:** `rgst_dt`, `chg_dt`, `sucsfbid_mthd_cd`, `pq_eval_yn`, `dsgnt_cmpt_yn`,
  `bid_prtcpt_lmt_yn`, `rbid_permsn_yn`, `govsply_amt`, 그리고 공사 계열 전체
  (`cnstrtsite_rgn_nm`, `rgn_duty_jntcontrct_*`, `jntcontrct_duty_rgn_nm`, `cmmn_spldmd_*`,
  `main_cnstty_*`, `cnstty_accot_shre_rate_list`, `subsi_cnstty`).
- **출처 교체:** 상세 URL `bidNtceUrl` → `bidNtceDtlUrl`(실데이터 확인, 둘 다 존재하나 상세 링크 채택).
  예산은 curated 필드명을 `asign_bdgt_amt` → `bdgt_amt`로 바꾸고, 출처도 업무구분별로 `bdgtAmt`/
  `asignBdgtAmt`를 모두 시도하는 fallback으로 변경(각주 ³).
- **제거:** 분류 계열(`ntceKindNm`, `srvceDivNm`, `pubPrcrmnt*` 4종), 담당자(`ofcl_nm`, `ofcl_tel`),
  평가비율(`techAbltEvlRt`, `bidPrceEvlRt`), `bidBeginDt`, `infoBizYn`, `chgNtceRsn`,
  `prcrmntClsfcNo` 등. 필요 시 `FIELD_MAP`에 `"필드": ("원본명", 변환함수)` 한 줄로 재추가 가능.
- **이름 변경:** `src_biz_div` → `bid_category`(다운로드 스크립트도 함께 갱신됨),
  `qlfct_rgst_dt` → `bid_qlfct_rgst_dt`, `lwlt_rate` → `sucsfbid_lwlt_rate`,
  `cntrct_mthd_nm` → `cntrct_cncls_mthd_nm`, `collected_at` 제거(원본 위치는 `raw_s3_key`로 대체).

---

## 7. 비고

- **복합 PK:** `bid_ntce_no`만으로는 중복(차수·재공고)이 있어 유일하지 않다. `bid_ntce_ord`와 묶어야 유일하다.
- **마감 필터:** daily 수집기는 `bid_clse_dt`(원본 `bidClseDt`)가 현재보다 미래인 공고만 적재한다.
  backfill은 과거 이력 보존이 목적이라 마감 필터를 적용하지 않는다.
- **업무구분별 채움 편차:** 공사(cnstwk) 전용 필드(2절 각주 ¹)는 용역/물품/외자에서 대부분 비어 있다.
- **금액 단위:** 모든 금액 필드는 원(KRW) 단위 정수다.
- **동기화 규칙:** `schema.py`의 `FIELD_MAP`/`to_curated()`를 수정하면 이 문서도 반드시 함께 갱신한다.
