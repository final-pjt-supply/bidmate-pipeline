# -*- coding: utf-8 -*-
"""기준선 → 표현 탐색 → 축소·선택 → 분류기·하이퍼파라미터 → 트리.

데이터·라벨·그룹·분할은 22_dataset.py가 정의한 것을 그대로 쓴다. 여기서는
"무엇을 시도할지"만 다룬다. 시험셋(7월)은 열지 않는다 - 24번이 최종 1회 연다.

단계와 대응 항목:
  baseline  기준선 3종                              (23번)
  repr      표현 480조합 + 축별 영향도 + 어휘 진단   (24·25·26번)
  reduce    축소·선택 4종 비교                       (29·31번)
  clf       분류기 4종 × 하이퍼파라미터 42조합       (32·33번)
  trees     SVD 위 트리 3종                          (30번)

모든 비교는 학습 풀(1~6월)의 StratifiedGroupKFold 5-fold 평균±표준편차로 한다.
판정 규칙(사전등록): 차이가 1위의 표준편차보다 작으면 동률로 보고, 동률이면
더 단순한 쪽을 택한다.

실행: .venv/Scripts/python.exe clustering/23_search.py --category thng --stage all
"""
import argparse
import importlib.util as _iu
import platform
import subprocess
import time
import warnings
from itertools import product
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.decomposition import TruncatedSVD
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import (ExtraTreesClassifier, HistGradientBoostingClassifier,
                              RandomForestClassifier)
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.feature_selection import SelectKBest, chi2
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.multiclass import OneVsRestClassifier
from sklearn.naive_bayes import ComplementNB
from sklearn.svm import LinearSVC

HERE = Path(__file__).resolve().parent
_spec = _iu.spec_from_file_location("dataset", HERE / "22_dataset.py")
dataset = _iu.module_from_spec(_spec)
_spec.loader.exec_module(dataset)

import sys
sys.path.insert(0, str(HERE))
from _record import record

warnings.filterwarnings("ignore", category=ConvergenceWarning)

SEED = dataset.SEED

# ---- 표현 탐색 공간 (480조합, 24번 확정) ----
TEXT_SOURCE = ["title", "instt_title"]
ANALYZER_NGRAMS = [("char", (2, 3)), ("char", (2, 4)), ("char", (2, 5)), ("char", (3, 5)),
                   ("char_wb", (2, 3)), ("char_wb", (2, 4)), ("char_wb", (2, 5)),
                   ("char_wb", (3, 5)),
                   ("word", (1, 1)), ("word", (1, 2))]
MIN_DF = [1, 2, 3]
MAX_DF = [1.0, 0.9]
SUBLINEAR = [True, False]
USE_IDF = [True, False]
# binary는 제외했다 - 조합이 두 배가 되는데 use_idf와 효과가 겹친다.

PROBE = dict(C=1.0, class_weight="balanced")   # 표현 비교용 통제 조건 (22번 확정)
# 축소·분류기 단계로 넘길 표현 수. 1단계 점수는 탐침(LinearSVC) 하나로 매긴 것이라
# 1등만 넘기면 "LinearSVC용으로 뽑은 표현"에 다른 분류기를 억지로 끼우게 된다.
TOP_N_REPR = 10

# ---- 축소·선택 후보 (29번 확정) ----
# L1은 축소기가 아니라 분류기 자체가 계수를 0으로 눌러 고르는 방식이라 따로 둔다.
REDUCERS = [("없음", None), ("SVD-100", ("svd", 100)), ("SVD-300", ("svd", 300)),
            ("chi2-2000", ("chi2", 2000)), ("chi2-5000", ("chi2", 5000))]
L1_CS = [0.3, 1.0, 3.0]

# ---- 분류기와 하이퍼파라미터 (32·33번 확정) ----
def _mk_svc(**p):
    return LinearSVC(random_state=SEED, max_iter=5000, **p)


def _mk_lr_ovr(**p):
    return OneVsRestClassifier(LogisticRegression(solver="liblinear", max_iter=2000, **p))


def _mk_lr_multi(**p):
    return LogisticRegression(solver="lbfgs", max_iter=2000, **p)


