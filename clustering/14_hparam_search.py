# -*- coding: utf-8 -*-
"""TF-IDF + LinearSVC 하이퍼파라미터를 업종별로 따로 탐색한다.

지금까지의 0.802(용역) / 0.765(물품)은 전부 기본값이다. 얼마나 더 올릴 수 있는지
전수 탐색으로 확인한다.

방법 선택:
  전수 그리드 2,400조합. 1회 학습이 평균 0.6초로 측정돼 업종당 20분대면 끝난다.
  랜덤 서치는 전수가 감당 안 될 때 쓰는 것이고, 여기선 감당된다. 전수로 하면
  덤으로 축별 중요도(어떤 하이퍼파라미터가 실제로 성능을 갈랐는지)를 집계할 수 있다.

  하이퍼밴드/SHA는 쓰지 않는다. 예산 축이 학습 표본 수인데, 표본을 줄이면
  min_df와 max_features의 의미가 같이 변한다 - 재려는 축을 예산 축이 오염시킨다.
  char n-gram은 어휘 크기가 표본 수에 민감해 특히 그렇다.

평가:
  선택은 Val에서만 하고, Test는 최종 1회만 본다. 지표는 Macro F1(태그 불균형).

실행: .venv/Scripts/python.exe clustering/14_hparam_search.py --category thng
"""
import argparse
import re
import time
import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             classification_report, f1_score)
from sklearn.model_selection import GroupShuffleSplit
from sklearn.svm import LinearSVC

from _record import record

warnings.filterwarnings("ignore", category=ConvergenceWarning)

BASE = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"
HERE = Path(__file__).resolve().parent

THNG_MAP = {
    "41": "실험·분석장비", "30": "토목·건설자재", "11": "토목·건설자재",
    "43": "IT·통신장비", "40": "공조·냉난방", "46": "안전·보안장비",
    "25": "차량·건설장비", "39": "전기·수배전", "42": "의료장비",
    "23": "산업·정밀기계", "60": "전시·교육기자재", "24": "운반·저장장비",
    "53": "피복·군장품", "51": "의약품·백신", "12": "시약·화학소모품",
    "26": "발전·전지", "55": "인쇄·사인물", "47": "환경·수처리설비",
    "56": "가구·침구", "50": "식품·급식",
}
SERVC_MAP = {
    "P81": "IT시스템", "B1468": "IT시스템", "B1169": "조사·연구",
    "P80": "행사·전시대행", "B5720": "행사·전시대행",
    "P82": "홍보·콘텐츠", "B1469": "홍보·콘텐츠", "B3244": "홍보·콘텐츠",
    "B6146": "감리·컨설팅", "B6525": "감리·컨설팅",
    "P78": "운송·차량임차", "B6728": "폐기물처리", "B1458": "통신망",
}

_PAT = [r"\[[^\]]*\]", r"\([^)]*(긴급|재공고|변경|정정)[^)]*\)",
        r"\b20\d{2}\s*년?\s*(度|년도)?", r"\b\d{2}년",
        r"재공고|변경공고|정정공고|입찰공고|긴급공고", r"제?\s*\d+\s*차(수|분)?",
        r"★[^★]*★", r"\(총괄\)|\(총액\)|\(계속비\)|\(가칭\)"]

# 탐색 공간. analyzer에 따라 쓸 수 있는 ngram_range가 다르므로 묶어서 둔다.
ANALYZER_NGRAMS = [("char", (2, 3)), ("char", (2, 4)), ("char", (2, 5)), ("char", (3, 5)),
                   ("char_wb", (2, 3)), ("char_wb", (2, 4)), ("char_wb", (2, 5)),
                   ("char_wb", (3, 5)),
                   ("word", (1, 1)), ("word", (1, 2))]
MIN_DF = [1, 2, 3]
SUBLINEAR = [True, False]
MAX_FEATURES = [None, 100_000]
C_VALUES = [0.1, 0.3, 1.0, 3.0, 10.0]
CLASS_WEIGHT = ["balanced", None]
USE_NORM = [False, True]      # 모델 입력으로 원본 제목 / 정규화 제목


def norm(t):
    s = str(t)
    for p in _PAT:
        s = re.sub(p, " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]+", " ", s)).strip()


