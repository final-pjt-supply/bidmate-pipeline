# -*- coding: utf-8 -*-
"""실험 수치를 기계가 읽을 수 있는 형태로 남긴다.

화면 출력만 하면 나중에 검증할 원본이 없다. 실제로 11번 실험 아홉 번의 결과를
사람이 손으로 옮겨 적는 일이 있었고, 그러면 오타를 잡을 방법이 없다.
모든 실험 스크립트는 결과를 여기로 흘려보낸다.

한 줄에 한 실험(JSONL). 나중에 pandas로 바로 읽힌다:
    pd.read_json('clustering/outputs/metrics.jsonl', lines=True)
"""
import json
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent / "outputs"


def record(experiment, **fields):
    OUT.mkdir(exist_ok=True)
    row = {"experiment": experiment,
           "ts": datetime.now().isoformat(timespec="seconds"), **fields}
    with open(OUT / "metrics.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
