# -*- coding: utf-8 -*-
"""qualifications/ S3 인벤토리 — bid_id별 병합 대상 파일 목록 조회 + 문서 로딩(#66).

bid_table.raw_s3_key(단일 컬럼, 다건 첨부 표현 불가)나 bid_attachments.s3_key를
common/paths.py의 raw_key_to_extracted_key/extracted_key_to_qualifications_key로
역산하는 방식은 채택하지 않았다 — 그 헬퍼들의 정규식은 daily(hour= 필수)만
파싱하고 backfill(hour= 없음)은 의도적으로 거부하는데(paths.py 주석 확인,
realtime 파이프라인 전용 설계), 이 병합 배치는 backfill/daily 두 stage를 전부
다뤄야 한다. 대신 qualifications/ 전체를 1회 리스팅해 bid_id별 인벤토리를
직접 만든다(2단계 설계 결정).
"""
import json
import logging
import re
from dataclasses import dataclass

from common import paths, s3

logger = logging.getLogger(__name__)

QUALIFICATIONS_PREFIX = "qualifications/"

# stage(backfill/daily) 뒤 biz_div/year/month/day는 공통이고 hour=만 daily에 있어
# 선택 그룹으로 둔다. paths.py의 _EXTRACTED_KEY_RE 대응 버전이지만 hour= 필수를
# 완화한 것 — 두 정규식을 계속 동기화해야 하는 부담이 있으니 qualifications/
# 파티션 구조(paths.py docstring)가 바뀌면 이쪽도 같이 고칠 것.
_KEY_RE = re.compile(
    r"^qualifications/(?P<stage>[^/]+)"
    r"/biz_div=(?P<biz_div>[^/]+)"
    r"/year=(?P<year>[^/]+)/month=(?P<month>[^/]+)/day=(?P<day>[^/]+)"
    r"(?:/hour=(?P<hour>[^/]+))?"
    r"/(?P<bid_id>[^/]+)/(?P<file_id>[^/]+)\.json$"
)


@dataclass(frozen=True)
class QualificationFileRef:
    s3_key: str
    document_id: str
    last_modified: str  # ISO8601 문자열(merge.logic의 extraction_meta.extracted_at 근사에 씀)


def build_inventory(bucket: str) -> dict[str, list[QualificationFileRef]]:
    """qualifications/ 전체를 1회 리스팅해 bid_id -> [QualificationFileRef]를 만든다.

    현재 규모(2026-07-12 기준 27,428건, list_objects_v2 페이지네이션 ~28회)에서는
    매 배치 실행마다 전체 리스팅해도 비용이 무시할 수준이라 캐싱/증분화하지
    않는다. 데이터가 커지면 bid_category(=biz_div로 추정, 미검증)로 prefix를
    좁히는 최적화를 재검토할 것(2단계 설계 메모 — 지금은 불필요해 보류).
    """
    inventory: dict[str, list[QualificationFileRef]] = {}
    skipped = 0
    for obj in s3.list_objects(bucket, QUALIFICATIONS_PREFIX):
        parsed = _parse_key(obj["key"])
        if parsed is None:
            skipped += 1
            continue
        bid_id, document_id = parsed
        inventory.setdefault(bid_id, []).append(
            QualificationFileRef(
                s3_key=obj["key"],
                document_id=document_id,
                last_modified=obj["last_modified"].isoformat(),
            )
        )
    if skipped:
        logger.warning("qualifications/ 인벤토리 파싱 실패로 건너뛴 키 %d개", skipped)
    return inventory


def _parse_key(key: str) -> tuple[str, str] | None:
    m = _KEY_RE.match(key)
    if m is None:
        logger.warning("qualifications key 형식이 아님 — 건너뜀: key=%s", key)
        return None
    bid_id = m.group("bid_id")
    file_id = m.group("file_id")
    try:
        document_id = paths.document_id_from_file_id(bid_id, file_id)
    except ValueError:
        logger.warning("file_id가 bid_id 접두어로 시작하지 않음 — 건너뜀: key=%s", key)
        return None
    return bid_id, document_id


def fetch_documents(bucket: str, refs: list[QualificationFileRef]) -> list[dict]:
    """refs가 가리키는 qualifications JSON들을 읽어 merge.logic이 바로 쓸 수 있는
    dict 리스트로 변환한다(각 문서에 s3_last_modified를 주입 — merge.logic의
    extraction_meta.extracted_at 근사가 이 값을 씀)."""
    documents = []
    for ref in refs:
        body = s3.get_object(bucket, ref.s3_key)
        doc = json.loads(body)
        doc["s3_last_modified"] = ref.last_modified
        documents.append(doc)
    return documents
