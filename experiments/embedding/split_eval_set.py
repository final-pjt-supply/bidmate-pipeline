# -*- coding: utf-8 -*-
"""eval_set.json(확정본, 111쿼리)을 튜닝셋 70% / 검증셋 30%로 층화 분할한다.

유형(탐색형/조건매칭형/공고내부형)별로 각각 70:30 비율로 나눈 뒤 합친다 —
전체를 통째로 무작위 분할하면 유형별 분포가 튜닝셋/검증셋 사이에서 우연히
치우칠 수 있어서, 유형마다 따로 섞고 잘라 붙인다. 시드 고정(RANDOM_SEED)으로
재실행해도 항상 같은 분할이 나온다.

eval_set.json 자체는 건드리지 않는다(사용자가 이미 확정한 파일) — 결과는
별도 파일(eval_set_tune.json / eval_set_val.json)로 저장한다.

실행(리포 루트에서):
    cd experiments/embedding && python split_eval_set.py
"""
import json
import random
from collections import defaultdict
from pathlib import Path

RANDOM_SEED = 42
TUNE_RATIO = 0.7

SRC_PATH = Path(__file__).parent / "eval_set.json"
TUNE_PATH = Path(__file__).parent / "eval_set_tune.json"
VAL_PATH = Path(__file__).parent / "eval_set_val.json"


def stratified_split(data: list[dict]) -> tuple[list[dict], list[dict]]:
    by_type = defaultdict(list)
    for entry in data:
        by_type[entry["type"]].append(entry)

    rng = random.Random(RANDOM_SEED)
    tune, val = [], []
    for type_name, entries in by_type.items():
        shuffled = entries[:]
        rng.shuffle(shuffled)
        cut = round(len(shuffled) * TUNE_RATIO)
        tune.extend(shuffled[:cut])
        val.extend(shuffled[cut:])
    return tune, val


def main() -> None:
    data = json.loads(SRC_PATH.read_text(encoding="utf-8"))
    tune, val = stratified_split(data)

    TUNE_PATH.write_text(json.dumps(tune, ensure_ascii=False, indent=2), encoding="utf-8")
    VAL_PATH.write_text(json.dumps(val, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    print(f"전체: {len(data)}개")
    print(f"튜닝셋: {len(tune)}개 -> {TUNE_PATH}")
    print(f"  유형별: {dict(Counter(d['type'] for d in tune))}")
    print(f"검증셋: {len(val)}개 -> {VAL_PATH}")
    print(f"  유형별: {dict(Counter(d['type'] for d in val))}")


if __name__ == "__main__":
    main()
