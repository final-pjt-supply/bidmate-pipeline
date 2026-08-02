# -*- coding: utf-8 -*-
"""용역 클러스터링 그리드 탐색 (03_servc_clustering.ipynb의 3번 셀과 동일).

노트북 CPU가 느려(UMAP 1회 157초) 데스크탑에서 대신 돌리기 위한 스크립트.
결과는 cache/grid_servc.csv 로 저장되며, 노트북에서 이 CSV를 읽어 4번(그룹 확인)부터
이어서 진행할 수 있다.

실행: .venv/Scripts/python.exe clustering/run_servc_grid.py
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd
import umap
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize

CACHE = Path(__file__).parent / "cache"

N_COMPONENTS = [5, 10, 15, 20, 30, 50, 100]
N_NEIGHBORS = [5, 15, 50]
K_GRID = list(range(4, 21))
HDBSCAN_GRID = [(mcs, ms) for mcs in (15, 30, 60) for ms in (5, 15)]

# 전체 dedup 캐시에서 용역만 잘라 쓴다(노트북에 보낸 vectors_servc.npy와 동일 내용).
X = np.load(CACHE / "vectors_dedup.npy")
df_all = pd.read_csv(CACHE / "meta_dedup.csv")
mask = (df_all["biz_div"] == "servc").to_numpy()
Xn = normalize(X[mask])
df = df_all[mask].reset_index(drop=True)
print(f"용역 {len(df):,}건, shape={Xn.shape}", flush=True)


def evaluate(labels):
    """원본 공간 실루엣. 노이즈(-1)는 제외. 평가 불가면 None."""
    m = labels >= 0
    if m.sum() < 100 or len(set(labels[m])) < 2:
        return None
    return silhouette_score(Xn[m], labels[m], metric="cosine",
                            sample_size=min(2000, int(m.sum())), random_state=42)


rows = []
t0 = time.perf_counter()
done = 0
total = len(N_COMPONENTS) * len(N_NEIGHBORS)

for n_comp in N_COMPONENTS:
    for n_nb in N_NEIGHBORS:
        emb = umap.UMAP(n_components=n_comp, n_neighbors=n_nb, min_dist=0.0,
                        metric="cosine", random_state=42).fit_transform(Xn)

        for k in K_GRID:
            labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(emb)
            rows.append({"model": "kmeans", "n_components": n_comp, "n_neighbors": n_nb,
                         "param": f"K={k}", "n_clusters": k, "noise_ratio": 0.0,
                         "silhouette": evaluate(labels)})

        for mcs, ms in HDBSCAN_GRID:
            labels = HDBSCAN(min_cluster_size=mcs, min_samples=ms).fit_predict(emb)
            n_cl = len(set(labels)) - (1 if -1 in labels else 0)
            rows.append({"model": "hdbscan", "n_components": n_comp, "n_neighbors": n_nb,
                         "param": f"mcs={mcs},ms={ms}", "n_clusters": n_cl,
                         "noise_ratio": round(float((labels == -1).mean()), 3),
                         "silhouette": evaluate(labels)})

        done += 1
        elapsed = time.perf_counter() - t0
        eta = elapsed / done * (total - done)
        print(f"  UMAP {done}/{total} (n_comp={n_comp}, n_nb={n_nb}) "
              f"누적 {elapsed:.0f}초 / 남은예상 {eta:.0f}초", flush=True)

        # 중간 저장 - 오래 걸리는 작업이라 중단돼도 여기까지는 남는다
        pd.DataFrame(rows).to_csv(CACHE / "grid_servc.csv", index=False,
                                  encoding="utf-8-sig")

print(f"\n조합 {len(rows)}개 완료, 총 {time.perf_counter()-t0:.0f}초")
print(f"저장: {CACHE / 'grid_servc.csv'}")
