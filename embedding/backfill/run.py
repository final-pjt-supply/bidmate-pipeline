# -*- coding: utf-8 -*-
"""backfill 청킹/임베딩 배치 CLI.

리포 루트에서:
    python -m embedding.backfill.run --stage chunk [--workers N] [--limit K]
    python -m embedding.backfill.run --stage embed [--workers N] [--limit K]
    python -m embedding.backfill.run --stage all   [--workers N] [--limit K]

단계별 per-doc 멱등(출력 키 존재 시 skip)이라 중단돼도 재실행하면 이어서 처리한다.
"""
import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

# 스크립트(`python embedding/backfill/run.py`)로 실행돼도 임포트되게 리포 루트 추가.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from embedding.backfill import config, s3_io, stage_chunk, stage_embed  # noqa: E402

logger = logging.getLogger("embedding.backfill.run")


def drive(keys: Iterable[str], process_one: Callable[[str, str], tuple], workers: int) -> dict:
    """keys를 workers개 스레드로 process_one 처리하고 상태별 건수를 집계한다."""
    counts = {"processed": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_one, config.BUCKET, k): k for k in keys}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                status, _ = fut.result()
                counts[status] += 1
            except Exception:
                counts["failed"] += 1
                logger.exception("실패: %s", key)
            done = sum(counts.values())
            if done % 200 == 0:
                logger.info("진행 %d건: %s", done, counts)
    return counts


def _run_stage(name: str, prefix: str, process_one, workers: int, limit) -> None:
    keys = s3_io.list_json_keys(config.BUCKET, prefix, limit)
    counts = drive(keys, process_one, workers)
    logger.info("%s 완료: %s", name, counts)


def main(argv=None) -> None:
    config.load_env()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="backfill 청킹/임베딩 배치")
    p.add_argument("--stage", choices=["chunk", "embed", "all"], required=True)
    p.add_argument("--workers", type=int, default=6, help="문서 단위 동시성(기본 6)")
    p.add_argument("--limit", type=int, default=None, help="처리할 문서 수 상한(스모크용)")
    args = p.parse_args(argv)

    if args.stage in ("chunk", "all"):
        _run_stage("청킹", config.EXTRACTED_PREFIX, stage_chunk.process_one,
                   args.workers, args.limit)
    if args.stage in ("embed", "all"):
        _run_stage("임베딩", config.CHUNKS_PREFIX, stage_embed.process_one,
                   args.workers, args.limit)


if __name__ == "__main__":
    main()
