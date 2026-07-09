import re
import json
from pathlib import Path

MAX_TOKENS = 512
MIN_TOKENS = 30        # 이보다 짧은 텍스트 청크는 다음 청크와 병합
OVERLAP_LINES = 2
CHARS_PER_TOKEN = 1.5  # 한국어 근사: 1글자 ≈ 1.5토큰 (BGE-M3 기준)
MAX_CHARS = 768         # MAX_TOKENS(512) * CHARS_PER_TOKEN(1.5) — 표/박스 잘림 기준(문자 단위)

_PAGE_SEP = re.compile(r'={10,}\n\d+페이지\n={10,}')
_INLINE_PAGE_NO = re.compile(r'\n-\s*\d+\s*-\n?')  # 문서 내 - N - 형태 페이지 번호
_TABLE = re.compile(r'\[표\][\s\S]*?\[/표\]')
_BOX = re.compile(r'\[박스\][\s\S]*?\[/박스\]')
_MAIN_SECTION = re.compile(r'(?m)^[0-9]+\.\s')
_SUB_SECTION = re.compile(
    r'(?m)^(?:[가나다라마바사아자차카타파하]\.|[0-9]+\.[0-9]+\.)\s'
)
_LINGUISTIC = re.compile(r'[가-힣a-zA-Z0-9]')


