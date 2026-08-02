# -*- coding: utf-8 -*-
"""sklearn TfidfVectorizer의 문자 n-gram 분해를 그대로 옮긴 것.

왜 옮겼나:
  추론에 sklearn을 쓰면 이 Lambda 이미지에 scikit-learn(41.9MB) + scipy(112.7MB)가
  들어간다. 정작 쓰는 건 희소 행렬 하나뿐이고 scipy의 적분/최적화/신호처리는
  한 줄도 안 쓴다. 이 Lambda 전체가 22MB인 걸 감안하면 과하다.
  학습에는 sklearn이 필요하지만 추론에는 필요 없다 - 곱하고 argmax 하는 게 전부다.

⚠ 이 파일을 고치면 학습 때와 다른 답이 나온다.
  clustering/20_numpy_inference.py가 sklearn과 대조해 100% 일치를 확인했고,
  tests/test_tagging.py가 그 회귀를 잡는다. 수정하려면 두 곳을 같이 봐야 한다.
"""
import re

# sklearn CountVectorizer._white_spaces와 동일 - 연속 공백을 하나로 줄인다.
_WHITE_SPACES = re.compile(r"\s\s+")


def char_ngrams(document: str, low: int, high: int) -> list[str]:
    """analyzer='char'. 문서 전체를 단어 경계 무시하고 자른다."""
    document = _WHITE_SPACES.sub(" ", document)
    n_doc = len(document)
    out = []
    for n in range(low, min(high, n_doc) + 1):
        for i in range(n_doc - n + 1):
            out.append(document[i:i + n])
    return out


def char_wb_ngrams(document: str, low: int, high: int) -> list[str]:
    """analyzer='char_wb'. 단어마다 앞뒤에 공백을 붙여 단어 경계를 살린다.

    단어가 n보다 짧으면 그 단어는 한 번만 센다(sklearn의 `if offset == 0: break`).
    이 분기를 빠뜨리면 짧은 단어가 중복 계산돼 결과가 달라진다.
    """
    document = _WHITE_SPACES.sub(" ", document)
    out = []
    for word in document.split():
        word = " " + word + " "
        w_len = len(word)
        for n in range(low, high + 1):
            offset = 0
            out.append(word[offset:offset + n])
            while offset + n < w_len:
                offset += 1
                out.append(word[offset:offset + n])
            if offset == 0:
                break
    return out


ANALYZERS = {"char": char_ngrams, "char_wb": char_wb_ngrams}
