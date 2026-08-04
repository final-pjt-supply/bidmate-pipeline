# -*- coding: utf-8 -*-
"""run.drive 집계 로직 테스트(실제 S3·임베딩 없이 fake process_one)."""
from embedding.backfill import run


def test_drive_counts_processed_skipped_failed():
    def fake_process_one(bucket, key):
        if key == "boom":
            raise RuntimeError("실패")
        if key.startswith("skip"):
            return "skipped", key
        return "processed", key

    keys = ["a", "skip1", "b", "boom", "skip2"]
    counts = run.drive(keys, fake_process_one, workers=2)
    assert counts == {"processed": 2, "skipped": 2, "failed": 1}
