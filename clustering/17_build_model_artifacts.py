# -*- coding: utf-8 -*-
"""운영에 실을 모델 파일을 만들고, 크기와 추론 속도를 잰다.

람다에 넣을 수 있는지 판단하려면 추정이 아니라 실제 파일 크기가 필요하다.
같이 재는 것:
  - joblib 파일 크기 (압축 유무, float32 변환 유무)
  - 로드 시간 (람다 콜드스타트에 더해지는 시간)
  - 1건 / 100건 추론 시간

학습 데이터는 Train+Val이다. Test는 성능 추정치를 남기기 위해 쓰지 않는다.

실행: .venv/Scripts/python.exe clustering/17_build_model_artifacts.py
"""
import importlib.util
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from _record import record

warnings.filterwarnings("ignore", category=ConvergenceWarning)
HERE = Path(__file__).resolve().parent
ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)

spec = importlib.util.spec_from_file_location("hp", HERE / "14_hparam_search.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

# 14번 그리드가 고른 설정
BEST = {
    "thng": dict(analyzer="char", ngram_range=(2, 3), min_df=1, sublinear_tf=True,
                 max_features=None, C=10.0, class_weight="balanced"),
    "servc": dict(analyzer="char_wb", ngram_range=(2, 3), min_df=2, sublinear_tf=False,
                  max_features=None, C=1.0, class_weight="balanced"),
}

rows = []
for cat, kor in [("thng", "물품"), ("servc", "용역")]:
    cfg = BEST[cat]
    sub = hp.load(cat)
    y = sub["tag"].to_numpy()
    groups = sub["norm"].to_numpy()
    tr, rest = next(GroupShuffleSplit(n_splits=1, test_size=0.3,
                                      random_state=42).split(sub, y, groups))
    v_rel, _ = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                    .split(sub.iloc[rest], y[rest], groups[rest]))
    trval = np.concatenate([tr, rest[v_rel]])
    texts = sub["title"].astype(str).to_numpy()

    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(analyzer=cfg["analyzer"], ngram_range=cfg["ngram_range"],
                                  min_df=cfg["min_df"], sublinear_tf=cfg["sublinear_tf"],
                                  max_features=cfg["max_features"])),
        ("clf", LinearSVC(C=cfg["C"], class_weight=cfg["class_weight"],
                          random_state=42, max_iter=3000)),
    ])
    pipe.fit(texts[trval], y[trval])
    n_feat = len(pipe.named_steps["tfidf"].vocabulary_)
    n_cls = len(pipe.named_steps["clf"].classes_)

    print("=" * 68)
    print(f"[{kor}] 학습 {len(trval):,}건, 피처 {n_feat:,}, 태그 {n_cls}종")
    print("=" * 68)

    # 저장 방식별 크기 비교. coef_는 float64인데 태그 분류에 그 정밀도는 필요 없다.
    variants = {}
    p = ART / f"model_{cat}_raw.joblib"
    joblib.dump(pipe, p)
    variants["압축 없음"] = p

    p = ART / f"model_{cat}.joblib"
    joblib.dump(pipe, p, compress=3)
    variants["압축(3)"] = p

    clf = pipe.named_steps["clf"]
    clf.coef_ = clf.coef_.astype(np.float32)
    clf.intercept_ = clf.intercept_.astype(np.float32)
    pipe.named_steps["tfidf"].idf_ = pipe.named_steps["tfidf"].idf_.astype(np.float32)
    p = ART / f"model_{cat}_f32.joblib"
    joblib.dump(pipe, p, compress=3)
    variants["압축 + float32"] = p

    print(f"  {'저장 방식':<16}{'크기':>10}")
    for name, path in variants.items():
        print(f"  {name:<16}{path.stat().st_size/1024**2:>9.2f}MB")

    # 실제로 쓸 것은 압축+float32. 로드와 추론 시간을 잰다.
    use = variants["압축 + float32"]
    t0 = time.perf_counter()
    m = joblib.load(use)
    load_sec = time.perf_counter() - t0

    m.predict(["워밍업"])                       # 첫 호출은 초기화가 섞이므로 제외
    t0 = time.perf_counter()
    for _ in range(100):
        m.predict(["레미콘 구매"])
    one_ms = (time.perf_counter() - t0) / 100 * 1000

    batch = texts[:100].tolist()
    t0 = time.perf_counter()
    m.predict(batch)
    batch_ms = (time.perf_counter() - t0) * 1000

    print(f"\n  로드      {load_sec*1000:>7.0f}ms   (콜드스타트에 1회)")
    print(f"  1건 추론  {one_ms:>7.2f}ms")
    print(f"  100건 일괄{batch_ms:>7.1f}ms  (건당 {batch_ms/100:.2f}ms)")

    r = {"category": cat, "n_train": len(trval), "n_features": n_feat, "n_classes": n_cls,
         "size_raw_mb": round(variants["압축 없음"].stat().st_size / 1024**2, 2),
         "size_compressed_mb": round(variants["압축(3)"].stat().st_size / 1024**2, 2),
         "size_f32_mb": round(use.stat().st_size / 1024**2, 2),
         "load_ms": round(load_sec * 1000, 1), "predict_1_ms": round(one_ms, 3),
         "predict_100_ms": round(batch_ms, 1), "config": {**cfg, "ngram_range": list(cfg["ngram_range"])}}
    rows.append(r)
    record("model_artifact", **r)
    print()

total = sum(r["size_f32_mb"] for r in rows)
print("=" * 68)
print(f"두 모델 합계 {total:.2f}MB")
print("=" * 68)
print("  람다 zip 배포 한도      250MB (압축 해제 기준)")
print("  람다 컨테이너 이미지     10GB")
print("  realtime 파이프라인은 컨테이너 이미지 방식이라 여유가 크다.")
