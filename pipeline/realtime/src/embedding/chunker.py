import logging
import re

logger = logging.getLogger(__name__)

# embedding/chunker.py(experiments/embedding 로컬 검증본)에서 핵심 로직을 그대로
# 가져옴 — 로컬 검증(#54)이 끝난 코드라 로직은 변경하지 않는다. 이 파일이 새로 한
# 것은 Lambda 배선(common/config·s3·sqs 연동)뿐, __main__ 로컬 데모 블록(experiments/
# 경로 의존)만 제거했다. 로직을 바꿀 일이 생기면 원본(embedding/chunker.py)과
# experiments/embedding/ 재검증도 함께 고려할 것.
#
# 로그 레벨 원칙 — 이 구조는 Lambda로 승격돼도 CloudWatch로 그대로 이어진다.
# "사람이 봐야 하는 것은 WARNING 이상" — DEBUG/INFO는 정상 동작 기록이라 평소엔
# 안 봐도 되고, WARNING부터는 데이터 품질이나 설계 가정(조항 구조가 있을 것,
# 표가 청크 하나에 다 들어갈 것 등)이 깨졌다는 신호다.
#   DEBUG   — 분할·병합 세부 동작(섹션/하위항목 경계 수, 슬라이딩 윈도우 청크 생성 등)
#   INFO    — 문서 단위 요약(청크 수/dropped)
#   WARNING — 표/박스가 한도 초과로 여러 청크에 분할됨(원본 길이·분할 개수 — 데이터
#             손실은 없지만 한 표가 여러 벡터로 쪼개진다는 신호), 잔재 청크 드랍
#             (드랍된 원문 포함), 조항 인식 실패(구조 분할이 안 먹혀 슬라이딩
#             윈도우로 강제 폴백)

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


def chunk(text: str, source: str = "") -> list[dict]:
    """source는 로그(특히 WARNING)에 함께 찍혀서 여러 문서를 한 번에 처리할 때
    어느 문서에서 표가 잘렸는지/잔재가 드랍됐는지 추적하는 데 쓰인다."""
    # 전처리: 페이지 구분자 및 본문 내 페이지 번호 제거
    text = _PAGE_SEP.sub('\n', text)
    text = _INLINE_PAGE_NO.sub('\n', text)

    # [표]/[박스] 원자 블록과 일반 텍스트로 분리
    raw = _extract_raw(text)

    chunks = []
    for item_text, item_type in raw:
        if item_type in ('table', 'box'):
            chunks.extend(_split_block(item_text, item_type, source))
        else:
            chunks.extend(_split_text(item_text, source))

    # 짧은 청크 병합/드랍 후 빈 청크 제거 및 메타데이터 부여
    chunks, dropped_count = _merge_short_chunks(chunks, source)

    result = []
    for i, c in enumerate(chunks):
        if c['text'].strip():
            result.append({
                'chunk_idx': i,
                'type': c['type'],
                'text': c['text'],
                'source': source,
            })

    violation_count = _check_invariants(result, source)
    logger.info(
        "청킹 완료: source=%s chunks=%d dropped=%d 불변식위반=%d",
        source, len(result), dropped_count, violation_count,
    )

    return result


def _check_invariants(result: list[dict], source: str) -> int:
    """모든 청크가 MAX_CHARS 이내인지 확인한다. 표/박스도 이제 절단 없이 행 단위로
    분할하므로 원칙적으로 전부 한도 이내여야 한다 — 유일한 예외는 행 하나가 그
    자체로 MAX_CHARS를 넘는 경우(행을 반토막 낼 수 없어 불가피하게 그 행만
    단독 청크가 됨). 위반은 병합/분할 로직의 버그를 뜻하므로 WARNING으로 남긴다."""
    violations = 0
    for c in result:
        if len(c['text']) > MAX_CHARS:
            violations += 1
            logger.warning(
                "청크 길이 불변식 위반: source=%s chunk_idx=%d type=%s length=%d자(한도 %d자)",
                source, c['chunk_idx'], c['type'], len(c['text']), MAX_CHARS,
                extra={
                    'event': 'invariant_violation',
                    'source': source,
                    'chunk_idx': c['chunk_idx'],
                    'length': len(c['text']),
                },
            )
    return violations


