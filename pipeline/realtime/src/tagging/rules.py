# -*- coding: utf-8 -*-
"""태그를 무엇으로 정할지 고르는 규칙.

우선순위는 정확한 것부터다. 모델은 마지막 수단이다.

  1. 공사        main_cnstty_nm 컬럼을 그대로 쓴다 (공고의 97.5%에 값이 있다)
  2. 조달청 코드  item_codes가 알려진 코드면 매핑한다 (정답이므로 모델보다 낫다)
  3. 모델        코드가 없는 공고만 추론한다
  4. 미분류      위 어느 것도 안 되거나 모델 신뢰도가 임계값 미만

외자(frgcpt)는 물품 모델을 쓴다. 외자는 업종이 아니라 조달 방식이고 실제로
사는 건 물품이다. 조사에서 정한 외자 태그 4종이 전부 물품 20종 안에 있었다.
검증은 clustering/19_frgcpt_with_thng_model.py - 신뢰도 중앙값이 물품 Test보다
오히려 높았고(1.394 vs 1.149), 예측 분포도 제목 조사 결과와 맞았다
(실험·분석장비 83.8% vs 조사 약 80%).
"""
from tagging.tagger import get_tagger

UNCLASSIFIED = "미분류"

# 신뢰도(1위-2위 점수차) 임계값. 업종별로 곡선 모양이 달라 따로 정했다.
# 임계값은 모델을 바꾸면 같이 바꿔야 한다 - 신뢰도가 확률이 아니라 1·2위의
# 점수차라서 모델마다 척도가 다르기 때문이다.
#
# 물품: 2026-08 재학습 모델. clustering/25_deploy.py 측정(out-of-fold, 목표 정확도 0.85)
#   0.30  커버리지 79.5% 정확도 0.864 (임계값 0이면 0.766)
# 용역: 기존 모델 유지. clustering/18_confidence_threshold.py 측정
#   0.15  커버리지 92.6% 정확도 0.879 (임계값 0이면 0.836)
#
# 외자는 물품 모델을 빌려 쓰므로 물품과 같은 값을 쓴다.
# 이 곡선들은 코드가 있는 공고에서 잰 것이라 낙관적이다. 실제 대상은 코드가 없는
# 공고라 미분류 비율이 더 높을 것이다. confidence를 저장해두므로 임계값을
# 바꿔도 재추론은 필요 없다.
THRESHOLD = {"thng": 0.30, "servc": 0.15, "frgcpt": 0.30}

# 어느 모델로 추론할지. 외자는 물품 모델을 빌려 쓴다.
MODEL_FOR = {"thng": "thng", "frgcpt": "thng", "servc": "servc"}

# 세부품명번호 앞 2자리 -> 태그. 11(토목자재)과 30(건설자재)은 성격이 같아 합쳤다.
THNG_CODE_TAGS = {
    "41": "실험·분석장비", "30": "토목·건설자재", "11": "토목·건설자재",
    "43": "IT·통신장비", "40": "공조·냉난방", "46": "안전·보안장비",
    "25": "차량·건설장비", "39": "전기·수배전", "42": "의료장비",
    "23": "산업·정밀기계", "60": "전시·교육기자재", "24": "운반·저장장비",
    "53": "피복·군장품", "51": "의약품·백신", "12": "시약·화학소모품",
    "26": "발전·전지", "55": "인쇄·사인물", "47": "환경·수처리설비",
    "56": "가구·침구", "50": "식품·급식",
}
# 세부품명번호(P 접두)를 업종코드(B 접두)보다 우선한다 - 품목 분류가 더 구체적이다.
SERVC_CODE_TAGS = {
    "P81": "IT시스템", "B1468": "IT시스템",
    "B1169": "조사·연구",
    "P80": "행사·전시대행", "B5720": "행사·전시대행",
    "P82": "홍보·콘텐츠", "B1469": "홍보·콘텐츠", "B3244": "홍보·콘텐츠",
    "B6146": "감리·컨설팅", "B6525": "감리·컨설팅",
    "P78": "운송·차량임차",
    "B6728": "폐기물처리",
    "B1458": "통신망",
}
# 실제 코드가 아니라 '해당없음' 자리표시자다. 태그 근거로 쓰면 안 된다.
JUNK_BIZ_CODES = {"9999", "9901", "9902", "9903", "9900"}


