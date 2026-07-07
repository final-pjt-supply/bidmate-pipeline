# -*- coding: utf-8 -*-
"""raw/ 스캔 → 확장자별 S3 Batch 매니페스트 CSV(bucket,key) 생성.

사용: python -m lambda_app.make_manifests --bucket bidmate --prefix raw/downloads/ \
        --outdir ./manifests [--sample-largest 40]
"""
import argparse
import csv
import os

_EXTS = (".hwp", ".hwpx", ".pdf")


def split_by_ext(objects, exts=_EXTS):
    """[{"Key","Size"}] → {ext: [(size, key)]}. 대상 외 확장자는 제외."""
    out = {e: [] for e in exts}
    for o in objects:
        e = os.path.splitext(o["Key"])[1].lower()
        if e in out:
            out[e].append((o["Size"], o["Key"]))
    return out


def pick_largest(rows, n):
    """[(size, key)]에서 크기 상위 n건의 key 리스트."""
    return [k for _, k in sorted(rows, reverse=True)[:n]]


def write_manifest(keys, bucket, path):
    """키 리스트를 bucket,key CSV로 기록."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for k in keys:
            w.writerow([bucket, k])


def _scan(s3, bucket, prefix):
    paginator = s3.get_paginator("list_objects_v2")
    for pg in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for o in pg.get("Contents", []):
            yield {"Key": o["Key"], "Size": o["Size"]}


def main():
    import boto3
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", default="raw/downloads/")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--sample-largest", type=int, default=0)
    args = ap.parse_args()

    s3 = boto3.client("s3")
    groups = split_by_ext(list(_scan(s3, args.bucket, args.prefix)))
    os.makedirs(args.outdir, exist_ok=True)
    for ext, rows in groups.items():
        name = ext.lstrip(".")
        write_manifest([k for _, k in rows], args.bucket,
                       os.path.join(args.outdir, f"{name}.csv"))
        if args.sample_largest:
            write_manifest(pick_largest(rows, args.sample_largest), args.bucket,
                           os.path.join(args.outdir, f"{name}-mini.csv"))
        print(f"{ext}: {len(rows)} rows")


if __name__ == "__main__":
    main()
