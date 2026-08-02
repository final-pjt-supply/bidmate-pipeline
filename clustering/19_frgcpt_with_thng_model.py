# -*- coding: utf-8 -*-
"""외자를 물품 모델로 처리할 수 있는지 확인한다.

외자는 업종이 아니라 조달 방식이다. 실제로 사는 건 물품이고, 09번 조사에서
정한 외자 태그 4종(실험·분석장비/국방·보안장비/시약·소모품/기타)은 전부
물품 20종 안에 이미 있다. 전용 체계를 만들 이유가 없다.

문제는 물품 모델이 외자 제목을 알아듣느냐다. 외자엔 영문 장비명과 모델번호가
많아 학습 데이터에 없는 표현일 수 있다. 외자엔 정답 라벨이 없어 정확도는 못 재지만
이 세 가지는 볼 수 있다.

  1. 신뢰도 분포가 물품 공고와 비슷한가 (낮으면 어휘가 안 겹친다는 뜻)
  2. 예측 분포가 09번 조사와 맞는가 (표본 60건 중 50건 이상이 분석장비였다)
  3. 예측을 눈으로 봤을 때 말이 되는가

유입 속도도 같이 본다. 계속 쌓이는 문제인지 판단하려면 필요하다.

실행: .venv/Scripts/python.exe clustering/19_frgcpt_with_thng_model.py
"""
import importlib.util
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from _record import record

warnings.filterwarnings("ignore", category=ConvergenceWarning)
HERE = Path(__file__).resolve().parent
BASE = HERE.parent

spec = importlib.util.spec_from_file_location("hp", HERE / "14_hparam_search.py")
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

CFG = dict(analyzer="char", ngram_range=(2, 3), min_df=1, sublinear_tf=True,
           max_features=None, C=10.0, class_weight="balanced")

# 물품 모델 학습 (Train+Val, 17번과 동일)
sub = hp.load("thng")
y = sub["tag"].to_numpy()
tr, rest = next(GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=42)
                .split(sub, y, sub["norm"].to_numpy()))
v_rel, t_rel = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                    .split(sub.iloc[rest], y[rest], sub["norm"].to_numpy()[rest]))
trval, test = np.concatenate([tr, rest[v_rel]]), rest[t_rel]
texts = sub["title"].astype(str).to_numpy()

pipe = Pipeline([
    ("tfidf", TfidfVectorizer(**{k: v for k, v in CFG.items() if k not in ("C", "class_weight")})),
    ("clf", LinearSVC(C=CFG["C"], class_weight=CFG["class_weight"],
                      random_state=42, max_iter=3000)),
]).fit(texts[trval], y[trval])
classes = pipe.named_steps["clf"].classes_


def margins(titles):
    s = pipe.decision_function(titles)
    o = np.argsort(s, axis=1)
    i = np.arange(len(s))
    return classes[o[:, -1]], classes[o[:, -2]], s[i, o[:, -1]] - s[i, o[:, -2]]


env = {}
for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"], user=env["RDS_Username"],
                        password=env["RDS_Password"], port=5432)
frg = pd.read_sql("""
    SELECT bid_id, bid_ntce_nm AS title, bid_ntce_dt
    FROM bid_table WHERE bid_category='frgcpt'
""", conn)
conn.close()
frg = frg.drop_duplicates("title").reset_index(drop=True)

print("=" * 74)
print(f"외자 고유 제목 {len(frg):,}건에 물품 모델 적용")
print("=" * 74)

f_pred, f_second, f_margin = margins(frg["title"].astype(str).tolist())
t_pred, _, t_margin = margins(texts[test].tolist())

print("\n1. 신뢰도 분포 - 외자 vs 물품 Test")
print(f"  {'':10}{'중앙값':>9}{'평균':>9}{'0.2 미만':>10}{'0.5 미만':>10}")
for name, mg in [("외자", f_margin), ("물품 Test", t_margin)]:
    print(f"  {name:<10}{np.median(mg):>9.3f}{mg.mean():>9.3f}"
          f"{(mg < 0.2).mean()*100:>9.1f}%{(mg < 0.5).mean()*100:>9.1f}%")

print("\n2. 예측 분포 - 09번 조사와 맞는가")
print("   (조사: 표본 60건 중 50건 이상이 분석장비, 국방 약 8%, 시약 약 5%)")
vc = pd.Series(f_pred).value_counts()
for t, n in vc.items():
    print(f"  {t:<16}{n:>5}건 {n/len(f_pred)*100:>6.1f}%")

print("\n3. 예측 표본 - 신뢰도 높은 것 12건")
hi = np.argsort(-f_margin)[:12]
for i in hi:
    print(f"  {f_margin[i]:>6.2f}  {f_pred[i]:<14} {str(frg['title'][i])[:48]}")

print("\n   신뢰도 낮은 것 8건 (미분류 후보)")
for i in np.argsort(f_margin)[:8]:
    print(f"  {f_margin[i]:>6.2f}  {f_pred[i]} vs {f_second[i]}")
    print(f"          {str(frg['title'][i])[:56]}")

print("\n4. 유입 속도")
frg["ym"] = pd.to_datetime(frg["bid_ntce_dt"], errors="coerce").dt.to_period("M")
by_m = frg["ym"].value_counts().sort_index()
for ym, n in by_m.items():
    print(f"  {ym}  {n:>4}건")
if len(by_m) > 1:
    print(f"  월평균 {by_m.mean():.0f}건")

record("frgcpt_with_thng_model", n_frgcpt=len(frg),
       margin_median_frgcpt=round(float(np.median(f_margin)), 4),
       margin_median_thng_test=round(float(np.median(t_margin)), 4),
       below_02_frgcpt=round(float((f_margin < 0.2).mean()), 4),
       below_02_thng_test=round(float((t_margin < 0.2).mean()), 4),
       pred_distribution={k: int(v) for k, v in vc.items()},
       monthly_counts={str(k): int(v) for k, v in by_m.items()})
