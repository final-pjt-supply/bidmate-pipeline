import re
import json
from pathlib import Path

MAX_TOKENS = 512
MIN_TOKENS = 30        # 이보다 짧은 텍스트 청크는 다음 청크와 병합
OVERLAP_LINES = 2
CHARS_PER_TOKEN = 1.5  # 한국어 근사: 1글자 ≈ 1.5토큰 (BGE-M3 기준)

_PAGE_SEP = re.compile(r'={10,}\n\d+페이지\n={10,}')
_INLINE_PAGE_NO = re.compile(r'\n-\s*\d+\s*-\n?')  # 문서 내 - N - 형태 페이지 번호
_TABLE = re.compile(r'\[표\][\s\S]*?\[/표\]')
_BOX = re.compile(r'\[박스\][\s\S]*?\[/박스\]')
_MAIN_SECTION = re.compile(r'(?m)^[0-9]+\.\s')
_SUB_SECTION = re.compile(
    r'(?m)^(?:[가나다라마바사아자차카타파하]\.|[0-9]+\.[0-9]+\.)\s'
)


def _tok(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def chunk(text: str, source: str = "") -> list[dict]:
    # 전처리: 페이지 구분자 및 본문 내 페이지 번호 제거
    text = _PAGE_SEP.sub('\n', text)
    text = _INLINE_PAGE_NO.sub('\n', text)

    # [표]/[박스] 원자 블록과 일반 텍스트로 분리
    raw = _extract_raw(text)

    chunks = []
    for item_text, item_type in raw:
        if item_type in ('table', 'box'):
            chunks.append({'text': item_text.strip(), 'type': item_type})
        else:
            chunks.extend(_split_text(item_text))

    # 짧은 청크 병합 후 빈 청크 제거 및 메타데이터 부여
    chunks = _merge_short_chunks(chunks)
    result = []
    for i, c in enumerate(chunks):
        if c['text'].strip():
            result.append({
                'chunk_idx': i,
                'type': c['type'],
                'text': c['text'],
                'source': source,
            })

    return result


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


def _merge_short_chunks(chunks: list[dict]) -> list[dict]:
    """MIN_TOKENS 미만 텍스트 청크를 다음 텍스트 청크와 병합."""
    merged = []
    i = 0
    while i < len(chunks):
        c = dict(chunks[i])
        # 짧은 텍스트 청크는 다음 텍스트 청크와 계속 합침
        while (
            c['type'] == 'text'
            and _tok(c['text']) < MIN_TOKENS
            and i + 1 < len(chunks)
            and chunks[i + 1]['type'] == 'text'
        ):
            i += 1
            c['text'] = c['text'] + '\n' + chunks[i]['text']
        merged.append(c)
        i += 1
    return merged


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