CLASSIFIERS = {
    "LinearSVC": (_mk_svc,
                  [dict(C=c, class_weight=w)
                   for c, w in product([0.1, 0.3, 1.0, 3.0, 10.0, 30.0],
                                       ["balanced", None])]),
    "LogReg(OvR)": (_mk_lr_ovr,
                    [dict(C=c, class_weight=w)
                     for c, w in product([0.3, 1.0, 3.0, 10.0, 30.0],
                                         ["balanced", None])]),
    "LogReg(다항)": (_mk_lr_multi,
                     [dict(C=c, class_weight=w)
                      for c, w in product([0.3, 1.0, 3.0, 10.0, 30.0],
                                          ["balanced", None])]),
    "ComplementNB": (lambda **p: ComplementNB(**p),
                     [dict(alpha=a, norm=n)
                      for a, n in product([0.01, 0.05, 0.1, 0.3, 1.0], [True, False])]),
}
C_GRID = [0.1, 0.3, 1.0, 3.0, 10.0, 30.0]      # 경계값 확장 판정에 씀

TREES = {
    "RandomForest": lambda: RandomForestClassifier(n_estimators=300, random_state=SEED,
                                                   class_weight="balanced", n_jobs=-1),
    "ExtraTrees": lambda: ExtraTreesClassifier(n_estimators=300, random_state=SEED,
                                               class_weight="balanced", n_jobs=-1),
    "HistGB": lambda: HistGradientBoostingClassifier(random_state=SEED),
}


def cv_score(texts, y, groups, idx, make_clf, vec_kwargs, reducer=None, df=None):
    """5-fold Macro F1 평균과 표준편차.

    벡터라이저와 축소기는 fold마다 학습 부분에만 fit한다. 전체에 미리 fit하면
    검증 부분의 정보가 새어 들어가 점수가 부풀어 오른다.
    """
    scores = []
    for tr, va in dataset.folds(df, y, groups, idx):
        vec = TfidfVectorizer(**vec_kwargs)
        try:
            Xtr = vec.fit_transform(texts[tr])
        except ValueError:          # min_df/max_df 조합으로 어휘가 비는 경우
            return None, None
        Xva = vec.transform(texts[va])

        if reducer is not None:
            kind, k = reducer
            if kind == "svd":
                red = TruncatedSVD(n_components=min(k, Xtr.shape[1] - 1), random_state=SEED)
            else:
                red = SelectKBest(chi2, k=min(k, Xtr.shape[1]))
            Xtr = red.fit_transform(Xtr, y[tr])
            Xva = red.transform(Xva)

        clf = make_clf()
        try:
            clf.fit(Xtr, y[tr])
        except ValueError:          # ComplementNB에 음수가 들어가는 경우(SVD 출력)
            return None, None
        scores.append(f1_score(y[va], clf.predict(Xva), average="macro", zero_division=0))
    return float(np.mean(scores)), float(np.std(scores))


def vec_from_row(r):
    return dict(analyzer=r["analyzer"], ngram_range=tuple(eval(r["ngram"])),
                min_df=r["min_df"], max_df=r["max_df"],
                sublinear_tf=r["sublinear"], use_idf=r["use_idf"])


def stage_baseline(ctx):
    """23번 - 이후 모든 개선폭을 재는 원점."""
    df, y, groups, pool, texts = ctx["df"], ctx["y"], ctx["groups"], ctx["pool"], ctx["texts"]
    print("\n[기준선]", flush=True)
    rows = []

    m, s = cv_score(texts["title"], y, groups, pool,
                    lambda: DummyClassifier(strategy="most_frequent"),
                    dict(analyzer="char", ngram_range=(2, 3)), df=df)
    rows.append({"기준선": "최빈 클래스", "CV_MacroF1": m, "CV_std": s})

    m, s = cv_score(texts["title"], y, groups, pool, lambda: _mk_svc(**PROBE),
                    dict(), df=df)      # TfidfVectorizer 전부 기본값(word 1-gram)
    rows.append({"기준선": "TF-IDF 기본값 + 탐침", "CV_MacroF1": m, "CV_std": s})

    res = pd.DataFrame(rows)
    print(res.to_string(index=False))
    # 세 번째 기준선은 학습이 아니라 커버리지 - 22번 7번 리포트에서 이미 측정됨
    print(f"  참고: 코드 규칙만으로 커버되는 비율 = {ctx['coded_ratio']*100:.1f}%"
          f" (나머지 {100-ctx['coded_ratio']*100:.1f}%가 모델이 맡을 몫)")
    return res


