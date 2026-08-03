# -*- coding: utf-8 -*-
"""23번이 고른 모델을 배포 형식(numpy 전용)으로 바꾸고 임계값을 다시 정한다.

배포 런타임(pipeline/realtime/src/tagging/tagger.py)은 sklearn 없이 numpy만
쓴다. 모델 파일이 '어휘사전 + idf + 계수행렬'이 전부라 가능한 구조인데,
LinearSVC든 LogisticRegression이든 둘 다 선형이라 같은 형식으로 접힌다.

주의한 것:
  use_idf=False로 학습한 모델은 vec.idf_가 없다. 런타임은 idf를 항상 곱하므로
  1로 채운 벡터를 넣는다(곱해도 값이 안 변한다).

임계값을 다시 정하는 이유:
  런타임의 신뢰도는 1위와 2위의 '점수차'다. 확률이 아니라 모델마다 척도가
  다르다. LinearSVC 마진과 LogisticRegression의 로짓 차이는 크기가 달라서
  기존 임계값(물품 0.20 / 용역 0.15)을 그대로 쓰면 의도와 다른 비율이 걸린다.

  곡선은 학습 풀의 out-of-fold 예측으로 잰다. 시험셋(7월)으로 임계값을 고르면
  그 시점에 시험셋이 검증셋이 되어 최종 수치의 의미가 사라진다.

실행: .venv/Scripts/python.exe clustering/25_deploy.py --category thng
"""
import argparse
import importlib.util as _iu
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from sklearn.feature_extraction.text import TfidfVectorizer

HERE = Path(__file__).resolve().parent
_spec = _iu.spec_from_file_location("dataset", HERE / "22_dataset.py")
dataset = _iu.module_from_spec(_spec)
_spec.loader.exec_module(dataset)
sys.path.insert(0, str(HERE))
from _record import record

ART = HERE / "artifacts"
DEPLOY_DIR = dataset.BASE / "pipeline" / "realtime" / "src" / "tagging" / "models"
RUNTIME_SRC = dataset.BASE / "pipeline" / "realtime" / "src"

THRESHOLDS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0]
TARGET_ACC = [0.85, 0.90]


def export_numpy(m, cat):
    """배포 형식(npz + json)으로 저장한다. 20번이 만든 형식을 그대로 따른다."""
    vec, clf = m["vectorizer"], m["classifier"]
    n_feat = len(vec.vocabulary_)
    # use_idf=False면 idf_가 없다. 런타임은 항상 곱하므로 1로 채운다.
    idf = vec.idf_ if getattr(vec, "use_idf", True) else np.ones(n_feat)

    npz = ART / f"tagger_{cat}.npz"
    meta = ART / f"tagger_{cat}.json"
    np.savez_compressed(npz, idf=np.asarray(idf, dtype=np.float32),
                        coef=clf.coef_.astype(np.float32),
                        intercept=clf.intercept_.astype(np.float32))
    meta.write_text(json.dumps({
        "vocab": {k: int(v) for k, v in vec.vocabulary_.items()},
        "classes": [str(c) for c in clf.classes_],
        "analyzer": m["vec_kwargs"]["analyzer"],
        "ngram_range": list(m["vec_kwargs"]["ngram_range"]),
        "sublinear_tf": bool(m["vec_kwargs"]["sublinear_tf"]),
    }, ensure_ascii=False), encoding="utf-8")
    size = (npz.stat().st_size + meta.stat().st_size) / 1e6
    print(f"  내보냄 {npz.name} + {meta.name}  피처 {n_feat:,} / 클래스 "
          f"{len(clf.classes_)} / 합계 {size:.2f}MB")
    return npz, meta, size


def verify_numpy(m, texts, test, y):
    """런타임 구현이 sklearn과 같은 답을 내는지 시험셋 전체로 대조한다.

    여기가 어긋나면 실험 성능과 배포 성능이 달라진다. 20번과 같은 절차다.
    """
    sys.path.insert(0, str(RUNTIME_SRC))
    from tagging.tagger import Tagger        # noqa: E402  런타임 코드 그대로

    tg = Tagger.load(m["cat"], model_dir=ART)
    sk = m["classifier"].decision_function(m["vectorizer"].transform(texts[test]))
    order = np.argsort(sk, axis=1)
    i = np.arange(len(sk))
    sk_pred = m["classifier"].classes_[order[:, -1]]
    sk_margin = sk[i, order[:, -1]] - sk[i, order[:, -2]]

    np_pred, np_margin = [], []
    for t in texts[test]:
        p, mg = tg.predict(str(t))
        np_pred.append(p)
        np_margin.append(mg)
    agree = float(np.mean(np.array(np_pred) == sk_pred))
    err = float(np.max(np.abs(np.array(np_margin) - sk_margin)))
    print(f"  대조 {len(test):,}건 - 예측 일치 {agree*100:.1f}% / 점수차 최대 오차 {err:.2e}")
    if agree < 1.0:
        print("  ! 예측이 어긋난다. 배포하면 실험 성능이 재현되지 않는다.")
    return {"agreement": agree, "max_margin_error": err}


