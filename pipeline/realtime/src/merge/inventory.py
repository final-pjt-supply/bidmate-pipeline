# -*- coding: utf-8 -*-
"""qualifications/daily/ S3 인벤토리 — bid_id별 병합 대상 파일 목록 조회 + 문서 로딩(#66).

bid_table.raw_s3_key(단일 컬럼, 다건 첨부 표현 불가)나 bid_attachments.s3_key를
common/paths.py의 raw_key_to_extracted_key/extracted_key_to_qualifications_key로
역산하는 방식은 채택하지 않았다 — 그 헬퍼들의 정규식은 daily(hour= 필수)만
파싱하고 backfill(hour= 없음)은 의도적으로 거부한다(paths.py 주석 확인,
realtime 파이프라인 전용 설계). 이 병합 배치도 #80부터 daily만 다룬다(아래
QUALIFICATIONS_PREFIX 참고) — 대신 paths.py의 그 헬퍼들을 그대로 쓰지 않고
qualifications/daily/ 전체를 1회 리스팅해 bid_id별 인벤토리를 직접 만드는
이유는, 이 배치가 DB 대상(bid_id)에서 출발해 "그 bid_id의 daily 파일이
있는가"를 거꾸로 찾아야 하기 때문이다(파일 하나에서 시작하는 추출 파이프라인과
반대 방향).
"""
import json
import logging
import re
from dataclasses import dataclass

from common import paths, s3

logger = logging.getLogger(__name__)

# 백필(qualifications/backfill/) 산출물은 조원 파이프라인 소관 — 병합 대상에서
# 영구 제외한다(#80, 팀 분담 원칙). 이 배치가 daily 대상 bid_id를 우연히
# backfill 쪽 결과와 병합해버리는 사고(#80 발견, 2026-07-13 실전 창)를 막는다.
QUALIFICATIONS_PREFIX = "qualifications/daily/"

# stage(daily 고정, 위 prefix 제한으로) 뒤 biz_div/year/month/day는 공통이고
# hour=만 daily에 있어 선택 그룹으로 둔다. paths.py의 _EXTRACTED_KEY_RE 대응
# 버전이지만 hour= 필수를 완화한 것 — 두 정규식을 계속 동기화해야 하는 부담이
# 있으니 qualifications/ 파티션 구조(paths.py docstring)가 바뀌면 이쪽도 같이
# 고칠 것. QUALIFICATIONS_PREFIX가 daily/로 고정된 뒤에도 stage 그룹 자체는
# 그대로 둔다 — prefix 필터링과 별개로 정규식이 스스로도 파티션 구조를
# 검증하게 하기 위함(불일치 시 _parse_key가 None을 반환해 걸러짐).
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
    """qualifications/daily/ 전체를 1회 리스팅해 bid_id -> [QualificationFileRef]를 만든다
    (#80부터 daily만 — backfill/은 QUALIFICATIONS_PREFIX 자체가 안 보므로 결과에
    안 실림).

    daily만으로 좁혀도(2026-07-12 기준 backfill 포함 전체가 27,428건이었던 것
    대비) 매 배치 실행마다 전체 리스팅하는 비용은 여전히 무시할 수준이라
    캐싱/증분화하지 않는다. 데이터가 커지면 bid_category(=biz_div로 추정,
    미검증)로 prefix를 좁히는 최적화를 재검토할 것(2단계 설계 메모 — 지금은
    불필요해 보류).
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
