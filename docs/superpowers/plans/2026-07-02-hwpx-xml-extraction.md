# HWPX 내부 XML 추출 모듈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HWPX(OWPML) 파일의 내부 XML을 직접 파싱해 본문·표·이미지 참조를 CLAUDE.md 통일 텍스트 형식으로 변환하는 `parsing/` 정식 모듈을 만든다.

**Architecture:** `zipfile`로 HWPX(zip)를 열고 `Contents/section*.xml`을 번호 순으로 `lxml`로 순회한다. 네임스페이스 버전차에 견고하도록 태그는 **local-name**으로 매칭한다. 상태(이미지 순번·registry)를 가진 `_Extractor`가 문단·표·이미지를 재귀적으로 렌더링한다.

**Tech Stack:** Python 3.14, lxml, zipfile(stdlib), pytest.

## Global Constraints

- Python 3.14. 새 런타임 의존성은 `lxml`만 추가(이미 프로젝트 주요 라이브러리), 테스트용 `pytest` 추가.
- 이미지 **바이트 추출/캡셔닝은 하지 않는다**. registry에 참조(item_id/path/mime)만 기록.
- 출력 형식(CLAUDE.md 통일): 표 = `[표]\n{행}\n[/표]`, 셀 구분 `" | "`, 행 구분 `"\n"`; 이미지 = `[이미지:img_XXX]`(XXX는 3자리 0패딩, 문서 전체 등장 순서); 문단 구분 `"\n"`.
- 태그 매칭은 네임스페이스 무시하고 **local-name**으로만 한다.
- 에러는 fail loud: 잘못된 입력/구조는 `ValueError`로 명확히 던진다.
- 커밋 메시지는 `Type : 설명`(한국어) 컨벤션. 커밋 말미에 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- Create: `parsing/__init__.py` — 패키지 마커, `extract_hwpx` 재노출.
- Create: `parsing/hwpx_extractor.py` — 추출 로직 전체(단일 책임: HWPX→(text, registry)).
- Create: `tests/parsing/test_hwpx_extractor.py` — 합성 HWPX 픽스처 + 단위 테스트.
- Modify: `requirement.txt` — `lxml`, `pytest` 추가.

**공개 인터페이스**

```python
def extract_hwpx(path) -> tuple[str, dict]:
    """HWPX 파일 경로를 받아 (본문 텍스트, 이미지 registry) 반환."""
```

`registry` 형태: `{"img_001": {"item_id": str|None, "path": str|None, "mime": str|None}, ...}`

---

### Task 1: 패키지 스캐폴드 + 의존성 + 단순 문단 추출

**Files:**
- Create: `parsing/__init__.py`
- Create: `parsing/hwpx_extractor.py`
- Create: `tests/parsing/test_hwpx_extractor.py`
- Modify: `requirement.txt`

**Interfaces:**
- Produces: `extract_hwpx(path) -> (str, dict)`; 헬퍼 `_local(el) -> str`, `_iter_section_names(zf) -> list[str]`, `_read_bin_map(zf) -> dict`; 클래스 `_Extractor(bin_map)` with `render_section(root)`, `_runs_to_text(el) -> str`, 속성 `parts: list[str]`, `registry: dict`, `img_seq: int`.
- 테스트 헬퍼 `make_hwpx(path, sections, image_items=None)` 및 상수 `SEC_TMPL` (같은 테스트 파일에 정의, 이후 모든 Task가 사용).

- [ ] **Step 1: `requirement.txt`에 의존성 추가**

파일 전체를 다음으로 만든다:

```
pywin32
lxml
pytest
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/parsing/test_hwpx_extractor.py` 생성:

```python
# -*- coding: utf-8 -*-
import zipfile

import pytest

from parsing.hwpx_extractor import extract_hwpx

SEC_TMPL = (
    '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
    ' xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    ' xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">{body}</hs:sec>'
)


def make_hwpx(path, sections, image_items=None):
    """합성 HWPX(zip) 생성.

    sections: {section번호(int): 섹션 본문 XML(str)}
    image_items: [(item_id, href, media_type), ...] → content.hpf 매니페스트 항목
    """
    items = "".join(
        f'<opf:item id="{i}" href="{h}" media-type="{m}"/>'
        for i, h, m in (image_items or [])
    )
    hpf = (
        '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/">'
        f"<opf:manifest>{items}</opf:manifest></opf:package>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/content.hpf", hpf)
        for idx, body in sections.items():
            z.writestr(f"Contents/section{idx}.xml", SEC_TMPL.format(body=body))


def _para(text):
    return f"<hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>"


def test_simple_paragraphs(tmp_path):
    hwpx = tmp_path / "a.hwpx"
    make_hwpx(hwpx, {0: _para("첫째 줄") + _para("둘째 줄")})

    text, registry = extract_hwpx(hwpx)

    assert text == "첫째 줄\n둘째 줄"
    assert registry == {}
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py::test_simple_paragraphs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'parsing'`

