# -*- coding: utf-8 -*-
"""데이터 누수를 실제로 집계하고, 그룹 분할 기준으로 두 방식을 비교한다.

이전 실험(랜덤 분할)의 문제:
  1. "2026년 컴퓨터 구매"와 "[긴급] 컴퓨터 구매 재공고"가 다른 제목으로 취급돼
     학습/시험 양쪽에 나뉘면 k-NN이 사실상 같은 제목을 찾아 맞힌다
  2. k를 시험 데이터로 골랐다 (하이퍼파라미터 선택에 test 오염)
  3. Accuracy만 봐서 작은 태그의 성능이 가려졌다

이 스크립트:
  1단계 제목 정규화 -> 그룹 생성 -> 누수 실제 집계
  2단계 그룹 단위 Train/Val/Test 분할 (같은 그룹은 반드시 한쪽에만)
  3단계 BGE-M3 k-NN vs TF-IDF LinearSVC 비교 (k는 Val에서만 선택)
  4단계 Accuracy / Macro F1 / Weighted F1 / Balanced Accuracy
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             classification_report, f1_score)
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import normalize
from sklearn.svm import LinearSVC

BASE = Path(r"C:\Users\user\Desktop\PROJECTS\bidding-agent")
CACHE = BASE / "clustering" / "cache"

env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

X = np.load(CACHE / "vectors_dedup.npy")
df = pd.read_csv(CACHE / "meta_dedup.csv")

conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                        user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
code_map = pd.read_sql("""
    SELECT DISTINCT ON (b.bid_id) b.bid_id, left(e->>'code', 2) AS tag
    FROM bid_table b, jsonb_array_elements(b.item_codes) e
    WHERE e->>'type' = '세부품명번호' AND e->>'code' ~ '^[0-9]{8,}'
    ORDER BY b.bid_id
