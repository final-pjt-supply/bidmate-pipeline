# 작업 핸드오프 — feat/pdf-parsing-embedding-#6

> 이 파일은 집에서 Claude Code로 작업 재개 시 컨텍스트 복원용입니다.
> 작업 완료 후 삭제 또는 커밋하지 않아도 됩니다.

---

## 현재 브랜치

```
feat/pdf-parsing-embedding-#6
```

## 이슈 #6 목표

조달 입찰 PDF에서 텍스트·표·이미지를 **문서 순서 그대로** 추출하여
이후 LLM 섹션 식별 → 청킹 → 임베딩 파이프라인에 넘기는 것.

---

## 완료된 작업

### `parsing/text_extractor.py` (핵심 파일)

| 기능 | 상태 |
|------|------|
| 텍스트 블록 추출 (y좌표 정렬) | 완료 |
| 표 감지 + 셀 구조 (`|` 구분자) 보존 | 완료 |
| 병합 셀 처리 (cells_flat 격자 재구성) | 완료 |
| 표 안 이미지 → 정확한 셀에 삽입 (중심점 탐색) | 완료 |
| 표 밖 이미지 → `[이미지: 분석 예정]` 플레이스홀더 | 완료 |
| 박스(bordered rect) 텍스트 → `[박스]`~`[/박스]` 마커 | 완료 |
| 가로 페이지 자동 감지 → 좌/우 분할 추출 | 완료 |
| `__main__` 테스트 블록 (txt 출력) | 완료 |

### 삭제된 파일

- `parsing/text_layer_detector.py` — 조달청 공식문서는 스캔본이 없으므로 불필요 판정, 삭제.

### 기타

- `.gitignore` : `CLAUDE.md`, `data/sample/*.pdf`, `data/sample/output/` 추가
- `requirement.txt` : `pymupdf` 추가
- 테스트 샘플: `data/sample/의료+전문+음성+입력+시스템+구축+사업+제안요청서.hwp.pdf`
- 테스트 출력: `data/sample/output/의료전문음성입력시스템.txt` (로컬 only, gitignore)

---

## 핵심 설계 결정 (변경 시 주의)

1. **전체 추출 우선**: 텍스트+표+이미지를 먼저 전부 구조화 → LLM이 필요 섹션 식별.
   (2단계 파싱 방식은 섹션 범위 바깥 이미지 누락 위험으로 폐기)

2. **표는 청킹 시 절대 분리 금지**: `[표]`~`[/표]` 하나가 하나의 임베딩 단위.

3. **이미지 플레이스홀더**: `[이미지: 분석 예정]` — 나중에 멀티모달 LLM 설명으로 교체 예정.

4. **병합 셀 처리**: `table.cells` (cells_flat) 개수 ≠ `row_count × col_count`.
   x/y 경계값으로 격자를 재구성하고, 각 (row, col)의 중심점으로 해당 cells_flat 셀을 탐색.

5. **이미지-셀 매핑**: overlap이 아닌 이미지 **중심점**으로 셀을 찾음.
   (병합 셀의 큰 bbox와의 오매칭 방지)

---

## 남은 작업 (우선순위 순)

### 1. 이미지 ID 추적 시스템
멀티모달 LLM이 이미지 설명을 반환할 때 원래 위치에 다시 삽입할 수 있도록
고유 ID와 메타데이터(페이지 번호, 좌표, 문서 내 순서)를 함께 관리해야 함.

현재 `[이미지: 분석 예정]` → 목표: `[이미지:img_001]` + `{img_001: {page:10, rect:..., xref:26}}`

### 2. `parsing/TEXT_EXTRACTOR.md` 문서 업데이트
현재 코드와 내용이 맞지 않음 (이전 버전 기준). 현행 구조 반영 필요.

### 3. 청킹 모듈 (`parsing/chunker.py`)
- `[표]`~`[/표]`, `[박스]`~`[/박스]` 경계에서는 청킹 분리 금지
- 적정 청크 크기 결정 (토큰 수 기준)
- 섹션 정보(LLM이 찾아준 섹션명) 메타데이터로 포함

### 4. LLM 섹션 식별 (`preprocessing/section_identifier.py`)
- 전체 추출 텍스트 → LLM에게 "어떤 섹션이 입찰 자격/기술 요구사항인지" 찾아달라 요청
- 섹션 범위(시작~끝 페이지 또는 텍스트 위치)를 반환받아 필터링

### 5. 임베딩 + OpenSearch 인덱싱
- 확정: PostgreSQL + OpenSearch 패턴 B (하이브리드 RAG)
- 벡터 임베딩 모델 미결정

---

## 로컬 실행 방법

```bash
# 의존성 설치
pip install -r requirement.txt

# 텍스트 추출 테스트 (샘플 PDF를 data/sample/에 넣어둬야 함)
python -X utf8 parsing/text_extractor.py

# 출력 확인
data/sample/output/*.txt
```

---

## 주요 함수 구조 (`parsing/text_extractor.py`)

```
extract_text(pdf_path)
  └── extract_page_text(page)
        ├── 가로 페이지면 좌/우 clip으로 분할
        └── _extract_elements(page, clip)
              ├── get_images(full=True) → 전체 이미지 수집
              ├── find_tables() → _format_table()
              │     ├── cells_flat 격자 재구성 (x/y 경계값)
              │     ├── 이미지 중심점 → cells_flat 인덱스 탐색
              │     └── 이미지 있는 셀 → _extract_cell_content() (좌표 기반)
              ├── 표 밖 이미지 → [이미지: 분석 예정]
              ├── _find_box_rects() → 박스 감지
              └── get_text("blocks") → 텍스트 블록 (표/박스 영역 제외)
```

---

## Git 컨벤션 (이 프로젝트)

- 커밋: `Type : 내용 (#6)` — Type = Feat/Fix/Docs/Test/Refactor 등
- PR 제목: `[#6] 변경사항`, 본문에 `Closes #6`
- Gemini 자동 코드리뷰: PR 올리면 `.github/workflows/gemini-pr-review.yml` 동작
