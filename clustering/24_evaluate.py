# -*- coding: utf-8 -*-
"""잠가둔 시험셋으로 최종 평가하고, 무엇을 왜 틀렸는지 본다.

23번이 저장한 모델을 그대로 불러 쓴다. 여기서 다시 학습하지 않는 이유는
'평가한 모델'과 '저장된 모델'이 같아야 하기 때문이다.

하는 일:
  1. 7월 시험셋 1회 평가            (15번)
  2. 4·5·6·7월 롤링 - 추세 확인      (15번)
  3. 무작위 분할 대비 낙관 편향      (16번)
  4. 혼동 구조와 클래스별 성능       (27번)
  5. 오류 귀인 - 모델 실패 vs 라벨    (28번)

2번 주의: 4·5·6월 점수는 설정 선택에 그 달 데이터가 포함됐으므로 낙관적이다.
깨끗한 수치는 7월 하나뿐이고 나머지는 추세 판단용으로만 쓴다.

실행: .venv/Scripts/python.exe clustering/24_evaluate.py --category thng
"""
import argparse
import importlib.util as _iu
import platform
import subprocess
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             classification_report, confusion_matrix, f1_score)
from sklearn.model_selection import GroupShuffleSplit

HERE = Path(__file__).resolve().parent
_spec = _iu.spec_from_file_location("dataset", HERE / "22_dataset.py")
dataset = _iu.module_from_spec(_spec)
_spec.loader.exec_module(dataset)
sys.path.insert(0, str(HERE))
from _record import record

ROLL_MONTHS = ["2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01"]


def load_model(cat):
    path = HERE / "artifacts" / f"model_{cat}.joblib"
    if not path.exists():
        raise SystemExit(f"{path.name}이 없다. 먼저 23_search.py를 실행할 것")
    return joblib.load(path), path


def scores(y_true, y_pred):
    return {"macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
            "accuracy": float(accuracy_score(y_true, y_pred)),
            "balanced_acc": float(balanced_accuracy_score(y_true, y_pred))}


def final_test(m, df, y, groups, pool, test, texts):
    """1 - 잠근 시험셋을 딱 한 번 연다. 저장된 모델을 그대로 쓴다."""
    pred = m["classifier"].predict(m["vectorizer"].transform(texts[test]))
    s = scores(y[test], pred)
    print(f"\n[1. 최종 시험 - 7월 {len(test):,}건, 1회]")
    print(f"  Macro F1 {s['macro_f1']:.4f} / Acc {s['accuracy']:.4f}"
          f" / BalAcc {s['balanced_acc']:.4f}")
    gap = s["macro_f1"] - m["cv_macro_f1"]
    print(f"  CV {m['cv_macro_f1']:.4f} (±{m['cv_std']:.4f}) 대비 {gap:+.4f}", end="  ")
    if abs(gap) <= m["cv_std"]:
        print("→ 표준편차 안. CV가 시험 성능을 잘 예측했다.")
    elif gap < 0:
        print("→ 표준편차 밖으로 하락. 설정 선택이 검증셋에 과적합됐거나 시간 드리프트다.")
    else:
        print("→ 표준편차 밖으로 상승. 7월이 상대적으로 쉬운 달일 수 있다.")
    return s, pred


