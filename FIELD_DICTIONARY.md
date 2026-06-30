# 나라장터 입찰공고 데이터 사전 (FIELD DICTIONARY)

조달청\_나라장터 입찰공고정보서비스(data.go.kr 15129394, 업무구분 4종)의 입찰공고목록
응답을 정제한 **큐레이션 스키마** 명세서다.<br>
코드의 `schema.py`(`FIELD_MAP` + `to_curated()`)와
1:1로 대응한다.

- **원본 API 응답:** 업무구분당 113개 필드 (필드 선택 옵션 없음 — 호출하면 전부 반환)
- **정제 결과:** **39개 필드** (원본 56개 채택 → 첨부 20개를 배열 1개로 통합)
- **표본:** 채움률(%)은 2026-06-23 용역(servc) 793건 기준의 참고치다 (업무구분·일자에 따라 달라질 수 있음)
---

## 1. 처분 요약

| 구분 | 개수 | 비고 |
|---|---:|---|
| 원본 전체 | 113 | API가 반환하는 전체 필드 |
| ├ 채택 (→ o) | 56 | 1:1 매핑 36 + 첨부 통합 20 |
| └ 탈락 (→ x) | 57 | 빈 컬럼 16 + 중복/대체 5 + 미사용 36 |
| **최종 큐레이션** | **39** | 채택 36(1:1) + `attachments` 1 + 수집기 주입 2 |

**변환 파이프라인:** `raw(113필드)` → `to_curated()` → `curated(39필드)`
원본은 `raw/`에 그대로 보존하고, 정제본은 `curated/`에 저장한다.

---

## 2. 최종 스키마 (39필드)

PK = (`bid_ntce_no`, `bid_ntce_ord`) 복합키.

| # | 필드 | 타입 | 변환 | 출처(원본) | 채움률 | 설명 |
|---:|---|---|---|---|---:|---|
| 1 | `bid_ntce_no` | text | text | bidNtceNo | 100% | 입찰공고번호 **(PK)** |
| 2 | `bid_ntce_ord` | text | text | bidNtceOrd | 100% | 입찰공고차수 **(PK)** |
| 3 | `unty_ntce_no` | text | text | untyNtceNo | 100% | 통합공고번호 |
| 4 | `bid_ntce_nm` | text | text | bidNtceNm | 100% | 입찰공고명 (임베딩 핵심) |
| 5 | `ntce_kind_nm` | text | text | ntceKindNm | 100% | 공고종류(등록/재공고/취소/변경) |
| 6 | `srvce_div_nm` | text | text | srvceDivNm | 100% | 용역구분(일반/기술) |
| 7 | `large_clsfc_nm` | text | text | pubPrcrmntLrgClsfcNm | 100% | 조달 대분류명 |
| 8 | `mid_clsfc_nm` | text | text | pubPrcrmntMidClsfcNm | 100% | 조달 중분류명 |
| 9 | `prcrmnt_clsfc_nm` | text | text | pubPrcrmntClsfcNm | 100% | 조달분류명 |
| 10 | `prcrmnt_clsfc_no` | text | text | pubPrcrmntClsfcNo | 100% | 조달분류번호(UNSPSC) |
| 11 | `dminstt_nm` | text | text | dminsttNm | 100% | 수요기관명(발주처) |
| 12 | `dminstt_cd` | text | text | dminsttCd | 100% | 수요기관코드 |
| 13 | `ntce_instt_nm` | text | text | ntceInsttNm | 100% | 공고기관명 |
| 14 | `ntce_instt_cd` | text | text | ntceInsttCd | 100% | 공고기관코드 |
| 15 | `ofcl_nm` | text | text | ntceInsttOfclNm | 100% | 담당자명 |
| 16 | `ofcl_tel` | text | text | ntceInsttOfclTelNo | 97% | 담당자 전화번호 |
| 17 | `asign_bdgt_amt` | bigint | int | asignBdgtAmt | 100% | 배정예산금액(원) |
| 18 | `presmpt_prce` | bigint | int | presmptPrce | 100% | 추정가격(원) |
| 19 | `vat` | bigint | int | VAT | 100% | 부가가치세(원) |
| 20 | `bid_ntce_dt` | timestamp | datetime | bidNtceDt | 100% | 입찰공고일시 |
| 21 | `bid_begin_dt` | timestamp | datetime | bidBeginDt | 90% | 입찰개시일시 |
| 22 | `bid_clse_dt` | timestamp | datetime | bidClseDt | 90% | 입찰마감일시 **(마감 필터 기준)** |
| 23 | `openg_dt` | timestamp | datetime | opengDt | 100% | 개찰일시 |
| 24 | `qlfct_rgst_dt` | timestamp | datetime | bidQlfctRgstDt | 90% | 참가자격등록 마감일시 |
| 25 | `cntrct_mthd_nm` | text | text | cntrctCnclsMthdNm | 100% | 계약체결방법(수의/경쟁) |
| 26 | `sucsfbid_mthd_nm` | text | text | sucsfbidMthdNm | 100% | 낙찰방법명 |
| 27 | `bid_methd_nm` | text | text | bidMethdNm | 100% | 입찰방식명 |
| 28 | `lwlt_rate` | numeric | float | sucsfbidLwltRate | 37% | 낙찰하한율(%) |
| 29 | `tech_evl_rt` | numeric | float | techAbltEvlRt | 40% | 기술능력 평가비율(%) |
| 30 | `prce_evl_rt` | numeric | float | bidPrceEvlRt | 40% | 입찰가격 평가비율(%) |
| 31 | `indstryty_lmt_yn` | boolean | bool | indstrytyLmtYn | 100% | 업종제한 여부 |
| 32 | `intrbid_yn` | boolean | bool | intrbidYn | 100% | 국제입찰 여부 |
| 33 | `re_ntce_yn` | boolean | bool | reNtceYn | 100% | 재공고 여부 |
| 34 | `info_biz_yn` | boolean | bool | infoBizYn | 8% | 정보화사업 여부 |
| 35 | `bid_ntce_url` | text | text | bidNtceUrl | 100% | 입찰공고 상세 URL |
| 36 | `chg_ntce_rsn` | text | text | chgNtceRsn | 11% | 변경/취소 사유 |
| 37 | `attachments` | jsonb(array) | 배열화 | ntceSpecDocUrl1~10 + ntceSpecFileNm1~10 | 86%¹ | 첨부 목록 `[{file_nm, file_url}]` |
| 38 | `src_biz_div` | text | 주입 | (수집기) | 100% | 업무구분(thng/cnstwk/servc/frgcpt) |
| 39 | `collected_at` | timestamp | 주입 | (수집기) | 100% | 수집 시각 |

