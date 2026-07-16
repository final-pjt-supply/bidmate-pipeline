# -*- coding: utf-8 -*-
"""S3 backfill 임베딩을 OpenSearch bid_chunks에 append하는 로더(Bastion 실행).

이 파일 상단은 순수 헬퍼(.env 로딩·체크포인트·추정)만 정의한다. CLI 오케스트레이션은
Task 3에서 추가된다. 문서 매핑은 같은 폴더의 opensearch_doc에 위임한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import opensearch_doc  # noqa: E402


def _clean(v: str) -> str:
    """dotenv가 남긴 잔재(따옴표·후행 쉼표) 방어적 제거."""
    if v is None:
        return v
    return v.strip().rstrip(",").strip().strip('"').strip("'")


def load_os_params(env_path=None) -> dict:
    """.env의 OPENSEARCH_*를 OpenSearch 접속 dict로. dotenv 사용(CLAUDE.md 규칙).

    .env 라벨 역전: 실제 유저=OPENSEARCH_DBNAME, 실제 패스워드=OPENSEARCH_USER.
    """
    from dotenv import dotenv_values
    env_path = Path(env_path) if env_path else Path(__file__).resolve().parents[1] / ".env"
    v = dotenv_values(env_path)
    return {
        "host": _clean(v["OPENSEARCH_HOST"]),
        "port": int(_clean(v["OPENSEARCH_PORT"])),
        "user": _clean(v["OPENSEARCH_DBNAME"]),
        "password": _clean(v["OPENSEARCH_USER"]),
    }


def load_checkpoint(path) -> set:
    """done_keys.txt를 로드(없으면 빈 set)."""
    p = Path(path)
    if not p.exists():
        return set()
    return {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()}


def append_checkpoint(path, key: str) -> None:
    """완료 S3 key 1건을 done_keys.txt에 append."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def estimate_total_chunks(sample_counts: list, total_files: int) -> int:
    """표본 파일들의 청크수 평균 × 전체 파일 수 = 전체 청크수 추정."""
    if not sample_counts:
        return 0
    avg = sum(sample_counts) / len(sample_counts)
    return round(avg * total_files)


# === Task 3: CLI orchestration (imports, constants, helpers, main) ===

import argparse
import json
import logging
import random
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BUCKET = "bidmate"
PREFIX = "embeddings/backfill/embedded/"