- [ ] **Step 4: 최소 구현**

`parsing/__init__.py` 생성:

```python
from .hwpx_extractor import extract_hwpx

__all__ = ["extract_hwpx"]
```

`parsing/hwpx_extractor.py` 생성:

```python
# -*- coding: utf-8 -*-
"""HWPX(OWPML) 내부 XML을 직접 파싱해 통일 텍스트 형식으로 추출."""
import re
import zipfile
from pathlib import Path

from lxml import etree

_SECTION_RE = re.compile(r"Contents/section(\d+)\.xml$")


def _local(el):
    """네임스페이스를 무시한 태그 local-name."""
    return etree.QName(el).localname


def _iter_section_names(zf):
    """Contents/sectionN.xml 이름들을 반환(정렬은 Task 2에서 개선)."""
    return sorted(n for n in zf.namelist() if _SECTION_RE.fullmatch(n))


def _read_bin_map(zf):
    """content.hpf → {item_id: {'path': href, 'mime': media-type}} (이미지 항목만)."""
    try:
        data = zf.read("Contents/content.hpf")
    except KeyError:
        return {}
    root = etree.fromstring(data)
    bin_map = {}
    for el in root.iter():
        if _local(el) != "item":
            continue
        mime = el.get("media-type") or ""
        item_id = el.get("id")
        if item_id and mime.startswith("image/"):
            bin_map[item_id] = {"path": el.get("href"), "mime": mime}
    return bin_map


class _Extractor:
    def __init__(self, bin_map):
        self.bin_map = bin_map
        self.img_seq = 0
        self.registry = {}
        self.parts = []

    def render_section(self, root):
        """섹션 root의 최상위 문단(p)들을 순서대로 렌더링."""
        for child in root:
            if _local(child) == "p":
                self.parts.append(self._runs_to_text(child))
                self.parts.append("\n")

    def _runs_to_text(self, el):
        """문단/런 내부의 인라인 텍스트를 재귀적으로 모은다."""
        out = []
        for child in el:
            if _local(child) == "t":
                out.append("".join(child.itertext()))
            else:
                out.append(self._runs_to_text(child))
        return "".join(out)


def extract_hwpx(path):
    """HWPX 파일을 (본문 텍스트, 이미지 registry)로 변환."""
    path = Path(path)
    zf = zipfile.ZipFile(path)
    with zf:
        section_names = _iter_section_names(zf)
        ex = _Extractor(_read_bin_map(zf))
        for name in section_names:
            ex.render_section(etree.fromstring(zf.read(name)))
    return "".join(ex.parts).strip(), ex.registry
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py::test_simple_paragraphs -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add requirement.txt parsing/__init__.py parsing/hwpx_extractor.py tests/parsing/test_hwpx_extractor.py
git commit -m "Feat : HWPX 문단 텍스트 추출 모듈 스캐폴드

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 다중 섹션 번호 순 정렬

**Files:**
- Modify: `parsing/hwpx_extractor.py` (`_iter_section_names`)
- Test: `tests/parsing/test_hwpx_extractor.py`

**Interfaces:**
- Consumes: Task 1의 `make_hwpx`, `_para`, `extract_hwpx`.
- Produces: `_iter_section_names`가 section 번호를 **정수 기준**으로 정렬(section10이 section2보다 뒤).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/parsing/test_hwpx_extractor.py`에 추가:

```python
def test_sections_ordered_numerically(tmp_path):
    hwpx = tmp_path / "multi.hwpx"
    # 0,1,2,10 → 사전식 정렬이면 0,1,10,2 로 뒤섞인다
    make_hwpx(hwpx, {i: _para(f"S{i}") for i in (0, 1, 2, 10)})

    text, _ = extract_hwpx(hwpx)

    assert text == "S0\nS1\nS2\nS10"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py::test_sections_ordered_numerically -v`
Expected: FAIL — 순서가 `S0\nS1\nS10\nS2` (사전식 정렬)

- [ ] **Step 3: 최소 구현**

`_iter_section_names`를 정수 정렬로 교체:

