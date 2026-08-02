# -*- coding: utf-8 -*-
"""용역 태그를 9종으로 보완하고 재검증한다.

이전 문제:
  세부품명번호만 써서 40건 이상 태그가 4종뿐이었다. 그 결과 조사·연구(1,240건)처럼
  큰 유형이 통째로 빠져, "환경오염 건강영향조사"가 IT시스템으로 분류됐다.

보완:
  업종코드를 함께 사용해 13종으로 넓히고, 성격이 겹치는 것을 묶어 9종으로 정리했다.
  30건 미만 소수 태그(155종, 702건)는 '기타'로 통합해 모델이 "이건 애매하다"를
  학습할 수 있게 했다.
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
JUNK = ("9999", "9901", "9902", "9903", "9900")

# 원본 코드 -> 서비스 태그. 성격이 겹치는 코드를 하나로 묶는다.
TAG_MAP = {
    "P81": "IT시스템",     "B1468": "IT시스템",        # 시스템 구축·운영·유지관리
    "B1169": "조사·연구",
    "P80": "행사·전시대행", "B5720": "행사·전시대행",   # 국제행사도 행사대행
    "P82": "홍보·콘텐츠",   "B1469": "홍보·콘텐츠", "B3244": "홍보·콘텐츠",
    "B6146": "감리·컨설팅", "B6525": "감리·컨설팅",     # PMO·감리·정보보호 컨설팅
    "P78": "운송·차량임차",
    "B6728": "폐기물처리",
    "B1458": "통신망",
}
MIN_COUNT = 30   # 이 미만인 코드는 기타로

env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

df = pd.read_csv(CACHE / "meta_dedup.csv")
X = np.load(CACHE / "vectors_dedup.npy")

conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                        user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
# 세부품명번호를 업종코드보다 우선한다(품목 분류가 더 구체적)
raw = pd.read_sql(f"""
    SELECT b.bid_id,
           COALESCE(
             MAX(CASE WHEN e->>'type'='세부품명번호' AND e->>'code' ~ '^[0-9]{{8,}}'
                      THEN 'P' || left(e->>'code',2) END),
             MAX(CASE WHEN e->>'type'='업종코드' AND e->>'code' NOT IN {JUNK}
                      THEN 'B' || (e->>'code') END)
           ) AS code
    FROM bid_table b, jsonb_array_elements(b.item_codes) e
    WHERE b.bid_category='servc'
    GROUP BY b.bid_id
""", conn)
conn.close()

raw = raw[raw["code"].notna()]
counts = raw["code"].value_counts()
# 매핑에 있으면 그 태그, 없고 30건 미만이면 기타, 없는데 30건 이상이면 누락 경고
raw["tag"] = raw["code"].map(TAG_MAP)
missing = counts[(counts >= MIN_COUNT) & (~counts.index.isin(TAG_MAP))]
if len(missing):
    print("!! 30건 이상인데 매핑 안 된 코드:", dict(missing))
raw.loc[raw["tag"].isna(), "tag"] = "기타"

df = df.merge(raw[["bid_id", "tag"]], on="bid_id", how="left")
sub = df[(df["biz_div"] == "servc") & df["tag"].notna()].copy()

print("=" * 74)
print(f"용역 태그 보완: 4종 -> {sub['tag'].nunique()}종, 학습 대상 {len(sub):,}건")
print("=" * 74)
print(sub["tag"].value_counts().to_string())

# 제목 정규화 (누수 방지용 그룹 키)
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
pred_svc = svc.predict(vec.transform(titles[test]))
results.append(ev("TF-IDF + LinearSVC", y[test], pred_svc))

print(f"\n[결과] (태그 4종일 때: kNN 0.899 / TF-IDF 0.945 - 직접 비교 불가, 태그 수가 다름)")
res = pd.DataFrame(results)
for c in ("Accuracy", "Macro F1", "Balanced Acc"):
    res[c] = res[c].map(lambda v: f"{v:.3f}")
print(res.to_string(index=False))

print("\n[태그별 상세 - TF-IDF]")
rep = classification_report(y[test], pred_svc, output_dict=True, zero_division=0)
rows = [(t, v["precision"], v["recall"], v["f1-score"], int(v["support"]))
        for t, v in rep.items() if t not in ("accuracy", "macro avg", "weighted avg")]
rows.sort(key=lambda r: -r[4])
print(f"  {'태그':>12} {'정밀도':>7} {'재현율':>7} {'F1':>7} {'건수':>5}")
for t, p, r, f, s in rows:
    print(f"  {t:>12} {p:>7.2f} {r:>7.2f} {f:>7.2f} {s:>5}")