def stage_repr(ctx, budget_min, reuse=True):
    """24·25·26번 - 표현 480조합, 축별 영향도, 어휘 특성.

    이미 저장된 결과가 있으면 다시 돌리지 않는다. 뒷단계(reduce/clf/trees)가
    표현 순위를 필요로 하는데, 단계를 따로 실행할 때마다 16분을 다시 쓰면
    중간에 실패했을 때 손해가 크다.
    """
    df, y, groups, pool, texts = ctx["df"], ctx["y"], ctx["groups"], ctx["pool"], ctx["texts"]
    cache = HERE / "outputs" / f"search_repr_{ctx['cat']}.csv"
    if reuse and cache.exists():
        rep = pd.read_csv(cache)
        print(f"\n[표현 탐색] 저장된 결과 재사용: {cache.name} ({len(rep)}조합)", flush=True)
        print(rep.head(TOP_N_REPR).to_string(index=False))
        return rep, None, None

    grid = list(product(TEXT_SOURCE, ANALYZER_NGRAMS, MIN_DF, MAX_DF, SUBLINEAR, USE_IDF))
    print(f"\n[표현 탐색] {len(grid)}조합 × 5-fold, 탐침 LinearSVC{PROBE}", flush=True)

    rows, t0, shrunk = [], time.perf_counter(), False
    i = 0
    while i < len(grid):
        src, (an, ng), mdf, xdf, stf, uidf = grid[i]
        i += 1
        vk = dict(analyzer=an, ngram_range=ng, min_df=mdf, max_df=xdf,
                  sublinear_tf=stf, use_idf=uidf)
        m, s = cv_score(texts[src], y, groups, pool, lambda: _mk_svc(**PROBE), vk, df=df)
        if m is not None:
            rows.append({"입력": src, "analyzer": an, "ngram": str(ng), "min_df": mdf,
                         "max_df": xdf, "sublinear": stf, "use_idf": uidf,
                         "CV_MacroF1": m, "CV_std": s})
        if i % 40 == 0 or i == len(grid):
            el = time.perf_counter() - t0
            print(f"  {i:>4}/{len(grid)}  {el:>6.0f}초 (잔여 약 {el/i*(len(grid)-i):.0f}초)",
                  flush=True)
            if not shrunk and el / i * len(grid) > budget_min * 60:
                keep = int(max(60, (budget_min * 60 - el) / (el / i)))
                rest = grid[i:]
                if len(rest) > keep:
                    rng = np.random.default_rng(SEED)
                    pick = sorted(rng.choice(len(rest), size=keep, replace=False))
                    grid = grid[:i] + [rest[j] for j in pick]
                    shrunk = True
                    print(f"  ! 예산 초과 예상 - 남은 조합을 {keep}개로 무작위 축소", flush=True)

    rep = pd.DataFrame(rows).sort_values("CV_MacroF1", ascending=False).reset_index(drop=True)
    rep.to_csv(HERE / "outputs" / f"search_repr_{ctx['cat']}.csv",
               index=False, encoding="utf-8-sig")

    print(f"\n[표현 상위 {TOP_N_REPR}]")
    print(rep.head(TOP_N_REPR).to_string(index=False))

    print("\n[축별 영향 - CV Macro F1 평균의 최대-최소 차이]")
    spread = {}
    for axis in ("입력", "analyzer", "ngram", "min_df", "max_df", "sublinear", "use_idf"):
        g = rep.groupby(axis)["CV_MacroF1"].mean().sort_values(ascending=False)
        spread[axis] = float(g.max() - g.min())
        print(f"  {axis:<10} 차이 {spread[axis]:.4f}   " +
              "  ".join(f"{k}={v:.3f}" for k, v in g.items()))

    # 26번 어휘 특성 - 1위 표현 기준
    best = rep.iloc[0]
    vec = TfidfVectorizer(**vec_from_row(best))
    X = vec.fit_transform(texts[best["입력"]][pool])
    dfreq = np.asarray((X > 0).sum(axis=0)).ravel()
    print(f"\n[어휘 특성 - 1위 표현]")
    print(f"  피처 {X.shape[1]:,} / 문서당 비영 {X.getnnz(axis=1).mean():.0f}"
          f" / 밀도 {X.nnz/(X.shape[0]*X.shape[1])*100:.3f}%"
          f" / 1회 등장 {(dfreq==1).mean()*100:.0f}%")
    return rep, spread, shrunk


