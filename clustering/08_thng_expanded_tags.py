# -*- coding: utf-8 -*-
"""물품 태그에 '기타'를 추가하고 재검증한다 (07_servc_expanded_tags.py의 물품판).

용역에서 확인한 문제가 물품에도 그대로 있다:
  LinearSVC는 주어진 태그 중 반드시 하나를 고른다. '기타'가 없으면 학습에 없던
  유형이 들어와도 억지로 20종 중 하나에 배정된다. 실제로 06번 적용 결과에서
  "계류구 제조"가 인쇄·사인물로, "데크로드"가 공조·냉난방으로 분류됐고
  신뢰도 0.3 미만이 37.7%였다.

30건 미만 소수 태그를 '기타'로 묶어 모델이 "애매하다"를 학습할 수 있게 한다.
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

BASE = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"

# 세부품명번호 앞2자리 -> 서비스 태그. 11(토목자재)과 30(건설자재)은 성격이 같아 합친다.
TAG_NAMES = {
    "41": "실험·분석장비", "30": "토목·건설자재", "11": "토목·건설자재",
    "43": "IT·통신장비", "40": "공조·냉난방", "46": "안전·보안장비",
    "25": "차량·건설장비", "39": "전기·수배전", "42": "의료장비",
    "23": "산업·정밀기계", "60": "전시·교육기자재", "24": "운반·저장장비",
    "53": "피복·군장품", "51": "의약품·백신", "12": "시약·화학소모품",
    "26": "발전·전지", "55": "인쇄·사인물", "47": "환경·수처리설비",
    "56": "가구·침구", "50": "식품·급식",
}
MIN_COUNT = 30   # 이 미만인 코드는 기타로 (용역과 동일 기준)

env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

df = pd.read_csv(CACHE / "meta_dedup.csv")
X = np.load(CACHE / "vectors_dedup.npy")

conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                        user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
raw = pd.read_sql("""
    SELECT DISTINCT ON (b.bid_id) b.bid_id, left(e->>'code', 2) AS code
    FROM bid_table b, jsonb_array_elements(b.item_codes) e
    WHERE b.bid_category='thng' AND e->>'type'='세부품명번호'
      AND e->>'code' ~ '^[0-9]{8,}'
    ORDER BY b.bid_id
""", conn)
conn.close()

counts = raw["code"].value_counts()
raw["tag"] = raw["code"].map(TAG_NAMES)
# 매핑에 있어도 30건 미만이면 기타로 내린다(용역과 같은 규칙)
small = counts[counts < MIN_COUNT].index
raw.loc[raw["code"].isin(small) | raw["tag"].isna(), "tag"] = "기타"

df = df.merge(raw[["bid_id", "tag"]], on="bid_id", how="left")
sub = df[(df["biz_div"] == "thng") & df["tag"].notna()].copy()

print("=" * 74)
print(f"물품 태그: 20종 -> {sub['tag'].nunique()}종 (기타 포함), 학습 {len(sub):,}건")
print("=" * 74)
print(sub["tag"].value_counts().to_string())

_PAT = [r"\[[^\]]*\]", r"\([^)]*(긴급|재공고|변경|정정)[^)]*\)", r"\b20\d{2}\s*년?\s*(度|년도)?",
        r"\b\d{2}년", r"재공고|변경공고|정정공고|입찰공고|긴급공고", r"제?\s*\d+\s*차(수|분)?",
        r"★[^★]*★", r"\(총괄\)|\(총액\)|\(계속비\)|\(가칭\)"]


def norm(t):
    s = str(t)
    for p in _PAT:
        s = re.sub(p, " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]+", " ", s)).strip()


sub["norm"] = sub["title"].map(norm)

idx = sub.index.to_numpy()
Xc = normalize(X[idx])
y = sub["tag"].to_numpy()
groups = sub["norm"].to_numpy()
titles = sub["title"].to_numpy()

gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=42)
tr, rest = next(gss.split(Xc, y, groups))
v_rel, t_rel = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                    .split(Xc[rest], y[rest], groups[rest]))
val, test = rest[v_rel], rest[t_rel]
print(f"\nTrain {len(tr):,} / Val {len(val):,} / Test {len(test):,}")


def ev(name, yt, yp):
    return {"모델": name, "Accuracy": accuracy_score(yt, yp),
            "Macro F1": f1_score(yt, yp, average="macro", zero_division=0),
            "Balanced Acc": balanced_accuracy_score(yt, yp)}


results = [ev("최빈 클래스", y[test],
              np.full(len(test), pd.Series(y[tr]).value_counts().index[0]))]

best_k, best_v = None, -1
for k in (1, 3, 5, 10):
    m = KNeighborsClassifier(n_neighbors=k, metric="cosine", weights="distance").fit(Xc[tr], y[tr])
    v = f1_score(y[val], m.predict(Xc[val]), average="macro", zero_division=0)
    if v > best_v:
        best_k, best_v = k, v
knn = KNeighborsClassifier(n_neighbors=best_k, metric="cosine", weights="distance").fit(Xc[tr], y[tr])
results.append(ev(f"BGE-M3 + kNN(k={best_k})", y[test], knn.predict(Xc[test])))

vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=2, sublinear_tf=True)
svc = LinearSVC(class_weight="balanced", random_state=42).fit(vec.fit_transform(titles[tr]), y[tr])
pred = svc.predict(vec.transform(titles[test]))
results.append(ev("TF-IDF + LinearSVC", y[test], pred))

print("\n[결과] (기타 없던 20종일 때: kNN 0.639 / TF-IDF 0.644)")
res = pd.DataFrame(results)
for c in ("Accuracy", "Macro F1", "Balanced Acc"):
    res[c] = res[c].map(lambda v: f"{v:.3f}")
print(res.to_string(index=False))

print("\n[태그별 상세 - TF-IDF]")
rep = classification_report(y[test], pred, output_dict=True, zero_division=0)
rows = [(t, v["precision"], v["recall"], v["f1-score"], int(v["support"]))
        for t, v in rep.items() if t not in ("accuracy", "macro avg", "weighted avg")]
rows.sort(key=lambda r: -r[4])
print(f"  {'태그':>14} {'정밀도':>7} {'재현율':>7} {'F1':>7} {'건수':>5}")
for t, p, r, f, s in rows:
    print(f"  {t:>14} {p:>7.2f} {r:>7.2f} {f:>7.2f} {s:>5}")