def _setup_logging(out_dir: Path, verbose: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fh = logging.FileHandler(out_dir / f"index_embeddings_{ts}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG); fh.setFormatter(fmt)
    ch = logging.StreamHandler(); ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    root.handlers[:] = [fh, ch]
    # opensearch-py/urllib3는 매 요청·응답 전문을 INFO/DEBUG로 쏟아낸다 — 파일 핸들러가
    # DEBUG라 100만 요청이면 로그가 수 GB로 불어 디스크를 채운다(실측: 4.5k 파일에 3.3GB).
    # 이 라이브러리 로거들을 WARNING으로 눌러 우리 로그만 남긴다.
    for noisy in ("opensearch", "opensearchpy", "urllib3",
                  "boto3", "botocore", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_client(params: dict):
    """basic auth + https OpenSearch 클라이언트(관리형 VPC 엔드포인트)."""
    from opensearchpy import OpenSearch
    return OpenSearch(
        hosts=[{"host": params["host"], "port": params["port"]}],
        http_auth=(params["user"], params["password"]),
        use_ssl=True, verify_certs=True, http_compress=True,
        timeout=120, max_retries=3, retry_on_timeout=True,
    )


def _s3_client(env_path=None):
    from dotenv import dotenv_values
    env_path = Path(env_path) if env_path else Path(__file__).resolve().parents[1] / ".env"
    v = {k: _clean(val) for k, val in dotenv_values(env_path).items()}
    import boto3
    return boto3.client(
        "s3",
        aws_access_key_id=v.get("AWS_ACCESS_KEY"),
        aws_secret_access_key=v.get("AWS_SECRET_KEY"),
        region_name=v.get("AWS_REGION") or "ap-northeast-2",
    )


def _list_keys(s3) -> list:
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _get_chunks(s3, key: str) -> list:
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return json.loads(body)


def _dry_run(s3, keys, done, sample_size, out_dir):
    pending = [k for k in keys if k not in done]
    sample = random.sample(pending, min(sample_size, len(pending))) if pending else []
    counts, sample_action = [], None
    for k in sample:
        chunks = _get_chunks(s3, k)
        counts.append(len(chunks))
        if sample_action is None and chunks:
            sample_action = opensearch_doc.chunk_to_action(
                chunks[0], opensearch_doc.INDEX_NAME, "DRYRUN")
    est = estimate_total_chunks(counts, len(pending))
    logger.info(
        "==== index_embeddings report (dry-run) ====\n"
        "전체 파일:        %d\n이미 완료(체크포인트): %d\n처리 대상 파일:   %d\n"
        "표본 파일:        %d (평균 청크 %.1f)\n추정 총 청크수:   ~%d\n"
        "==========================================",
        len(keys), len(done), len(pending), len(sample),
        (sum(counts) / len(counts)) if counts else 0.0, est,
    )
    if sample_action is not None:
        logger.info("매핑 샘플 _id=%s file_id=%s model=%s",
                    sample_action["_id"], sample_action["_source"]["file_id"],
                    sample_action["_source"]["embedding_model"])


def _failure_status(info):
    """streaming_bulk 실패 info에서 HTTP status 추출(없으면 None)."""
    if isinstance(info, dict) and info:
        op = next(iter(info.values()))
        if isinstance(op, dict):
            return op.get("status")
    return None


def _is_permanent(status) -> bool:
    """비-429 4xx만 영구 실패. 그 외(429·5xx·None·'N/A' 등 전송 오류)는 일시로 간주."""
    return isinstance(status, int) and 400 <= status < 500 and status != 429


def _index_file(client, key, chunks, failed_path):
    """한 파일의 청크를 streaming_bulk로 색인. (성공, 영구실패, 일시실패) 반환.

    - 영구 실패(비-429 4xx, 매핑/버전 오류 등): failed_docs.txt 기록 + 카운트.
    - 일시 실패(429 재시도 소진·5xx·status 불명·전송 예외 yield): 기록하지 않고 카운트만
      → 호출부가 체크포인트 없이 런을 중단, 재실행 시 그 파일부터 멱등 재개.
    배치는 파일 경계를 넘지 않는다(파일당 1회 호출).
    """
    from opensearchpy.helpers import streaming_bulk
    now_iso = datetime.now(timezone.utc).isoformat()
    actions = opensearch_doc.actions_for_chunks(chunks, opensearch_doc.INDEX_NAME, now_iso)
    ok_n = perm_n = trans_n = 0
    for ok, info in streaming_bulk(
        client, actions, chunk_size=200, max_retries=5, initial_backoff=2,
        max_backoff=60, raise_on_error=False, raise_on_exception=False,
        request_timeout=120,
    ):
        if ok:
            ok_n += 1
            continue
        status = _failure_status(info)
        if _is_permanent(status):
            perm_n += 1
            with open(failed_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "error": info}, ensure_ascii=False, default=str) + "\n")
        else:
            trans_n += 1  # 429 소진·5xx·불명 → 일시 장애로 간주
    return ok_n, perm_n, trans_n


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="S3 임베딩 → OpenSearch bid_chunks append")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--out-dir", default="logs", help="로그·체크포인트 위치(재실행 시 동일하게)")
    parser.add_argument("--sleep", type=float, default=0.0, help="파일 간 간격(초). rejected 늘면 0.3 등")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    _setup_logging(out_dir, args.verbose)
    done_path = out_dir / "done_keys.txt"
    failed_path = out_dir / "failed_docs.txt"
    skipped_path = out_dir / "skipped_files.txt"

    s3 = _s3_client()
    keys = _list_keys(s3)
    done = load_checkpoint(done_path)
    logger.info("시작: dry_run=%s 전체 %d 파일, 완료 %d", args.dry_run, len(keys), len(done))

    if args.dry_run:
        _dry_run(s3, keys, done, args.sample_size, out_dir)
        return 0

    client = get_client(load_os_params())
    index = opensearch_doc.INDEX_NAME
    # refresh_interval 완화(원래 값 읽어두고 try/finally로 원복)
    orig = client.indices.get_settings(index=index)[index]["settings"]["index"].get("refresh_interval")
    orig_disp = orig if orig is not None else "미설정(기본 1s)"
    client.indices.put_settings(index=index, body={"index": {"refresh_interval": "30s"}})
    logger.info("refresh_interval %s → 30s (완료 후 원복)", orig_disp)

    pending = [k for k in keys if k not in done]
    total_ok = total_perm = processed = 0
    start = time.time()
    try:
        for key in pending:
            try:
                chunks = _get_chunks(s3, key)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("파싱 실패 스킵: %s (%s)", key, e)
                with open(skipped_path, "a", encoding="utf-8") as f:
                    f.write(f"{key}\tparse_error\n")
                append_checkpoint(done_path, key)
                continue
            if not chunks:
                logger.warning("빈 임베딩 파일 스킵: %s", key)
                with open(skipped_path, "a", encoding="utf-8") as f:
                    f.write(f"{key}\tempty\n")
                append_checkpoint(done_path, key)
                continue
            ok_n, perm_n, trans_n = _index_file(client, key, chunks, failed_path)
            total_ok += ok_n; total_perm += perm_n
            if trans_n > 0:
                # 일시 장애 → 체크포인트 없이 중단(finally가 refresh 원복). 재실행 시 이 파일부터 멱등 재개.
                logger.error("일시 장애 %d건으로 중단: %s (재실행 시 재개)", trans_n, key)
                raise RuntimeError(f"transient bulk failures on {key}: {trans_n}")
            append_checkpoint(done_path, key)  # 모든 청크가 최종 상태(성공/영구실패) 도달
            processed += 1
            if processed <= 10 or processed % 200 == 0:
                logger.info("[%d/%d] 파일, 누적 색인 %d(영구실패 %d), elapsed %.0fs",
                            processed, len(pending), total_ok, total_perm, time.time() - start)
            if args.sleep:
                time.sleep(args.sleep)
    finally:
        client.indices.put_settings(index=index, body={"index": {"refresh_interval": orig}})
        client.indices.refresh(index=index)
        logger.info("refresh_interval 원복(%s) + _refresh 완료", orig_disp)

    count = client.count(index=index)["count"]
    logger.info(
        "==== index_embeddings report (apply) ====\n"
        "처리 파일:        %d\n색인 성공 청크:   %d\n영구 실패 청크:   %d -> failed_docs.txt\n"
        "인덱스 docs.count(refresh 후): %d\n총 소요:          %.0fs\n"
        "==========================================",
        processed, total_ok, total_perm, count, time.time() - start,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
