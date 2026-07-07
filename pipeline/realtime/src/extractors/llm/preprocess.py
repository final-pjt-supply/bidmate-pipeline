# -*- coding: utf-8 -*-
"""문서 전처리: 붙임/서식 페이지 제거 + 참가자격 관련 섹션 선별.

문서 전문을 필터 없이 통짜로 넣으면(1차 스펙) 청렴계약·서약서·불공정행위 조항 같은
보일러플레이트가 너무 많아, 모델이 실제 참가자격 조항을 놓치는 경향이 관찰됐다
(특히 페이지 수가 많은 공사 유형에서 심함). 그래서
1) 첫 붙임/별첨/서식 페이지부터는 잘라내고
2) 참가자격·낙찰기준 관련 키워드가 있는 페이지만 남기는 선별을 추가로 적용한다.

과도한 필터링으로 실제 정보를 통째로 날릴 위험이 있어, 선별 결과가 비면(키워드가
하나도 안 걸리면) 원본을 그대로 반환하는 안전장치를 둔다.
"""
import re

_APPENDIX_START_RE = re.compile(
    r"^\s*(\[붙임|\[별첨|\[부록|\[서식|붙임\s*\d|별첨\s*\d|부록\s*\d|서식\s*\d|【붙임|【별첨|【서식)"
)

_QUALIFICATION_KEYWORDS = (
    "참가자격", "입찰참가자격", "견적제출 참가자격", "제안입찰 참가자격",
    "공동계약", "공동수급", "공동도급", "하도급",
    "낙찰자 결정", "낙찰자 선정", "적격심사", "협상에 의한 계약",
    "지역제한", "지역 제한", "본점소재지", "본점 소재지",
    "면허", "등록규정", "업종코드", "세부품명번호",
    "실적", "기술인력", "인증", "배점", "평점",
)


def remove_appendix_pages(pages: list[dict]) -> list[dict]:
    """첫 붙임/별첨/서식 페이지 이후는 전부 잘라낸다(그 이전 페이지만 유지)."""
    kept = []
    for page in pages:
        if _APPENDIX_START_RE.match(page["text"].lstrip()):
            break
        kept.append(page)
    return kept or pages  # 1페이지부터 붙임이면(비정상) 원본 유지


def select_qualification_pages(pages: list[dict]) -> list[dict]:
    """참가자격/낙찰기준 관련 키워드가 있는 페이지만 남긴다. 하나도 안 걸리면 원본 유지."""
    selected = [p for p in pages if any(kw in p["text"] for kw in _QUALIFICATION_KEYWORDS)]
    return selected if selected else pages


def filter_pages(pages: list[dict]) -> list[dict]:
    """원본 pages(문서 원래 페이지 번호 유지)에 두 필터를 순서대로 적용한다."""
    return select_qualification_pages(remove_appendix_pages(pages))
