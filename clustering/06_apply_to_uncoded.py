# -*- coding: utf-8 -*-
"""학습한 분류기를 '코드가 없는 공고'에 실제로 적용하고 결과를 검증한다.

지금까지의 검증은 코드가 있는 공고 안에서만 이뤄졌다. 하지만 실제 목적은
코드가 없는 공고(물품 51.9%, 용역 83.7%)에 태그를 붙이는 것이고,
두 집단의 성격이 다르면 실험 성능이 그대로 재현되지 않는다.

확인할 것:
  1. 예측이 특정 태그로 쏠리는가 (학습 분포와 예측 분포 비교)
  2. 신뢰도가 낮은 건이 얼마나 되는가 (자동 확정 / 검수 기준 근거)
  3. 실제 예측 결과가 사람 눈에 말이 되는가
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC

BASE = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"
OUT = Path(__file__).resolve().parent / "outputs"
OUT.mkdir(exist_ok=True)

env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

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

# 물품 코드 앞2자리 -> 사람이 읽을 이름 (8. 태그 체계 페이지 초안)
TAG_NAMES = {
    "41": "실험·분석장비", "30": "토목·건설자재", "43": "IT·통신장비",
    "40": "공조·냉난방", "46": "안전·보안장비", "25": "차량·건설장비",
    "39": "전기·수배전", "42": "의료장비", "23": "산업·정밀기계",
    "60": "전시·교육기자재", "24": "운반·저장장비", "53": "피복·군장품",
    "51": "의약품·백신", "12": "시약·화학소모품", "26": "발전·전지",
    "55": "인쇄·사인물", "47": "환경·수처리설비", "56": "가구·침구",
    "11": "토목·건설자재", "50": "식품·급식",
    "81": "IT시스템", "80": "행사·전시대행", "82": "홍보·콘텐츠",
    "78": "운송·차량임차", "76": "청소·경비", "72": "전시연출", "55s": "발간·인쇄",
}


def run(category, kor, min_per_tag=40, n_show=6):
    sub = df[df["biz_div"] == category]
    coded = sub[sub["tag"].notna()].copy()
    counts = coded["tag"].value_counts()
    coded = coded[coded["tag"].isin(counts[counts >= min_per_tag].index)]
    uncoded = sub[sub["tag"].isna()].copy()

    print("=" * 78)
    print(f"[{kor}] 학습 {len(coded):,}건({coded['tag'].nunique()}종) "
          f"-> 적용 대상 {len(uncoded):,}건")
    print("=" * 78)

    vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=2, sublinear_tf=True)
    Xtr = vec.fit_transform(coded["title"])
    clf = LinearSVC(class_weight="balanced", random_state=42)
    clf.fit(Xtr, coded["tag"])

    Xte = vec.transform(uncoded["title"])
    pred = clf.predict(Xte)
    # LinearSVC는 확률을 안 주므로 결정함수 1위와 2위의 차이를 신뢰도로 쓴다.
    # 차이가 작으면 두 태그 사이에서 헷갈린 것이다.
    margins = np.sort(clf.decision_function(Xte), axis=1)
    conf = margins[:, -1] - margins[:, -2]
    uncoded = uncoded.assign(pred=pred, conf=conf)

    # 1. 쏠림 확인 - 학습 분포와 예측 분포가 크게 다르면 의심해야 한다
    print("\n[1] 학습 분포 vs 예측 분포 (상위 8)")
    tr_d = coded["tag"].value_counts(normalize=True)
    pr_d = uncoded["pred"].value_counts(normalize=True)
    print(f"    {'태그':>18} {'학습':>7} {'예측':>7} {'차이':>7}")
    for t in pr_d.head(8).index:
        name = f"{t} {TAG_NAMES.get(t, '?')}"
        print(f"    {name:>18} {tr_d.get(t, 0):>6.1%} {pr_d[t]:>6.1%} "
              f"{pr_d[t] - tr_d.get(t, 0):>+6.1%}")

    # 2. 신뢰도 분포 - 자동 확정 기준을 정하는 근거
    print("\n[2] 신뢰도(1위-2위 점수차) 분포")
    for q in (10, 25, 50, 75, 90):
        print(f"    p{q:<2}: {np.percentile(conf, q):.3f}")
    for th in (0.1, 0.3, 0.5):
        n = (conf < th).sum()
        print(f"    {th} 미만: {n:,}건 ({n/len(conf)*100:.1f}%) <- 검수 대상 후보")

    # 3. 실제 예측 결과 - 신뢰도 높은 것과 낮은 것을 나눠서 본다
    print("\n[3] 신뢰도 높은 예측 (상위)")
    for t in pr_d.head(5).index:
        g = uncoded[uncoded["pred"] == t].nlargest(n_show, "conf")
        print(f"\n  == {t} {TAG_NAMES.get(t, '?')} ({(pred == t).sum():,}건) ==")
        for _, r in g.iterrows():
            print(f"    [{r['conf']:.2f}] {r['title'][:62]}")

    print("\n[4] 신뢰도 낮은 예측 (하위 10건) - 검수가 필요한 유형")
    for _, r in uncoded.nsmallest(10, "conf").iterrows():
        print(f"    [{r['conf']:.2f}] {r['pred']} {TAG_NAMES.get(r['pred'], '?'):<12} "
              f"{r['title'][:52]}")

    uncoded[["bid_id", "title", "pred", "conf"]].to_csv(
        OUT / f"predicted_{category}.csv", index=False, encoding="utf-8-sig")
    print(f"\n  저장: outputs/predicted_{category}.csv")
    print()


for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    run(cat, kor)
