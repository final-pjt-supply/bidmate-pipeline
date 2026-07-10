# -*- coding: utf-8 -*-
"""하이브리드 검색 가중치 스윕 + recall@5/recall@10/MRR 측정.

BM25:knn 가중치 조합(0:100/30:70/50:50/70:30/100:0)과 RRF(Reciprocal Rank
Fusion)를 비교한다. 0:100/100:0은 각각 knn 단독/BM25 단독과 동치이므로
하이브리드 파이프라인을 안 거치고 원시 검색 결과를 그대로 쓴다(호출 절약).
RRF는 OpenSearch 파이프라인이 아니라 이 스크립트에서 직접 계산한다 —
설치된 OpenSearch 2.19.1의 neural-search 플러그인이 rrf 결합 기법을
지원하는지 불확실해서, BM25/knn 각각의(공고 단위로 중복 제거한) 순위
리스트로 표준 RRF 공식(1/(k+rank), k=60)을 직접 적용하는 쪽이 더 확실하다.

평가 단위는 "공고"(bid_id)다 — 문서 하나가 청크 수십 개로 쪼개져 있어서
(문서당 청크 수 중앙값 약 29~30) 원시 검색 결과 상위 10개가 전부 같은
공고의 청크로 채워질 수 있다. 그래서 원시 hit을 처음 등장한 순서대로
bid_id 기준 중복 제거한 뒤(CANDIDATE_SIZE=50개 원시 hit에서 중복 제거) 그
순위로 recall@5/recall@10/MRR을 계산한다. answer_bid_id가 리스트인 경우
(동일사업 재공고 등) 그중 하나라도 top-k 안에 들면 hit으로 센다.

실행(리포 루트에서, 사전에 index_to_opensearch.py --analyzer nori로 v3
인덱스 적재 필요):
    cd experiments/embedding && python run_recall_eval.py --split tune
    cd experiments/embedding && python run_recall_eval.py --split val --weights 50:50,70:30
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        if _k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
            os.environ.setdefault(_k, _v)

from opensearchpy import OpenSearch  # noqa: E402

from embedding.cloudflare_embedder import embed  # noqa: E402
from experiments.embedding.hybrid_search import create_pipeline, hybrid_search  # noqa: E402

INDEX_NAME = "embedding-experiment-chunks-v3"
CANDIDATE_SIZE = 50  # bid_id 중복 제거 전 원시 hit 수 — 문서당 청크가 많아 넉넉히 확보
RRF_K = 60

DEFAULT_WEIGHTS = ["0:100", "30:70", "50:50", "70:30", "100:0", "rrf"]

TUNE_PATH = Path(__file__).parent / "eval_set_tune.json"
VAL_PATH = Path(__file__).parent / "eval_set_val.json"

# "탐색형/조건매칭형"과 "공고내부형"을 별도 버킷으로 — 전자는 의미 기반 탐색이
# 핵심이라 knn 강점이, 후자는 특정 공고를 정확히 찾아야 해서 BM25 강점이
# 두드러질 것으로 예상 — 실제로 그런지 이 구분으로 확인한다.
BUCKET_A_TYPES = ("탐색형", "조건매칭형")
BUCKET_B_TYPES = ("공고내부형",)


def get_client() -> OpenSearch:
    return OpenSearch(hosts=[{"host": "localhost", "port": 9200}], use_ssl=False, verify_certs=False)


def _raw_search(client: OpenSearch, body: dict) -> list[dict]:
    resp = client.search(index=INDEX_NAME, body=body)
    return resp["hits"]["hits"]


def bm25_raw(client: OpenSearch, query_text: str, size: int) -> list[dict]:
    return _raw_search(client, {"size": size, "query": {"match": {"text": query_text}}})


def knn_raw(client: OpenSearch, query_vector: list[float], size: int) -> list[dict]:
    return _raw_search(client, {"size": size, "query": {"knn": {"vector": {"vector": query_vector, "k": size}}}})


def unique_bid_ranking(hits: list[dict]) -> list[str]:
    seen: list[str] = []
    for h in hits:
        bid = h["_source"]["bid_id"]
        if bid not in seen:
            seen.append(bid)
    return seen


def rrf_combine(bm25_bids: list[str], knn_bids: list[str], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for rank, bid in enumerate(bm25_bids, start=1):
        scores[bid] = scores.get(bid, 0.0) + 1.0 / (k + rank)
    for rank, bid in enumerate(knn_bids, start=1):
        scores[bid] = scores.get(bid, 0.0) + 1.0 / (k + rank)
    return [bid for bid, _ in sorted(scores.items(), key=lambda x: -x[1])]


def compute_metrics(ranked_bids: list[str], answer_bid_ids: list[str]) -> dict:
    answer_set = set(answer_bid_ids)
    recall_5 = any(b in answer_set for b in ranked_bids[:5])
    recall_10 = any(b in answer_set for b in ranked_bids[:10])
    rr = 0.0
    for i, b in enumerate(ranked_bids, start=1):
        if b in answer_set:
            rr = 1.0 / i
            break
    return {"recall_5": recall_5, "recall_10": recall_10, "rr": rr}


def _parse_weight_token(token: str) -> tuple[str, float | None, float | None]:
    if token.lower() == "rrf":
        return ("RRF", None, None)
    bm25_pct, knn_pct = token.split(":")
    return (token, float(bm25_pct) / 100.0, float(knn_pct) / 100.0)


def run_sweep(client: OpenSearch, entries: list[dict], weight_tokens: list[str]) -> dict:
    """entries: eval_set 항목 리스트. weight_tokens: ["0:100","50:50","rrf",...].
    반환: {config_name: [{"query":..., "answer_bid_id":..., "type":..., "ranked": [...], **metrics}, ...]}
    """
    configs = [_parse_weight_token(t) for t in weight_tokens]

    # 쿼리별 임베딩 + BM25/knn 원시 hit을 한 번만 계산해서 캐시 — 모든 설정이 공유
    cache = []
    for i, entry in enumerate(entries, start=1):
        query_text = entry["query"]
        vector = embed([{"text": query_text}], batch_size=1)[0]["vector"]
        bm25_hits = bm25_raw(client, query_text, CANDIDATE_SIZE)
        knn_hits = knn_raw(client, vector, CANDIDATE_SIZE)
        cache.append({
            "entry": entry,
            "vector": vector,
            "bm25_bids": unique_bid_ranking(bm25_hits),
            "knn_bids": unique_bid_ranking(knn_hits),
        })
        print(f"  [{i}/{len(entries)}] 임베딩+원시검색 완료: {query_text[:30]!r}")

    results: dict[str, list[dict]] = {name: [] for name, _, _ in configs}

    for name, bw, kw in configs:
        print(f"설정 [{name}] 평가 중...")
        if name == "RRF":
            for c in cache:
                ranked = rrf_combine(c["bm25_bids"], c["knn_bids"])
                _record(results, name, c["entry"], ranked)
            continue
        if bw == 0.0:
            for c in cache:
                _record(results, name, c["entry"], c["knn_bids"])
            continue
        if kw == 0.0:
            for c in cache:
                _record(results, name, c["entry"], c["bm25_bids"])
            continue

        create_pipeline(client, bw, kw)
        for c in cache:
            hits = hybrid_search(client, INDEX_NAME, c["entry"]["query"], c["vector"], top_k=CANDIDATE_SIZE)
            ranked = unique_bid_ranking(hits)
            _record(results, name, c["entry"], ranked)

    return results


def _record(results: dict, config_name: str, entry: dict, ranked_bids: list[str]) -> None:
    metrics = compute_metrics(ranked_bids, entry["answer_bid_id"])
    results[config_name].append({
        "query": entry["query"],
        "answer_bid_id": entry["answer_bid_id"],
        "type": entry["type"],
        "ranked": ranked_bids[:10],
        **metrics,
    })


def _agg(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "recall_5": None, "recall_10": None, "mrr": None}
    return {
        "n": n,
        "recall_5": sum(r["recall_5"] for r in rows) / n,
        "recall_10": sum(r["recall_10"] for r in rows) / n,
        "mrr": sum(r["rr"] for r in rows) / n,
    }


def print_report(results: dict) -> None:
    header = f"{'설정':16} {'전체 n':>7} {'R@5':>7} {'R@10':>7} {'MRR':>7}   {'탐색+조건 n':>10} {'R@5':>7} {'R@10':>7} {'MRR':>7}   {'공고내부 n':>9} {'R@5':>7} {'R@10':>7} {'MRR':>7}"
    print(header)
    print("-" * len(header))
    for name, rows in results.items():
        overall = _agg(rows)
        bucket_a = _agg([r for r in rows if r["type"] in BUCKET_A_TYPES])
        bucket_b = _agg([r for r in rows if r["type"] in BUCKET_B_TYPES])
        print(
            f"{name:16} {overall['n']:>7} {overall['recall_5']:>7.3f} {overall['recall_10']:>7.3f} {overall['mrr']:>7.3f}   "
            f"{bucket_a['n']:>10} {bucket_a['recall_5']:>7.3f} {bucket_a['recall_10']:>7.3f} {bucket_a['mrr']:>7.3f}   "
            f"{bucket_b['n']:>9} {bucket_b['recall_5']:>7.3f} {bucket_b['recall_10']:>7.3f} {bucket_b['mrr']:>7.3f}"
        )


def print_zero_recall(results: dict, config_name: str) -> None:
    rows = results[config_name]
    zeros = [r for r in rows if not r["recall_10"]]
    print(f"\n=== recall@10 = 0 쿼리 목록 (설정: {config_name}, {len(zeros)}건) ===")
    for r in zeros:
        print(f"  [{r['type']}] {r['query']!r}")
        print(f"    정답={r['answer_bid_id']}  검색상위={r['ranked'][:5]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="하이브리드 검색 recall/MRR 스윕")
    parser.add_argument("--split", choices=["tune", "val"], default="tune")
    parser.add_argument("--weights", default=",".join(DEFAULT_WEIGHTS), help="콤마구분 bm25:knn 퍼센트 또는 rrf. 예: 0:100,50:50,rrf")
    parser.add_argument("--zero-recall-config", default=None, help="recall=0 쿼리를 뽑을 설정명(지정 없으면 생략)")
    parser.add_argument("--out", default=None, help="결과 JSON 저장 경로(지정 없으면 저장 안 함)")
    args = parser.parse_args()

    path = TUNE_PATH if args.split == "tune" else VAL_PATH
    entries = json.loads(path.read_text(encoding="utf-8"))
    print(f"평가 대상: {args.split} ({len(entries)}개)")

    weight_tokens = [w.strip() for w in args.weights.split(",")]
    client = get_client()
    results = run_sweep(client, entries, weight_tokens)

    print()
    print_report(results)

    if args.zero_recall_config:
        print_zero_recall(results, args.zero_recall_config)

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n결과 저장: {args.out}")


if __name__ == "__main__":
    main()