¹ 첨부 ≥1개를 보유한 레코드 비율. 레코드당 0~10개(표본 분포: 0개 108 · 1개 53 · 2개 151 · 3개 228 · 4개 166 · 5개 44 · 6개 20 · 7개 14 · 8개 4 · 10개 5).

---

## 3. `attachments` 구조

원본의 평면 컬럼 `ntceSpecDocUrl1~10` + `ntceSpecFileNm1~10` (최대 20개)을 객체 배열로 접는다.
URL·파일명은 항상 1:1로 동시 출현하며, 둘 중 하나라도 있으면 항목으로 추가한다.

```json
"attachments": [
  { "file_nm": "1.전자수의시담 안내(원본).hwp", "file_url": "https://www.g2b.go.kr/pn/.../downloadFile.do?...&fileSeq=1..." },
  { "file_nm": "2.전자수의시담 안내(변환본).pdf", "file_url": "https://www.g2b.go.kr/pn/.../downloadFile.do?...&fileSeq=2..." }
]
```

| 키 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `file_nm` | text | ntceSpecFileNm{i} | 첨부 파일명 |
| `file_url` | text | ntceSpecDocUrl{i} | 첨부 다운로드 URL (Processing Layer에서 사용) |

---

## 4. 타입 변환 규칙

`to_curated()`가 적용하는 캐스팅 규칙. 빈 문자열·공백·`null`은 모두 `None`으로 정규화한다.

| 변환 함수 | 대상 | 규칙 | 실패 시 |
|---|---|---|---|
| `_txt` | 모든 text 필드 | 앞뒤 공백 제거 | 빈값 → `None` |
| `_int` | 금액 3종 | 순수 숫자열만 `int` | 숫자 아니면 `None` |
| `_num` | 율(rate) 3종 | `float` 변환 | 변환 불가 시 `None` |
| `_dt` | 일시 5종 | `YYYY-MM-DD HH:MM:SS`로 정규화 (`%Y-%m-%d %H:%M:%S` / `%Y-%m-%d %H:%M` / `%Y-%m-%d` 허용) | 모르는 포맷은 원본 문자열 유지 |
| `_bool` | Yn 4종 | `Y`→`true`, `N`→`false` | 그 외(빈값 등) → `None` |

> 참고: `qlfct_rgst_dt`처럼 초 단위 없는 원본(`2026-06-23 18:00`)은 `2026-06-23 18:00:00`으로 보정된다.

---

## 5. 탈락 필드 (→ x · 57개)

### (가) 완전 빈 컬럼 — 16개 (표본 793건 전부 0%)

