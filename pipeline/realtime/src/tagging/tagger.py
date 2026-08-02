# -*- coding: utf-8 -*-
"""공고 제목으로 품목 태그를 정한다. numpy 외 의존성 없음.

모델은 어휘 사전 + idf 벡터 + 가중치 행렬이 전부다. 학습은 clustering/ 쪽에서
하고 여기서는 그 결과물만 읽는다.

  물품 모델  35,458 피처 / 20종 / Macro F1 0.813
  용역 모델  10,731 피처 /  9종 / Macro F1 0.840

sklearn과 결과가 같은지는 clustering/20_numpy_inference.py가 Test 전체에서
확인했다(예측 100% 일치, 점수 오차 1e-07 - float32 반올림 수준).
"""
import json
import logging
from pathlib import Path

import numpy as np

from tagging.ngrams import ANALYZERS

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent / "models"


class Tagger:
    """어휘 사전과 가중치로 (태그, 신뢰도)를 낸다.

    신뢰도는 1위와 2위의 점수차다. 확률이 아니라 상대적인 여유폭이므로
    모델끼리 비교하면 안 된다 - 임계값도 업종별로 따로 정했다.
    """

    def __init__(self, vocab, idf, coef, intercept, classes, analyzer,
                 ngram_range, sublinear_tf):
        self.vocab = vocab
        self.idf = np.asarray(idf, dtype=np.float32)
        self.coef = np.asarray(coef, dtype=np.float32)
        self.intercept = np.asarray(intercept, dtype=np.float32)
        self.classes = list(classes)
        self.ngram = ANALYZERS[analyzer]
        self.low, self.high = ngram_range
        self.sublinear_tf = sublinear_tf

    @classmethod
    def load(cls, name: str, model_dir: Path = MODEL_DIR) -> "Tagger":
        weights = np.load(model_dir / f"tagger_{name}.npz")
        meta = json.loads((model_dir / f"tagger_{name}.json").read_text(encoding="utf-8"))
        return cls(
            vocab=meta["vocab"], idf=weights["idf"], coef=weights["coef"],
            intercept=weights["intercept"], classes=meta["classes"],
            analyzer=meta["analyzer"], ngram_range=tuple(meta["ngram_range"]),
            sublinear_tf=meta["sublinear_tf"],
        )

    def _vectorize(self, title: str):
        """제목을 tf-idf 벡터로. 학습에 없던 n-gram은 버린다(sklearn transform과 동일)."""
        counts: dict[int, int] = {}
        # lowercase=True가 TfidfVectorizer 기본값이라 여기서도 소문자로 맞춘다.
        for gram in self.ngram(title.lower(), self.low, self.high):
            j = self.vocab.get(gram)
            if j is not None:
                counts[j] = counts.get(j, 0) + 1
        if not counts:
            return None, None
        idx = np.fromiter(counts.keys(), dtype=np.int32, count=len(counts))
        tf = np.fromiter(counts.values(), dtype=np.float64, count=len(counts))
        if self.sublinear_tf:
            tf = 1.0 + np.log(tf)
        vec = tf * self.idf[idx]
        norm = np.sqrt((vec * vec).sum())        # TfidfVectorizer(norm='l2') 기본값
        if norm > 0:
            vec = vec / norm
        return idx, vec

    def scores(self, title: str) -> np.ndarray:
        idx, vec = self._vectorize(title)
        if idx is None:
            # 아는 n-gram이 하나도 없다. 제목이 비었거나 전부 미지의 문자다.
            # 절편만 남으므로 점수차가 거의 없어 임계값에서 미분류로 걸러진다.
            return self.intercept.astype(np.float64).copy()
        return self.coef[:, idx] @ vec + self.intercept

    def predict(self, title: str) -> tuple[str, float]:
        s = self.scores(title)
        order = np.argsort(s)
        return self.classes[order[-1]], float(s[order[-1]] - s[order[-2]])


_cache: dict[str, Tagger] = {}


def get_tagger(name: str) -> Tagger:
    """모델을 콜드스타트당 1번만 읽는다(웜스타트 사이 재사용).

    로드가 물품 161ms / 용역 72ms라 매 호출마다 읽으면 5분 주기 배치에서
    누적 손해가 크다.
    """
    if name not in _cache:
        _cache[name] = Tagger.load(name)
        logger.info("태깅 모델 로드: %s (%d종)", name, len(_cache[name].classes))
    return _cache[name]