def stage_reduce(ctx, rep):
    """29·31번 - 축소·선택 4종. 상위 표현에만 적용."""
    df, y, groups, pool, texts = ctx["df"], ctx["y"], ctx["groups"], ctx["pool"], ctx["texts"]
    top = rep.head(TOP_N_REPR).to_dict("records")
    print(f"\n[축소·선택] 상위 {len(top)}개 표현 × {len(REDUCERS) + len(L1_CS)}가지", flush=True)

    rows = []
    for rank, r in enumerate(top, 1):
        vk, txt = vec_from_row(r), texts[r["입력"]]
        for name, red in REDUCERS:
            m, s = cv_score(txt, y, groups, pool, lambda: _mk_svc(**PROBE), vk,
                            reducer=red, df=df)
            rows.append({"표현순위": rank, "축소": name, "설정": str(PROBE),
                         "CV_MacroF1": m, "CV_std": s})
        for c in L1_CS:      # L1은 분류기 쪽에서 계수를 눌러 고르는 방식
            m, s = cv_score(txt, y, groups, pool,
                            lambda: LinearSVC(penalty="l1", dual=False, C=c,
                                              class_weight="balanced",
                                              random_state=SEED, max_iter=5000),
                            vk, df=df)
            rows.append({"표현순위": rank, "축소": "L1", "설정": f"C={c}",
                         "CV_MacroF1": m, "CV_std": s})
        print(f"  표현 {rank}/{len(top)} 완료", flush=True)

    res = pd.DataFrame(rows).dropna(subset=["CV_MacroF1"])
    res.to_csv(HERE / "outputs" / f"search_reduce_{ctx['cat']}.csv",
               index=False, encoding="utf-8-sig")
    print("\n[축소 방식별 최고 - 표현 순위별]")
    print(res.sort_values("CV_MacroF1", ascending=False).head(15).to_string(index=False))

    base = res[res["축소"] == "없음"]["CV_MacroF1"].max()
    best = res.iloc[res["CV_MacroF1"].idxmax()]
    print(f"\n  축소 없음 최고 {base:.4f} / 전체 최고 {best['축소']} {best['CV_MacroF1']:.4f}"
          f" (±{best['CV_std']:.4f})  차이 {best['CV_MacroF1']-base:+.4f}")
    if best["CV_MacroF1"] - base < best["CV_std"]:
        print("  -> 표준편차 안. 사전등록 규칙에 따라 동률로 보고 더 단순한 '없음'을 택한다.")
    return res


def stage_clf(ctx, rep):
    """32·33번 - 분류기 4종 × 42조합. 경계값이면 범위를 한 번 넓힌다."""
    df, y, groups, pool, texts = ctx["df"], ctx["y"], ctx["groups"], ctx["pool"], ctx["texts"]
    top = rep.head(TOP_N_REPR).to_dict("records")
    n = len(top) * sum(len(g) for _, g in CLASSIFIERS.values())
    print(f"\n[분류기·하이퍼파라미터] 표현 {len(top)} × 42조합 = {n}회 × 5-fold", flush=True)

    rows, done = [], 0
    for rank, r in enumerate(top, 1):
        vk, txt = vec_from_row(r), texts[r["입력"]]
        for name, (make, grid) in CLASSIFIERS.items():
            for params in grid:
                m, s = cv_score(txt, y, groups, pool, lambda: make(**params), vk, df=df)
                done += 1
                if m is not None:
                    rows.append({"분류기": name, "표현순위": rank, "입력": r["입력"],
                                 "analyzer": r["analyzer"], "ngram": r["ngram"],
                                 "min_df": r["min_df"], "max_df": r["max_df"],
                                 "sublinear": r["sublinear"], "use_idf": r["use_idf"],
                                 "설정": str(params), "CV_MacroF1": m, "CV_std": s})
        print(f"  {done}/{n}", flush=True)

    res = pd.DataFrame(rows).sort_values("CV_MacroF1", ascending=False).reset_index(drop=True)

    # 경계값 확장(33번 사전등록): 최적 C가 그리드 끝이면 그 방향으로 한 번 넓힌다
    extra = []
    for name in ("LinearSVC", "LogReg(OvR)", "LogReg(다항)"):
        sub = res[res["분류기"] == name]
        if sub.empty:
            continue
        best = sub.iloc[0]
        c = eval(best["설정"])["C"]
        lo, hi = min(C_GRID), max(C_GRID)
        newcs = ([lo / 3, lo / 10] if c == lo else [hi * 3, hi * 10] if c == hi else [])
        if not newcs:
            continue
        print(f"  ! {name} 최적 C={c}가 그리드 경계 - {newcs}로 확장", flush=True)
        r = top[int(best["표현순위"]) - 1]
        vk, txt = vec_from_row(r), texts[r["입력"]]
        make = CLASSIFIERS[name][0]
        for nc in newcs:
            p = dict(eval(best["설정"]))
            p["C"] = nc
            m, s = cv_score(txt, y, groups, pool, lambda: make(**p), vk, df=df)
            if m is not None:
                extra.append({"분류기": name, "표현순위": best["표현순위"],
                              "입력": r["입력"], "analyzer": r["analyzer"],
                              "ngram": r["ngram"], "min_df": r["min_df"],
                              "max_df": r["max_df"], "sublinear": r["sublinear"],
                              "use_idf": r["use_idf"], "설정": str(p),
                              "CV_MacroF1": m, "CV_std": s})
    if extra:
        res = pd.concat([res, pd.DataFrame(extra)], ignore_index=True) \
                .sort_values("CV_MacroF1", ascending=False).reset_index(drop=True)

    res.to_csv(HERE / "outputs" / f"search_clf_{ctx['cat']}.csv",
               index=False, encoding="utf-8-sig")
    print("\n[상위 15]")
    print(res.head(15).to_string(index=False))
    print("\n[분류기별 최고]")
    by = res.loc[res.groupby("분류기")["CV_MacroF1"].idxmax()].sort_values(
        "CV_MacroF1", ascending=False)
    print(by[["분류기", "표현순위", "입력", "설정", "CV_MacroF1", "CV_std"]].to_string(index=False))

    top1 = res.iloc[0]
    tie = res[res["CV_MacroF1"] >= top1["CV_MacroF1"] - top1["CV_std"]]
    print(f"\n  1위 {top1['분류기']} {top1['CV_MacroF1']:.4f} (±{top1['CV_std']:.4f})")
    print(f"  표준편차 안 동률 후보 {len(tie)}개, 분류기 {sorted(set(tie['분류기']))}")
    print("  귀인: LinearSVC vs LogReg(OvR)는 손실 함수만, "
          "LogReg(OvR) vs LogReg(다항)은 다중클래스 전략만 다르다.")
    return res


