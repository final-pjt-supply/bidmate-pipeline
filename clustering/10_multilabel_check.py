# -*- coding: utf-8 -*-
"""다중 태그가 실제로 필요한지 데이터로 확인한다.

주/부 태그 구조를 만들기 전에, 정말로 두 태그에 해당하는 공고가 있는지 봐야 한다.
확인할 것:
  1. 세부품명번호가 2개 이상(다른 앞2자리) 붙은 공고 비율과 조합
  2. 그 공고들의 실제 제목 - 정말 두 성격인지, 코드 오류인지
  3. 모델이 헷갈린 공고 - 1위와 2위가 박빙인 건들이 실제로 애매한지
"""
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC

BASE = Path(r"C:\Users\user\Desktop\PROJECTS\bidding-agent")
CACHE = BASE / "clustering" / "cache"

env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

TAG_NAMES = {
    "41": "실험·분석장비", "30": "토목·건설자재", "11": "토목·건설자재",
    "43": "IT·통신장비", "40": "공조·냉난방", "46": "안전·보안장비",
    "25": "차량·건설장비", "39": "전기·수배전", "42": "의료장비",
    "23": "산업·정밀기계", "60": "전시·교육기자재", "24": "운반·저장장비",
    "53": "피복·군장품", "51": "의약품·백신", "12": "시약·화학소모품",
    "26": "발전·전지", "55": "인쇄·사인물", "47": "환경·수처리설비",
    "56": "가구·침구", "50": "식품·급식",
}

conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                        user=env["RDS_Username"], password=env["RDS_Password"], port=5432)

print("=" * 76)
print("1. 세부품명번호가 여러 그룹에 걸친 공고")
print("=" * 76)

for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    multi = pd.read_sql(f"""
        SELECT b.bid_id, b.bid_ntce_nm AS title,
               array_agg(DISTINCT left(e->>'code',2)) AS codes
        FROM bid_table b, jsonb_array_elements(b.item_codes) e
        WHERE b.bid_category='{cat}' AND e->>'type'='세부품명번호'
          AND e->>'code' ~ '^[0-9]{{8,}}'
        GROUP BY b.bid_id, b.bid_ntce_nm
        HAVING count(DISTINCT left(e->>'code',2)) > 1
    """, conn)
    total = pd.read_sql(f"""
        SELECT count(DISTINCT b.bid_id) AS n
        FROM bid_table b, jsonb_array_elements(b.item_codes) e
        WHERE b.bid_category='{cat}' AND e->>'type'='세부품명번호'
          AND e->>'code' ~ '^[0-9]{{8,}}'
    """, conn)["n"][0]

    print(f"\n[{kor}] 코드 보유 {total:,}건 중 2개 그룹 이상: "
          f"{len(multi):,}건 ({len(multi)/total*100:.1f}%)")

    if len(multi):
        combos = Counter(tuple(sorted(c)) for c in multi["codes"])
        print("  자주 나오는 조합:")
        for combo, n in combos.most_common(6):
            names = " + ".join(TAG_NAMES.get(c, c) for c in combo)
            print(f"    {n:>3}건  {names}")
        print("\n  실제 제목 (10건):")
        for _, r in multi.head(10).iterrows():
            names = "+".join(TAG_NAMES.get(c, c) for c in sorted(r["codes"]))
            print(f"    [{names}]")
            print(f"      {r['title'][:66]}")

conn.close()

print("\n" + "=" * 76)
print("2. 모델이 헷갈린 공고 - 1위와 2위가 박빙인 건")
print("=" * 76)

# 물품 모델을 다시 학습해 결정함수 상위 2개를 본다
conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                        user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
raw = pd.read_sql("""
    SELECT DISTINCT ON (b.bid_id) b.bid_id, left(e->>'code', 2) AS code
    FROM bid_table b, jsonb_array_elements(b.item_codes) e
    WHERE b.bid_category='thng' AND e->>'type'='세부품명번호'
      AND e->>'code' ~ '^[0-9]{8,}' ORDER BY b.bid_id
""", conn)
conn.close()

df = pd.read_csv(CACHE / "meta_dedup.csv")
counts = raw["code"].value_counts()
raw["tag"] = raw["code"].map(TAG_NAMES)
raw.loc[raw["code"].isin(counts[counts < 30].index) | raw["tag"].isna(), "tag"] = "기타"
df = df.merge(raw[["bid_id", "tag"]], on="bid_id", how="left")
sub = df[(df["biz_div"] == "thng") & df["tag"].notna()].reset_index(drop=True)

vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=2, sublinear_tf=True)
Xt = vec.fit_transform(sub["title"])
clf = LinearSVC(class_weight="balanced", random_state=42).fit(Xt, sub["tag"])

scores = clf.decision_function(Xt)
order = np.argsort(scores, axis=1)
top1 = clf.classes_[order[:, -1]]
top2 = clf.classes_[order[:, -2]]
margin = scores[np.arange(len(scores)), order[:, -1]] - scores[np.arange(len(scores)), order[:, -2]]

close = pd.DataFrame({"title": sub["title"], "실제": sub["tag"],
                      "1위": top1, "2위": top2, "차이": margin})
close = close[close["차이"] < 0.15].sort_values("차이")

print(f"\n1-2위 점수차 0.15 미만: {len(close):,}건 / {len(sub):,}건 "
      f"({len(close)/len(sub)*100:.1f}%)")
print("\n  실제로 두 태그에 다 걸치는지 확인 (15건):")
for _, r in close.head(15).iterrows():
    print(f"    [{r['1위']} vs {r['2위']}]  실제={r['실제']}")
    print(f"      {r['title'][:66]}")

print("\n  자주 헷갈리는 태그 쌍:")
pairs = Counter(tuple(sorted([a, b])) for a, b in zip(close["1위"], close["2위"]))
for (a, b), n in pairs.most_common(8):
    print(f"    {n:>3}건  {a} <-> {b}")