def load(category):
    env = {}
    for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                            user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
    if category == "thng":
        raw = pd.read_sql("""
            SELECT DISTINCT ON (b.bid_id) b.bid_id, left(e->>'code',2) AS code
            FROM bid_table b, jsonb_array_elements(b.item_codes) e
            WHERE b.bid_category='thng' AND e->>'type'='세부품명번호'
              AND e->>'code' ~ '^[0-9]{8,}' ORDER BY b.bid_id
        """, conn)
        counts = raw["code"].value_counts()
        raw["tag"] = raw["code"].map(THNG_MAP)
        raw.loc[raw["code"].isin(counts[counts < 30].index) | raw["tag"].isna(), "tag"] = "기타"
    else:
        raw = pd.read_sql("""
            SELECT b.bid_id, COALESCE(
                MAX(CASE WHEN e->>'type'='세부품명번호' AND e->>'code' ~ '^[0-9]{8,}'
                         THEN 'P' || left(e->>'code',2) END),
                MAX(CASE WHEN e->>'type'='업종코드'
                          AND e->>'code' NOT IN ('9999','9901','9902','9903','9900')
                         THEN 'B' || (e->>'code') END)) AS code
            FROM bid_table b, jsonb_array_elements(b.item_codes) e
            WHERE b.bid_category='servc' GROUP BY b.bid_id
        """, conn)
        raw = raw[raw["code"].notna()]
        raw["tag"] = raw["code"].map(SERVC_MAP).fillna("기타")
    conn.close()

    df = pd.read_csv(CACHE / "meta_dedup.csv").merge(raw[["bid_id", "tag"]], on="bid_id", how="left")
    sub = df[(df["biz_div"] == category) & df["tag"].notna()].copy()
    sub["norm"] = sub["title"].map(norm)
    return sub.reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="thng", choices=["thng", "servc"])
    args = ap.parse_args()
    cat = args.category
    kor = {"thng": "물품", "servc": "용역"}[cat]
    base_f1 = {"thng": 0.765, "servc": 0.802}[cat]   # 기본값으로 낸 현재 성적

    sub = load(cat)
    y = sub["tag"].to_numpy()
    groups = sub["norm"].to_numpy()

    # 08/07번과 동일한 분할을 그대로 재현한다 (결과를 이어서 비교하기 위해)
    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=42)
    tr, rest = next(gss.split(sub, y, groups))
    v_rel, t_rel = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                        .split(sub.iloc[rest], y[rest], groups[rest]))
    val, test = rest[v_rel], rest[t_rel]

    n_grid = (len(ANALYZER_NGRAMS) * len(MIN_DF) * len(SUBLINEAR) * len(MAX_FEATURES)
              * len(C_VALUES) * len(CLASS_WEIGHT) * len(USE_NORM))
    print("=" * 74)
    print(f"[{kor}] 전수 그리드 {n_grid:,}조합")
    print(f"Train {len(tr):,} / Val {len(val):,} / Test {len(test):,}  (기준선 {base_f1:.3f})")
    print("=" * 74, flush=True)

    rows = []
    t0 = time.perf_counter()
    done = 0
    for use_norm in USE_NORM:
        col = "norm" if use_norm else "title"
        texts = sub[col].astype(str).to_numpy()
        # 벡터라이저 설정 하나당 fit은 1회만 하고, C/class_weight는 그 위에서 돌린다
        for (an, ng), mdf, sub_tf, mf in product(ANALYZER_NGRAMS, MIN_DF, SUBLINEAR, MAX_FEATURES):
            vec = TfidfVectorizer(analyzer=an, ngram_range=ng, min_df=mdf,
                                  sublinear_tf=sub_tf, max_features=mf)
            try:
                Xtr = vec.fit_transform(texts[tr])
            except ValueError:      # min_df가 커서 어휘가 비는 경우
                done += len(C_VALUES) * len(CLASS_WEIGHT)
                continue
            Xval = vec.transform(texts[val])
            for C, cw in product(C_VALUES, CLASS_WEIGHT):
                clf = LinearSVC(C=C, class_weight=cw, random_state=42, max_iter=3000)
                clf.fit(Xtr, y[tr])
                f1 = f1_score(y[val], clf.predict(Xval), average="macro", zero_division=0)
                rows.append({"정규화": use_norm, "analyzer": an, "ngram": str(ng),
                             "min_df": mdf, "sublinear": sub_tf,
                             "max_features": mf or 0, "C": C,
                             "class_weight": cw or "none",
                             "피처수": Xtr.shape[1], "Val_MacroF1": f1})
                done += 1
            if done % 200 < len(C_VALUES) * len(CLASS_WEIGHT):
                el = time.perf_counter() - t0
                print(f"  {done:>5}/{n_grid}  {el:>5.0f}초 경과  "
                      f"(잔여 약 {el/max(done,1)*(n_grid-done):.0f}초)", flush=True)

    res = pd.DataFrame(rows).sort_values("Val_MacroF1", ascending=False)
    res.to_csv(HERE / "outputs" / f"hparam_{cat}.csv", index=False, encoding="utf-8-sig")
    print(f"\n총 {len(res):,}조합 완료, {time.perf_counter()-t0:.0f}초\n")

    print("[Val 상위 10]")
    print(res.head(10).to_string(index=False))

    # 어떤 축이 실제로 성능을 갈랐나 - 전수 탐색이라 이 집계가 가능하다
    print("\n[축별 영향 - 각 값의 Val Macro F1 평균]")
    for axis in ("정규화", "analyzer", "ngram", "min_df", "sublinear",
                 "max_features", "C", "class_weight"):
        g = res.groupby(axis)["Val_MacroF1"].agg(["mean", "max"]).sort_values("mean", ascending=False)
        spread = g["mean"].max() - g["mean"].min()
        print(f"\n  {axis}  (평균 최대-최소 차이 {spread:.3f})")
        for k, r in g.iterrows():
            print(f"    {str(k):<10} 평균 {r['mean']:.3f}  최고 {r['max']:.3f}")

    # 최종 확인은 Test에서 딱 한 번
    best = res.iloc[0]
    col = "norm" if best["정규화"] else "title"
    texts = sub[col].astype(str).to_numpy()
    ng = tuple(int(x) for x in re.findall(r"\d+", best["ngram"]))
    vec = TfidfVectorizer(analyzer=best["analyzer"], ngram_range=ng,
                          min_df=int(best["min_df"]), sublinear_tf=bool(best["sublinear"]),
                          max_features=int(best["max_features"]) or None)
    # 최종 모델은 Train+Val을 함께 쓴다 (설정은 이미 정해졌으므로 데이터를 더 준다)
    trval = np.concatenate([tr, val])
    Xtr = vec.fit_transform(texts[trval])
    clf = LinearSVC(C=float(best["C"]),
                    class_weight=None if best["class_weight"] == "none" else "balanced",
                    random_state=42, max_iter=3000).fit(Xtr, y[trval])
    pred = clf.predict(vec.transform(texts[test]))

    print("\n" + "=" * 74)
    print(f"[{kor}] 최종 - Test에서 1회만 확인")
    print("=" * 74)
    print(f"  설정  정규화={bool(best['정규화'])} analyzer={best['analyzer']} "
          f"ngram={best['ngram']} min_df={int(best['min_df'])} "
          f"sublinear={bool(best['sublinear'])} max_features={int(best['max_features']) or None} "
          f"C={best['C']} class_weight={best['class_weight']}")
    print(f"\n  {'':16}{'튜닝 후':>10}{'기본값':>10}")
    print(f"  {'Macro F1':16}{f1_score(y[test], pred, average='macro', zero_division=0):>10.3f}"
          f"{base_f1:>10.3f}")
    print(f"  {'Accuracy':16}{accuracy_score(y[test], pred):>10.3f}")
    print(f"  {'Balanced Acc':16}{balanced_accuracy_score(y[test], pred):>10.3f}")

    record("hparam_grid", category=cat, n_combos=len(res),
           elapsed_sec=round(time.perf_counter() - t0, 1),
           n_train=len(tr), n_val=len(val), n_test=len(test),
           baseline_macro_f1=base_f1,
           best_config={k: (None if pd.isna(best[k]) else
                            (int(best[k]) if k in ("min_df", "max_features") else
                             bool(best[k]) if k in ("정규화", "sublinear") else
                             float(best[k]) if k == "C" else str(best[k])))
                        for k in ("정규화", "analyzer", "ngram", "min_df", "sublinear",
                                  "max_features", "C", "class_weight")},
           best_val_macro_f1=float(best["Val_MacroF1"]),
           test_macro_f1=float(f1_score(y[test], pred, average="macro", zero_division=0)),
           test_accuracy=float(accuracy_score(y[test], pred)),
           test_balanced_acc=float(balanced_accuracy_score(y[test], pred)),
           axis_spread={a: float(res.groupby(a)["Val_MacroF1"].mean().max()
                                 - res.groupby(a)["Val_MacroF1"].mean().min())
                        for a in ("정규화", "analyzer", "ngram", "min_df", "sublinear",
                                  "max_features", "C", "class_weight")},
           grid_csv=f"hparam_{cat}.csv")

    print("\n[태그별 상세]")
    rep = classification_report(y[test], pred, output_dict=True, zero_division=0)
    r2 = [(t, v["precision"], v["recall"], v["f1-score"], int(v["support"]))
          for t, v in rep.items() if t not in ("accuracy", "macro avg", "weighted avg")]
    r2.sort(key=lambda x: -x[4])
    print(f"  {'태그':>14} {'정밀도':>7} {'재현율':>7} {'F1':>7} {'건수':>5}")
    for t, p, r, f, s in r2:
        print(f"  {t:>14} {p:>7.2f} {r:>7.2f} {f:>7.2f} {s:>5}")


if __name__ == "__main__":
    main()