```python
def _iter_section_names(zf):
    """Contents/sectionN.xml 이름들을 N(정수) 오름차순으로 반환."""
    found = []
    for name in zf.namelist():
        m = _SECTION_RE.fullmatch(name)
        if m:
            found.append((int(m.group(1)), name))
    return [name for _, name in sorted(found)]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py -v`
Expected: PASS (test_simple_paragraphs, test_sections_ordered_numerically)

- [ ] **Step 5: 커밋**

```bash
git add parsing/hwpx_extractor.py tests/parsing/test_hwpx_extractor.py
git commit -m "Feat : HWPX 다중 섹션 번호 순 정렬

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 표 추출 ([표]…[/표], 셀 구분, 빈 셀)

**Files:**
- Modify: `parsing/hwpx_extractor.py` (`_runs_to_text`에 `tbl` 분기 + `_table_to_text`, `_cell_text` 추가)
- Test: `tests/parsing/test_hwpx_extractor.py`

**Interfaces:**
- Produces: `_Extractor._table_to_text(tbl) -> str`, `_Extractor._cell_text(tc) -> str`. `_runs_to_text`가 `tbl` 자식을 만나면 `_table_to_text` 결과를 삽입하고 그 하위로는 일반 재귀하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/parsing/test_hwpx_extractor.py`에 헬퍼와 테스트 추가:

```python
def _cell(text):
    inner = _para(text) if text else "<hp:p></hp:p>"
    return f"<hp:tc><hp:subList>{inner}</hp:subList></hp:tc>"


def _row(*cells):
    return "<hp:tr>" + "".join(_cell(c) for c in cells) + "</hp:tr>"


def _table(*rows):
    return "<hp:p><hp:run><hp:tbl>" + "".join(rows) + "</hp:tbl></hp:run></hp:p>"


def test_table_with_empty_cell(tmp_path):
    hwpx = tmp_path / "t.hwpx"
    body = _table(_row("항목", "내용"), _row("금액", ""))
    make_hwpx(hwpx, {0: body})

    text, _ = extract_hwpx(hwpx)

    assert text == "[표]\n항목 | 내용\n금액 | \n[/표]"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py::test_table_with_empty_cell -v`
Expected: FAIL — 표 마커 없이 셀 텍스트가 이어붙어 `"항목내용금액"` 형태

- [ ] **Step 3: 최소 구현**

`_runs_to_text`의 분기에 `tbl`을 추가:

```python
    def _runs_to_text(self, el):
        """문단/런 내부의 인라인 텍스트를 재귀적으로 모은다."""
        out = []
        for child in el:
            tag = _local(child)
            if tag == "t":
                out.append("".join(child.itertext()))
            elif tag == "tbl":
                out.append(self._table_to_text(child))
            else:
                out.append(self._runs_to_text(child))
        return "".join(out)
```

`_Extractor`에 메서드 추가:

```python
    def _table_to_text(self, tbl):
        rows = []
        for tr in tbl:
            if _local(tr) != "tr":
                continue
            cells = []
            for tc in tr:
                if _local(tc) != "tc":
                    continue
                cells.append(self._cell_text(tc))
            rows.append(" | ".join(cells))
        return "[표]\n" + "\n".join(rows) + "\n[/표]"

    def _cell_text(self, tc):
        sub = next((c for c in tc if _local(c) == "subList"), None)
        if sub is None:
            return ""
        paras = []
        for p in sub:
            if _local(p) == "p":
                t = self._runs_to_text(p).strip()
                if t:
                    paras.append(t)
        return " ".join(paras)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py -v`
Expected: PASS (3개 테스트)

- [ ] **Step 5: 커밋**

```bash
git add parsing/hwpx_extractor.py tests/parsing/test_hwpx_extractor.py
git commit -m "Feat : HWPX 표 추출([표] 형식) 및 빈 셀 처리

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 중첩 표

**Files:**
- Test: `tests/parsing/test_hwpx_extractor.py` (신규 테스트만; 구현은 Task 3 재귀로 이미 지원)

**Interfaces:**
- Consumes: Task 3의 `_table`, `_row`, `_cell`(단, 셀 안에 표를 넣으려면 raw XML을 직접 구성).

- [ ] **Step 1: 실패하는(또는 회귀 방지) 테스트 작성**

`tests/parsing/test_hwpx_extractor.py`에 추가:

```python
def test_nested_table(tmp_path):
    hwpx = tmp_path / "nested.hwpx"
    inner = "<hp:tbl>" + _row("안") + "</hp:tbl>"
    # 바깥 표의 단일 셀 subList 문단 안에 표를 넣는다
    outer_cell = f"<hp:tc><hp:subList><hp:p><hp:run>{inner}</hp:run></hp:p></hp:subList></hp:tc>"
    body = f"<hp:p><hp:run><hp:tbl><hp:tr>{outer_cell}</hp:tr></hp:tbl></hp:run></hp:p>"
    make_hwpx(hwpx, {0: body})

    text, _ = extract_hwpx(hwpx)

    assert text == "[표]\n[표]\n안\n[/표]\n[/표]"
