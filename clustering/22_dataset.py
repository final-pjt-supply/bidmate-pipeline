# -*- coding: utf-8 -*-
"""데이터·라벨·그룹·분할을 한 곳에서 정의한다. 23·24번이 이 모듈을 가져다 쓴다.

여기 있는 규칙은 전부 실험 착수 전에 확정한 것이고, 실행 결과를 보고 바꾸지
않는다. 바꾸면 그 전에 낸 수치와 비교가 성립하지 않는다.

확정 사항과 근거:
  [소스]   RDS bid_table 단일 소스, ORDER BY bid_id.
           OpenSearch 캐시(meta_dedup.csv) 경유를 폐기했다. 그 파일은 fetch
           순서를 물려받았는데 그 순서를 복원할 방법이 없어 재현이 불가능했다.

  [라벨]   물품 = 세부품명번호 앞 2자리 -> 20종, 30건 미만 코드는 '기타'.
           용역 = COALESCE(세부품명번호, 업종코드) -> 9종.
           물품에 업종코드 대체 경로는 넣지 않는다. 업종코드는 '무슨 사업이냐'를,
           세부품명번호는 '무슨 물건이냐'를 가리켜 의미가 어긋난다. 용역은 사업
           성격이 곧 태그라 COALESCE가 성립하지만 물품은 태그가 품목 기준이다.

  [중복]   제목이 같아도 지우지 않는다. 실측 결과 중복 그룹의 태그가 물품
           99.5% / 용역 98.9% 일치해서, 지워도 새로 얻는 정보가 없고 남겨도
           잃는 것이 없다. 차이는 가중치뿐이다. 지우면 keep="first"의 순서
           의존성이 따라붙는데 그게 애초 문제의 출발점이었다.

  [그룹]   정규화 제목 + bid_ntce_no를 union-find로 이어붙인다.
           제목만으로는 제목이 수정된 변경공고(차수)를 못 막고, 공고번호만
           으로는 서로 다른 공고가 같은 정형 문구를 쓰는 경우를 못 막는다.

  [분할]   1~6월이 학습·검증 풀, 7월 이후가 시험셋(잠금).
           무작위 분할은 미래 공고로 배워 과거를 맞히는 셈이라 낙관적이다.
           실측: 6일 간격만으로도 새 제목의 char n-gram 중 12.2%가 학습
           어휘에 없었다. 그룹이 경계를 걸치면 통째로 학습 쪽에 붙여 시험셋을
           깨끗하게 유지한다.

  [CV]     StratifiedGroupKFold k=5. 그룹 제약과 층화를 동시에 만족하는
           유일한 sklearn 분할기다. 모든 비교는 평균±표준편차로 본다.

실행하면 7번(두 모집단 특성화) 리포트를 낸다:
    .venv/Scripts/python.exe clustering/22_dataset.py
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from sklearn.model_selection import StratifiedGroupKFold

BASE = Path(__file__).resolve().parent.parent

TEST_FROM = "2026-07-01"      # 이 날짜 이후 공고가 시험셋
N_FOLDS = 5
SEED = 42
MIN_TAG_COUNT = 30            # 물품에서 이보다 적은 코드는 '기타'로 병합

THNG_MAP = {
    "41": "실험·분석장비", "30": "토목·건설자재", "11": "토목·건설자재",
    "43": "IT·통신장비", "40": "공조·냉난방", "46": "안전·보안장비",
    "25": "차량·건설장비", "39": "전기·수배전", "42": "의료장비",
    "23": "산업·정밀기계", "60": "전시·교육기자재", "24": "운반·저장장비",
    "53": "피복·군장품", "51": "의약품·백신", "12": "시약·화학소모품",
    "26": "발전·전지", "55": "인쇄·사인물", "47": "환경·수처리설비",
    "56": "가구·침구", "50": "식품·급식",
}
SERVC_MAP = {
    "P81": "IT시스템", "B1468": "IT시스템", "B1169": "조사·연구",
    "P80": "행사·전시대행", "B5720": "행사·전시대행",
    "P82": "홍보·콘텐츠", "B1469": "홍보·콘텐츠", "B3244": "홍보·콘텐츠",
    "B6146": "감리·컨설팅", "B6525": "감리·컨설팅",
    "P78": "운송·차량임차", "B6728": "폐기물처리", "B1458": "통신망",
}

# 그룹 키를 만들 때만 쓰는 제목 정규화. 모델 입력에는 쓰지 않는다
# (14번 그리드에서 두 업종 모두 원문 제목이 최적이었다).
_PAT = [r"\[[^\]]*\]", r"\([^)]*(긴급|재공고|변경|정정)[^)]*\)",
        r"\b20\d{2}\s*년?\s*(度|년도)?", r"\b\d{2}년",
        r"재공고|변경공고|정정공고|입찰공고|긴급공고", r"제?\s*\d+\s*차(수|분)?",
        r"★[^★]*★", r"\(총괄\)|\(총액\)|\(계속비\)|\(가칭\)"]

TAG_SQL = {
    "thng": """
        SELECT DISTINCT ON (b.bid_id) b.bid_id, left(e->>'code',2) AS code
        FROM bid_table b, jsonb_array_elements(b.item_codes) e
        WHERE b.bid_category='thng' AND e->>'type'='세부품명번호'
          AND e->>'code' ~ '^[0-9]{8,}' ORDER BY b.bid_id
    """,
    "servc": """
        SELECT b.bid_id, COALESCE(
            MAX(CASE WHEN e->>'type'='세부품명번호' AND e->>'code' ~ '^[0-9]{8,}'
                     THEN 'P' || left(e->>'code',2) END),
            MAX(CASE WHEN e->>'type'='업종코드'
                      AND e->>'code' NOT IN ('9999','9901','9902','9903','9900')
                     THEN 'B' || (e->>'code') END)) AS code
        FROM bid_table b, jsonb_array_elements(b.item_codes) e
        WHERE b.bid_category='servc' GROUP BY b.bid_id
    """,
}

META_SQL = """
    SELECT bid_id, bid_ntce_no, bid_ntce_nm, dminstt_nm, bid_ntce_dt, presmpt_prce
    FROM bid_table
    WHERE bid_ntce_nm IS NOT NULL AND bid_category = %s
    ORDER BY bid_id
