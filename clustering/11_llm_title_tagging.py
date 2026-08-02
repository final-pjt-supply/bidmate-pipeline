# -*- coding: utf-8 -*-
"""LLM에게 제목만 주고 태그를 고르게 한 뒤, TF-IDF와 같은 잣대로 비교한다.

비교 조건을 맞추는 게 핵심이다:
  - TF-IDF가 평가받은 것과 동일한 Test 셋에서 표본을 뽑는다
  - TF-IDF 점수도 그 표본에서 다시 계산해 나란히 놓는다
    (전체 Test에서 잰 0.765와 표본 100건 점수는 다를 수 있으므로)

LLM은 학습이 없으므로 Train/Val이 필요 없다. 다만 few-shot 예시는 Train에서만
뽑아 Test 오염을 피한다.

실행: .venv/Scripts/python.exe clustering/11_llm_title_tagging.py [--n 100] [--category thng]
"""
import argparse
import json
import re
import time
from pathlib import Path

import boto3
import numpy as np
import pandas as pd
import psycopg2
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import balanced_accuracy_score, classification_report, f1_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.svm import LinearSVC

from _record import record

BASE = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"
OUT = Path(__file__).resolve().parent / "outputs"
OUT.mkdir(exist_ok=True)

# 업종별 태그 목록 - 08~11번 실험에서 확정한 것
TAGS = {
    "thng": ["실험·분석장비", "토목·건설자재", "IT·통신장비", "공조·냉난방",
             "안전·보안장비", "차량·건설장비", "전기·수배전", "의료장비",
             "산업·정밀기계", "전시·교육기자재", "운반·저장장비", "피복·군장품",
             "의약품·백신", "시약·화학소모품", "발전·전지", "인쇄·사인물",
             "환경·수처리설비", "가구·침구", "식품·급식", "기타"],
    "servc": ["IT시스템", "조사·연구", "행사·전시대행", "홍보·콘텐츠",
              "감리·컨설팅", "운송·차량임차", "폐기물처리", "통신망", "기타"],
}
TAG_HINTS = {
    "IT시스템": "시스템 구축·운영·유지관리·SW개발·데이터 플랫폼",
    "조사·연구": "실태조사·정책연구·평가·기초연구·컨설팅 연구",
    "행사·전시대행": "행사 운영 대행·박람회·포럼·전시 연출",
    "홍보·콘텐츠": "홍보영상·SNS 운영·콘텐츠 제작·간행물",
    "감리·컨설팅": "정보시스템 감리·PMO·개인정보 영향평가",
    "폐기물처리": "건설폐기물·폐기물 위탁처리",
    "실험·분석장비": "크로마토그래피·질량분석기·현미경·분석기",
    "토목·건설자재": "레미콘·아스콘·골재·시멘트·사석",
    "IT·통신장비": "서버·GPU·네트워크·소프트웨어·통신기기",
    "전기·수배전": "수배전반·계측제어·조명·전력설비",
    "산업·정밀기계": "공작기계·증착기·가공장비",
    "운반·저장장비": "지게차·냉장고·창고설비",
}

THNG_CODE_MAP = {
    "41": "실험·분석장비", "30": "토목·건설자재", "11": "토목·건설자재",
    "43": "IT·통신장비", "40": "공조·냉난방", "46": "안전·보안장비",
    "25": "차량·건설장비", "39": "전기·수배전", "42": "의료장비",
    "23": "산업·정밀기계", "60": "전시·교육기자재", "24": "운반·저장장비",
    "53": "피복·군장품", "51": "의약품·백신", "12": "시약·화학소모품",
    "26": "발전·전지", "55": "인쇄·사인물", "47": "환경·수처리설비",
    "56": "가구·침구", "50": "식품·급식",
}
SERVC_CODE_MAP = {
    "P81": "IT시스템", "B1468": "IT시스템", "B1169": "조사·연구",
    "P80": "행사·전시대행", "B5720": "행사·전시대행",
    "P82": "홍보·콘텐츠", "B1469": "홍보·콘텐츠", "B3244": "홍보·콘텐츠",
    "B6146": "감리·컨설팅", "B6525": "감리·컨설팅",
    "P78": "운송·차량임차", "B6728": "폐기물처리", "B1458": "통신망",
}

