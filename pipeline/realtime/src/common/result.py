# -*- coding: utf-8 -*-
"""추출 결과를 최종 출력 스키마로 조립.

포맷별 pages 정리(실제 페이지 vs 1000자 인위 분할)는 각 extractors/*.py가 이미
끝내둔 상태로 넘어오므로, 여기서는 포맷을 몰라도 되게 그대로 감싸기만 한다.
"""


def build_result(bid_id: str, document_id: str, pages: list[dict]) -> dict:
    return {"bid_id": bid_id, "document_id": document_id, "pages": pages}
