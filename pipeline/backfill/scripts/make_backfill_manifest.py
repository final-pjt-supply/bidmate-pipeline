# -*- coding: utf-8 -*-
"""extracted/downloads/backfill/ 스캔 → 아직 qualifications에 없는 키만 S3 Batch 매니페스트 CSV(bucket,key)로 출력.

이미지에 빌드되지 않는 로컬 운영 도구. backfill/paths.py의 변환 규약을 재사용해
extracted↔qualifications diff로 멱등 skip과 짝을 맞춘다(재실행 시 미완료분만 담김).

사용:
  cd pipeline/backfill/scripts
  python make_backfill_manifest.py --bucket bidmate --out manifest.csv [--limit 10]
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from backfill.paths import extracted_backfill_key_to_qualifications_key  # noqa: E402


def select_pending_keys(extracted_keys: list[str], existing_qual_keys: set[str]) -> list[str]:
    """qualifications에 대응 결과가 아직 없는 extracted 키만 순서 유지하며 반환."""
    pending = []
    for k in extracted_keys:
        try:
            out = extracted_backfill_key_to_qualifications_key(k)
        except ValueError:
            continue  # 형식 밖 키(.json 아님 등)는 제외
        if out not in existing_qual_keys:
            pending.append(k)
    return pending


def _list_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for pg in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for o in pg.get("Contents", []):
            keys.append(o["Key"])
    return keys


def write_manifest(bucket: str, keys: list[str], out_path: str) -> None:
    """bucket,key CSV로 기록. csv 모듈이 키 내 쉼표/따옴표를 이스케이핑한다."""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for k in keys:
            w.writerow([bucket, k])


def main():
    import boto3
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default="bidmate")
    ap.add_argument("--extracted-prefix", default="extracted/downloads/backfill/")
    ap.add_argument("--qualifications-prefix", default="qualifications/backfill/")
    ap.add_argument("--out", default="manifest.csv")
    ap.add_argument("--limit", type=int, default=0, help="선검증용: 앞에서 N건만(0=전량)")
    args = ap.parse_args()

    s3 = boto3.client("s3")
    extracted_keys = _list_keys(s3, args.bucket, args.extracted_prefix)
    existing_qual_keys = set(_list_keys(s3, args.bucket, args.qualifications_prefix))

    pending = select_pending_keys(extracted_keys, existing_qual_keys)
    if args.limit:
        pending = pending[:args.limit]

    write_manifest(args.bucket, pending, args.out)
    print(f"extracted={len(extracted_keys)} 이미완료={len(existing_qual_keys)} "
          f"대상={len(pending)} → {args.out}")


if __name__ == "__main__":
    main()
