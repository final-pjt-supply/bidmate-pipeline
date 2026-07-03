# ADR 0001: HWP/HWPX 문서 텍스트 추출 및 S3 파이프라인

- **상태:** 채택됨 (Accepted)
- **날짜:** 2026-07-03
- **브랜치:** `feat/hwp-to-pdf`
- **관련 문서:** [추출 파이프라인 결정](../2026-06-30-추출-파이프라인-결정.md), [project.md](../../project.md)

## 맥락 (Context)

입찰 공고 문서(HWP/HWPX/PDF)를 텍스트로 추출 → 임베딩 → 에이전트가 활용하는
파이프라인을 만든다. 원본 문서는 **S3**(`s3://bid-testing/raw/`)에 있고, 추출한
txt를 다시 S3(`s3://bid-testing/txts/`)에 저장해야 한다.

문서 형식마다 성격이 다르다:
- **HWP**: 바이너리(OLE) 포맷. 사람이 못 읽어 도구로 XML화 필요.
- **HWPX**: zip 컨테이너 안에 이미 XML이 들어있음(변환 불필요).
- **PDF**: 별도 팀원이 `#6` 브랜치(`parsing/text_extractor.py`)에서 담당.

임베딩 이후 단계에서 표·읽기순서가 깨지면 검색 품질이 급락하므로, **원본의 논리
구조(표/셀/순서)를 최대한 보존**하는 추출이 요구된다.

## 결정 (Decision)

**1. HWP/HWPX는 XML 경로로 추출한다 (PDF 변환 폐기).**
- HWP: `hwp5proc xml --no-validate-wellformed <path>`(pyhwp)로 XML을 뽑아 `lxml`로 파싱.
  `--embedbin`은 **빼고**(위치만 추출) 실행한다.
- HWPX: `zipfile` + `lxml`로 `Contents/section*.xml`을 직접 파싱.
- HWP→PDF(한컴 COM) 경로는 **폐기**.

**2. 출력 텍스트 형식(마커)을 통일한다.**
- 표: `[표]\n{행}\n[/표]` (셀은 `" | "` join, 행은 `"\n"` join, 표는 행×셀로 재귀 누적).
- 이미지: `[이미지:img_XXX]` **위치 placeholder** + registry(`{source_type, ref}`). 캡션은 미구현.
- 페이지 개념은 두지 않는다(HWP/HWPX엔 렌더링 전 페이지가 없음). 결과는 단일 `text`.

**3. 공통 인메모리 계약(`ExtractResult`)을 둔다.**
```python
{"source_type": "hwp"|"hwpx", "text": str, "images": {img_id: {"source_type", "ref"}}}
```
`parsing/__init__.py`의 `extract(path)` / `extract_bytes(data, filename)`가 확장자로 라우팅.

**4. S3 오케스트레이션.**
- `.env`에서 `AWS_REGION`(ap-northeast-2), `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`,
  `BUCKET_SRC_ADDRESS`, `BUCKET_LOC_ADDRESS`를 읽는다.
- `raw/` 아래 `.hwp/.hwpx`를 **키 이름 정렬순**으로 나열 → 다운로드 → 추출 →
  `txts/doc_N.txt`(1부터)로 업로드. `--dry-run`으로 업로드 없이 검증 가능.

## 대안 및 근거 (Alternatives considered)

| 결정 | 채택 | 폐기한 대안 | 근거 |
|---|---|---|---|
| HWP/HWPX 추출 | XML 직접 파싱 | PDF로 변환 후 좌표 추출 | PDF는 표/읽기순서 구조를 버려 다단 표가 깨짐(표 재현율 0.945 vs 0.567). |
| HWP 이미지 | embedbin 없이 위치만 | `--embedbin`으로 base64 내장 | 대용량 무압축 BMP에서 busy-hang(120s+). 위치는 embedbin 없이도 나옴. |
| HWP 배포 | hwp5proc(크로스플랫폼) | 한컴 COM(Hwp.exe) | COM은 Windows+한컴 설치 필수 → 서버 배포 불가. |
| 이미지 처리 | placeholder+registry(캡션 유예) | 인라인 즉시 캡션 | 캡션 모델(공유 describer)은 나중에. 자리만 예약해 재작업 방지. |
| 병합 셀 | span 무시(빈 칸) | colspan/rowspan 반영 | 읽기에 지장 없음. 정확 정렬 필요 시 후속 개선. |

## 결과 (Consequences)

**긍정적**
- 원본 논리 구조 보존으로 표/순서가 안정적. HWPX는 외부 의존성 없이(zipfile+lxml) 파싱.
- S3 bytes를 그대로 처리(입력 소스 교체 용이). `--dry-run`으로 안전 검증.
- 검증: `doc_1`(HWP) 13,234자 / `doc_2`(HWPX) 6,552자, 이미지 0, `txts/`에 정상 업로드.

**트레이드오프 / 알려진 한계**
- HWP의 박스/콜아웃이 내부적으로 1칸 표라 `[표]`로 표기됨(#6 PDF의 `[박스]`와 다름).
- 병합 많은 표는 빈 파이프(`|  |`)가 생김.
- HWP는 `hwp5proc` 실행 파일 의존(PATH 필요).

**유예된 것 (Deferred)**
- 이미지 캡션(describer) — PDF 팀과 **동일 이미지 분석 모델 공유** 예정. 지금은 위치만.
- 에러 처리(암호/손상 파일) — 정상 파일만 가정.
- 이미지 포함 문서 검증 — 현재 두 샘플 모두 이미지 없음.
- PDF 경로와의 통합 — 각 브랜치에서 독립 구현 후 `main`에서 병합.