```

- [ ] **Step 2: 테스트 실행(통과 확인)**

Run: `pytest tests/parsing/test_hwpx_extractor.py::test_nested_table -v`
Expected: PASS — `_cell_text`가 셀 문단을 `_runs_to_text`로 처리하고, 그 안의 `tbl`이 다시 `_table_to_text`로 렌더링되어 중첩 `[표]`가 생성됨.

> 이 Task는 Task 3의 재귀 설계를 고정(회귀 방지)하는 테스트다. 만약 FAIL이면 Task 3의 `_cell_text`가 `_runs_to_text`를 쓰는지 확인하고 수정한다.

- [ ] **Step 3: 커밋**

```bash
git add tests/parsing/test_hwpx_extractor.py
git commit -m "Test : HWPX 중첩 표 처리 회귀 테스트 추가

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 이미지 플레이스홀더 + registry

**Files:**
- Modify: `parsing/hwpx_extractor.py` (`_runs_to_text`에 `pic` 분기 + `_image_placeholder`)
- Test: `tests/parsing/test_hwpx_extractor.py`

**Interfaces:**
- Produces: `_Extractor._image_placeholder(pic) -> str`. `_runs_to_text`가 `pic`을 만나면 `[이미지:img_XXX]`를 반환하고 하위로 재귀하지 않는다. registry에 `{item_id, path, mime}` 기록. 매핑 실패 시 `path=None, mime=None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/parsing/test_hwpx_extractor.py`에 추가:

```python
def _pic(ref):
    return f'<hp:p><hp:run><hp:pic><hc:img binaryItemIDRef="{ref}"/></hp:pic></hp:run></hp:p>'


def test_image_placeholder_and_registry(tmp_path):
    hwpx = tmp_path / "img.hwpx"
    make_hwpx(
        hwpx,
        {0: _para("위") + _pic("image1") + _para("아래")},
        image_items=[("image1", "BinData/image1.png", "image/png")],
    )

    text, registry = extract_hwpx(hwpx)

    assert text == "위\n[이미지:img_001]\n아래"
    assert registry == {
        "img_001": {
            "item_id": "image1",
            "path": "BinData/image1.png",
            "mime": "image/png",
        }
    }


def test_image_without_manifest_mapping(tmp_path):
    hwpx = tmp_path / "img2.hwpx"
    make_hwpx(hwpx, {0: _pic("ghost")})  # content.hpf에 매핑 없음

    text, registry = extract_hwpx(hwpx)

    assert text == "[이미지:img_001]"
    assert registry == {
        "img_001": {"item_id": "ghost", "path": None, "mime": None}
    }
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py::test_image_placeholder_and_registry -v`
Expected: FAIL — 플레이스홀더 미생성(빈 문자열), registry 비어 있음

- [ ] **Step 3: 최소 구현**

`_runs_to_text`의 분기에 `pic` 추가:

```python
    def _runs_to_text(self, el):
        """문단/런 내부의 인라인 텍스트를 재귀적으로 모은다."""
        out = []
        for child in el:
            tag = _local(child)
            if tag == "t":
                out.append("".join(child.itertext()))
            elif tag == "tbl":
                out.append(self._table_to_text(child))
            elif tag == "pic":
                out.append(self._image_placeholder(child))
            else:
                out.append(self._runs_to_text(child))
        return "".join(out)
```

`_Extractor`에 메서드 추가:

```python
    def _image_placeholder(self, pic):
        ref = None
        for el in pic.iter():
            if _local(el) == "img" and el.get("binaryItemIDRef"):
                ref = el.get("binaryItemIDRef")
                break
        self.img_seq += 1
        key = f"img_{self.img_seq:03d}"
        info = self.bin_map.get(ref, {})
        self.registry[key] = {
            "item_id": ref,
            "path": info.get("path"),
            "mime": info.get("mime"),
        }
        return f"[이미지:{key}]"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add parsing/hwpx_extractor.py tests/parsing/test_hwpx_extractor.py
git commit -m "Feat : HWPX 이미지 플레이스홀더 및 registry 기록

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 에러 처리 (fail loud)

**Files:**
- Modify: `parsing/hwpx_extractor.py` (`extract_hwpx` 예외 처리)
- Test: `tests/parsing/test_hwpx_extractor.py`

**Interfaces:**
- Produces: `extract_hwpx`가 (a) zip이 아니거나 손상, (b) section이 하나도 없을 때 `ValueError`를 던진다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/parsing/test_hwpx_extractor.py`에 추가:

