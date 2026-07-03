"""
코랩 GPU 임베딩 벤치마크
사용법:
  1. chunks.json 업로드 (왼쪽 파일 탭 → 업로드)
  2. 셀 전체 실행
"""
import json
import time

# ── 패키지 설치 ──────────────────────────────────────────────
import subprocess
subprocess.run(["pip", "install", "FlagEmbedding", "-q"])

import torch
from FlagEmbedding import BGEM3FlagModel

# ── 환경 정보 ─────────────────────────────────────────────────
if torch.cuda.is_available():
    device_name = torch.cuda.get_device_name(0)
    total_mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f"디바이스 : GPU — {device_name} ({total_mem:.1f}GB)")
else:
    print("디바이스 : CPU")

# ── 청크 로드 ─────────────────────────────────────────────────
with open("chunks.json", encoding="utf-8") as f:
    chunks = json.load(f)
texts = [c["text"] for c in chunks]
print(f"청크 수  : {len(texts)}개")

# ── 모델 로딩 ─────────────────────────────────────────────────
use_fp16 = torch.cuda.is_available()
t0 = time.time()
model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=use_fp16)
load_time = time.time() - t0
print(f"모델 로딩: {load_time:.1f}초")

# ── 임베딩 ───────────────────────────────────────────────────
t1 = time.time()
result = model.encode(texts, batch_size=12, max_length=512)
embed_time = time.time() - t1

# ── 결과 출력 ─────────────────────────────────────────────────
print("\n───────── 결과 ─────────────────")
print(f"총 임베딩 시간 : {embed_time:.1f}초")
print(f"청크당 평균    : {embed_time / len(texts) * 1000:.0f}ms")
print(f"초당 처리량    : {len(texts) / embed_time:.1f} 청크/초")
print(f"벡터 차원      : {result['dense_vecs'].shape[1]}")