def _split_block(text: str, item_type: str, source: str) -> list[dict]:
    """표/박스를 행(줄) 단위로 순차 분할한다 — 위에서부터 줄을 채우다가 다음 줄을
    더하면 MAX_CHARS를 넘는 지점에서 끊고 새 청크를 시작한다. 어떤 행도 중간에서
    잘리지 않는다(행 하나가 그 자체로 MAX_CHARS를 넘는 극단적인 경우만 예외 —
    그 행만 단독으로 초과 청크가 됨, 반토막 내는 것보다는 낫다).

    오버랩을 두지 않는다(텍스트 폴백용 _sliding_window와의 차이) — 표는 각 행이
    독립된 데이터라 경계에서 앞 청크 마지막 행을 다음 청크 앞에도 반복하면 같은
    행이 중복 매칭되기만 하고 문맥 보강 효과가 없다(프로즈와 다름).

    표는 원자 블록이 원칙이라 쪼개면 한 표가 여러 벡터로 나뉘어 개별 청크 하나가
    표 전체 맥락을 담지 못하게 된다는 트레이드오프가 있다. 다만 이전의 "앞부분만
    남기고 잘라버리는" 방식과 달리 전체 내용이 어떤 청크로든 보존되므로(데이터
    손실 없음), 모델명 등 축자 검색은 여전히 SQL 등 원문 조회가 더 정확하지만
    벡터 검색으로도 표의 어느 부분이든 찾을 수 있다.
    """
    text = text.strip()
    lines = text.split('\n')
    chunks = []
    current: list[str] = []
    for line in lines:
        candidate_len = len('\n'.join(current + [line]))
        if candidate_len > MAX_CHARS and current:
            chunk_text = '\n'.join(current).strip()
            if chunk_text:
                chunks.append({'text': chunk_text, 'type': item_type})
            current = []
        current.append(line)
    if current:
        chunk_text = '\n'.join(current).strip()
        if chunk_text:
            chunks.append({'text': chunk_text, 'type': item_type})

    if len(chunks) > 1:
        logger.warning(
            "표/박스 청크 분할: source=%s original=%d자 %d개 청크로 분할",
            source, len(text), len(chunks),
            extra={
                'event': 'block_split',
                'source': source,
                'original_chars': len(text),
                'chunk_count': len(chunks),
            },
        )
    return chunks


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


def _sliding_window(text: str, source: str) -> list[dict]:
    """MAX_CHARS 초과 시 슬라이딩 윈도우 (OVERLAP_LINES 오버랩).

    자를지 판단은 실제 결합 문자 길이(len('\\n'.join(...)))로 한다 — MAX_CHARS는
    문자 단위 한도인데 토큰 추정치(_tok, CHARS_PER_TOKEN 근사)로 판단하면 환산
    오차가 구조적으로 남는다. 실제로 숫자 하나씩 한 줄인 표(줄바꿈만 수백 개,
    줄 자체는 짧음)에서 토큰 추정은 한도 안인데 줄바꿈이 누적되며 문자 수가
    1125자까지 새는 사례가 있었다. 토큰 수(current_tok)는 로그 참고용으로만 남긴다.
    """
    chunks = []
    lines = text.split('\n')
    current: list[str] = []
    current_tok = 0

    for line in lines:
        candidate_len = len('\n'.join(current + [line]))
        if candidate_len > MAX_CHARS and current:
            chunk_text = '\n'.join(current).strip()
            if chunk_text:
                chunks.append({'text': chunk_text, 'type': 'text'})
                logger.debug(
                    "슬라이딩 윈도우 청크 생성: source=%s tokens=%d chars=%d",
                    source, current_tok, len(chunk_text),
                )
            current = current[-OVERLAP_LINES:]
            current_tok = sum(_tok(l) for l in current)
        current.append(line)
        current_tok += _tok(line)

    if current:
        chunk_text = '\n'.join(current).strip()
        if chunk_text:
            chunks.append({'text': chunk_text, 'type': 'text'})
            logger.debug(
                "슬라이딩 윈도우 마지막 청크: source=%s tokens=%d chars=%d",
                source, current_tok, len(chunk_text),
            )

    return chunks


def _has_linguistic_content(text: str) -> bool:
    """한글/영문/숫자가 하나도 없으면(기호·구분선 위주 잔재) 언어적 내용이 없다고 본다."""
    return bool(_LINGUISTIC.search(text))


