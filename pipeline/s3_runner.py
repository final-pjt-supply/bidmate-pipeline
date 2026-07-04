# -*- coding: utf-8 -*-
"""S3 파이프라인: raw/ 문서를 txt로 추출해 txts/ 에 doc_N.txt 로 업로드.

.env 에서 자격증명·리전·소스/대상 버킷 주소를 읽는다.
  AWS_REGION, AWS_ACCESS_KEY, AWS_SECRET_KEY,
  BUCKET_SRC_ADDRESS(s3://.../raw/), BUCKET_LOC_ADDRESS(s3://.../txts/)
"""
import os
from urllib.parse import urlparse

import boto3
from dotenv import find_dotenv, load_dotenv

from parsing import extract_bytes, to_txt


def _parse_uri(uri: str):
    p = urlparse(uri)
    return p.netloc, p.path.lstrip("/")


def _load_config():
    load_dotenv(find_dotenv(usecwd=True))
    s3 = boto3.client(
        "s3",
        region_name=os.environ["AWS_REGION"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY"],
        aws_secret_access_key=os.environ["AWS_SECRET_KEY"],
    )
    return s3, os.environ["BUCKET_SRC_ADDRESS"], os.environ["BUCKET_LOC_ADDRESS"]


def _list_source_keys(s3, src_uri: str):
    bucket, prefix = _parse_uri(src_uri)
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    keys = [
        o["Key"] for o in resp.get("Contents", [])
        if o["Key"].lower().endswith((".hwp", ".hwpx"))
    ]
    return bucket, sorted(keys)


def run(dry_run: bool = False):
    s3, src_uri, dst_uri = _load_config()
    src_bucket, keys = _list_source_keys(s3, src_uri)
    dst_bucket, dst_prefix = _parse_uri(dst_uri)

    results = []
    for i, key in enumerate(keys, 1):
        try:
            data = s3.get_object(Bucket=src_bucket, Key=key)["Body"].read()
            result = extract_bytes(data, key)
            txt = to_txt(result)
        except Exception:
            # 다운로드 실패·읽을 수 없는 문서(암호/손상/미지원)는 건너뛰고 배치는 계속 진행
            print(f"unacceptable file: {key}")
            continue
        out_key = f"{dst_prefix}doc_{i}.txt"
        if not dry_run:
            s3.put_object(
                Bucket=dst_bucket, Key=out_key,
                Body=txt.encode("utf-8"),
                ContentType="text/plain; charset=utf-8",
            )
        results.append({
            "src_key": key, "out_key": out_key,
            "source_type": result["source_type"],
            "chars": len(txt), "images": len(result["images"]),
        })
    return results


if __name__ == "__main__":
    import sys

    dry = "--dry-run" in sys.argv
    try:
        results = run(dry_run=dry)
    except Exception as e:
        # S3 접속·설정 문제는 한 줄로 떨구고 종료
        print(f"S3 error: {e}")
        sys.exit(1)
    for r in results:
        arrow = "(dry-run, 업로드 안 함)" if dry else "->"
        print(f"[{r['source_type']}] {r['src_key']}  {arrow}  "
              f"{r['out_key']}  ({r['chars']}자, 이미지 {r['images']}개)")
