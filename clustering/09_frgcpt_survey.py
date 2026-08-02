# -*- coding: utf-8 -*-
"""외자(frgcpt) 565건의 성격을 파악해 태그 목록을 만들 근거를 뽑는다.

외자는 조달청 코드가 3건(0.5%)뿐이라 코드에서 태그를 유도할 수 없다.
제목을 직접 훑어서 사람이 목록을 정해야 한다.
"""
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import psycopg2

BASE = Path(r"C:\Users\user\Desktop\PROJECTS\bidding-agent")
env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")

conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                        user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
df = pd.read_sql("""
    SELECT bid_ntce_nm AS title, ntce_instt_nm AS instt, bdgt_amt, presmpt_prce
    FROM bid_table WHERE bid_category='frgcpt'
""", conn)
conn.close()

print(f"외자 {len(df):,}건 (중복 포함)")
print(f"고유 제목 {df['title'].nunique():,}건\n")

# 자주 나오는 키워드 - 태그 후보를 찾는 단서
print("=" * 74)
print("자주 등장하는 단어 (2글자 이상)")
print("=" * 74)
words = Counter()
for t in df["title"].drop_duplicates():
    for w in re.findall(r"[가-힣]{2,}", str(t)):
        words[w] += 1
for w, n in words.most_common(40):
    print(f"  {w:<14} {n:>4}", end="")
    if list(words.most_common(40)).index((w, n)) % 3 == 2:
        print()
print("\n")

print("=" * 74)
print("발주기관 상위 15 - 어떤 기관이 외자를 사는지")
print("=" * 74)
for inst, n in df["instt"].value_counts().head(15).items():
    print(f"  {n:>4}건  {str(inst)[:56]}")

print("\n" + "=" * 74)
print("무작위 표본 60건")
print("=" * 74)
for t in df["title"].drop_duplicates().sample(min(60, df["title"].nunique()),
                                              random_state=42):
    print(f"  {str(t)[:76]}")
