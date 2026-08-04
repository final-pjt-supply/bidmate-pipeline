# -*- coding: utf-8 -*-
"""테스트에서 리포 루트를 import 경로에 추가(embedding.* / embedding.backfill.* 임포트용)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
