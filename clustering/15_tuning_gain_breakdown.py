# -*- coding: utf-8 -*-
"""튜닝으로 오른 몫과 데이터를 더 줘서 오른 몫을 분리한다.

14번에서 최종 모델을 Train+Val로 학습했는데, 기준선(물품 0.765 / 용역 0.802)은
Train만으로 낸 값이다. 그대로 비교하면 두 효과가 섞인다.

네 조합을 같은 Test에서 재서 분해한다.
  기본설정 + Train        <- 기준선. 이 값이 재현되는지도 같이 확인
  기본설정 + Train+Val    <- 데이터만 더 준 효과
  최적설정 + Train        <- 튜닝만 한 효과
  최적설정 + Train+Val    <- 14번이 보고한 값

실행: .venv/Scripts/python.exe clustering/15_tuning_gain_breakdown.py
"""
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import balanced_accuracy_score, f1_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.svm import LinearSVC

import importlib.util

from _record import record

warnings.filterwarnings("ignore", category=ConvergenceWarning)
HERE = Path(__file__).resolve().parent

# 14번의 데이터 로딩을 그대로 재사용한다 (같은 라벨/같은 분할이어야 비교가 성립)
spec = importlib.util.spec_from_file_location("hp", HERE / "14_hparam_search.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

# 07/08번이 쓴 기본 설정
BASELINE = dict(analyzer="char", ngram_range=(2, 5), min_df=2, sublinear_tf=True,
                max_features=None, C=1.0, class_weight="balanced", use_norm=False)


def run(sub, y, idx_fit, idx_test, cfg):
    col = "norm" if cfg["use_norm"] else "title"
    texts = sub[col].astype(str).to_numpy()
    vec = TfidfVectorizer(analyzer=cfg["analyzer"], ngram_range=cfg["ngram_range"],
                          min_df=cfg["min_df"], sublinear_tf=cfg["sublinear_tf"],
                          max_features=cfg["max_features"])
    X = vec.fit_transform(texts[idx_fit])
    clf = LinearSVC(C=cfg["C"], class_weight=cfg["class_weight"],
                    random_state=42, max_iter=3000).fit(X, y[idx_fit])
    p = clf.predict(vec.transform(texts[idx_test]))
    return (f1_score(y[idx_test], p, average="macro", zero_division=0),
            balanced_accuracy_score(y[idx_test], p))


for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    best_path = HERE / "outputs" / f"hparam_{cat}.csv"
    if not best_path.exists():
        print(f"[{kor}] {best_path.name} 없음 - 14번을 먼저 실행할 것")
        continue

    sub = hp.load(cat)
    y = sub["tag"].to_numpy()
    groups = sub["norm"].to_numpy()
    tr, rest = next(GroupShuffleSplit(n_splits=1, test_size=0.3,
                                      random_state=42).split(sub, y, groups))
    v_rel, t_rel = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                        .split(sub.iloc[rest], y[rest], groups[rest]))
    val, test = rest[v_rel], rest[t_rel]
    trval = np.concatenate([tr, val])

    b = pd.read_csv(best_path).sort_values("Val_MacroF1", ascending=False).iloc[0]
    tuned = dict(analyzer=b["analyzer"],
                 ngram_range=tuple(int(x) for x in re.findall(r"\d+", b["ngram"])),
                 min_df=int(b["min_df"]), sublinear_tf=bool(b["sublinear"]),
                 max_features=int(b["max_features"]) or None, C=float(b["C"]),
                 class_weight=None if b["class_weight"] == "none" else "balanced",
                 use_norm=bool(b["정규화"]))

    print("=" * 72)
    print(f"[{kor}] 상승분 분해  (Test {len(test)}건 고정)")
    print("=" * 72)
    rows = []
    for cname, cfg in [("기본설정", BASELINE), ("최적설정", tuned)]:
        for dname, idx in [("Train", tr), ("Train+Val", trval)]:
            f1, ba = run(sub, y, idx, test, cfg)
            rows.append({"설정": cname, "학습데이터": dname,
                         "학습건수": len(idx), "Macro F1": f1, "Balanced Acc": ba})
    r = pd.DataFrame(rows)
    print(r.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    base = r[(r["설정"] == "기본설정") & (r["학습데이터"] == "Train")]["Macro F1"].iloc[0]
    d_only = r[(r["설정"] == "기본설정") & (r["학습데이터"] == "Train+Val")]["Macro F1"].iloc[0]
    t_only = r[(r["설정"] == "최적설정") & (r["학습데이터"] == "Train")]["Macro F1"].iloc[0]
    both = r[(r["설정"] == "최적설정") & (r["학습데이터"] == "Train+Val")]["Macro F1"].iloc[0]
    print(f"\n  기준선            {base:.3f}")
    print(f"  데이터 추가만      {d_only - base:+.3f}")
    print(f"  튜닝만            {t_only - base:+.3f}")
    print(f"  둘 다 (최종)      {both - base:+.3f}   -> {both:.3f}")
    print(f"\n  최적설정: {tuned}\n")

    record("tuning_gain_breakdown", category=cat, n_test=len(test),
           cells=r.to_dict("records"),
           baseline=float(base), data_only_gain=float(d_only - base),
           tuning_only_gain=float(t_only - base), combined_gain=float(both - base),
           tuning_gain_at_trval=float(both - d_only),
           best_config={k: (list(v) if isinstance(v, tuple) else v)
                        for k, v in tuned.items()})
