# Project: bidding-agent

> Claude가 이 파일만 읽고도 프로젝트 맥락을 바로 잡을 수 있도록 정리한 문서.
> 작업 규칙은 [CLAUDE.md](CLAUDE.md), 결정 근거는 [docs/adr/](docs/adr/) 참고.

## 한 줄 소개

입찰 공고 문서(HWP / HWPX / PDF)를 **텍스트로 추출 → 임베딩 → 에이전트**가 활용하는
파이프라인. 원본은 S3에 있고, 추출한 txt를 다시 S3에 저장한다.

## 현재 상태 (2026-07-03)

**동작하는 것 (이 브랜치 `feat/hwp-to-pdf`):**
- **HWP → txt**: `hwp5proc xml`(embedbin 없이) → `lxml` 파싱. `parsing/hwp_extractor.py`
- **HWPX → txt**: `zipfile` + `lxml`로 내부 XML 직접 파싱. `parsing/hwpx_extractor.py`
- **S3 파이프라인**: `raw/` 문서 나열 → 다운로드 → 추출 → `txts/doc_N.txt` 업로드.
  `pipeline/s3_runner.py`
- 검증 완료: `doc_1`(HWP, 13,234자) · `doc_2`(HWPX, 6,552자) → `s3://bid-testing/txts/`

**다른 브랜치:**
- `#6`(`feat/pdf-parsing-embedding-#6`): **PDF → txt**(`parsing/text_extractor.py`) + 임베딩.
  우리는 PDF 쪽 구조를 따르지 않는다. 각자 구현 후 `main`에서 병합.

## 데이터 흐름

```
s3://bid-testing/raw/*.hwp,*.hwpx
        │  (boto3, .env 자격증명)
        ▼
  parsing.extract_bytes(data, filename)   ← 확장자로 라우팅
        ├─ .hwp  → hwp5proc xml → lxml
        └─ .hwpx → zipfile + lxml
        ▼
  ExtractResult{source_type, text, images}
        ▼
s3://bid-testing/txts/doc_1.txt, doc_2.txt, …
```

## 디렉터리 & 핵심 파일

```
parsing/
  contract.py         # ExtractResult 계약 + 마커 상수([표], image_placeholder)
  hwp_extractor.py    # HWP: hwp5proc xml → 파싱
  hwpx_extractor.py   # HWPX: zip+xml 직접 파싱
  __init__.py         # extract(path) / extract_bytes(data, filename) / to_txt() 라우터
pipeline/
  s3_runner.py        # .env 로드 → 나열/다운로드/추출/업로드 (--dry-run 지원)
docs/
  adr/                # Architecture Decision Records
  2026-06-30-*.md     # 추출 방식 비교·결정 문서
transforming/         # 실험·비교 코드(gitignore 대상, 참고용)
```

## 출력 형식 규약

- **표:** `[표]\n{행}\n[/표]` — 셀 `" | "` join, 행 `"\n"` join. 표는 행×셀로 재귀 누적.
- **이미지:** `[이미지:img_XXX]` 위치 placeholder + `images` registry(`{source_type, ref}`).
  캡션은 **아직 미구현**(자리만 예약).
- **페이지 없음:** HWP/HWPX는 페이지 개념이 없어 결과는 단일 `text`.

## 실행 방법

```bash
pip install -r requirement.txt          # lxml, boto3, python-dotenv, pyhwp 등
# .env 필요: AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY,
#            BUCKET_SRC_ADDRESS(s3://.../raw/), BUCKET_LOC_ADDRESS(s3://.../txts/)

python -m pipeline.s3_runner --dry-run  # 추출만(업로드 X)로 검증
python -m pipeline.s3_runner            # 실제 업로드
```
- HWP 처리는 `hwp5proc`(pyhwp)가 PATH에 있어야 함.
- `.env`는 gitignore됨(자격증명 커밋 금지).

## 주요 결정 (근거는 ADR)

- HWP/HWPX는 **XML 직접 추출**, PDF 변환·한컴 COM은 **폐기**. → [ADR 0001](docs/adr/0001-hwp-hwpx-txt-extraction-pipeline.md)
- HWP는 `--embedbin` 빼고 위치만, 이미지 바이트는 필요 시 별도 추출.
- 병합 셀 span 무시(빈 칸), 셀 내부 개행 유지.

## 앞으로 할 일 (Roadmap)

1. **이미지 캡션**: PDF 팀과 **동일한 이미지 분석 모델** 공유해 `[이미지:img_XXX]` 자리에 설명 삽입.
2. **에러 처리**: 암호/손상 파일 공통 처리(현재는 정상 파일 가정).
3. **이미지 포함 문서 검증**: 현재 샘플 2개 모두 이미지 없음.
4. **임베딩/에이전트 단계** 연결.
5. `main` 병합 시 PDF(`#6`) 경로와 통합.

## 규칙 & 규약

- 커밋: 수동, `Type : 설명` (한국어). [CLAUDE.md](CLAUDE.md) / [Github_Convention.md](Github_Convention.md).
- gitignore: 실험 코드·데이터·`.env`는 추적 안 함. 기능 코드만 push.