def oof_margins(m, df, y, groups, pool, texts):
    """학습 풀의 out-of-fold 예측과 점수차. 임계값 곡선의 재료."""
    pred = np.empty(len(pool), dtype=object)
    margin = np.zeros(len(pool))
    pos = {v: i for i, v in enumerate(pool)}
    for tr, va in dataset.folds(df, y, groups, pool):
        vec = TfidfVectorizer(**m["vec_kwargs"])
        X = vec.fit_transform(texts[tr])
        clf = clone(m["classifier"])
        clf.fit(X, y[tr])
        s = clf.decision_function(vec.transform(texts[va]))
        order = np.argsort(s, axis=1)
        i = np.arange(len(s))
        for k, v in enumerate(va):
            pred[pos[v]] = clf.classes_[order[k, -1]]
            margin[pos[v]] = s[k, order[k, -1]] - s[k, order[k, -2]]
    return pred, margin


def threshold_curve(y_true, pred, margin):
    rows = []
    for t in THRESHOLDS:
        keep = margin >= t
        if keep.sum() == 0:
            continue
        rows.append({"임계값": t, "커버리지": round(float(keep.mean()), 4),
                     "미분류": int((~keep).sum()),
                     "정확도": round(float((pred[keep] == y_true[keep]).mean()), 4),
                     "맞을걸버림": int(((~keep) & (pred == y_true)).sum())})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="thng", choices=["thng", "servc"])
    ap.add_argument("--install", action="store_true",
                    help="검증 통과 시 배포 디렉터리에 모델 파일을 복사한다")
    args = ap.parse_args()
    cat, kor = args.category, {"thng": "물품", "servc": "용역"}[args.category]
    t0 = time.perf_counter()

    m = joblib.load(ART / f"model_{cat}.joblib")
    m["cat"] = cat
    df = dataset.load(cat)
    y = df["tag"].to_numpy()
    groups = dataset.build_groups(df)
    pool, test = dataset.time_split(df, groups)
    texts = df[f"text_{m['text_source']}"].to_numpy()

    print("=" * 78)
    print(f"[{kor}] 배포 준비  {m['clf_name']} {m['clf_params']}")
    print(f"  {m['vec_kwargs']}")
    print("=" * 78, flush=True)

    print("\n[1. numpy 형식으로 변환]")
    npz, meta, size = export_numpy(m, cat)

    print("\n[2. 런타임 구현 대조]")
    ver = verify_numpy(m, texts, test, y)

    print("\n[3. 임계값 곡선 - 학습 풀 out-of-fold]")
    pred, margin = oof_margins(m, df, y, groups, pool, texts)
    curve = threshold_curve(y[pool], pred, margin)
    print(curve.to_string(index=False))

    picked = {}
    for target in TARGET_ACC:
        ok = curve[curve["정확도"] >= target]
        if len(ok):
            r = ok.iloc[0]
            picked[target] = float(r["임계값"])
            print(f"  정확도 {target:.2f} 이상 → 임계값 {r['임계값']}"
                  f" (커버리지 {r['커버리지']*100:.1f}%, 미분류 {r['미분류']}건)")
        else:
            print(f"  정확도 {target:.2f} 이상 → 도달 불가")

    # 7월 시험셋에서 같은 임계값이 어떻게 작동하는지 확인만 한다(선택에는 안 씀)
    if picked:
        t = picked[min(picked)]
        s = m["classifier"].decision_function(m["vectorizer"].transform(texts[test]))
        o = np.argsort(s, axis=1)
        i = np.arange(len(s))
        tp = m["classifier"].classes_[o[:, -1]]
        tm = s[i, o[:, -1]] - s[i, o[:, -2]]
        keep = tm >= t
        print(f"\n  [확인] 7월 시험셋에 임계값 {t} 적용 → 커버리지 {keep.mean()*100:.1f}%"
              f" / 정확도 {(tp[keep]==y[test][keep]).mean():.4f}")

    if args.install:
        if ver["agreement"] < 1.0:
            raise SystemExit("대조 불일치 - 설치하지 않는다")
        DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
        for f in (npz, meta):
            shutil.copy2(f, DEPLOY_DIR / f.name)
        print(f"\n[4. 설치] {DEPLOY_DIR} 에 복사 완료")
        print(f"  ! rules.py의 THRESHOLD를 {picked} 기준으로 직접 고쳐야 한다")

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=dataset.BASE,
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    record("deploy_prep", category=cat, clf=m["clf_name"], clf_params=str(m["clf_params"]),
           size_mb=round(size, 3), verify=ver, curve=curve.to_dict("records"),
           picked_thresholds=picked, installed=bool(args.install),
           env={"sklearn": sklearn.__version__, "numpy": np.__version__,
                "python": platform.python_version(), "commit": commit},
           elapsed_sec=round(time.perf_counter() - t0, 1))
    print(f"\n총 {time.perf_counter()-t0:.0f}초")


if __name__ == "__main__":
    main()
