# -*- coding: utf-8 -*-
import csv
from backfill_lambda import make_manifests as mm


def test_split_by_ext_groups_and_ignores_others():
    objs = [{"Key": "raw/a.hwp", "Size": 10}, {"Key": "raw/b.HWPX", "Size": 20},
            {"Key": "raw/c.pdf", "Size": 30}, {"Key": "raw/d.xlsx", "Size": 40}]
    got = mm.split_by_ext(objs)
    assert [k for _, k in got[".hwp"]] == ["raw/a.hwp"]
    assert [k for _, k in got[".hwpx"]] == ["raw/b.HWPX"]
    assert [k for _, k in got[".pdf"]] == ["raw/c.pdf"]
    assert ".xlsx" not in got


def test_pick_largest_returns_top_n_by_size():
    assert mm.pick_largest([(10, "a"), (50, "b"), (30, "c")], 2) == ["b", "c"]


def test_write_manifest_bucket_key_csv(tmp_path):
    out = tmp_path / "hwp.csv"
    mm.write_manifest(["raw/a.hwp", "raw/b.hwp"], "bidmate", str(out))
    assert list(csv.reader(out.open(encoding="utf-8"))) == [
        ["bidmate", "raw/a.hwp"], ["bidmate", "raw/b.hwp"]]