_PAT = [r"\[[^\]]*\]", r"\([^)]*(긴급|재공고|변경|정정)[^)]*\)",
        r"\b20\d{2}\s*년?\s*(度|년도)?", r"\b\d{2}년",
        r"재공고|변경공고|정정공고|입찰공고|긴급공고", r"제?\s*\d+\s*차(수|분)?",
        r"★[^★]*★", r"\(총괄\)|\(총액\)|\(계속비\)|\(가칭\)"]


def norm(t):
    s = str(t)
    for p in _PAT:
        s = re.sub(p, " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]+", " ", s)).strip()


def load_env():
    env = {}
    for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def load_labeled(category, env):
    """조달청 코드가 붙은 공고 + 태그(정답)를 가져온다."""
    conn = psycopg2.connect(host=env["RDS_Host"], dbname=env["RDS_DB"],
                            user=env["RDS_Username"], password=env["RDS_Password"], port=5432)
    if category == "thng":
        raw = pd.read_sql("""
            SELECT DISTINCT ON (b.bid_id) b.bid_id, left(e->>'code',2) AS code
            FROM bid_table b, jsonb_array_elements(b.item_codes) e
            WHERE b.bid_category='thng' AND e->>'type'='세부품명번호'
              AND e->>'code' ~ '^[0-9]{8,}' ORDER BY b.bid_id
        """, conn)
        counts = raw["code"].value_counts()
        raw["tag"] = raw["code"].map(THNG_CODE_MAP)
        raw.loc[raw["code"].isin(counts[counts < 30].index) | raw["tag"].isna(), "tag"] = "기타"
    else:
        raw = pd.read_sql("""
            SELECT b.bid_id, COALESCE(
                MAX(CASE WHEN e->>'type'='세부품명번호' AND e->>'code' ~ '^[0-9]{8,}'
                         THEN 'P' || left(e->>'code',2) END),
                MAX(CASE WHEN e->>'type'='업종코드'
                          AND e->>'code' NOT IN ('9999','9901','9902','9903','9900')
                         THEN 'B' || (e->>'code') END)) AS code
            FROM bid_table b, jsonb_array_elements(b.item_codes) e
            WHERE b.bid_category='servc' GROUP BY b.bid_id
        """, conn)
        raw = raw[raw["code"].notna()]
        raw["tag"] = raw["code"].map(SERVC_CODE_MAP).fillna("기타")
    conn.close()

    df = pd.read_csv(CACHE / "meta_dedup.csv").merge(raw[["bid_id", "tag"]], on="bid_id", how="left")
    sub = df[(df["biz_div"] == category) & df["tag"].notna()].copy()
    sub["norm"] = sub["title"].map(norm)
    return sub.reset_index(drop=True)


def build_prompt(category, titles, few_shot):
    tags = TAGS[category]
    lines = []
    for t in tags:
        hint = TAG_HINTS.get(t)
        lines.append(f"- {t}" + (f" : {hint}" if hint else ""))
    tag_block = "\n".join(lines)
    fs = "\n".join(f'  "{t}" -> {g}' for t, g in few_shot)
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))

    return f"""공공입찰 공고 제목을 보고 아래 카테고리 중 하나를 고르십시오.

[카테고리]
{tag_block}

규칙:
- 반드시 위 목록에 있는 이름 그대로만 사용합니다. 새로 만들지 마십시오.
- 어느 것에도 명확히 해당하지 않으면 "기타"를 선택합니다.
- 제목이 짧아 판단이 어려워도 가장 가까운 것을 고르되, 근거가 전혀 없으면 "기타"로 둡니다.

[예시]
{fs}

[분류할 제목]
{numbered}

번호 순서대로 카테고리만 JSON 배열로 출력하십시오. 설명 없이 배열만 출력합니다.
예: ["실험·분석장비", "기타", ...]"""


