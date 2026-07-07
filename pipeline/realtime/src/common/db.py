# -*- coding: utf-8 -*-
"""bid_attachments 상태 갱신. DB 연결은 아직 구현 전 — 로그만 남기는 no-op."""
import logging

logger = logging.getLogger(__name__)


def mark_attachment_failed(bid_id: str, document_id: str) -> None:
    # TODO: 여기 실제 DB UPDATE(bid_attachments.status = 'failed') 들어갈 자리
    logger.warning(
        "bid_attachments 상태 갱신 스킵(DB 미연결): bid_id=%s document_id=%s",
        bid_id, document_id,
    )
