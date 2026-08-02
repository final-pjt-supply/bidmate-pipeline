# -*- coding: utf-8 -*-
"""LLM이 TF-IDF에 진 원인을 분해한다.

11번 결과에서 눈에 띈 것: 오답 사례에 '정답=기타'가 유독 많다.
LLM은 목록에 그럴듯한 이름이 있으면 '기타'를 잘 고르지 않는 것으로 보인다.
추측으로 두지 않고 집계로 확인한다.

실행: .venv/Scripts/python.exe clustering/12_llm_error_analysis.py
"""
from collections import Counter
from pathlib import Path

import pandas as pd
from sklearn.metrics import f1_score

OUT = Path(__file__).resolve().parent / "outputs"

for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    df = pd.read_csv(OUT / f"llm_vs_tfidf_{cat}.csv")
    print("=" * 72)
    print(f"[{kor}] {len(df):,}건")
    print("=" * 72)

    # 1. '기타' 예측 성향 - LLM이 기타를 회피하는가
    n_true = (df["tag"] == "기타").sum()
    print(f"\n1. '기타' 예측 성향")
    print(f"   정답이 기타인 건수      {n_true:>4} ({n_true/len(df)*100:.1f}%)")
    for col, name in [("llm", "LLM"), ("tfidf", "TF-IDF")]:
        pred_etc = (df[col] == "기타").sum()
        hit = ((df["tag"] == "기타") & (df[col] == "기타")).sum()
        print(f"   {name:<7} 기타로 예측  {pred_etc:>4}건  "
              f"(정답 기타 중 {hit}/{n_true} = 재현율 {hit/max(n_true,1):.2f})")

    # 2. 기타를 뺀 나머지에서만 비교 - 기타가 원인의 전부인지 확인
    non_etc = df[df["tag"] != "기타"]
    print(f"\n2. 정답이 기타가 아닌 {len(non_etc):,}건만으로 재계산")
    for col, name in [("llm", "LLM"), ("tfidf", "TF-IDF")]:
        acc = (non_etc[col] == non_etc["tag"]).mean()
        f1 = f1_score(non_etc["tag"], non_etc[col], average="macro", zero_division=0)
        print(f"   {name:<7} Accuracy {acc:.3f}  Macro F1 {f1:.3f}")

    # 3. LLM만 틀린 건과 TF-IDF만 틀린 건
    llm_ok = df["llm"] == df["tag"]
    tf_ok = df["tfidf"] == df["tag"]
    print(f"\n3. 누가 어디서 틀리나")
    print(f"   둘 다 정답        {(llm_ok & tf_ok).sum():>4}")
    print(f"   LLM만 정답        {(llm_ok & ~tf_ok).sum():>4}")
    print(f"   TF-IDF만 정답     {(~llm_ok & tf_ok).sum():>4}")
    print(f"   둘 다 오답        {(~llm_ok & ~tf_ok).sum():>4}")

    # 4. LLM이 자주 빠지는 오답 방향
    wrong = df[~llm_ok]
    pairs = Counter(zip(wrong["tag"], wrong["llm"]))
    print(f"\n4. LLM이 자주 틀리는 방향 (정답 -> LLM)")
    for (t, p), n in pairs.most_common(6):
        print(f"   {n:>3}건  {t} -> {p}")

    # 5. LLM만 맞춘 건 - LLM이 실제로 나은 지점이 있는가
    only_llm = df[llm_ok & ~tf_ok]
    if len(only_llm):
        print(f"\n5. LLM만 맞춘 사례 (5건)")
        for _, r in only_llm.head(5).iterrows():
            print(f"   정답={r['tag']:<12} TF-IDF={r['tfidf']:<12} {str(r['title'])[:40]}")
    print()
