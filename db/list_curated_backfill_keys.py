"""List curated backfill JSON keys from S3.

Example:
    python db/list_curated_backfill_keys.py --bucket bidmate --limit 10
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from dotenv import find_dotenv, load_dotenv


DEFAULT_BUCKET = "bidmate"
DEFAULT_PREFIX = "raw/curated/backfill/"


def iter_json_keys(s3_client, bucket: str, prefix: str) -> Iterator[str]:
    # S3는 한 번에 최대 1,000개만 반환하므로 paginator로 전체 prefix를 순회한다.
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # RDS 적재 대상은 curated JSON만이므로 폴더 placeholder나 다른 확장자는 제외한다.
            if key.endswith(".json"):
                yield key


def list_json_keys(s3_client, bucket: str, prefix: str, limit: int | None = None) -> list[str]:
    keys: list[str] = []
    for key in iter_json_keys(s3_client, bucket, prefix):
        keys.append(key)
        if limit is not None and len(keys) >= limit:
            break
    return keys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List S3 JSON keys under raw/curated/backfill/.",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET") or DEFAULT_BUCKET,
        help="S3 bucket name. Defaults to S3_BUCKET_NAME, S3_BUCKET, or bidmate.",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"S3 prefix to scan. Defaults to {DEFAULT_PREFIX}.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of keys to print. Use 0 for no limit.",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2",
        help="AWS region. Defaults to AWS_REGION, AWS_DEFAULT_REGION, or ap-northeast-2.",
    )
    return parser.parse_args()


def create_s3_client(region: str):
    # 로컬 실행은 .env를 우선 허용하고, EC2/Lambda에서는 IAM Role 기본 체인을 그대로 쓴다.
    load_dotenv(find_dotenv(usecwd=True))
    access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY")

    if access_key and secret_key:
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
    return boto3.client("s3", region_name=region)


def main() -> None:
    args = parse_args()
    limit = None if args.limit == 0 else args.limit
    s3 = create_s3_client(args.region)
    try:
        keys = list_json_keys(s3, args.bucket, args.prefix, limit)
    except NoCredentialsError:
        print(
            "AWS credentials not found. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY "
            "or AWS_ACCESS_KEY/AWS_SECRET_KEY.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except (BotoCoreError, ClientError) as exc:
        print(f"S3 list failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    for key in keys:
        print(key)
    print(f"listed={len(keys)} bucket={args.bucket} prefix={args.prefix}")


if __name__ == "__main__":
    main()
