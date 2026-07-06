# -*- coding: utf-8 -*-
"""bid_id/file_id 조립·파싱. 언더스코어 구분자를 여기 하나로 관리한다."""


def build_id(bid_id: str, file_id: str) -> str:
    raise NotImplementedError


def parse_id(combined: str) -> tuple[str, str]:
    raise NotImplementedError