def _merge_short_chunks(chunks: list[dict], source: str) -> tuple[list[dict], int]:
    """MIN_TOKENS 미만 텍스트 청크를 처리한다.

    - 기호/구분선 위주라 언어적 내용이 없는 잔재(예: 흐름도의 '|' 하나만 남은 조각)는
      드랍한다 — 살려둬도 임베딩 호출만 낭비되고 검색에 도움이 안 됨. 드랍된 원문은
      WARNING 로그(event=chunk_dropped)로 남긴다 — 별도 통계 파일 대신 로그에서 추출.
    - 언어적 내용이 있으면 우선 뒤따르는 텍스트 청크와 합치고(기존 동작), 그래도
      짧으면(다음이 표/박스라 못 합친 경우) 직전에 확정된 텍스트 청크에 붙인다
      — "가장 가까운 텍스트 청크"가 뒤에 없으면 앞으로 붙인다는 뜻.
    - 병합은 결과가 MAX_CHARS를 넘지 않을 때만 실행한다(내용 손실 금지 원칙 —
      "짧아서 병합"이 "한도 초과로 잘림"을 낳으면 병합의 취지가 무너짐). 한쪽 방향이
      한도를 넘으면 반대 방향을 시도하고, 양쪽 다 넘으면 병합을 포기하고 짧은
      청크를 그대로 독립 청크로 남긴다.
    두 번째 반환값은 드랍된 청크 수(원문은 로그에서 확인).
    """
    dropped_count = 0
    merged: list[dict] = []
    i = 0
    n = len(chunks)
    while i < n:
        c = dict(chunks[i])

        if c['type'] == 'text' and _tok(c['text']) < MIN_TOKENS and not _has_linguistic_content(c['text']):
            logger.warning(
                "잔재 청크 드랍: source=%s text=%r",
                source, c['text'],
                extra={'event': 'chunk_dropped', 'source': source, 'dropped_text': c['text']},
            )
            dropped_count += 1
            i += 1
            continue

        # 짧은 텍스트 청크는 다음 텍스트 청크와 계속 합침 — 단, 합친 결과가
        # MAX_CHARS를 넘으면 그 병합은 취소하고(다음 청크는 소비하지 않고) 멈춘다.
        while c['type'] == 'text' and _tok(c['text']) < MIN_TOKENS and i + 1 < n and chunks[i + 1]['type'] == 'text':
            candidate = c['text'] + '\n' + chunks[i + 1]['text']
            if len(candidate) > MAX_CHARS:
                break
            i += 1
            logger.debug("짧은 청크를 다음 텍스트 청크와 병합: source=%s", source)
            c['text'] = candidate

        # 그래도 여전히 짧다면(뒤가 표/박스라 못 합쳤거나, 정방향 병합이 한도 초과로
        # 취소됐음) 직전 텍스트 청크에 붙여본다 — 이것도 한도를 넘으면 포기.
        if c['type'] == 'text' and _tok(c['text']) < MIN_TOKENS:
            prev_text = next((m for m in reversed(merged) if m['type'] == 'text'), None)
            if prev_text is not None:
                candidate = prev_text['text'] + '\n' + c['text']
                if len(candidate) <= MAX_CHARS:
                    logger.debug("짧은 청크를 직전 텍스트 청크에 병합: source=%s", source)
                    prev_text['text'] = candidate
                    i += 1
                    continue
                logger.debug(
                    "짧은 청크 병합 불가(양방향 모두 한도 초과) — 독립 청크로 유지: source=%s",
                    source,
                )

        merged.append(c)
        i += 1

    return merged, dropped_count


def _split_text(text: str, source: str) -> list[dict]:
    """일반 텍스트를 섹션 헤더 기준 → 토큰 제한 순으로 분리."""
    chunks = []

    # 1차: 주 섹션 (1. 2. 3. ...)
    main_bounds = [m.start() for m in _MAIN_SECTION.finditer(text)]
    sections = _split_at(text, main_bounds)
    logger.debug("주 섹션 분할: source=%s 섹션 경계=%d개", source, len(main_bounds))

    for section in sections:
        if _tok(section) <= MAX_TOKENS:
            chunks.append({'text': section.strip(), 'type': 'text'})
            continue

        # 2차: 하위 항목 (가. 나. / 1.1. 1.2. ...)
        sub_bounds = [m.start() for m in _SUB_SECTION.finditer(section)]
        sub_sections = _split_at(section, sub_bounds)
        logger.debug("하위 항목 분할: source=%s 하위 경계=%d개", source, len(sub_bounds))

        for sub in sub_sections:
            if _tok(sub) <= MAX_TOKENS:
                chunks.append({'text': sub.strip(), 'type': 'text'})
            else:
                if not sub_bounds:
                    # 하위 항목 경계도 못 찾았는데(가.나.다. / 1.1. 1.2. 같은 패턴이
                    # 전혀 없음) 여전히 MAX_TOKENS 초과 — 조항 구조를 아예 못 찾아서
                    # 슬라이딩 윈도우로 강제 폴백하는 것이므로 사람이 봐야 함.
                    logger.warning(
                        "조항 인식 실패 — 슬라이딩 윈도우로 폴백: source=%s length=%d자 preview=%r",
                        source, len(sub), sub[:80],
                        extra={
                            'event': 'section_split_failed',
                            'source': source,
                            'length': len(sub),
                        },
                    )
                # 3차: 슬라이딩 윈도우
                chunks.extend(_sliding_window(sub, source))

    return chunks
