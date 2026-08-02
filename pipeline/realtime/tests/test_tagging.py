# -*- coding: utf-8 -*-
"""품목 태깅(#97) 테스트.

핵심은 ngrams.py가 sklearn의 분해와 계속 같은지 지키는 것이다. 이 파일들은
sklearn 계산을 손으로 옮긴 것이라, 누가 "정리"하다 한 줄만 바꿔도 학습 때와
다른 답이 나오는데 그게 조용히 지나간다.

sklearn이 설치돼 있으면 실제로 대조하고, 없으면(Lambda 이미지처럼) 저장해둔
기대값으로 검사한다 — CI에 sklearn을 요구하지 않기 위해서다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tagging import rules  # noqa: E402
from tagging.ngrams import char_ngrams, char_wb_ngrams  # noqa: E402

try:
    from sklearn.feature_extraction.text import CountVectorizer
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# --- n-gram 분해 ------------------------------------------------------------

SAMPLES = [
    "레미콘 구매",
    "2026년도 실험실습기자재 확충사업 [슬라이드 스캐너] 구입",
    "비데",
    "a",                                  # ngram보다 짧은 입력
    "액체크로마토그래피  질량분석기",        # 연속 공백
    "Gas Chromatograph Mass Spectrometer",
    "",                                   # 빈 문자열
]


@pytest.mark.skipif(not HAS_SKLEARN, reason="sklearn 없음 - 아래 고정값 테스트로 대체")
@pytest.mark.parametrize("analyzer,func", [("char", char_ngrams), ("char_wb", char_wb_ngrams)])
@pytest.mark.parametrize("ngram_range", [(2, 3), (2, 5), (3, 5)])
def test_ngrams_match_sklearn(analyzer, func, ngram_range):
    """sklearn의 분해와 완전히 같아야 한다. 순서까지 같을 필요는 없지만
    개수는 같아야 한다 - tf가 개수 기반이라 중복 수가 달라지면 결과가 달라진다."""
    vec = CountVectorizer(analyzer=analyzer, ngram_range=ngram_range, lowercase=True)
    build = vec.build_analyzer()
    for doc in SAMPLES:
        assert sorted(func(doc.lower(), *ngram_range)) == sorted(build(doc)), (
            f"{analyzer} {ngram_range} 불일치: {doc!r}"
        )


def test_char_ngrams_고정값():
    """sklearn이 없어도 회귀를 잡을 수 있게 손으로 계산한 기대값을 둔다."""
    assert char_ngrams("abcd", 2, 3) == ["ab", "bc", "cd", "abc", "bcd"]
    assert char_ngrams("ab", 2, 5) == ["ab"]          # 문서보다 긴 n은 건너뛴다
    assert char_ngrams("", 2, 3) == []


def test_char_wb_단어보다_긴_n은_한_번만():
    """sklearn의 `if offset == 0: break` 분기. 빠뜨리면 짧은 단어가 중복 계산된다.

    "a"는 공백을 붙이면 " a "로 3글자다. n=3은 딱 한 번 나오고, n=4/5는
    단어보다 길어 더 자를 게 없으므로 break로 빠져나온다.
    """
    assert char_wb_ngrams("a", 3, 5) == [" a "]
    # "ab" -> " ab "(4글자). n=3은 두 조각, n=4는 한 조각 뒤 break.
    assert char_wb_ngrams("ab", 3, 5) == [" ab", "ab ", " ab "]
    assert char_wb_ngrams("a b", 2, 2) == [" a", "a ", " b", "b "]


def test_연속_공백은_하나로():
    assert char_ngrams("a  b", 2, 2) == char_ngrams("a b", 2, 2)


# --- 태그 결정 규칙 ---------------------------------------------------------

def test_공사는_컬럼을_그대로():
    tag, source, conf = rules.decide_tag(
        {"bid_category": "cnstwk", "main_cnstty_nm": "철근콘크리트공사"})
    assert (tag, source, conf) == ("철근콘크리트공사", "cnstty_column", None)


def test_공사_공종명_없으면_미분류():
    tag, source, _ = rules.decide_tag({"bid_category": "cnstwk", "main_cnstty_nm": None})
    assert (tag, source) == (rules.UNCLASSIFIED, "none")


def test_코드가_모델보다_우선():
    """코드는 정답이므로 제목이 무엇이든 코드를 따라야 한다."""
    tag, source, conf = rules.decide_tag({
        "bid_category": "thng",
        "bid_ntce_nm": "전혀 상관없는 제목",
        "item_codes": [{"type": "세부품명번호", "code": "41111701"}],
    })
    assert (tag, source, conf) == ("실험·분석장비", "code", None)


def test_용역은_세부품명번호를_업종코드보다_우선():
    tag, source, _ = rules.decide_tag({
        "bid_category": "servc",
        "bid_ntce_nm": "제목",
        "item_codes": [{"type": "업종코드", "code": "1169"},
                       {"type": "세부품명번호", "code": "81111899"}],
    })
    assert (tag, source) == ("IT시스템", "code")


def test_자리표시자_업종코드는_무시():
    """9999 등은 '해당없음' 자리표시자라 태그 근거로 쓰면 안 된다."""
    assert rules.tag_from_codes("servc", [{"type": "업종코드", "code": "9999"}]) is None


def test_item_codes_형식_파손에도_죽지_않음():
    for broken in (None, "문자열", [None, 3, {"code": None}], [{}]):
        assert rules.tag_from_codes("thng", broken) is None


def test_외자는_물품_모델을_쓴다():
    tag, source, conf = rules.decide_tag({
        "bid_category": "frgcpt",
        "bid_ntce_nm": "액체크로마토그래피 질량분석기 구매",
        "item_codes": None,
    })
    assert source.startswith("model_frgcpt")
    assert tag == "실험·분석장비"
    assert conf > 1.0


def test_신뢰도_낮으면_source에_low가_붙되_태그는_남는다():
    """태그를 버리면 임계값을 낮출 때 재추론해야 한다. 저장은 하고 표시만 한다."""
    tag, source, conf = rules.decide_tag({
        "bid_category": "thng", "bid_ntce_nm": "포충기", "item_codes": None})
    assert conf is not None
    if conf < rules.THRESHOLD["thng"]:
        assert source.endswith("_low")
        assert tag != rules.UNCLASSIFIED       # 태그 자체는 보존
        assert not rules.is_confident(source, conf)
        # 임계값을 낮추면 같은 데이터로 다시 살아난다
        assert rules.is_confident(source, conf, {"thng": 0.0})


def test_제목이_비면_미분류():
    tag, source, _ = rules.decide_tag(
        {"bid_category": "thng", "bid_ntce_nm": "   ", "item_codes": None})
    assert (tag, source) == (rules.UNCLASSIFIED, "none")


def test_모르는_업종은_미분류():
    tag, source, _ = rules.decide_tag({"bid_category": "unknown", "bid_ntce_nm": "무엇"})
    assert (tag, source) == (rules.UNCLASSIFIED, "none")


def test_is_confident_코드와_컬럼은_항상_참():
    assert rules.is_confident("code", None)
    assert rules.is_confident("cnstty_column", None)
    assert not rules.is_confident("none", None)
