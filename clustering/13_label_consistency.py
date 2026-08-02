# -*- coding: utf-8 -*-
"""정답(조달청 코드) 자체가 얼마나 일관적인지 잰다.

LLM이 제목만으로 진 이유를 "정답이 의미가 아니라 관행"이라고 설명했는데,
추측으로 두면 안 된다. 확인 방법:

  같은(정규화된) 제목에 서로 다른 코드가 붙었다면, 그건 제목에서 유도할 수 없는
  정보다. 어떤 모델도 두 건을 동시에 맞출 수 없다 - 정답의 상한선이 된다.

실행: .venv/Scripts/python.exe clustering/13_label_consistency.py
"""
import re
from pathlib import Path

import pandas as pd
import psycopg2

BASE = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"

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


def norm(t):
    s = str(t)
    for p in _PAT:
        s = re.sub(p, " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]+", " ", s)).strip()


env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                        user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
meta = pd.read_csv(CACHE / "meta_dedup.csv")

for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    if cat == "thng":
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

    df = meta.merge(raw[["bid_id", "tag"]], on="bid_id", how="left")
    sub = df[(df["biz_div"] == cat) & df["tag"].notna()].copy()
    sub["norm"] = sub["title"].map(norm)

    # 같은 정규화 제목이 2건 이상인 그룹만 본다
    g = sub.groupby("norm")["tag"].agg(["nunique", "count"])
    dup = g[g["count"] > 1]
    conflict = dup[dup["nunique"] > 1]

    n_rows_conflict = sub[sub["norm"].isin(conflict.index)].shape[0]
    print("=" * 72)
    print(f"[{kor}] 학습 대상 {len(sub):,}건")
    print("=" * 72)
    print(f"  제목이 겹치는 그룹        {len(dup):,}개")
    print(f"  그중 태그가 갈린 그룹     {len(conflict):,}개 "
          f"({len(conflict)/max(len(dup),1)*100:.1f}%)")
    print(f"  해당 공고 수             {n_rows_conflict:,}건 "
          f"({n_rows_conflict/len(sub)*100:.1f}%)")

    if len(conflict):
        print(f"\n  실제 사례 (같은 제목, 다른 태그)")
        for nm in conflict.sort_values("count", ascending=False).head(6).index:
            rows = sub[sub["norm"] == nm]
            tags = rows["tag"].value_counts().to_dict()
            print(f"    {rows['title'].iloc[0][:52]}")
            print(f"      -> {tags}")
    print()

conn.close()
