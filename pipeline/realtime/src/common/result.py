# -*- coding: utf-8 -*-
"""추출 결과를 최종 출력 스키마로 조립.

포맷별 pages 정리(실제 페이지 vs 1000자 인위 분할)는 각 extractors/*.py가 이미
끝내둔 상태로 넘어오므로, 여기서는 포맷을 몰라도 되게 그대로 감싸기만 한다.

is_scanned 등 포맷 전용 부가 필드(예: pdf의 스캔본 감지 결과)는 통일 스키마엔
안 싣도록 여기서 걸러낸다 — 감지 로직 자체는 extractors에 그대로 남아있어서
OCR 붙일 때 바로 재사용 가능하다.
"""
import logging

logger = logging.getLogger(__name__)

# 이보다 짧으면 스캔본 등이 빈 자격으로 조용히 새고 있을 수 있어 로그만 남긴다
_MIN_TEXT_LENGTH = 20
_SCHEMA_KEYS = ("page", "text")


def build_result(
    bid_id: str, document_id: str, pages: list[dict], partial: bool = False
) -> dict:
    result_pages = []
    for p in pages:
        stripped_len = len(p["text"].strip())
        if stripped_len < _MIN_TEXT_LENGTH:
            logger.warning(
                "텍스트 비정상적으로 짧음: bid_id=%s document_id=%s page=%s (%d자)",
                bid_id, document_id, p["page"], stripped_len,
            )
        result_pages.append({k: p[k] for k in _SCHEMA_KEYS})
    result = {"bid_id": bid_id, "document_id": document_id, "pages": result_pages}
    if partial:
        # 본문 일부만 회수된 문서. 소비처(embed 등)는 pages만 읽으므로 동작에는
        # 영향이 없고, 나중에 잘린 문서만 골라 재처리할 수 있게 표시만 남긴다.
        logger.warning(
            "부분 추출 결과 저장: bid_id=%s document_id=%s", bid_id, document_id,
        )
        result["partial"] = True
    return result
