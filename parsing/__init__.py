# -*- coding: utf-8 -*-
"""패키지 공개 API. 실제 라우팅 로직은 parsing.hwp_hwpx가 갖고 있고, 여기서는 재노출만 한다."""
from parsing.hwp_hwpx import extract, extract_bytes, to_txt
from parsing.hwp_hwpx.contract import ExtractResult

__all__ = [
    "extract", "extract_bytes", "to_txt", "ExtractResult",
]