def _tok(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def chunk(text: str, source: str = "", dropped_out: list[str] | None = None) -> list[dict]:
    """dropped_out을 넘기면 드랍된(기호/구분선 위주라 언어적 내용이 없는) 잔재 청크의
    원문을 그 리스트에 추가한다(통계·눈검사용, 기본은 무시하고 안 쓸 수 있음)."""
    # 전처리: 페이지 구분자 및 본문 내 페이지 번호 제거
    text = _PAGE_SEP.sub('\n', text)
    text = _INLINE_PAGE_NO.sub('\n', text)

    # [표]/[박스] 원자 블록과 일반 텍스트로 분리
    raw = _extract_raw(text)

    chunks = []
    for item_text, item_type in raw:
        if item_type in ('table', 'box'):
            chunks.append(_build_block_chunk(item_text, item_type))
        else:
            chunks.extend(_split_text(item_text))

    # 짧은 청크 병합/드랍 후 빈 청크 제거 및 메타데이터 부여
    chunks, dropped = _merge_short_chunks(chunks)
    if dropped_out is not None:
        dropped_out.extend(dropped)

    result = []
    for i, c in enumerate(chunks):
        if c['text'].strip():
            entry = {
                'chunk_idx': i,
                'type': c['type'],
                'text': c['text'],
                'source': source,
            }
            if 'truncated' in c:
                entry['truncated'] = c['truncated']
                if c['truncated']:
                    entry['original_chars'] = c['original_chars']
                    entry['kept_chars'] = c['kept_chars']
            result.append(entry)

    return result


def _build_block_chunk(text: str, item_type: str) -> dict:
    """표/박스는 원자 블록이라 절대 안 쪼갠다 — 행/열 구조가 잘리면 의미가 깨지기 때문.
    다만 MAX_CHARS(768자, ≈512토큰)를 넘으면 임베딩 시 뒷부분이 어차피 잘려나간다
    (embedder.py의 max_length=512는 토크나이저가 앞부분만 남기고 뒷부분을 조용히
    버리는 hard truncation — 에러 없이 그냥 사라짐).

    표 내용의 축자 검색(모델명 등)은 SQL 영역, 벡터 검색은 의미 단위 담당 —
    따라서 대형 표는 존재·성격 전달용 앞부분만 임베딩한다. 잘림은 truncated
    메타로 추적한다(원문 전체는 여기서 버리지 않고 raw 데이터에 그대로 남아있으므로
    필요하면 SQL 등 다른 경로로 찾아갈 수 있다).
    """
    text = text.strip()
    if len(text) <= MAX_CHARS:
        return {'text': text, 'type': item_type, 'truncated': False}

    original_chars = len(text)
    body = _truncate_block_body(text)
    truncated_text = f'{body}\n[표 일부 — 전체 내용은 원문 참조]'
    return {
        'text': truncated_text,
        'type': item_type,
        'truncated': True,
        'original_chars': original_chars,
        'kept_chars': len(body),
    }


def _truncate_block_body(text: str) -> str:
    """MAX_CHARS 이내로, 마지막 완전한 행(줄) 기준으로 자른다(행 중간에서 안 끊음)."""
    lines = text.split('\n')
    kept: list[str] = []
    total = 0
    for line in lines:
        extra = len(line) + (1 if kept else 0)  # 이미 있는 줄과 합칠 개행 1자
        if kept and total + extra > MAX_CHARS:
            break
        kept.append(line)
        total += extra
    return '\n'.join(kept).rstrip()


def _extract_raw(text: str) -> list[tuple[str, str]]:
    """텍스트를 (내용, 타입) 순서 리스트로 분리."""
    result = []
    pos = 0

    specials = []
    for m in _TABLE.finditer(text):
        specials.append((m.start(), m.end(), 'table', m.group()))
    for m in _BOX.finditer(text):
        specials.append((m.start(), m.end(), 'box', m.group()))
    specials.sort(key=lambda x: x[0])

    for start, end, btype, bcontent in specials:
        if pos < start and text[pos:start].strip():
            result.append((text[pos:start], 'text'))
        result.append((bcontent, btype))
        pos = end

    if pos < len(text) and text[pos:].strip():
        result.append((text[pos:], 'text'))

    return result


def _split_at(text: str, boundaries: list[int]) -> list[str]:
    if not boundaries:
        return [text] if text.strip() else []

    parts = []
    if boundaries[0] > 0 and text[:boundaries[0]].strip():
        parts.append(text[:boundaries[0]])

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(text)
        part = text[start:end]
        if part.strip():
            parts.append(part)

    return parts


def _sliding_window(text: str) -> list[dict]:
    """토큰 제한 초과 시 슬라이딩 윈도우 (OVERLAP_LINES 오버랩)."""
    chunks = []
    lines = text.split('\n')
    current: list[str] = []
    current_tok = 0

    for line in lines:
        lt = _tok(line)
        if current_tok + lt > MAX_TOKENS and current:
            chunk_text = '\n'.join(current).strip()
            if chunk_text:
                chunks.append({'text': chunk_text, 'type': 'text'})
            current = current[-OVERLAP_LINES:]
            current_tok = sum(_tok(l) for l in current)
        current.append(line)
        current_tok += lt

    if current:
        chunk_text = '\n'.join(current).strip()
        if chunk_text:
            chunks.append({'text': chunk_text, 'type': 'text'})

    return chunks


def _has_linguistic_content(text: str) -> bool:
    """한글/영문/숫자가 하나도 없으면(기호·구분선 위주 잔재) 언어적 내용이 없다고 본다."""
    return bool(_LINGUISTIC.search(text))


def _merge_short_chunks(chunks: list[dict]) -> tuple[list[dict], list[str]]:
    """MIN_TOKENS 미만 텍스트 청크를 처리한다.

    - 기호/구분선 위주라 언어적 내용이 없는 잔재(예: 흐름도의 '|' 하나만 남은 조각)는
      드랍한다 — 살려둬도 임베딩 호출만 낭비되고 검색에 도움이 안 됨.
    - 언어적 내용이 있으면 우선 뒤따르는 텍스트 청크와 합치고(기존 동작), 그래도
      짧으면(다음이 표/박스라 못 합친 경우) 직전에 확정된 텍스트 청크에 붙인다
      — "가장 가까운 텍스트 청크"가 뒤에 없으면 앞으로 붙인다는 뜻.
    두 번째 반환값은 드랍된 청크의 원문 리스트(통계·눈검사용).
    """
    dropped: list[str] = []
    merged: list[dict] = []
    i = 0
    n = len(chunks)
    while i < n:
        c = dict(chunks[i])

        if c['type'] == 'text' and _tok(c['text']) < MIN_TOKENS and not _has_linguistic_content(c['text']):
            dropped.append(c['text'])
            i += 1
            continue

        # 짧은 텍스트 청크는 다음 텍스트 청크와 계속 합침
        while (
            c['type'] == 'text'
            and _tok(c['text']) < MIN_TOKENS
            and i + 1 < n
            and chunks[i + 1]['type'] == 'text'
        ):
            i += 1
            c['text'] = c['text'] + '\n' + chunks[i]['text']

        # 그래도 여전히 짧다면(뒤가 표/박스라 못 합쳤음) 직전 텍스트 청크에 붙인다
        if c['type'] == 'text' and _tok(c['text']) < MIN_TOKENS:
            prev_text = next((m for m in reversed(merged) if m['type'] == 'text'), None)
            if prev_text is not None:
                prev_text['text'] = prev_text['text'] + '\n' + c['text']
                i += 1
                continue

        merged.append(c)
        i += 1

    return merged, dropped


def _split_text(text: str) -> list[dict]:
    """일반 텍스트를 섹션 헤더 기준 → 토큰 제한 순으로 분리."""
    chunks = []

    # 1차: 주 섹션 (1. 2. 3. ...)
    main_bounds = [m.start() for m in _MAIN_SECTION.finditer(text)]
    sections = _split_at(text, main_bounds)

    for section in sections:
        if _tok(section) <= MAX_TOKENS:
            chunks.append({'text': section.strip(), 'type': 'text'})
            continue

        # 2차: 하위 항목 (가. 나. / 1.1. 1.2. ...)
        sub_bounds = [m.start() for m in _SUB_SECTION.finditer(section)]
        sub_sections = _split_at(section, sub_bounds)

        for sub in sub_sections:
            if _tok(sub) <= MAX_TOKENS:
                chunks.append({'text': sub.strip(), 'type': 'text'})
            else:
                # 3차: 슬라이딩 윈도우
                chunks.extend(_sliding_window(sub))

    return chunks


if __name__ == "__main__":
    txt_dir = Path(__file__).parent.parent / "data" / "sample" / "output" / "txt"
    for txt_file in sorted(txt_dir.glob("*.txt")):
        text = txt_file.read_text(encoding="utf-8")
        result = chunk(text, source=txt_file.name)
        print(f"\n{'='*60}")
        print(f"{txt_file.name}  →  {len(result)}개 청크")
        print(f"{'='*60}")
        for c in result[:5]:
            preview = c['text'][:80].replace('\n', ' ')
            print(f"  [{c['chunk_idx']:03d}] ({c['type']:5}) {preview}")
        if len(result) > 5:
            print(f"  ... 외 {len(result) - 5}개")