def stage_trees(ctx, rep):
    """30번 - SVD k=300 밀집 표현 위에서 트리 3종. 선형 모델과 같은 조건에서 비교."""
    df, y, groups, pool, texts = ctx["df"], ctx["y"], ctx["groups"], ctx["pool"], ctx["texts"]
    r = rep.iloc[0]
    vk, txt = vec_from_row(r), texts[r["입력"]]
    print(f"\n[트리 계열] 1위 표현 + SVD-300 위에서 평가", flush=True)

    rows = []
    m, s = cv_score(txt, y, groups, pool, lambda: _mk_svc(**PROBE), vk,
                    reducer=("svd", 300), df=df)
    rows.append({"모델": "LinearSVC(대조)", "CV_MacroF1": m, "CV_std": s})
    for name, make in TREES.items():
        t0 = time.perf_counter()
        m, s = cv_score(txt, y, groups, pool, make, vk, reducer=("svd", 300), df=df)
        rows.append({"모델": name, "CV_MacroF1": m, "CV_std": s})
        print(f"  {name} {time.perf_counter()-t0:.0f}초", flush=True)

    res = pd.DataFrame(rows).sort_values("CV_MacroF1", ascending=False)
    res.to_csv(HERE / "outputs" / f"search_trees_{ctx['cat']}.csv",
               index=False, encoding="utf-8-sig")
    print(res.to_string(index=False))
    return res


