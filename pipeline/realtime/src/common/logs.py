# -*- coding: utf-8 -*-
"""handler 처리 단계 로그 헬퍼 — 형식/이벤트 어휘를 여기 한 곳에서 통일한다.

handler의 보강 로그(시작/완료/스킵)는 반드시 이 모듈을 통해서만 찍는다.
자유 텍스트로 각자 찍으면(예전 조사에서 실제로 handler마다 "PDF 추출 실패: ..."
/ "HWP 추출 실패: ..."처럼 메시지 형식이 갈라져 있었음) CloudWatch Logs
Insights에서 문자열 매칭 이상의 조회가 안 된다.

## 형식
"이벤트토큰 key=value key=value ..." — 이벤트토큰은 항상 첫 단어, 대문자
스네이크케이스(예: PROCESS_START). 그 뒤로 공백으로 구분한 key=value 쌍.

## 이벤트 어휘
- PROCESS_START : 문서 하나의 처리를 시작함(S3에서 입력을 읽기 직전)
- PROCESS_DONE  : 문서 하나의 처리를 완료하고 다음 단계로 넘길 산출물까지 씀
- PROCESS_SKIP  : 실제 파일 이벤트가 아니라서(S3 TestEvent 등) 처리 대상이 아님

실패는 이 헬퍼를 쓰지 않는다 — handler의 기존 logger.exception이 이미
bid_id/document_id/key를 포함해서 찍고 있고(조사로 확인됨) Traceback까지
필요하므로 그대로 둔다.

## 필수 키
PROCESS_START/PROCESS_DONE은 bid_id, document_id를 필수 인자로 강제한다.
이 두 값이 없으면 CloudWatch Logs Insights에서 특정 문서를 추적할 수 없기
때문이다(예: filter @message like "bid_id=R26BK01623468_000").

## 레벨
전부 INFO. 호출하는 handler 쪽에서 로거 레벨을 INFO로 명시 설정해야 실제로
CloudWatch에 남는다 — 조사에서 코드상 유일했던 INFO 로그(TestEvent skip)가
보존기간 전체에서 0건이었는데, 이는 프로젝트 어디에도 로거 레벨 설정이 없어
루트 로거 기본 레벨(WARNING)에 INFO가 걸러진 것으로 추정된다.
"""
import logging

logger = logging.getLogger(__name__)


def log_process_start(bid_id: str, document_id: str, key: str) -> None:
    logger.info("PROCESS_START bid_id=%s document_id=%s key=%s", bid_id, document_id, key)


def log_process_done(bid_id: str, document_id: str, duration_ms: int, output_key: str) -> None:
    logger.info(
        "PROCESS_DONE bid_id=%s document_id=%s duration_ms=%d output_key=%s",
        bid_id, document_id, duration_ms, output_key,
    )


def log_process_skip(bid_id: str, document_id: str, reason: str) -> None:
    logger.info("PROCESS_SKIP bid_id=%s document_id=%s reason=%s", bid_id, document_id, reason)
