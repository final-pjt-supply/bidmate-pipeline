# -*- coding: utf-8 -*-
"""sklearn 없이 numpy만으로 추론하는 코드를 만들고, sklearn과 결과가 같은지 대조한다.

왜 필요한가:
  추론에 sklearn을 쓰면 람다 이미지에 scikit-learn(41.9MB) + scipy(112.7MB) +
  numpy(33MB)가 들어간다. 그런데 정작 쓰는 건 희소 행렬 하나뿐이고, scipy의
  적분/최적화/신호처리는 한 줄도 안 쓴다. merge 람다 전체가 22MB인 걸 감안하면
  과하다.

  학습에는 sklearn이 필요하지만 추론에는 필요 없다. 학습 결과물은 결국
  어휘 사전 + idf + 가중치 행렬이고, 추론은 그걸 곱하고 argmax 하는 게 전부다.
  numpy만 쓰면 33MB로 끝난다.

무엇을 하는가:
  1. 학습된 파이프라인에서 숫자만 뽑아 npz + json으로 내보낸다
  2. sklearn의 TfidfVectorizer + LinearSVC.decision_function을 numpy로 옮긴다
  3. Test 전체와 외자 전체에서 두 방식이 같은 답을 내는지 대조한다

옮겨 적다 틀리면 실험 결과(0.813/0.840)가 운영에서 재현되지 않으므로,
예측 일치율뿐 아니라 점수의 수치 오차까지 본다.

실행: .venv/Scripts/python.exe clustering/20_numpy_inference.py
"""
import importlib.util
import json
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from _record import record

warnings.filterwarnings("ignore", category=ConvergenceWarning)
HERE = Path(__file__).resolve().parent
ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)

