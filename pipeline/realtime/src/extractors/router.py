# -*- coding: utf-8 -*-
"""매직바이트로 실제 포맷을 판정해 알맞은 추출기로 라우팅(확장자 오라벨 대응, #75 후속).

⚠ backfill_lambda/router.py의 _detect_format과 동일 로직 — 한쪽 수정 시 다른 쪽도
확인. 별도 Lambda·별도 이미지라 의도적으로 중복 유지(import로 결합시키지 않음).

배경: raw/downloads/daily/의 확장자가 실제 콘텐츠와 어긋나는 케이스가 실측으로
확인됐다(.hwpx인데 실제론 OLE/HWP5, .hwp인데 실제론 ZIP/HWPX). S3 알림 라우팅은
확장자(suffix)만 보고 큐를 정하므로, 어긋난 파일은 항상 틀린 추출기로 가서
결정적으로(재시도해도 매번 동일하게) 실패한다. dispatch()가 실제 콘텐츠를 보고
올바른 추출기를 호출해 이 오배송을 흡수한다.

backfill_lambda의 extract_document는 그대로 가져오지 않는다 — 거기서 호출하는
parsing.hwp_hwpx.*_extractor는 반환 계약이 다르다(pages 아닌 통짜 text). 여기서는
realtime이 이미 가진 extractors.pdf/hwp/hwpx.extract()로만 디스패치해 pages 계약을
그대로 유지한다.
"""
import logging
import os

from extractors.base import ExtractResult

logger = logging.getLogger(__name__)

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect_format(data: bytes, filename: str) -> str:
    """매직바이트로 실제 포맷 판정, 불명 시 확장자 폴백."""
    head = data[:8]
    if head[:4] == b"PK\x03\x04":
        return "hwpx"
    if head == _OLE:
        return "hwp"
    if head[:4] == b"%PDF":
        return "pdf"
    return os.path.splitext(filename)[1].lower().lstrip(".")


def dispatch(data: bytes, key: str) -> ExtractResult:
    """실제 포맷을 판정해 맞는 추출기를 호출한다. 확장자와 다르면 WARNING으로 남긴다
    (오배송 빈도 추적용 — #75에서 82건 발견됨).

    위치 인자 1개(data)만 넘긴다 — pdf.extract()에는 describe_fn이 없어서
    hwp/hwpx와 획일적으로 describe_fn까지 넘기면 TypeError가 난다.
    """
    ext = os.path.splitext(key)[1].lower().lstrip(".")
    fmt = detect_format(data, key)
    if fmt != ext:
        logger.warning(
            "확장자-실제 포맷 불일치: key=%s 확장자=%s 실제포맷=%s", key, ext, fmt,
        )
    # backfill_lambda/router.py와 동일하게 branch 안에서만 import — 매 호출마다
    # 실제로 쓰는 추출기 하나만 로드한다(예: hwp 처리 시 무거운 pymupdf/fitz는
    # 아예 안 건드림).
    if fmt == "hwpx":
        from extractors import hwpx
        return hwpx.extract(data)
    if fmt == "hwp":
        from extractors import hwp
        return hwp.extract(data)
    if fmt == "pdf":
        from extractors import pdf
        return pdf.extract(data)
    raise ValueError(f"지원하지 않는 형식: {fmt} (key={key})")
