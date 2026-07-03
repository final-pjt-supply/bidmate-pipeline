# CLAUDE.md

이 저장소에서 작업할 때 Claude가 따라야 할 규칙.

## 프로젝트 개요

입찰 공고 문서(HWP / HWPX / PDF)를 텍스트로 추출 → 임베딩 → 에이전트가 활용하는
파이프라인(**bidding-agent**). 현재는 **문서 추출 방식 실험 단계**로, 실제 기능
모듈(`parsing/`, `embedding/`, `agents/` 등)은 아직 정리 중이다.

- 추출 결정: HWPX는 내부 XML을 바로 파싱, HWP는 `hwp5proc xml`(embedbin 없이) →
  위치 추출 → 이미지는 별도 캡션. 근거는 [docs/](docs/) 참고.
- 추출 텍스트 출력 형식은 PDF 파서(`parsing/text_extractor.py`, `#6` 브랜치)와
  통일: 표는 `[표]\n{행}\n[/표]`(셀 `" | "`, 행 `"\n"`), 이미지는 `[이미지:img_XXX]`
  플레이스홀더 + registry.

## 커밋 규칙

커밋은 **논리적 체크포인트마다 수동으로** 한다(자동 커밋 없음). 메시지는
[Github_Convention.md](Github_Convention.md)의 컨벤션을 따른다.

**형식:** `Type : 설명` (콜론 앞뒤 공백, 설명은 한국어)

**Type 종류:**
`Feat`(기능 추가) · `Fix`(버그 수정) · `Docs`(문서) · `Style`(포맷팅) ·
`Refactor`(리팩터링) · `Test`(테스트 코드) · `Chore`(빌드/패키지) · `Build` ·
`Ci` · `Perf`(성능) · `Rename`(파일/폴더명) · `Remove`(파일 삭제)

예시:
```
Feat : HWPX 텍스트·표 추출 추가
Docs : README.md 내용 추가
```

**브랜치:** 소문자, 이음자는 `-`. `main` / `develop` / `feat/{기능명}` /
`refactor/{기능명}` / `hotfix`.

**PR:** 제목은 `[#이슈번호] 변경 사항`, 이슈와 연동.

## 커밋에 포함하지 않는 것 (.gitignore)

**진짜 공유해야 하는 기능 코드만** push한다. 다음은 추적하지 않는다:

- 실험·비교 코드: `transforming/compare/`, `transforming/testing.py`,
  `transforming/trans_test_optimizing*.py`
- 생성·중간 데이터: `transforming/output/`
- 샘플 원본/변환 문서: `*.hwp`, `*.hwpx`, `*.pdf`, 루트의 `*.txt`
  (단 `requirement.txt`는 추적)
- Python 캐시: `__pycache__/`, `*.pyc`

새 파일을 커밋하기 전에 실험/데이터에 해당하는지 먼저 판단하고, 그렇다면
`.gitignore`에 추가한다.

## 개발 환경

- Python 3.14, 의존성은 [requirement.txt](requirement.txt).
- 주요 라이브러리: PyMuPDF(`fitz`), `lxml`, `Pillow`, `pyhwp`(`hwp5proc`).
- `hwp5proc.exe`는 Python Scripts 경로에 설치되어 있어야 한다(HWP 처리용).