""", conn)
conn.close()
df = df.merge(code_map, on="bid_id", how="left")


# ---------------------------------------------------------------- 1. 정규화
# 보수적으로 제거한다. 구매/용역/공사/유지관리 같은 분류 단서는 남긴다.
_PATTERNS = [
    r"\[[^\]]*\]",                      # [긴급], [재공고] 등 대괄호 표기
    r"\([^)]*(긴급|재공고|변경|정정)[^)]*\)",  # (긴급), (재공고)
    r"\b20\d{2}\s*년?\s*(度|년도)?",      # 2026년, 2026년도
    r"\b\d{2}년",                        # 26년
    r"재공고|변경공고|정정공고|입찰공고|긴급공고",
    r"견적제출\s*안내|제출\s*안내",
    r"제?\s*\d+\s*차(수|분)?",            # 제1차, 3차분
    r"★[^★]*★",                         # ★실적보유★
    r"\(총괄\)|\(총액\)|\(계속비\)|\(가칭\)",
]


def normalize_title(t: str) -> str:
    s = str(t)
    for p in _PATTERNS:
        s = re.sub(p, " ", s)
    s = re.sub(r"[^\w가-힣]+", " ", s)   # 특수문자 정리
    return re.sub(r"\s+", " ", s).strip()


df["norm_title"] = df["title"].map(normalize_title)


def evaluate(name, y_true, y_pred):
    return {
        "모델": name,
        "Accuracy": accuracy_score(y_true, y_pred),
        "Macro F1": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "Weighted F1": f1_score(y_true, y_pred, average="weighted", zero_division=0),
        "Balanced Acc": balanced_accuracy_score(y_true, y_pred),
    }


def run(category, kor, min_per_tag=40):
    sub = df[(df["biz_div"] == category) & df["tag"].notna()].copy()
    counts = sub["tag"].value_counts()
    sub = sub[sub["tag"].isin(counts[counts >= min_per_tag].index)]
    if len(sub) < 300:
        print(f"\n[{kor}] 표본 부족 - 건너뜀")
        return

    print(f"\n{'='*74}")
    print(f"[{kor}] {len(sub):,}건, 태그 {sub['tag'].nunique()}종")
    print("=" * 74)

    # ---- 누수 실제 집계 (추정이 아니라 수치로) ----
    n_groups = sub["norm_title"].nunique()
    dup = sub["norm_title"].value_counts()
    n_dup_groups = (dup > 1).sum()
    n_dup_rows = dup[dup > 1].sum()
    print(f"\n[1] 정규화 후 중복 집계")
    print(f"    고유 제목(그룹): {n_groups:,} / 전체 {len(sub):,}")
    print(f"    2건 이상인 그룹: {n_dup_groups:,}개, 해당 공고 {n_dup_rows:,}건 "
          f"({n_dup_rows/len(sub)*100:.1f}%)")
    print(f"    예시:")
    for t in dup[dup > 1].head(3).index:
        orig = sub[sub["norm_title"] == t]["title"].head(2).tolist()
        print(f"      '{t[:40]}' <- {orig}")

    # 랜덤 분할이었다면 이 중 몇 %가 양쪽에 걸쳤을지 시뮬레이션
    rng = np.random.default_rng(42)
    fake_split = rng.random(len(sub)) < 0.7
    tmp = sub.assign(_tr=fake_split)
    straddle = tmp.groupby("norm_title")["_tr"].nunique()
    n_straddle = (straddle > 1).sum()
    rows_straddle = tmp[tmp["norm_title"].isin(straddle[straddle > 1].index)]
    print(f"\n    랜덤 분할 시뮬레이션: {n_straddle:,}개 그룹이 양쪽에 걸침 "
          f"-> {len(rows_straddle):,}건 ({len(rows_straddle)/len(sub)*100:.1f}%) 누수")

    # ---- 그룹 단위 분할 ----
    idx = sub.index.to_numpy()
    Xc = normalize(X[idx])
    y = sub["tag"].to_numpy()
    groups = sub["norm_title"].to_numpy()
    titles = sub["title"].to_numpy()

    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=42)
    tr_idx, rest_idx = next(gss.split(Xc, y, groups))
    gss2 = GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
    v_rel, t_rel = next(gss2.split(Xc[rest_idx], y[rest_idx], groups[rest_idx]))
    val_idx, test_idx = rest_idx[v_rel], rest_idx[t_rel]

    print(f"\n[2] 그룹 단위 분할 (같은 정규화 제목은 한쪽에만)")
    print(f"    Train {len(tr_idx):,} / Val {len(val_idx):,} / Test {len(test_idx):,}")
    assert not (set(groups[tr_idx]) & set(groups[test_idx])), "그룹이 겹침!"

    baseline_tag = pd.Series(y[tr_idx]).value_counts().index[0]
    results = [evaluate("최빈 클래스", y[test_idx],
                        np.full(len(test_idx), baseline_tag))]

    # ---- BGE-M3 + kNN (k는 Val에서만 선택) ----
    print(f"\n[3] k 선택 (Validation)")
    best_k, best_v = None, -1
    for k in (1, 3, 5, 10):
        clf = KNeighborsClassifier(n_neighbors=k, metric="cosine", weights="distance")
        clf.fit(Xc[tr_idx], y[tr_idx])
        v = f1_score(y[val_idx], clf.predict(Xc[val_idx]), average="macro", zero_division=0)
        print(f"    k={k:>2}: Macro F1 {v:.4f}")
        if v > best_v:
            best_k, best_v = k, v
    print(f"    -> k={best_k} 선택")

    knn = KNeighborsClassifier(n_neighbors=best_k, metric="cosine", weights="distance")
    knn.fit(Xc[tr_idx], y[tr_idx])
    pred_knn = knn.predict(Xc[test_idx])
    results.append(evaluate(f"BGE-M3 + kNN(k={best_k})", y[test_idx], pred_knn))

    # ---- TF-IDF + LinearSVC ----
    vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=2, sublinear_tf=True)
    Ttr = vec.fit_transform(titles[tr_idx])
    svc = LinearSVC(class_weight="balanced", random_state=42)
    svc.fit(Ttr, y[tr_idx])
    pred_svc = svc.predict(vec.transform(titles[test_idx]))
    results.append(evaluate("TF-IDF(char2-5) + LinearSVC", y[test_idx], pred_svc))

    print(f"\n[4] 최종 결과 (Test, 한 번만 평가)")
    res = pd.DataFrame(results)
    for c in ("Accuracy", "Macro F1", "Weighted F1", "Balanced Acc"):
        res[c] = res[c].map(lambda v: f"{v:.3f}")
    print(res.to_string(index=False))

    print(f"\n    태그별 (kNN, 상위 8):")
    rep = classification_report(y[test_idx], pred_knn, output_dict=True, zero_division=0)
    rows = [(t, v["precision"], v["recall"], v["f1-score"], int(v["support"]))
            for t, v in rep.items() if t not in ("accuracy", "macro avg", "weighted avg")]
    rows.sort(key=lambda r: -r[4])
    print(f"      {'태그':>5} {'정밀도':>7} {'재현율':>7} {'F1':>7} {'건수':>5}")
    for t, p, r, f, s in rows[:8]:
        print(f"      {t:>5} {p:>7.2f} {r:>7.2f} {f:>7.2f} {s:>5}")


for cat, kor in [("servc", "용역"), ("thng", "물품")]:
    run(cat, kor)
