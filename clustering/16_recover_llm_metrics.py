# -*- coding: utf-8 -*-
"""저장된 예측 CSV에서 LLM 실험 수치를 다시 계산해 metrics.jsonl에 남긴다.

11번은 처음에 결과를 화면에만 출력했고, few-shot 스윕 여덟 번의 수치를 사람이
손으로 옮겨 적었다(results_llm_fewshot_sweep.txt). 그러면 오타를 잡을 수 없다.

다행히 예측 CSV는 매 실행마다 저장돼 있어 LLM을 다시 부르지 않고 재계산할 수 있다.
손으로 적은 값과 대조해 어긋나는 게 있으면 표시한다.

한계: 모델 비교 실행(qwen3-vl, nova-pro)은 파일명에 모델이 없어 서로 덮어썼다.
      마지막 실행분만 남아 있어 재계산 불가다. 11번의 파일명 규칙은 고쳤다.

실행: .venv/Scripts/python.exe clustering/16_recover_llm_metrics.py
"""
import re
from pathlib import Path

import pandas as pd
from sklearn.metrics import balanced_accuracy_score, f1_score

from _record import record

OUT = Path(__file__).resolve().parent / "outputs"

# results_llm_fewshot_sweep.txt에 손으로 적어둔 값 (category, few_shot) -> Macro F1
HANDWRITTEN = {
    ("thng", 8): 0.627, ("thng", 60): 0.720, ("thng", 200): 0.732, ("thng", 500): 0.746,
    ("servc", 8): 0.660, ("servc", 60): 0.662, ("servc", 200): 0.634, ("servc", 500): 0.623,
}

# 모델 비교 실행이 파일명에 모델을 안 넣어 같은 파일을 세 번 덮어썼다.
# servc/fs60 파일에 남은 건 마지막 실행분(nova-pro)이므로 qwen 손기록과 대조하면 안 된다.
OVERWRITTEN = {("servc", 60)}

rows, mismatch = [], 0
for p in sorted(OUT.glob("llm_vs_tfidf_*.csv")):
    m = re.match(r"llm_vs_tfidf_(thng|servc)(?:_fs(\d+))?(?:_(.+))?\.csv$", p.name)
    if not m:
        continue
    cat = m.group(1)
    fs = int(m.group(2)) if m.group(2) else 8      # fs 접미사 없는 것은 최초 8개 실행
    model = m.group(3) or "qwen-qwen3-next-80b-a3b"

    df = pd.read_csv(p)
    y = df["tag"]
    r = {"file": p.name, "category": cat, "few_shot": fs, "model": model, "n": len(df)}
    for col in ("llm", "tfidf"):
        r[f"{col}_macro_f1"] = round(f1_score(y, df[col], average="macro", zero_division=0), 4)
        r[f"{col}_balanced_acc"] = round(balanced_accuracy_score(y, df[col]), 4)
        r[f"{col}_accuracy"] = round((df[col] == y).mean(), 4)

    if m.group(3) is None and (cat, fs) in OVERWRITTEN:
        r["model"] = "불명(덮어쓰기, 마지막 실행분)"
        r["비고"] = "모델 비교 3회가 이 파일을 덮어씀 - 대조 불가"
    else:
        hand = HANDWRITTEN.get((cat, fs))
        if hand is not None:
            diff = abs(r["llm_macro_f1"] - hand)
            r["손기록"] = hand
            r["차이"] = round(diff, 4)
            if diff > 0.002:                        # 반올림 오차를 넘어서면 불일치
                r["불일치"] = True
                mismatch += 1
    rows.append(r)

res = pd.DataFrame(rows).sort_values(["category", "few_shot"])
print("=" * 96)
print("저장된 예측 CSV에서 재계산한 값 vs 손으로 적은 값")
print("=" * 96)
cols = ["category", "few_shot", "model", "n", "llm_macro_f1", "llm_balanced_acc",
        "tfidf_macro_f1", "손기록", "차이"]
print(res[[c for c in cols if c in res]].to_string(index=False))

print(f"\n대조 가능한 {res['손기록'].notna().sum()}건 중 불일치 {mismatch}건")
if mismatch == 0:
    print("손으로 옮긴 수치는 모두 맞았다. 다만 앞으로는 자동 기록에 의존한다.")

for r in rows:
    record("llm_metrics_recovered", **{k: v for k, v in r.items() if k != "불일치"})
print(f"\n{len(rows)}건을 outputs/metrics.jsonl에 기록했다.")

print("\n[복구 불가]")
print("  모델 비교 3회(qwen3-next / qwen3-vl-235b / nova-pro)가 같은 파일명을 써서")
print("  servc_fs60 파일을 서로 덮어썼다. 남은 건 마지막 실행분 하나뿐이라,")
print("  results_llm_model_comparison.txt의 세 수치는 손기록만 남아 있다.")
print("  11번 파일명 규칙은 모델 슬러그를 포함하도록 고쳤다(재발 방지).")