"""


def norm(t):
    """그룹 키용 제목 정규화. 연도·차수·재공고 표기를 지운다."""
    s = str(t)
    for p in _PAT:
        s = re.sub(p, " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]+", " ", s)).strip()


def connect():
    """RDS_* 또는 PG_* 어느 쪽으로든 붙는다.

    파이프라인 .env는 RDS_*, 백엔드 .env는 PG_*를 쓴다. 같은 DB인데 키 이름만
    다르므로 둘 다 시도한다 - 환경마다 .env를 고쳐 쓰는 것보다 낫다.
    """
    for path, prefix in [(BASE / ".env", "RDS"), (BASE / ".env", "PG"),
                         (BASE.parent / "bidmate-backend" / ".env", "PG")]:
        if not path.exists():
            continue
        env = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z_]+)=(.*)$", line.strip())
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
        k = ({"h": "RDS_Host", "d": "RDS_DB", "u": "RDS_Username", "p": "RDS_Password"}
             if prefix == "RDS" else
             {"h": "PG_HOST", "d": "PG_DATABASE", "u": "PG_USER", "p": "PG_PASSWORD"})
        if all(v in env for v in k.values()):
            return psycopg.connect(host=env[k["h"]], dbname=env[k["d"]], user=env[k["u"]],
                                   password=env[k["p"]], port=int(env.get("PG_PORT", 5432)))
    raise SystemExit("DB 접속 정보를 찾지 못했다 (.env의 RDS_* 또는 PG_*)")


def load(category, labeled_only=True):
    """공고를 읽어 태그를 붙인다. 중복 제목은 지우지 않는다.

    labeled_only=False면 코드가 없어 태그를 못 붙인 공고까지 함께 돌려준다
    (tag가 NaN). 7번 두 모집단 비교에 쓴다.
    """
    with connect() as conn:
        raw = pd.DataFrame(conn.execute(TAG_SQL[category]).fetchall(),
                           columns=["bid_id", "code"])
        meta = pd.DataFrame(conn.execute(META_SQL, (category,)).fetchall(),
                            columns=["bid_id", "ntce_no", "title", "instt",
                                     "ntce_dt", "price"])

    if category == "thng":
        counts = raw["code"].value_counts()
        raw["tag"] = raw["code"].map(THNG_MAP)
        rare = raw["code"].isin(counts[counts < MIN_TAG_COUNT].index)
        raw.loc[rare | raw["tag"].isna(), "tag"] = "기타"
    else:
        raw = raw[raw["code"].notna()].copy()
        raw["tag"] = raw["code"].map(SERVC_MAP).fillna("기타")

    df = meta.merge(raw[["bid_id", "tag"]], on="bid_id", how="left")
    if labeled_only:
        df = df[df["tag"].notna()]
    df = df.reset_index(drop=True)
    df["norm"] = df["title"].map(norm)
    # 모델 입력 후보 두 갈래. 공고기관(ntce_instt_nm)이 아니라 수요기관을 쓴다 -
    # 공고기관은 12곳 전부가 조달청이라 태그를 전혀 좁히지 못한다.
    df["text_title"] = df["title"].astype(str)
    df["text_instt_title"] = (df["instt"].fillna("").astype(str) + " "
                              + df["title"].astype(str)).str.strip()
    return df


def build_groups(df):
    """정규화 제목이 같거나 공고번호가 같은 행을 한 그룹으로 잇는다(union-find)."""
    parent = list(range(len(df)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for col in ("norm", "ntce_no"):
        first = {}
        for i, key in enumerate(df[col].to_numpy()):
            if not key:
                continue
            if key in first:
                union(first[key], i)
            else:
                first[key] = i
    return np.array([find(i) for i in range(len(df))])


def time_split(df, groups, test_from=TEST_FROM):
    """시험셋은 test_from 이후 공고. 그룹이 경계를 걸치면 통째로 학습 쪽에 둔다.

    걸친 그룹을 시험에 넣으면 같은 사업의 앞 차수가 학습에 있어 유출이 된다.
    """
    is_test = (df["ntce_dt"] >= pd.Timestamp(test_from)).to_numpy()
    g = pd.DataFrame({"g": groups, "t": is_test})
    all_test = g.groupby("g")["t"].all()
    test_groups = set(all_test[all_test].index)
    mask = np.array([gi in test_groups for gi in groups])
    return np.where(~mask)[0], np.where(mask)[0]


def folds(df, y, groups, idx, n_folds=N_FOLDS, seed=SEED):
    """StratifiedGroupKFold 분할을 (학습 인덱스, 검증 인덱스) 쌍으로 돌려준다."""
    skf = StratifiedGroupKFold(n_splits=n_folds, shuffle=True, random_state=seed)
    for tr_rel, va_rel in skf.split(idx, y[idx], groups[idx]):
        yield idx[tr_rel], idx[va_rel]


def check_no_leak(df, y, groups, pool, test):
    """분할이 그룹을 가로지르지 않는지 확인한다.

    여기가 깨지면 이후 모든 수치가 낙관적으로 부풀어 오르므로, 조용히 넘어가지
    않고 즉시 멈춘다.
    """
    assert not (set(groups[pool]) & set(groups[test])), "학습 풀과 시험셋이 그룹을 공유한다"
    for tr, va in folds(df, y, groups, pool):
        assert not (set(groups[tr]) & set(groups[va])), "fold가 그룹을 가로질렀다"
    assert df["ntce_dt"].iloc[test].min() >= pd.Timestamp(TEST_FROM), "시험셋에 과거 공고가 있다"


def describe_populations(category, kor):
    """7번 - 코드 있는 공고(학습·평가 대상)와 없는 공고(실제 적용 대상) 비교.

    라벨이 무작위로 빠진 게 아니면 학습·평가 집단은 편향된 표본이고, 거기서 잰
    성능은 실사용 성능과 다르다. 얼마나 다른지를 숫자로 남긴다.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer

    df = load(category, labeled_only=False)
    coded = df[df["tag"].notna()]
    uncoded = df[df["tag"].isna()]
    print(f"\n{'=' * 74}\n[{kor}] 두 모집단 비교  전체 {len(df):,}건")
    print(f"{'=' * 74}")
    print(f"  코드 있음(학습·평가 대상) {len(coded):,}건 ({len(coded)/len(df)*100:.1f}%)")
    print(f"  코드 없음(실제 적용 대상) {len(uncoded):,}건 ({len(uncoded)/len(df)*100:.1f}%)")
    if len(uncoded) == 0:
        print("  코드 없는 공고가 없어 비교를 건너뛴다")
        return

    rows = []
    for name, g in [("코드 있음", coded), ("코드 없음", uncoded)]:
        rows.append({
            "집단": name, "건수": len(g),
            "제목길이 평균": round(g["title"].str.len().mean(), 1),
            "제목길이 중앙": int(g["title"].str.len().median()),
            "수요기관 수": g["instt"].nunique(),
            "예정가격 중앙": f"{g['price'].median():,.0f}" if g["price"].notna().any() else "-",
        })
    print("\n" + pd.DataFrame(rows).to_string(index=False))

    # 코드 있는 공고로 만든 어휘가 코드 없는 공고를 얼마나 덮는가
    vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 3), min_df=1)
    vec.fit(coded["title"].astype(str))
    vocab = vec.vocabulary_

    def oov(t):
        grams = [t[i:i + k] for k in (2, 3) for i in range(len(t) - k + 1)]
        return sum(g not in vocab for g in grams) / len(grams) if grams else 0.0

    r_in = coded["title"].astype(str).map(oov)
    r_out = uncoded["title"].astype(str).map(oov)
    print(f"\n  학습 어휘 미포함(OOV) 비율 - 코드 있음 {r_in.mean()*100:.1f}%"
          f" / 코드 없음 {r_out.mean()*100:.1f}%")

    top_in = set(coded["instt"].value_counts().head(30).index)
    top_out = set(uncoded["instt"].value_counts().head(30).index)
    print(f"  상위 30개 수요기관 겹침: {len(top_in & top_out)}/30")

    m = df.assign(월=df["ntce_dt"].dt.to_period("M").astype(str),
                  집단=np.where(df["tag"].notna(), "코드 있음", "코드 없음"))
    print("\n  월별 분포")
    print(m.pivot_table(index="월", columns="집단", values="bid_id",
                        aggfunc="count").fillna(0).astype(int).to_string())


