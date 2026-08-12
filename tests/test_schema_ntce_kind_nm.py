# -*- coding: utf-8 -*-
"""ntceKindNm → ntce_kind_nm 변환 테스트 (#122).

ingestion/ 모듈들은 flat import 구조라 그 디렉토리를 sys.path에 넣는다
(test_ingestion_archive.py와 동일 방식).
"""
import os
import sys

INGESTION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ingestion")
if INGESTION not in sys.path:
    sys.path.insert(0, INGESTION)

from schema import to_curated  # noqa: E402


def _record(**extra):
    return {"bidNtceNo": "R26BK01649438", "bidNtceOrd": "001", **extra}


def test_ntce_kind_nm_mapped():
    curated = to_curated(_record(ntceKindNm="취소공고"), "cnstwk")
    assert curated["ntce_kind_nm"] == "취소공고"


def test_ntce_kind_nm_blank_and_missing_are_none():
    # 원본에 키가 없거나 공백이면 None으로 정규화(_txt 규칙).
    assert to_curated(_record(), "servc")["ntce_kind_nm"] is None
    assert to_curated(_record(ntceKindNm="  "), "servc")["ntce_kind_nm"] is None
