# -*- coding: utf-8 -*-
"""임베딩 유사도로 태그를 맞출 수 있는지 검증한다.

정답지: 조달청 세부품명번호 앞2자리 (사람이 태깅한 게 아니라 공식 코드라 신뢰도 높음)
방법:   코드가 있는 공고를 학습/시험으로 나눠, 시험 공고마다 가장 비슷한 학습 공고들을
        찾아 다수결로 태그를 정하고 정답과 대조한다 (kNN 분류).

이 테스트가 잘 나오면 -> OpenSearch 유사도 방식으로 진행
안 나오면            -> 임베딩 자체의 한계이므로 LLM 태깅으로 전환
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import normalize

BASE = Path(r"C:\Users\user\Desktop\PROJECTS\bidding-agent")
CACHE = BASE / "clustering" / "cache"

env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

X = np.load(CACHE / "vectors_dedup.npy")
df = pd.read_csv(CACHE / "meta_dedup.csv")
assert len(X) == len(df)

# 조달청 코드(정답지) 가져오기 - 세부품명번호 앞2자리를 태그로 본다
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

MIN_PER_TAG = 40   # 이보다 적은 태그는 학습/시험 분할이 무의미해 제외


def run(category, kor):
    sub = df[(df["biz_div"] == category) & df["tag"].notna()].copy()
    counts = sub["tag"].value_counts()
    keep = counts[counts >= MIN_PER_TAG].index
    sub = sub[sub["tag"].isin(keep)]

    if len(sub) < 200:
        print(f"\n[{kor}] 표본 부족 ({len(sub)}건) - 건너뜀")
        return

    idx = sub.index.to_numpy()
    Xc = normalize(X[idx])
    y = sub["tag"].to_numpy()

    print(f"\n{'='*70}")
    print(f"[{kor}] 코드 보유 {len(sub):,}건, 태그 {len(keep)}종 ({MIN_PER_TAG}건 이상)")

    X_tr, X_te, y_tr, y_te = train_test_split(
        Xc, y, test_size=0.3, random_state=42, stratify=y)
    print(f"  학습(대표사례) {len(X_tr):,}건 / 시험 {len(X_te):,}건")

    # 항상 최빈 태그로만 찍었을 때의 정확도 - 이보다 나아야 의미가 있다
    baseline = pd.Series(y_te).value_counts(normalize=True).iloc[0]

    print(f"\n  {'k':>3} {'정확도':>8}")
    best = None
    for k in (1, 3, 5, 10):
        clf = KNeighborsClassifier(n_neighbors=k, metric="cosine", weights="distance")
        clf.fit(X_tr, y_tr)
        acc = clf.score(X_te, y_te)
        print(f"  {k:>3} {acc:>7.1%}")
        if best is None or acc > best[1]:
            best = (k, acc, clf)

    k, acc, clf = best
    print(f"\n  최고: k={k}, 정확도 {acc:.1%}")
    print(f"  기준선(최빈 태그로만 찍기): {baseline:.1%}")
    print(f"  -> 기준선 대비 {acc - baseline:+.1%}p")

    print(f"\n  태그별 상세 (상위 10개):")
    rep = classification_report(y_te, clf.predict(X_te), output_dict=True, zero_division=0)
    rows = [(t, v["precision"], v["recall"], v["f1-score"], int(v["support"]))
            for t, v in rep.items() if t not in ("accuracy", "macro avg", "weighted avg")]
    rows.sort(key=lambda r: -r[4])
    print(f"    {'태그':>5} {'정밀도':>7} {'재현율':>7} {'F1':>7} {'건수':>6}")
    for t, p, r, f, s in rows[:10]:
        print(f"    {t:>5} {p:>7.2f} {r:>7.2f} {f:>7.2f} {s:>6}")


for cat, kor in [("servc", "용역"), ("thng", "물품")]:
    run(cat, kor)