def fit_and_save_final(ctx, clf_res):
    """탐색에서 1위로 뽑힌 설정을 학습 풀 전체로 학습해 저장한다.

    여기서 저장한 것을 24번이 그대로 불러 7월 시험셋에 쓴다. 재학습하지 않아야
    '평가한 모델'과 '저장된 모델'이 같음을 보장할 수 있다.
    """
    df, y, groups, pool, texts = ctx["df"], ctx["y"], ctx["groups"], ctx["pool"], ctx["texts"]
    best = clf_res.iloc[0]
    vk = dict(analyzer=best["analyzer"], ngram_range=tuple(eval(best["ngram"])),
              min_df=int(best["min_df"]), max_df=float(best["max_df"]),
              sublinear_tf=bool(best["sublinear"]), use_idf=bool(best["use_idf"]))
    params = eval(best["설정"])
    make = CLASSIFIERS[best["분류기"]][0]

    vec = TfidfVectorizer(**vk)
    X = vec.fit_transform(texts[best["입력"]][pool])
    clf = make(**params)
    clf.fit(X, y[pool])

    art = HERE / "artifacts"
    art.mkdir(exist_ok=True)
    path = art / f"model_{ctx['cat']}.joblib"
    joblib.dump({"vectorizer": vec, "classifier": clf, "text_source": best["입력"],
                 "vec_kwargs": vk, "clf_name": best["분류기"], "clf_params": params,
                 "classes": list(clf.classes_), "n_train": int(len(pool)),
                 "cv_macro_f1": float(best["CV_MacroF1"]), "cv_std": float(best["CV_std"]),
                 "sklearn": sklearn.__version__, "seed": SEED}, path)
    print(f"\n[모델 저장] {path.name}  피처 {X.shape[1]:,} / 학습 {len(pool):,}건")
    print(f"  {best['분류기']} {params} / 입력 {best['입력']} / {vk}")
    return {"path": str(path), "clf": best["분류기"], "params": str(params),
            "input": best["입력"], "vec": {k: str(v) for k, v in vk.items()},
            "cv_macro_f1": float(best["CV_MacroF1"]), "cv_std": float(best["CV_std"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="thng", choices=["thng", "servc"])
    ap.add_argument("--stage", default="all",
                    choices=["all", "baseline", "repr", "reduce", "clf", "trees"])
    ap.add_argument("--budget-min", type=float, default=40.0)
    args = ap.parse_args()
    cat, kor = args.category, {"thng": "물품", "servc": "용역"}[args.category]
    t_start = time.perf_counter()

    df = dataset.load(cat)
    y = df["tag"].to_numpy()
    groups = dataset.build_groups(df)
    pool, test = dataset.time_split(df, groups)
    dataset.check_no_leak(df, y, groups, pool, test)
    all_df = dataset.load(cat, labeled_only=False)

    ctx = dict(cat=cat, df=df, y=y, groups=groups, pool=pool,
               texts={s: df[f"text_{s}"].to_numpy() for s in TEXT_SOURCE},
               coded_ratio=len(df) / len(all_df))

    print("=" * 78)
    print(f"[{kor}] 탐색  풀 {len(pool):,}건 / 태그 {len(set(y))}종 / 시험셋은 열지 않음")
    print(f"  sklearn {sklearn.__version__} / numpy {np.__version__}"
          f" / python {platform.python_version()} / seed {SEED}")
    print("=" * 78, flush=True)

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=dataset.BASE,
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    env = {"sklearn": sklearn.__version__, "numpy": np.__version__,
           "pandas": pd.__version__, "python": platform.python_version(), "commit": commit}

    def log(stage, **fields):
        """단계가 끝날 때마다 기록한다. 마지막에 한 번만 쓰면 중간에 죽었을 때
        그때까지의 결과가 metrics.jsonl에 남지 않는다."""
        record("search", category=cat, stage=stage, n_pool=len(pool),
               n_classes=int(len(set(y))), n_groups=int(len(set(groups))),
               top_n_repr=TOP_N_REPR, seed=SEED, env=env,
               elapsed_sec=round(time.perf_counter() - t_start, 1), **fields)

    if args.stage in ("all", "baseline"):
        log("baseline", baseline=stage_baseline(ctx).to_dict("records"),
            coded_ratio=round(ctx["coded_ratio"], 4))
    if args.stage in ("all", "repr", "reduce", "clf", "trees"):
        rep, spread, shrunk = stage_repr(ctx, args.budget_min)
        if spread is not None:      # 캐시 재사용이면 다시 기록하지 않는다
            log("repr", axis_spread=spread, repr_shrunk=shrunk,
                n_combos=len(rep), repr_top=rep.head(TOP_N_REPR).to_dict("records"))
    if args.stage in ("all", "reduce"):
        log("reduce", reduce=stage_reduce(ctx, rep).head(12).to_dict("records"))
    if args.stage in ("all", "clf"):
        clf_res = stage_clf(ctx, rep)
        log("clf", clf_best=clf_res.head(12).to_dict("records"))
        log("final_model", final=fit_and_save_final(ctx, clf_res))
    if args.stage in ("all", "trees"):
        log("trees", trees=stage_trees(ctx, rep).to_dict("records"))

    print(f"\n총 {time.perf_counter()-t_start:.0f}초")


if __name__ == "__main__":
    main()