def _codes(item_codes) -> list[dict]:
    """item_codes는 jsonb라 list[dict]로 오지만, null이나 형식 파손도 대비한다."""
    if not isinstance(item_codes, list):
        return []
    return [c for c in item_codes if isinstance(c, dict)]


def tag_from_codes(bid_category: str, item_codes) -> str | None:
    """조달청 코드에서 태그를 유도한다. 못 하면 None."""
    codes = _codes(item_codes)
    if bid_category in ("thng", "frgcpt"):
        for c in codes:
            code = str(c.get("code") or "")
            if c.get("type") == "세부품명번호" and code[:8].isdigit() and len(code) >= 8:
                tag = THNG_CODE_TAGS.get(code[:2])
                if tag:
                    return tag
        return None

    if bid_category == "servc":
        for c in codes:                                  # 세부품명번호 우선
            code = str(c.get("code") or "")
            if c.get("type") == "세부품명번호" and code[:8].isdigit() and len(code) >= 8:
                tag = SERVC_CODE_TAGS.get("P" + code[:2])
                if tag:
                    return tag
        for c in codes:
            code = str(c.get("code") or "")
            if c.get("type") == "업종코드" and code and code not in JUNK_BIZ_CODES:
                tag = SERVC_CODE_TAGS.get("B" + code)
                if tag:
                    return tag
    return None


def decide_tag(row: dict) -> tuple[str, str, float | None]:
    """공고 한 건의 (태그, 출처, 신뢰도)를 정한다.

    row에 필요한 키: bid_category, bid_ntce_nm, item_codes, main_cnstty_nm
    신뢰도는 모델 추론일 때만 값이 있다 - 코드와 컬럼은 정답이라 개념이 없다.
    """
    category = row.get("bid_category")

    if category == "cnstwk":
        # 공사는 발주기관이 입력한 공종명이 이미 있다. 모델을 만들 이유가 없었다.
        name = (row.get("main_cnstty_nm") or "").strip()
        return (name, "cnstty_column", None) if name else (UNCLASSIFIED, "none", None)

    model_name = MODEL_FOR.get(category)
    if model_name is None:
        return UNCLASSIFIED, "none", None

    tag = tag_from_codes(category, row.get("item_codes"))
    if tag:
        return tag, "code", None

    title = (row.get("bid_ntce_nm") or "").strip()
    if not title:
        return UNCLASSIFIED, "none", None

    tag, confidence = get_tagger(model_name).predict(title)
    # 임계값 미만이어도 태그를 버리지 않는다. 추론 결과와 신뢰도를 그대로 저장하고
    # source에 _low를 붙여 표시만 한다 - 그래야 나중에 임계값을 조정할 때
    # 재추론 없이 조회 조건만 바꾸면 된다(is_confident 참고).
    suffix = "" if confidence >= THRESHOLD[category] else "_low"
    return tag, f"model_{category}{suffix}", confidence


def is_confident(source: str, confidence: float | None,
                 threshold: dict | None = None) -> bool:
    """이 태그를 화면·통계에 써도 되는지. 코드와 컬럼 출처는 항상 참이다.

    임계값을 바꾸고 싶으면 threshold를 넘겨 재추론 없이 다시 판정할 수 있다.
    저장된 source의 _low 표시는 저장 시점 임계값 기준이므로, 임계값을 바꿀 때는
    source가 아니라 confidence로 판정해야 한다.
    """
    if not source.startswith("model_"):
        return source != "none"
    if confidence is None:
        return False
    category = source.removeprefix("model_").removesuffix("_low")
    return confidence >= (threshold or THRESHOLD).get(category, 0.0)