def main():
    for cat, kor in [("thng", "물품"), ("servc", "용역")]:
        df = load(cat)
        y = df["tag"].to_numpy()
        groups = build_groups(df)
        pool, test = time_split(df, groups)
        check_no_leak(df, y, groups, pool, test)

        print(f"\n{'=' * 74}\n[{kor}] 데이터셋\n{'=' * 74}")
        print(f"  라벨 있는 공고 {len(df):,}건 / 그룹 {len(set(groups)):,}개"
              f" / 태그 {df['tag'].nunique()}종")
        print(f"  학습·검증 풀 {len(pool):,}건 (~{TEST_FROM} 이전)")
        print(f"  시험셋      {len(test):,}건 ({TEST_FROM}~, 잠금)")
        print(f"  기간 {df['ntce_dt'].min():%Y-%m-%d} ~ {df['ntce_dt'].max():%Y-%m-%d}")
        print(f"  누수 검사 통과 (그룹이 풀/시험, fold 간을 가로지르지 않음)")

        vc = df.iloc[pool]["tag"].value_counts()
        print(f"\n  풀의 태그 분포 (최다 {vc.iloc[0]:,} / 최소 {vc.iloc[-1]:,})")
        print("   " + "  ".join(f"{k}:{v}" for k, v in vc.items()))

        describe_populations(cat, kor)


if __name__ == "__main__":
    main()