def rolling(m, df, y, groups, texts):
    """2 - 시험 달을 바꿔가며 재학습·평가. 한 달만 보면 그 달이 특이한지 알 수 없다."""
    print(f"\n[2. 롤링 - 시험 달을 바꿔가며]")
    rows = []
    make = type(m["classifier"])
    for cut in ROLL_MONTHS:
        tr, te = dataset.time_split(df, groups, test_from=cut)
        nxt = pd.Timestamp(cut) + pd.offsets.MonthBegin(1)
        te = te[df["ntce_dt"].iloc[te] < nxt]        # 그 달만 (이후 달 제외)
        if len(te) < 50 or len(set(y[te])) < 2:
            rows.append({"시험 달": cut[:7], "학습": len(tr), "시험": len(te),
                         "Macro F1": None, "비고": "표본 부족"})
            continue
        vec = TfidfVectorizer(**m["vec_kwargs"])
        X = vec.fit_transform(texts[tr])
        clf = sklearn.base.clone(m["classifier"])
        clf.fit(X, y[tr])
        s = scores(y[te], clf.predict(vec.transform(texts[te])))
        rows.append({"시험 달": cut[:7], "학습": len(tr), "시험": len(te),
                     "Macro F1": round(s["macro_f1"], 4),
                     "Acc": round(s["accuracy"], 4),
                     "비고": "깨끗" if cut == ROLL_MONTHS[-1] else "선택에 포함(낙관)"})
    res = pd.DataFrame(rows)
    print(res.to_string(index=False))
    got = res["Macro F1"].dropna()
    if len(got) >= 2:
        print(f"  변동폭 {got.max()-got.min():.4f} (최저 {got.min():.4f} ~ 최고 {got.max():.4f})")
        print("  변동이 CV 표준편차보다 작으면 7월 점수를 그대로 믿어도 된다.")
    return rows


def optimism(m, df, y, groups, pool, test, texts):
    """3 - 같은 크기를 무작위로 떼어 평가. 시간 분할과의 차이가 낙관 편향이다."""
    print(f"\n[3. 낙관 편향 - 무작위 분할 vs 시간 분할]")
    idx = np.arange(len(df))
    frac = len(test) / len(df)
    rows = []
    for seed in (0, 1, 2):
        tr_rel, te_rel = next(GroupShuffleSplit(n_splits=1, test_size=frac,
                                                random_state=seed).split(idx, y, groups))
        vec = TfidfVectorizer(**m["vec_kwargs"])
        X = vec.fit_transform(texts[tr_rel])
        clf = sklearn.base.clone(m["classifier"])
        clf.fit(X, y[tr_rel])
        rows.append(f1_score(y[te_rel], clf.predict(vec.transform(texts[te_rel])),
                             average="macro", zero_division=0))
    rnd = float(np.mean(rows))
    tim = scores(y[test], m["classifier"].predict(m["vectorizer"].transform(texts[test])))["macro_f1"]
    print(f"  무작위 분할(시드 3회 평균) {rnd:.4f}   시간 분할(7월) {tim:.4f}")
    print(f"  낙관 편향 {rnd - tim:+.4f}")
    print("  양수면 무작위 분할이 그만큼 후하게 준 것. 미래 공고에 쓰는 모델은 시간 분할이 정직하다.")
    return {"random_macro_f1": rnd, "time_macro_f1": tim, "optimism": rnd - tim,
            "random_seeds": [round(r, 4) for r in rows]}


def confusion(m, df, y, test, pred):
    """4 - 어떤 클래스끼리 헷갈리나. 평균만 보면 어디를 고쳐야 할지 모른다."""
    print(f"\n[4. 혼동 구조]")
    labels = sorted(set(y[test]) | set(pred))
    cm = confusion_matrix(y[test], pred, labels=labels)
    pairs = [(labels[i], labels[j], int(cm[i, j]))
             for i in range(len(labels)) for j in range(len(labels))
             if i != j and cm[i, j] > 0]
    pairs.sort(key=lambda x: -x[2])
    print("  자주 헷갈리는 조합 (정답 → 예측, 상위 12)")
    for a, b, c in pairs[:12]:
        print(f"    {c:>3}건  {a} → {b}")
    print(f"\n  클래스별 성능")
    print(classification_report(y[test], pred, zero_division=0))
    return [{"정답": a, "예측": b, "건수": c} for a, b, c in pairs[:20]]


