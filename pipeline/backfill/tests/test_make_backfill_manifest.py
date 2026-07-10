# -*- coding: utf-8 -*-
"""make_backfill_manifest.select_pending_keys 단위테스트 — 이미 처리된 키 제외 검증."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import make_backfill_manifest as m  # noqa: E402

K1 = ("extracted/downloads/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
      "/R25BK01213271_001/R25BK01213271_001_doc01.json")
K2 = ("extracted/downloads/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
      "/R25BK01213271_001/R25BK01213271_001_doc02.json")
Q1 = ("qualifications/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
      "/R25BK01213271_001/R25BK01213271_001_doc01.json")


def test_excludes_already_processed():
    assert m.select_pending_keys([K1, K2], existing_qual_keys={Q1}) == [K2]


def test_includes_all_when_none_processed():
    assert m.select_pending_keys([K1, K2], existing_qual_keys=set()) == [K1, K2]


def test_empty_when_all_processed():
    q2 = ("qualifications/backfill/biz_div=cnstwk/year=2026/month=01/day=02"
          "/R25BK01213271_001/R25BK01213271_001_doc02.json")
    assert m.select_pending_keys([K1, K2], existing_qual_keys={Q1, q2}) == []