# Bedrock on-demand 표준 단가 (USD / 1M 토큰, us-east-1).
# AWS Pricing API에서 확인한 값이다(2026-08 기준). 배치 추론을 쓰면 대략 절반이다.
#   boto3.client('pricing').get_products(ServiceCode='AmazonBedrock', ...)
# next-80b의 표준 입력 단가는 요금표에 배치/우선순위만 노출돼 있어,
# 배치 단가의 2배로 뒀다(출력은 배치 0.0006 - 표준 0.0012로 정확히 2배였다).
PRICE = {
    "qwen.qwen3-next-80b-a3b": (0.14, 1.20),
    "qwen.qwen3-vl-235b-a22b": (0.53, 2.66),
    "amazon.nova-pro-v1:0": (0.80, 3.20),
}


def ask_llm(client, model, category, titles, few_shot, batch=25):
    """Bedrock converse API로 호출한다. 파이프라인은 OpenAI 호환 엔드포인트를 쓰지만
    모델과 리전이 같으므로 결과는 동일하다. 여기선 베어러 토큰 없이 IAM 자격증명만
    쓰면 되어 더 간단하다."""
    preds, tok_in, tok_out = [], 0, 0
    for i in range(0, len(titles), batch):
        chunk = titles[i:i + batch]
        for attempt in range(3):
            try:
                r = client.converse(
                    modelId=model,
                    messages=[{"role": "user", "content": [
                        {"text": build_prompt(category, chunk, few_shot)}]}],
                    inferenceConfig={"temperature": 0, "maxTokens": 2000})
                tok_in += r["usage"]["inputTokens"]
                tok_out += r["usage"]["outputTokens"]
                text = r["output"]["message"]["content"][0]["text"].strip()
                text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
                got = json.loads(text[text.index("["):text.rindex("]") + 1])
                if len(got) != len(chunk):
                    raise ValueError(f"개수 불일치 {len(got)} != {len(chunk)}")
                preds.extend(got)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  !! 배치 {i} 실패({e}) - 기타로 채움")
                    preds.extend(["기타"] * len(chunk))
                else:
                    time.sleep(2)
        print(f"  {min(i+batch, len(titles))}/{len(titles)}", flush=True)
    return preds, tok_in, tok_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="thng", choices=["thng", "servc"])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--few-shot", type=int, default=8,
                    help="프롬프트에 넣을 정답 예시 수 (Train에서만 뽑음)")
    ap.add_argument("--model", default="qwen.qwen3-next-80b-a3b",
                    help="Bedrock 모델 ID (기본값은 파이프라인이 쓰는 모델)")
    args = ap.parse_args()
    cat, kor = args.category, {"thng": "물품", "servc": "용역"}[args.category]

    env = load_env()
    sub = load_labeled(cat, env)

    # TF-IDF와 동일한 분할을 재현한다 (같은 random_state, 같은 그룹 키)
    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=42)
    tr, rest = next(gss.split(sub, sub["tag"], sub["norm"]))
    _, t_rel = next(GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=42)
                    .split(sub.iloc[rest], sub.iloc[rest]["tag"], sub.iloc[rest]["norm"]))
    test = rest[t_rel]

    print(f"[{kor}] 전체 {len(sub):,} / Train {len(tr):,} / Test {len(test):,}")

    rng = np.random.default_rng(42)
    pick = rng.choice(test, min(args.n, len(test)), replace=False)
    sample = sub.iloc[pick].reset_index(drop=True)
    print(f"표본 {len(sample)}건 (Test에서 추출 - TF-IDF와 동일 모집단)\n")

    # few-shot은 Train에서만 뽑는다 (Test 오염 방지).
    # 태그별로 돌아가며 뽑아 소수 태그도 예시를 갖게 한다. 태그 순서대로 나열하면
    # 모델이 순서를 단서로 삼을 수 있으므로 섞는다.
    train_df = sub.iloc[tr]
    pools = {t: g["title"].sample(frac=1, random_state=42).tolist()
             for t, g in train_df.groupby("tag")}
    few_shot = []
    while len(few_shot) < args.few_shot and any(pools.values()):
        for t in list(pools):
            if pools[t] and len(few_shot) < args.few_shot:
                few_shot.append((pools[t].pop(), t))
    few_shot = [few_shot[i] for i in rng.permutation(len(few_shot))]
    print(f"few-shot 예시 {len(few_shot)}개 (Train에서 태그별 균등 추출)\n")

    client = boto3.client("bedrock-runtime", region_name="us-east-1")

    print("LLM 호출 중...")
    t0 = time.perf_counter()
    preds, tok_in, tok_out = ask_llm(client, args.model, cat,
                                     sample["title"].tolist(), few_shot)
    elapsed = time.perf_counter() - t0

    valid = set(TAGS[cat])
    preds = [p if p in valid else "기타" for p in preds]

    # 같은 표본에서 TF-IDF도 재계산 (전체 Test 점수와 다를 수 있으므로)
    vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=2, sublinear_tf=True)
    Xtr = vec.fit_transform(train_df["title"])
    svc = LinearSVC(class_weight="balanced", random_state=42).fit(Xtr, train_df["tag"])
    tfidf_pred = svc.predict(vec.transform(sample["title"]))

    y = sample["tag"].to_numpy()
    print(f"\n{'='*66}")
    print(f"[{kor}] {args.model}")
    print(f"표본 {len(sample)}건, few-shot {len(few_shot)}개, 소요 {elapsed:.0f}초")
    print("=" * 66)

    # 비용: 토큰은 측정값, 금액은 단가표가 있을 때만 계산한다
    per = tok_in / len(sample)
    usd = None
    print(f"토큰  입력 {tok_in:,} (건당 {per:.0f}) / 출력 {tok_out:,}")
    if args.model in PRICE:
        pin, pout = PRICE[args.model]
        usd = tok_in / 1e6 * pin + tok_out / 1e6 * pout
        full = usd / len(sample) * 24227      # 전체 공고 백필 시
        print(f"비용  이번 실행 ${usd:.4f}  |  24,227건 백필 환산 ${full:.2f}"
              f"  (단가 ${pin}/${pout} per 1M)")
    else:
        print(f"비용  단가 미상 - 토큰 수만 기록. "
              f"24,227건 환산 입력 {per*24227/1e6:.1f}M 토큰")

    rows = []
    for name, p in [("LLM (제목만)", preds), ("TF-IDF + LinearSVC", tfidf_pred)]:
        rows.append({"모델": name,
                     "Accuracy": f"{(np.array(p) == y).mean():.3f}",
                     "Macro F1": f"{f1_score(y, p, average='macro', zero_division=0):.3f}",
                     "Balanced Acc": f"{balanced_accuracy_score(y, p):.3f}"})
    print(pd.DataFrame(rows).to_string(index=False))

    print(f"\n[불일치 사례 - LLM vs 정답]")
    diff = sample.assign(llm=preds, tfidf=tfidf_pred)
    diff = diff[diff["llm"] != diff["tag"]]
    print(f"  {len(diff)}건 틀림")
    for _, r in diff.head(12).iterrows():
        mark = "  " if r["tfidf"] != r["tag"] else " *"  # *는 TF-IDF는 맞춘 것
        print(f"{mark} 정답={r['tag']:<12} LLM={r['llm']:<12} {r['title'][:44]}")

    # 모델명을 파일명에 넣지 않으면 모델을 바꿔가며 돌릴 때 서로 덮어쓴다
    slug = re.sub(r"[^0-9a-z]+", "-", args.model.lower()).strip("-")
    diff_path = OUT / f"llm_vs_tfidf_{cat}_fs{len(few_shot)}_{slug}.csv"
    sample.assign(llm=preds, tfidf=tfidf_pred).to_csv(diff_path, index=False,
                                                      encoding="utf-8-sig")

    m = {n: {"accuracy": float((np.array(p) == y).mean()),
             "macro_f1": float(f1_score(y, p, average="macro", zero_division=0)),
             "balanced_acc": float(balanced_accuracy_score(y, p))}
         for n, p in [("llm", preds), ("tfidf", list(tfidf_pred))]}
    record("llm_title_tagging", model=args.model, category=cat, few_shot=len(few_shot),
           n_test=len(sample), elapsed_sec=round(elapsed, 1),
           tokens_in=tok_in, tokens_out=tok_out, usd=round(usd, 4) if usd else None,
           metrics=m, predictions_csv=diff_path.name)
    print(f"\n저장: {diff_path.name}, outputs/metrics.jsonl")


if __name__ == "__main__":
    main()