`indutyVAT`, `bidPrtcptFeePaymntYn`, `rgnDutyJntcontrctRt`, `brffcBidprcPermsnYn`,
`tpEvalApplMthdNm`, `jntcontrctDutyRgnNm1`, `jntcontrctDutyRgnNm2`, `jntcontrctDutyRgnNm3`,
`mnfctYn`, `chgDt`, `ntceInsttOfclEmailAdrs`, `dminsttOfclEmailAdrs`, `dtlsBidYn`,
`bidGrntymnyPaymntYn`, `tpEvalApplClseDt`, `ntceDscrptYn`

### (나) 중복·대체됨 — 5개

| 원본 | 사유 |
|---|---|
| bidNtceDtlUrl | `bid_ntce_url`(동일 공고 링크)로 대체 |
| rgstDt | `bid_ntce_dt`(공고일시)로 대체 |
| exctvNm | `ofcl_nm`(담당자)로 대체 |
| crdtrNm | 기관명 필드로 충분 |
| sucsfbidMthdCd | 코드 대신 명칭(`sucsfbid_mthd_nm`) 채택 |

### (다) 현재 미사용 — 36개 (저활용 플래그·세부절차·예가/PQ/실적 관련)

| 원본 | 의미 | 원본 | 의미 |
|---|---|---|---|
| rbidOpengDt | 재입찰개찰일시 | befBidBbancNo | 이전입찰공고번호 |
| dsgntCmptYn | 지정경쟁여부 | tpEvalYn | 적격심사대상여부 |
| cmmnSpldmdCorpRgnLmtYn | 공동수급기업지역제한 | sucsfbidMthdAppStd | 낙찰방법적용기준 |
| ppswGnrlSrvceYn | 조달청일반용역여부 | rgnLmtBidLocplcJdgmBssNm | 지역제한판단기준명 |
| cmmnSpldmdMethdNm | 공동수급방식명 | rgnLmtBidLocplcJdgmBssCd | 지역제한판단기준코드 |
| cmmnSpldmdMethdCd | 공동수급방식코드 | pqEvalYn | PQ심사대상여부 |
| cmmnSpldmdAgrmntRcptdocMethd | 공동수급협정접수방법 | pqApplDocRcptMthdNm | PQ신청서접수방법 |
| cmmnSpldmdAgrmntClseDt | 공동수급협정마감일시 | pqApplDocRcptDt | PQ신청서접수일시 |
| rgstTyNm | 등록유형명 | purchsObjPrdctList | 구매대상물품리스트 |
| arsltCmptYn | 실적경쟁여부 | arsltApplDocRcptMthdNm | 실적신청서접수방법 |
| arsltReqstdocRcptDt | 실적증빙접수일시 | prearngPrceDcsnMthdNm | 예정가격결정방법명 |
| prdctClsfcLmtYn | 물품분류제한여부 | rsrvtnPrceReMkngMthdNm | 예비가격재생성방법 |
| bidPrtcptLmtYn | 입찰참가제한여부 | totPrdprcNum | 총예가건수 |
| bidPrtcptFee | 입찰참가수수료 | drwtPrdprcNum | 추첨예가건수 |
| opengPlce | 개찰장소 | rbidPermsnYn | 재입찰허용여부 |
| refNo | 참조번호 | dcmtgOprtnPlce | 설명회개최장소 |
| stdNtceDocUrl | 표준공고서류URL | dcmtgOprtnDt | 설명회개최일시 |
| orderPlanUntyNo | 발주계획통합번호 | bfSpecRgstNo | 사전규격등록번호 |

> 필요 시 (다) 그룹의 필드는 `schema.py`의 `FIELD_MAP`에 `"필드명": ("원본명", 변환함수)` 한 줄을
> 추가하면 즉시 큐레이션에 포함된다. (예: 예가 관련 분석이 필요해지면 `totPrdprcNum`/`drwtPrdprcNum` 추가)

---

## 6. 비고

- **복합 PK:** `bid_ntce_no`만으로는 중복(차수·재공고)이 있어 유일하지 않다. `bid_ntce_ord`와 묶어야 유일(표본 793/793).
- **마감 필터:** 수집기는 `bid_clse_dt`(원본 `bidClseDt`)가 현재 시각보다 미래인 공고만 적재한다. `bidClseDt`가 없는 공고(표본 약 10%)는 "마감이 지났다"고 볼 수 없어 유지한다.
- **상태 필드 부재:** 웹 UI의 '세부절차상태'(진행완료 등)는 이 목록 API 응답에 없다. 공고의 생존 여부는 `bid_clse_dt` + 취소 여부(`ntce_kind_nm`/`chg_ntce_rsn`)로 판단한다.
- **금액 단위:** 모든 금액 필드는 원(KRW) 단위 정수다.