def attribute(m, df, y, groups, pool, test, pred):
    """5 - 오답이 모델 실패인지 라벨 문제인지 나눈다.

    같은 정규화 제목이 학습 풀에 있는데 태그가 다르면 라벨 자체가 모순된 것이라
    모델을 고쳐도 못 맞힌다. 나머지가 실제로 고칠 수 있는 몫이다.
    """
    print(f"\n[5. 오류 귀인]")
    wrong = test[y[test] != pred]
    pool_tags = df.iloc[pool].groupby("norm")["tag"].agg(set)
    conflict, unseen, plain = [], [], []
    for i, p in zip(wrong, pred[y[test] != pred]):
        n = df["norm"].iloc[i]
        tags = pool_tags.get(n)
        if tags is None:
            unseen.append((i, p))
        elif y[i] not in tags or len(tags) > 1:
            conflict.append((i, p))
        else:
            plain.append((i, p))
    n_w = len(wrong)
    print(f"  오답 {n_w}건 / 시험 {len(test)}건 ({n_w/len(test)*100:.1f}%)")
    print(f"    라벨 모순 - 같은 제목이 학습에서 다른 태그   {len(conflict):>3}건"
          f" ({len(conflict)/max(n_w,1)*100:.0f}%)  → 모델을 고쳐도 못 맞힘")
    print(f"    처음 보는 제목                              {len(unseen):>3}건"
          f" ({len(unseen)/max(n_w,1)*100:.0f}%)  → 데이터·어휘 문제")
    print(f"    학습에 같은 제목이 있는데 틀림               {len(plain):>3}건"
          f" ({len(plain)/max(n_w,1)*100:.0f}%)  → 실제 모델 실패")

    print(f"\n  오답 표본 (정답 → 예측 | 제목)")
    for i, p in list(zip(wrong, pred[y[test] != pred]))[:12]:
        print(f"    {y[i]} → {p} | {df['title'].iloc[i][:60]}")
    return {"n_wrong": int(n_w), "label_conflict": len(conflict),
            "unseen_title": len(unseen), "model_failure": len(plain)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="thng", choices=["thng", "servc"])
    args = ap.parse_args()
    cat, kor = args.category, {"thng": "물품", "servc": "용역"}[args.category]
    t0 = time.perf_counter()

    m, path = load_model(cat)
    df = dataset.load(cat)
    y = df["tag"].to_numpy()
    groups = dataset.build_groups(df)
    pool, test = dataset.time_split(df, groups)
    dataset.check_no_leak(df, y, groups, pool, test)
    texts = df[f"text_{m['text_source']}"].to_numpy()

    print("=" * 78)
    print(f"[{kor}] 최종 평가  모델 {path.name}")
    print(f"  {m['clf_name']} {m['clf_params']} / 입력 {m['text_source']}")
    print(f"  {m['vec_kwargs']}")
    print(f"  학습 {m['n_train']:,}건 / 시험 {len(test):,}건 / 태그 {len(m['classes'])}종")
    print("=" * 78, flush=True)

    s, pred = final_test(m, df, y, groups, pool, test, texts)
    roll = rolling(m, df, y, groups, texts)
    opt = optimism(m, df, y, groups, pool, test, texts)
    conf = confusion(m, df, y, test, pred)
    attr = attribute(m, df, y, groups, pool, test, pred)

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=dataset.BASE,
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    record("evaluate", category=cat, model=str(path.name), clf=m["clf_name"],
           clf_params=str(m["clf_params"]), text_source=m["text_source"],
           n_test=len(test), cv_macro_f1=m["cv_macro_f1"], cv_std=m["cv_std"],
           test_scores=s, rolling=roll, optimism=opt, top_confusions=conf,
           error_attribution=attr,
           env={"sklearn": sklearn.__version__, "numpy": np.__version__,
                "pandas": pd.__version__, "python": platform.python_version(),
                "commit": commit},
           elapsed_sec=round(time.perf_counter() - t0, 1))
    print(f"\n총 {time.perf_counter()-t0:.0f}초")


if __name__ == "__main__":
    main()
