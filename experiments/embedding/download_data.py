# -*- coding: utf-8 -*-
"""bidmate의 extracted/daily/ 전체 JSON을 experiments/embedding/data/에
파티션 구조(biz_div=.../year=.../month=.../day=.../hour=.../{bid_id}/{file}.json)를
그대로 유지한 채 다운로드한다.

data/는 실데이터라 .gitignore 처리돼 있음 — 이 스크립트가 재현 수단이므로
스크립트 자체만 커밋한다.

실행(리포 루트에서):
    python experiments/embedding/download_data.py
"""
import json
from pathlib import Path

import boto3

BUCKET = "bidmate"
SOURCE_PREFIX = "extracted/daily/"
DATA_DIR = Path(__file__).parent / "data"


def download_all() -> list[Path]:
    s3 = boto3.client("s3", region_name="ap-northeast-2")
    paginator = s3.get_paginator("list_objects_v2")

    saved = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=SOURCE_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # SOURCE_PREFIX(extracted/daily/) 이후 경로만 data/ 밑에 그대로 재현
            rel_path = key[len(SOURCE_PREFIX):]
            local_path = DATA_DIR / rel_path
            local_path.parent.mkdir(parents=True, exist_ok=True)

            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            local_path.write_bytes(body)
            saved.append(local_path)

    return saved


def main() -> None:
    saved = download_all()
    print(f"다운로드 완료: {len(saved)}개 파일 -> {DATA_DIR}")


if __name__ == "__main__":
    main()
