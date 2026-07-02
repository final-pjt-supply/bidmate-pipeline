# HWPX 내부 XML 직접 추출 모듈 설계

작성일: 2026-07-02

## 배경 / 목적

입찰 공고 문서 추출 파이프라인에서 **HWPX**를 텍스트로 변환하는 정식 모듈을
추가한다. 현재 코드베이스에는:

- HWP → txt: `transforming/compare/`에 비교 실험 하니스로만 존재
  (Method A: 한컴 COM→PDF→PyMuPDF, Method B: `hwp5proc xml`→lxml).
- HWPX → txt: **없음.** `transforming/trans_test.py`가 한컴 COM으로
  HWPX→PDF 변환을 테스트할 뿐, 내부 XML을 직접 파싱하는 코드는 없다.

CLAUDE.md의 추출 결정("HWPX는 내부 XML을 바로 파싱")을 실제 코드로 구현한다.

## 범위

포함:

- HWPX(ZIP/OWPML) 내부 `Contents/section*.xml`을 직접 파싱해 본문·표·이미지
  참조를 CLAUDE.md 통일 출력 형식의 텍스트로 변환.
- 라이브러리 함수 `extract_hwpx(path) -> (text, registry)` 제공.

제외 (YAGNI):

- 이미지 바이트 추출 / 캡셔닝(Bedrock). 이미지는 **참조만** 기록.
- CLI / 폴더 배치 러너. 저장·배치는 호출측 책임.
- HWP·PDF 경로 변경. 기존 실험 코드는 건드리지 않는다.

## 접근법

**A. `zipfile` + `lxml`, local-name 기반 순회** (선정)

- HWPX는 ZIP 컨테이너, 본문은 `Contents/section0.xml, section1.xml…`(OWPML).
- 네임스페이스 URI가 HWPML 버전(2011 등)마다 달라질 수 있으므로, 태그를
  **네임스페이스 무시하고 local-name으로 매칭**해 버전 견고성을 확보한다.
- 새 의존성 없음: `lxml`은 프로젝트 주요 라이브러리, `zipfile`은 stdlib.
  pyhwp(hwp5proc)는 HWP 전용이라 HWPX엔 불필요.

대안 B(외부 HWPX 라이브러리)는 정확도/유지보수 불확실 + 출력 형식 후처리가
어차피 필요, C(정규식 스크래핑)는 표·중첩 구조에서 깨져 모두 기각.

## 모듈 구조

```
parsing/
  __init__.py
  hwpx_extractor.py
```

공개 인터페이스:

```python
def extract_hwpx(path: str | Path) -> tuple[str, dict]:
    """HWPX 파일을 (본문 텍스트, 이미지 registry)로 변환."""
```

내부 보조 함수(단일 책임으로 분리):

- `_read_bin_map(zf)` — `Contents/content.hpf` 파싱 → `{item_id: {href, mime}}`
- `_iter_section_names(zf)` — `Contents/section*.xml`을 번호 순 정렬로 반환
- `_para_text(p_el)` — 문단(`p`) 내 텍스트 런(`t`) 이어붙이기
- `_table_to_text(tbl_el, ...)` — 표(`tbl`)를 `[표]…[/표]`로 (중첩 재귀)
- `_local(el)` — 태그의 local-name 반환 헬퍼

## 파싱 흐름

1. `zipfile.ZipFile(path)`로 열기.
2. `_read_bin_map`으로 이미지 항목 id → 경로/MIME 매핑 구성.
3. `_iter_section_names`로 `section0.xml, section1.xml…` 번호 순 확보.
4. 각 섹션 root를 문서 순서대로 walk, local-name으로 분기:
   - `p`(문단) → 텍스트 런 이어붙이고 문단 끝에 `\n`.
   - `tbl`(표) → `_table_to_text`로 변환해 삽입.
   - `pic`/`img`(그림) → 등장 순서로 `img_001, img_002…` 부여, 본문에
     `[이미지:img_XXX]` 삽입, registry에 참조 기록.
5. 모든 섹션 결과를 이어 하나의 문자열로 반환.

## 요소별 출력 형식 (CLAUDE.md 통일)

- **문단 구분**: `\n`
- **표**: `[표]\n{행}\n[/표]`
  - 셀 구분: `" | "`, 행 구분: `"\n"`
  - 셀 텍스트 = 셀 내부 문단들의 텍스트를 공백/개행 정리해 하나로. 빈 셀은 `""`.
  - **중첩 표**: 셀 안에 표가 있으면 셀 텍스트 위치에 재귀적으로 `[표]…[/표]`.
- **이미지**: 본문에 `[이미지:img_XXX]` 플레이스홀더.

예시:

```
[표]
항목 | 내용 | 비고
금액 | 1,000원 | -
[/표]

[이미지:img_001]
```

## 이미지 registry (참조만, 바이트 추출 안 함)

본문의 `<img binaryItemIDRef="image1">`가 매니페스트(`content.hpf`)의 항목 id를
가리킨다. registry는 그 참조 정보만 담는다:

```python
registry = {
    "img_001": {"item_id": "image1", "path": "BinData/image1.png", "mime": "image/png"},
    "img_002": {"item_id": "image2", "path": "BinData/image2.jpg", "mime": "image/jpeg"},
}
```

- 순번 `img_XXX`는 **문서 전체를 통틀어** 등장 순서로 001부터.
- 매니페스트에서 경로를 못 찾으면 `path=None`, `mime=None`, `item_id`만 채운다.

## 에러 처리 (fail loud)

- 파일이 ZIP이 아니거나 손상 → `ValueError`(명확한 메시지).
- `Contents/section*.xml`이 하나도 없음 → `ValueError`.
- 개별 section XML 파싱 실패 → 예외 전파(조용히 건너뛰지 않음).
- `content.hpf`가 없거나 이미지 매핑 실패 → 본문 `[이미지:img_XXX]`는 유지하고
  registry 값은 채울 수 있는 만큼만 채운다(치명적 아님).

## 테스트 (TDD, pytest)

외부 샘플 문서(*.hwpx는 .gitignore 대상)에 의존하지 않도록, **테스트 안에서
최소 합성 HWPX(zip)** 를 만들어 검증한다. 픽스처 헬퍼가 `content.hpf` +
`section*.xml`을 담은 zip을 생성한다.

케이스:

1. 단순 문단 여러 개 → `\n`으로 구분된 텍스트.
2. 2×2 표 → `[표]…[/표]`, 셀 `" | "`, 행 `"\n"`.
3. 이미지 참조 → 본문 `[이미지:img_001]` + registry 항목(경로/MIME).
4. 다중 섹션 → section0, section1 순서 보존.
5. 중첩 표 → 셀 안 `[표]…[/표]`.
6. 빈 셀 → 빈 문자열로 유지.
7. 손상/비-zip 입력 → `ValueError`.
8. section 없는 zip → `ValueError`.

의존성: `lxml`, `zipfile`(stdlib), 테스트용 `pytest`. requirement.txt에
`lxml`, `pytest`가 없으면 추가한다.

## 향후 연계 (이 스펙 범위 밖)

- registry의 참조를 이용해 이미지 바이트 추출 + LLM 캡셔닝을 임베딩 파이프라인
  단계에서 수행.
- HWP/PDF 경로도 동일 통일 형식으로 정리해 `parsing/` 하위로 통합.