```python
def test_non_zip_raises(tmp_path):
    bad = tmp_path / "bad.hwpx"
    bad.write_text("이건 zip이 아님", encoding="utf-8")

    with pytest.raises(ValueError):
        extract_hwpx(bad)


def test_zip_without_section_raises(tmp_path):
    empty = tmp_path / "empty.hwpx"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr(
            "Contents/content.hpf",
            '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/">'
            "<opf:manifest/></opf:package>",
        )

    with pytest.raises(ValueError):
        extract_hwpx(empty)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py::test_non_zip_raises tests/parsing/test_hwpx_extractor.py::test_zip_without_section_raises -v`
Expected: FAIL — 각각 `BadZipFile`이 그대로 전파되거나, section 없는 경우 빈 문자열을 반환(ValueError 미발생)

- [ ] **Step 3: 최소 구현**

`extract_hwpx`를 예외 처리 포함으로 교체:

```python
def extract_hwpx(path):
    """HWPX 파일을 (본문 텍스트, 이미지 registry)로 변환."""
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError(f"유효한 HWPX(zip) 파일이 아닙니다: {path} ({e})")
    with zf:
        section_names = _iter_section_names(zf)
        if not section_names:
            raise ValueError(f"HWPX에 Contents/section*.xml이 없습니다: {path}")
        ex = _Extractor(_read_bin_map(zf))
        for name in section_names:
            ex.render_section(etree.fromstring(zf.read(name)))
    return "".join(ex.parts).strip(), ex.registry
```

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `pytest tests/parsing/test_hwpx_extractor.py -v`
Expected: PASS (전체 8개 테스트)

- [ ] **Step 5: 커밋**

```bash
git add parsing/hwpx_extractor.py tests/parsing/test_hwpx_extractor.py
git commit -m "Feat : HWPX 잘못된 입력/구조 ValueError 처리

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 실제 샘플로 스모크 확인 (선택, 코드 변경 없음)

**Files:** 없음 (수동 검증)

- [ ] **Step 1: 실제 HWPX로 동작 확인**

로컬에 샘플이 있으면(예: `C:\Users\Administrator\Downloads\*.hwpx`) 다음을 실행해 표/문단이 통일 형식으로 나오는지 눈으로 확인한다. 샘플은 데이터라 커밋하지 않는다.

Run:
```bash
python -c "import glob; from parsing.hwpx_extractor import extract_hwpx; f=glob.glob(r'C:\Users\Administrator\Downloads\*.hwpx')[0]; t,r=extract_hwpx(f); import sys; sys.stdout.reconfigure(encoding='utf-8'); print(t[:800]); print('---IMAGES---', r)"
```

Expected: 문단 텍스트와 `[표]…[/표]` 블록이 보이고, 이미지가 있으면 `[이미지:img_XXX]` + registry 출력. 깨진 부분이 있으면 이슈로 기록.

---

## Self-Review

**1. Spec coverage:**
- 모듈 위치 `parsing/` → Task 1 ✓
- 통일 출력 형식(문단/표/이미지) → Task 1(문단)/3(표)/5(이미지) ✓
- 이미지 참조만 기록(바이트 추출 없음) → Task 5 ✓
- local-name 매칭 → `_local`, 전 Task ✓
- 다중 섹션 순서 → Task 2 ✓
- 중첩 표 → Task 4 ✓
- 빈 셀 → Task 3 ✓
- fail loud(비-zip/section 없음) → Task 6 ✓
- 라이브러리 함수만(CLI 없음) → 공개 API는 `extract_hwpx`뿐 ✓
- 의존성 lxml/pytest → Task 1 requirement.txt ✓
- 테스트에서 합성 HWPX(외부 샘플 비의존) → `make_hwpx` ✓

**2. Placeholder scan:** 각 Step에 실제 코드/명령/기대값 포함, TBD 없음 ✓

**3. Type consistency:** `_local`, `_iter_section_names`, `_read_bin_map`, `_Extractor.{render_section,_runs_to_text,_table_to_text,_cell_text,_image_placeholder}`, `extract_hwpx` 시그니처가 Task 전반에서 일치. registry 값 키(`item_id`/`path`/`mime`)가 Task 5 구현과 테스트에서 일치 ✓
