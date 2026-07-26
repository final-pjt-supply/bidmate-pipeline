# -*- coding: utf-8 -*-
"""기존 공고 제목 벡터를 bid_chunks에 일회성 백필한다.

본문 vectors/나 임베딩 큐를 재사용하지 않는다. S3 raw/curated의 공고 JSON에서
bid_id·bid_ntce_nm만 읽고, 제목만 BGE-M3로 임베딩해 type=title 문서를 bulk
upsert한다. 문서 ID는 실시간 Lambda와 같은 ``{bid_id}_title::0``이라 재실행해도
중복이 생기지 않는다.

안전한 실행 순서:
    python backfill_title_vectors.py --env-file <env> --dry-run
    python backfill_title_vectors.py --env-file <env> --limit 10
    python backfill_title_vectors.py --env-file <env> --yes

필수 환경변수(왼쪽 이름 우선, 오른쪽은 bidmate-backend 로컬 호환):
    CLOUDFLARE_ACCOUNT_ID 또는 CF_ACCOUNT_ID
    CLOUDFLARE_API_TOKEN 또는 CF_API_TOKEN
    OPENSEARCH_LOCAL_URL 또는 OPENSEARCH_HOST/OPENSEARCH_PORT
    OPENSEARCH_USER, OPENSEARCH_PASSWORD
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import boto3
import requests
import urllib3
from dotenv import load_dotenv

REGION = "ap-northeast-2"
BUCKET = "bidmate"
CURATED_PREFIX = "raw/curated/"
INDEX_NAME = "bid_chunks"
MODEL = "@cf/baai/bge-m3"
EMBEDDING_VERSION = "v1"
DEFAULT_BATCH_SIZE = 50
S3_READ_WORKERS = 16
SCROLL_TTL = "2m"


@dataclass(frozen=True)
class TitleRecord:
    bid_id: str
    title: str
    key: str
    last_modified: datetime


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise RuntimeError(f"필수 환경변수 없음: {' 또는 '.join(names)}")


def opensearch_url() -> str:
    local_url = os.getenv("OPENSEARCH_LOCAL_URL") or os.getenv("OPENSEARCH_URL")
    if local_url:
        return local_url.rstrip("/")
    host = os.getenv("OPENSEARCH_HOST")
    if os.getenv("OPENSEARCH_MODE") == "aws" and host:
        port = os.getenv("OPENSEARCH_PORT", "443")
        scheme = os.getenv("OPENSEARCH_SCHEME", "https")
        return f"{scheme}://{host}:{port}".rstrip("/")
    # 로컬 실행은 Private VPC 도메인에 직접 붙을 수 없으므로 기존 추천 검증
    # 스크립트와 같은 SSH 터널 기본값을 쓴다.
    return "https://localhost:9243"


def list_curated_objects(s3) -> list[dict]:
    paginator = s3.get_paginator("list_objects_v2")
    objects: list[dict] = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=CURATED_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                objects.append(
                    {"key": obj["Key"], "last_modified": obj["LastModified"]}
                )
    return objects


def load_latest_titles(
    s3, objects: list[dict], *, only_bid_id: str | None = None
) -> tuple[dict[str, TitleRecord], list[str]]:
    """curated JSON을 읽어 bid_id별 최신 제목만 남긴다."""
    latest: dict[str, TitleRecord] = {}
    malformed: list[str] = []
    only_suffix = None
    if only_bid_id:
        bid_ntce_no, sep, bid_ntce_ord = only_bid_id.rpartition("_")
        if not sep:
            raise ValueError(f"--bid-id 형식이 잘못됨: {only_bid_id}")
        only_suffix = f"/{bid_ntce_no}-{bid_ntce_ord}.json"
    candidates = [
        obj
        for obj in objects
        if not only_suffix or obj["key"].endswith(only_suffix)
    ]

    def load_one(obj: dict) -> tuple[TitleRecord | None, str | None]:
        try:
            body = json.loads(
                s3.get_object(Bucket=BUCKET, Key=obj["key"])["Body"].read()
            )
            bid_id = body.get("bid_id")
            title = body.get("bid_ntce_nm")
            if not isinstance(bid_id, str) or not bid_id:
                raise ValueError("bid_id 없음")
            if only_bid_id and bid_id != only_bid_id:
                raise ValueError("요청 bid_id와 JSON bid_id 불일치")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("bid_ntce_nm 없음")
            return (
                TitleRecord(
                    bid_id=bid_id,
                    title=title.strip(),
                    key=obj["key"],
                    last_modified=obj["last_modified"],
                ),
                None,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
            return None, obj["key"]

    # boto3 client는 생성 후 동시 읽기에 사용할 수 있다. 2만 건대 JSON을 순차
    # GetObject하면 백필 준비만 오래 걸리므로 제한된 워커 수로 I/O만 병렬화한다.
    with ThreadPoolExecutor(max_workers=S3_READ_WORKERS) as executor:
        loaded = executor.map(load_one, candidates)
        for record, bad_key in loaded:
            if bad_key:
                malformed.append(bad_key)
                continue
            assert record is not None
            current = latest.get(record.bid_id)
            if current is None or record.last_modified > current.last_modified:
                latest[record.bid_id] = record
    return latest, malformed


def _request(
    method: str,
    url: str,
    *,
    auth: tuple[str, str],
    verify: bool,
    **kwargs,
) -> requests.Response:
    response = requests.request(
        method, url, auth=auth, verify=verify, timeout=120, **kwargs
    )
    response.raise_for_status()
    return response


def load_existing_titles(
    base_url: str, auth: tuple[str, str], verify: bool
) -> dict[str, dict]:
    """OpenSearch의 현재 title 문서를 전부 읽어 bid_id별 source로 반환한다."""
    url = f"{base_url}/{INDEX_NAME}/_search?scroll={quote(SCROLL_TTL)}"
    body = {
        "size": 1000,
        "query": {"term": {"type": "title"}},
        "_source": ["bid_id", "text", "embedding_model", "embedding_version"],
    }
    response = _request(
        "POST", url, auth=auth, verify=verify, json=body
    ).json()
    scroll_id = response.get("_scroll_id")
    existing: dict[str, dict] = {}
    try:
        while True:
            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                break
            for hit in hits:
                source = hit.get("_source", {})
                bid_id = source.get("bid_id")
                if bid_id:
                    existing[bid_id] = source
            response = _request(
                "POST",
                f"{base_url}/_search/scroll",
                auth=auth,
                verify=verify,
                json={"scroll": SCROLL_TTL, "scroll_id": scroll_id},
            ).json()
            scroll_id = response.get("_scroll_id", scroll_id)
    finally:
        if scroll_id:
            try:
                _request(
                    "DELETE",
                    f"{base_url}/_search/scroll",
                    auth=auth,
                    verify=verify,
                    json={"scroll_id": [scroll_id]},
                )
            except requests.RequestException:
                pass
    return existing


def select_targets(
    records: dict[str, TitleRecord], existing: dict[str, dict]
) -> tuple[list[TitleRecord], int]:
    targets: list[TitleRecord] = []
    unchanged = 0
    for bid_id, record in records.items():
        current = existing.get(bid_id)
        if (
            current
            and current.get("text") == record.title
            and current.get("embedding_model") == MODEL
            and current.get("embedding_version") == EMBEDDING_VERSION
        ):
            unchanged += 1
            continue
        targets.append(record)
    targets.sort(key=lambda record: record.bid_id)
    return targets, unchanged


def embed_titles(texts: list[str]) -> list[list[float]]:
    account = _env("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")
    token = _env("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN")
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account}/ai/run/{MODEL}"
    )
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"text": texts},
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(f"Cloudflare 임베딩 실패: {str(body)[:500]}")
    vectors = body.get("result", {}).get("data", [])
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"임베딩 개수 불일치: 요청 {len(texts)}개, 응답 {len(vectors)}개"
        )
    return vectors


def build_title_action(
    record: TitleRecord, vector: list[float], indexed_at: str
) -> tuple[dict, dict]:
    metadata = {
        "index": {
            "_index": INDEX_NAME,
            "_id": f"{record.bid_id}_title::0",
        }
    }
    source = {
        "file_id": f"{record.bid_id}_title",
        "bid_id": record.bid_id,
        "document_id": "title",
        "chunk_idx": 0,
        "type": "title",
        "text": record.title,
        "vector": vector,
        "indexed_at": indexed_at,
        "embedding_model": MODEL,
        "embedding_version": EMBEDDING_VERSION,
    }
    return metadata, source


def bulk_upsert(
    base_url: str,
    auth: tuple[str, str],
    verify: bool,
    records: list[TitleRecord],
    vectors: list[list[float]],
) -> None:
    indexed_at = datetime.now(timezone.utc).isoformat()
    lines: list[str] = []
    for record, vector in zip(records, vectors):
        metadata, source = build_title_action(record, vector, indexed_at)
        lines.append(json.dumps(metadata, ensure_ascii=False))
        lines.append(json.dumps(source, ensure_ascii=False))
    payload = "\n".join(lines) + "\n"
    response = _request(
        "POST",
        f"{base_url}/_bulk",
        auth=auth,
        verify=verify,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
    ).json()
    if response.get("errors"):
        failures = [
            item
            for item in response.get("items", [])
            if item.get("index", {}).get("error")
        ]
        raise RuntimeError(f"OpenSearch bulk 일부 실패: {failures[:5]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="공고 제목 벡터 일회성 백필")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="접속 환경변수 파일 경로(기본 .env)",
    )
    parser.add_argument("--limit", type=int, help="앞에서부터 N건만 처리")
    parser.add_argument("--bid-id", help="특정 bid_id 한 건만 처리")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        choices=range(1, 101),
        metavar="1..100",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="S3/OpenSearch 현황만 조회하고 임베딩·적재하지 않음",
    )
    parser.add_argument("--yes", action="store_true", help="전체 실행 확인 생략")
    parser.add_argument(
        "--verify-certs",
        action="store_true",
        help="OpenSearch TLS 인증서 검증(로컬 SSH 터널이 아니면 권장)",
    )
    args = parser.parse_args()
    load_dotenv(Path(args.env_file), override=False)
    if not args.verify_certs:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    s3 = boto3.client("s3", region_name=REGION)
    base_url = opensearch_url()
    auth = (_env("OPENSEARCH_USER"), _env("OPENSEARCH_PASSWORD"))

    print("S3 curated 공고 목록 조회 중...")
    objects = list_curated_objects(s3)
    records, malformed = load_latest_titles(
        s3, objects, only_bid_id=args.bid_id
    )
    print("OpenSearch 기존 title 문서 조회 중...")
    existing = load_existing_titles(base_url, auth, args.verify_certs)
    targets, unchanged = select_targets(records, existing)
    if args.limit is not None:
        targets = targets[: args.limit]

    print(f"curated JSON: {len(objects):,}개")
    print(f"고유 제목 공고: {len(records):,}건")
    print(f"형식 이상/제목 없음: {len(malformed):,}개")
    print(f"동일 제목·모델로 이미 완료: {unchanged:,}건")
    print(f"이번 처리 대상: {len(targets):,}건")

    if args.dry_run or not targets:
        print("쓰기 없이 종료합니다.")
        return
    if not args.yes:
        answer = input(
            f"{len(targets):,}건을 Cloudflare로 임베딩해 "
            f"OpenSearch {INDEX_NAME}에 upsert합니다. 계속할까요? [y/N] "
        )
        if answer.strip().lower() != "y":
            print("취소했습니다.")
            return

    completed = 0
    for start in range(0, len(targets), args.batch_size):
        batch = targets[start : start + args.batch_size]
        vectors = embed_titles([record.title for record in batch])
        bulk_upsert(base_url, auth, args.verify_certs, batch, vectors)
        completed += len(batch)
        print(f"진행: {completed:,}/{len(targets):,}", flush=True)
    print(f"백필 완료: {completed:,}건")


if __name__ == "__main__":
    main()