spec = importlib.util.spec_from_file_location("hp", HERE / "14_hparam_search.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

CFG = {
    "thng": dict(analyzer="char", ngram_range=(2, 3), min_df=1, sublinear_tf=True,
                 max_features=None, C=10.0, class_weight="balanced"),
    "servc": dict(analyzer="char_wb", ngram_range=(2, 3), min_df=2, sublinear_tf=False,
                  max_features=None, C=1.0, class_weight="balanced"),
}

# ---------------------------------------------------------------------------
# 여기부터가 람다에 들어갈 추론 코드. sklearn을 import하지 않는다.
# sklearn의 TfidfVectorizer/LinearSVC 계산을 그대로 옮긴 것이라 임의로 고치면
# 학습 때와 다른 답이 나온다. 아래 대조 검증이 그걸 막는 장치다.
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s\s+")


def char_ngrams(doc, lo, hi):
    """sklearn TfidfVectorizer(analyzer='char')의 _char_ngrams와 동일."""
    doc = _WS.sub(" ", doc)
    n_doc = len(doc)
    out = []
    for n in range(lo, min(hi, n_doc) + 1):
        for i in range(n_doc - n + 1):
            out.append(doc[i:i + n])
    return out


def char_wb_ngrams(doc, lo, hi):
    """sklearn의 _char_wb_ngrams와 동일. 단어마다 앞뒤로 공백을 붙여 자른다.

    단어가 n보다 짧으면 그 단어는 한 번만 센다(sklearn의 `if offset == 0: break`).
    """
    doc = _WS.sub(" ", doc)
    out = []
    for w in doc.split():
        w = " " + w + " "
        w_len = len(w)
        for n in range(lo, hi + 1):
            offset = 0
            out.append(w[offset:offset + n])
            while offset + n < w_len:
                offset += 1
                out.append(w[offset:offset + n])
            if offset == 0:
                break
    return out


class NumpyTagger:
    """어휘 사전 + idf + 가중치로 태그를 정한다. numpy 외 의존성 없음."""

    def __init__(self, vocab, idf, coef, intercept, classes, analyzer,
                 ngram_range, sublinear_tf):
        self.vocab = vocab
        self.idf = np.asarray(idf, dtype=np.float32)
        self.coef = np.asarray(coef, dtype=np.float32)
        self.intercept = np.asarray(intercept, dtype=np.float32)
        self.classes = list(classes)
        self.ngram = char_wb_ngrams if analyzer == "char_wb" else char_ngrams
        self.lo, self.hi = ngram_range
        self.sublinear = sublinear_tf

    @classmethod
    def load(cls, npz_path, vocab_path):
        z = np.load(npz_path)
        meta = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
        return cls(meta["vocab"], z["idf"], z["coef"], z["intercept"],
                   meta["classes"], meta["analyzer"],
                   tuple(meta["ngram_range"]), meta["sublinear_tf"])

    def _vectorize(self, title):
        counts = {}
        for g in self.ngram(title.lower(), self.lo, self.hi):
            j = self.vocab.get(g)
            if j is not None:
                counts[j] = counts.get(j, 0) + 1
        if not counts:
            return None, None
        idx = np.fromiter(counts.keys(), dtype=np.int32, count=len(counts))
        tf = np.fromiter(counts.values(), dtype=np.float64, count=len(counts))
        if self.sublinear:
            tf = 1.0 + np.log(tf)
        v = tf * self.idf[idx]
        norm = np.sqrt((v * v).sum())          # TfidfVectorizer(norm='l2') 기본값
        if norm > 0:
            v = v / norm
        return idx, v

    def scores(self, title):
        idx, v = self._vectorize(title)
        if idx is None:                        # 아는 n-gram이 하나도 없는 경우
            return self.intercept.astype(np.float64).copy()
        return self.coef[:, idx] @ v + self.intercept

    def predict(self, title):
        """(태그, 신뢰도) 반환. 신뢰도는 1위와 2위의 점수차."""
        s = self.scores(title)
        o = np.argsort(s)
        return self.classes[o[-1]], float(s[o[-1]] - s[o[-2]])


# ---------------------------------------------------------------------------
# 여기부터는 내보내기와 검증. 람다에는 안 들어간다.
# ---------------------------------------------------------------------------
def export(pipe, cat, cfg):
    vec = pipe.named_steps["tfidf"]
    clf = pipe.named_steps["clf"]
    npz = ART / f"tagger_{cat}.npz"
    meta_path = ART / f"tagger_{cat}.json"
    np.savez_compressed(npz, idf=vec.idf_.astype(np.float32),
                        coef=clf.coef_.astype(np.float32),
                        intercept=clf.intercept_.astype(np.float32))
    meta_path.write_text(json.dumps({
        "vocab": {k: int(v) for k, v in vec.vocabulary_.items()},
        "classes": [str(c) for c in clf.classes_],
        "analyzer": cfg["analyzer"], "ngram_range": list(cfg["ngram_range"]),
        "sublinear_tf": cfg["sublinear_tf"],
    }, ensure_ascii=False), encoding="utf-8")
    return npz, meta_path


results = []
for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    cfg = CFG[cat]
    sub = hp.load(cat)
    y = sub["tag"].to_numpy()
    tr, rest = next(GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=42)
                    .split(sub, y, sub["norm"].to_numpy()))
    v_rel, t_rel = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                        .split(sub.iloc[rest], y[rest], sub["norm"].to_numpy()[rest]))
    trval, test = np.concatenate([tr, rest[v_rel]]), rest[t_rel]
    texts = sub["title"].astype(str).to_numpy()

    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(**{k: v for k, v in cfg.items()
                                     if k not in ("C", "class_weight")})),
        ("clf", LinearSVC(C=cfg["C"], class_weight=cfg["class_weight"],
                          random_state=42, max_iter=3000)),
    ]).fit(texts[trval], y[trval])

    npz, meta_path = export(pipe, cat, cfg)
    tagger = NumpyTagger.load(npz, meta_path)

    # 대조: Test 전체를 두 방식으로 돌린다
    sk_scores = pipe.decision_function(texts[test])
    sk_order = np.argsort(sk_scores, axis=1)
    classes = pipe.named_steps["clf"].classes_
    i = np.arange(len(sk_scores))
    sk_pred = classes[sk_order[:, -1]]
    sk_margin = sk_scores[i, sk_order[:, -1]] - sk_scores[i, sk_order[:, -2]]

    np_pred, np_margin = [], []
    for t in texts[test]:
        p, m = tagger.predict(str(t))
        np_pred.append(p)
        np_margin.append(m)
    np_pred = np.array(np_pred)
    np_margin = np.array(np_margin)

    agree = (np_pred == sk_pred).mean()
    max_score_err = np.abs(np.array([tagger.scores(str(t)) for t in texts[test]])
                           - sk_scores).max()
    max_margin_err = np.abs(np_margin - sk_margin).max()

    size_mb = (npz.stat().st_size + meta_path.stat().st_size) / 1024**2
    print("=" * 70)
    print(f"[{kor}] Test {len(test)}건 대조")
    print("=" * 70)
    print(f"  예측 일치율        {agree*100:.2f}%  ({(np_pred==sk_pred).sum()}/{len(test)})")
    print(f"  점수 최대 오차     {max_score_err:.2e}")
    print(f"  신뢰도 최대 오차   {max_margin_err:.2e}")
    print(f"  파일 크기         {size_mb:.2f}MB  "
          f"(joblib {(ART/f'model_{cat}.joblib').stat().st_size/1024**2:.2f}MB)")

    if agree < 1.0:
        bad = np.where(np_pred != sk_pred)[0][:5]
        print("\n  !! 불일치 사례")
        for b in bad:
            print(f"    sklearn={sk_pred[b]}  numpy={np_pred[b]}  {texts[test][b][:44]}")

    results.append({"category": cat, "n_test": len(test), "agreement": round(float(agree), 6),
                    "max_score_error": float(max_score_err),
                    "max_margin_error": float(max_margin_err),
                    "size_mb": round(size_mb, 3)})
    print()

record("numpy_inference_check", results=results)

print("=" * 70)
print("의존성 비교")
print("=" * 70)
print("  sklearn 추론   scikit-learn 41.9 + scipy 112.7 + numpy 33.0 + joblib 2.0 = 189.6MB")
print("  numpy 추론     numpy 33.0MB")
print(f"  모델 파일 합계  {sum(r['size_mb'] for r in results):.2f}MB")
