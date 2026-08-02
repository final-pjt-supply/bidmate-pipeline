# -*- coding: utf-8 -*-
"""bid_tags를 전부 다시 매긴다(#97).

Lambda의 정기 스윕은 태그가 아직 없는 공고만 집는다. 이미 태그가 있는 행까지
매 회차 재판정하게 두면, 코드가 있어도 매핑에 없는 공고가 무한히 다시 뽑힌다.
그래서 전체 재매김은 이 스크립트로 명시적으로 돌린다.

언제 쓰나:
  - 모델을 재학습했을 때
  - 태그 체계를 바꿨을 때(태그 추가/통합)
  - 임계값을 바꿔 _low 표시를 다시 매기고 싶을 때
    (조회 조건만 바꿔도 되지만, source 값을 실제로 맞추고 싶을 때)

기존 24,227건 초기 적재에는 쓸 필요가 없다 — Lambda 스윕이 회차마다
500건씩 갉아먹어 약 4시간이면 끝난다. 그보다 빨리 채우고 싶을 때만 쓴다.

실행 (기본은 미리보기, 실제 반영은 --apply 필요):
  python scripts/retag_all.py                    # 무엇이 바뀌는지만 보여준다
  python scripts/retag_all.py --apply
  python scripts/retag_all.py --apply --category thng
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tagging.rules import decide_tag  # noqa: E402

BATCH = 2000


def connect():
    missing = [k for k in ("MERGE_DB_HOST", "MERGE_DB_NAME", "MERGE_DB_USER",
                           "MERGE_DB_PASSWORD") if not os.environ.get(k)]
    if missing:
        sys.exit(f"환경변수가 없다: {missing}")
    return psycopg2.connect(
        host=os.environ["MERGE_DB_HOST"], port=int(os.environ.get("MERGE_DB_PORT", "5432")),
        dbname=os.environ["MERGE_DB_NAME"], user=os.environ["MERGE_DB_USER"],
        password=os.environ["MERGE_DB_PASSWORD"],
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="실제로 DB에 쓴다. 없으면 무엇이 바뀌는지만 보여준다")
    ap.add_argument("--category", choices=["cnstwk", "thng", "servc", "frgcpt"],
                    help="특정 업종만 재매김")
    args = ap.parse_args()

    conn = connect()
    where = "WHERE b.bid_id IS NOT NULL"
    params: list = []
    if args.category:
        where += " AND b.bid_category = %s"
        params.append(args.category)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT b.bid_id, b.bid_ntce_nm, b.bid_category, b.item_codes, "
            "b.main_cnstty_nm, t.tag AS old_tag, t.source AS old_source "
            f"FROM bid_table b LEFT JOIN bid_tags t ON t.bid_id = b.bid_id {where}",
            params,
        )
        rows = cur.fetchall()

    print(f"대상 {len(rows):,}건")
    changed, new, by_source = [], 0, Counter()
    for row in rows:
        tag, source, confidence = decide_tag(dict(row))
        by_source[source] += 1
        if row["old_tag"] is None:
            new += 1
        elif row["old_tag"] != tag or row["old_source"] != source:
            changed.append((row["bid_ntce_nm"], row["old_tag"], tag, source))

    print(f"  새로 매김  {new:,}건")
    print(f"  값이 바뀜  {len(changed):,}건")
    print("\n출처별")
    for source, n in by_source.most_common():
        print(f"  {source:<24}{n:>7,}건 {n/len(rows)*100:>5.1f}%")

    if changed:
        print(f"\n바뀌는 사례 (최대 15건)")
        for title, old, new_tag, source in changed[:15]:
            print(f"  {old} -> {new_tag} [{source}]")
            print(f"    {str(title)[:60]}")

    if not args.apply:
        print("\n미리보기만 했다. 실제로 반영하려면 --apply를 붙일 것.")
        return

    upserts = []
    for row in rows:
        tag, source, confidence = decide_tag(dict(row))
        upserts.append((row["bid_id"], tag, source, confidence))

    written = 0
    with conn.cursor() as cur:
        for i in range(0, len(upserts), BATCH):
            chunk = upserts[i:i + BATCH]
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO bid_tags (bid_id, tag, source, confidence) VALUES %s "
                "ON CONFLICT (bid_id) DO UPDATE SET tag = EXCLUDED.tag, "
                "source = EXCLUDED.source, confidence = EXCLUDED.confidence, "
                "updated_at = NOW()",
                chunk,
            )
            conn.commit()
            written += len(chunk)
            print(f"  {written:,}/{len(upserts):,}", flush=True)
    print(f"완료: {written:,}건")


if __name__ == "__main__":
    main()
