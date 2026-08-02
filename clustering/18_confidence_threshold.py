# -*- coding: utf-8 -*-
"""'미분류' 임계값을 감이 아니라 곡선을 보고 정한다.

LinearSVC는 태그마다 점수를 내므로 1위와 2위의 점수차를 신뢰도로 쓸 수 있다.
차이가 작다는 건 모델이 둘 사이에서 헷갈린다는 뜻이다.

임계값을 올리면 미분류가 늘고 남은 예측의 정확도가 오른다. 그 교환비가
쓸 만한지 봐야 "0.3" 같은 숫자에 근거가 생긴다.

재는 것:
  1. 임계값별 커버리지와 남은 예측의 정확도/Macro F1
  2. 목표 정확도(0.90, 0.95)를 만족하는 최소 임계값과 그때의 커버리지
  3. 미분류가 어느 태그에 몰리는가
  4. 미분류로 걸러지는 공고가 실제로 애매한가 (제목 눈으로 확인)

Test에서만 잰다. 임계값은 운영 설정이지 학습 대상이 아니므로 Test로 골라도
모델 선택 편향이 생기지 않는다. 다만 이 곡선 자체가 낙관적일 수 있다는 점은
남는다 - Test는 학습에 안 쓰였지만 표본이 500건대라 세밀한 구간은 흔들린다.

실행: .venv/Scripts/python.exe clustering/18_confidence_threshold.py
"""
import importlib.util
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from _record import record

warnings.filterwarnings("ignore", category=ConvergenceWarning)
HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("hp", HERE / "14_hparam_search.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

BEST = {
    "thng": dict(analyzer="char", ngram_range=(2, 3), min_df=1, sublinear_tf=True,
                 max_features=None, C=10.0, class_weight="balanced"),
    "servc": dict(analyzer="char_wb", ngram_range=(2, 3), min_df=2, sublinear_tf=False,
                  max_features=None, C=1.0, class_weight="balanced"),
}
THRESHOLDS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]

for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    cfg = BEST[cat]
    sub = hp.load(cat)
    y = sub["tag"].to_numpy()
    groups = sub["norm"].to_numpy()
    tr, rest = next(GroupShuffleSplit(n_splits=1, test_size=0.3,
                                      random_state=42).split(sub, y, groups))
    v_rel, t_rel = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                        .split(sub.iloc[rest], y[rest], groups[rest]))
    trval, test = np.concatenate([tr, rest[v_rel]]), rest[t_rel]
    texts = sub["title"].astype(str).to_numpy()

    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(analyzer=cfg["analyzer"], ngram_range=cfg["ngram_range"],
                                  min_df=cfg["min_df"], sublinear_tf=cfg["sublinear_tf"],
                                  max_features=cfg["max_features"])),
        ("clf", LinearSVC(C=cfg["C"], class_weight=cfg["class_weight"],
                          random_state=42, max_iter=3000)),
    ]).fit(texts[trval], y[trval])

    scores = pipe.decision_function(texts[test])
    order = np.argsort(scores, axis=1)
    classes = pipe.named_steps["clf"].classes_
    pred = classes[order[:, -1]]
    second = classes[order[:, -2]]
    idx = np.arange(len(scores))
    margin = scores[idx, order[:, -1]] - scores[idx, order[:, -2]]
    yt = y[test]

    print("=" * 74)
    print(f"[{kor}] Test {len(test)}건, 임계값별 교환비")
    print("=" * 74)
    print(f"  {'임계값':>6}{'커버리지':>10}{'미분류':>8}{'정확도':>9}{'MacroF1':>9}"
          f"{'실제 맞출걸 버림':>16}")

    curve = []
    for th in THRESHOLDS:
        keep = margin >= th
        cov = keep.mean()
        acc = (pred[keep] == yt[keep]).mean() if keep.any() else float("nan")
        f1 = (f1_score(yt[keep], pred[keep], average="macro", zero_division=0)
              if keep.any() else float("nan"))
        # 버려진 것 중 원래 맞았을 건수 - 임계값의 실제 손실
        lost = int(((~keep) & (pred == yt)).sum())
        curve.append({"threshold": th, "coverage": round(cov, 4),
                      "n_abstain": int((~keep).sum()), "accuracy": round(float(acc), 4),
                      "macro_f1": round(float(f1), 4), "lost_correct": lost})
        print(f"  {th:>6.2f}{cov*100:>9.1f}%{(~keep).sum():>8}{acc:>9.3f}{f1:>9.3f}"
              f"{lost:>16}")

    c = pd.DataFrame(curve)
    print("\n  [목표 정확도를 만족하는 최소 임계값]")
    for target in (0.85, 0.90, 0.95):
        ok = c[c["accuracy"] >= target]
        if len(ok):
            r = ok.iloc[0]
            print(f"    정확도 {target:.2f} 이상 -> 임계값 {r['threshold']:.2f}, "
                  f"커버리지 {r['coverage']*100:.1f}% "
                  f"(미분류 {int(r['n_abstain'])}건, 맞출 걸 버린 게 {int(r['lost_correct'])}건)")
        else:
            print(f"    정확도 {target:.2f} 이상 -> 어떤 임계값으로도 도달 못 함 "
                  f"(최대 {c['accuracy'].max():.3f})")

    # 미분류가 어디에 몰리는가 - 특정 태그만 통째로 사라지면 필터가 망가진다
    th_ref = 0.15
    low = margin < th_ref
    print(f"\n  [임계값 {th_ref}에서 미분류가 몰리는 태그 - 정답 기준]")
    tab = (pd.crosstab(yt, low).rename(columns={False: "유지", True: "미분류"})
           .reindex(columns=["유지", "미분류"], fill_value=0))
    tab["미분류율"] = tab["미분류"] / (tab["유지"] + tab["미분류"])
    for t, r in tab.sort_values("미분류율", ascending=False).head(6).iterrows():
        print(f"    {t:<14} {int(r['미분류']):>3}/{int(r['유지']+r['미분류']):>3} "
              f"= {r['미분류율']*100:>5.1f}%")

    print(f"\n  [미분류로 걸러질 제목 - 실제로 애매한지 확인]")
    for i in np.argsort(margin)[:8]:
        ok = "정답" if pred[i] == yt[i] else "오답"
        print(f"    차이 {margin[i]:.3f}  {pred[i]} vs {second[i]}  [{ok}]")
        print(f"      {texts[test][i][:56]}")

    record("confidence_threshold", category=cat, n_test=len(test),
           curve=curve, top_abstain_tags=tab["미분류율"].sort_values(
               ascending=False).head(6).round(4).to_dict(), threshold_ref=th_ref)
    print()
