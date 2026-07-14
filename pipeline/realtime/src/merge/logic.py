# -*- coding: utf-8 -*-
"""qualifications/ 문서 N개 → bid_table 자격요건 1행 병합(순수 함수, DB/S3 비의존).

RDS 병합 배치(#64 후속)의 핵심 로직. bid_id 하나에 딸린 qualifications JSON들
(문서당 1파일, doc01/doc02/...)을 받아 bid_table의 자격요건 18개 컬럼 +
merge_conflicts/extraction_evidence/extraction_meta를 조립한다.

입력 문서 스키마(2026-07-12 S3 실샘플 확인, qualifications/*.json):
    {"bid_id", "document_id", <18개 자격요건 필드>, "evidence": [{field,page,snippet}],
     "not_found": [field, ...], "_meta": {"dropped_evidence": int, "demoted_values": int},
     "s3_last_modified": <호출부가 주입, ISO8601 문자열>}
"s3_last_modified"는 소스 JSON에 없는 필드다 — 호출부(S3 인벤토리 단계)가 각
문서를 읽을 때 S3 LastModified를 여기에 채워 넣어야 extraction_meta.extracted_at
근사가 동작한다. 없으면 그 문서는 extracted_at 계산에서 빠진다.
"""
import logging

logger = logging.getLogger(__name__)

# bid_table 자격요건 18개 컬럼(db/schema/01_bid_table.sql 기준, 2026-07-12 라이브 DB
# 컬럼 존재 확인됨) — 스칼라 11개 + 리스트 7개.
SCALAR_FIELDS = (
    "company_size_limit",
    "direct_production_req",
    "credit_rating_req",
    "region_limit_type",
    "region_basis",
    "award_cutline_type",
    "award_cutline_value",
    "tech_weight",
    "price_weight",
    "joint_venture_allowed",
    "subcontract_allowed",
)

# 값 객체 리스트 필드의 "같은 실체" 판정 키. 이 키가 같은데 나머지 필드 값이
# 다르면 진짜 충돌(merge_conflicts에 기록). None이면 항목 자체를 키로 써서
# 완전동일 항목만 dedupe하고 부분 충돌은 감지하지 않는다(항목 전체가 곧 키인
# item_codes).
# LLM 스키마(extractors/llm/schema.py) 기준 object list 필드만 여기 둔다.
# required_certs는 스키마상 list[str]이라 여기 두지 않는다(2026-07-14 정정 —
# 과거 여기 있었으나 dict 리스트로 잘못 취급돼 str 항목에 AttributeError 발생).
OBJECT_LIST_FIELD_KEYS: dict[str, tuple[str, ...] | None] = {
    "item_codes": ("type", "code"),
    "performance_reqs": ("category", "basis"),
    "capacity_reqs": ("name",),
    "personnel_reqs": ("field", "grade"),
}

# required_licenses는 or_group 재번호가 필요해 별도 함수(_merge_required_licenses)로 처리.
# region_limit_names/required_certs는 문자열 리스트라 별도 함수(_merge_string_list_field)로 처리.
# 11(스칼라) + 4(OBJECT_LIST_FIELD_KEYS) + 1(required_licenses) + 1(region_limit_names) + 1(required_certs) = 18.

# bid_table 자격요건 18개 컬럼 전체 — merge/db.py가 UPDATE 문의 SET 대상 컬럼
# 목록을 여기서 그대로 가져다 쓴다(컬럼 목록을 두 곳에 따로 하드코딩하면 필드
# 추가/삭제 시 어긋날 위험이 있어 단일 소스로 둔다).
ALL_QUALIFICATION_FIELDS = (
    SCALAR_FIELDS
    + ("region_limit_names", "required_licenses", "required_certs")
    + tuple(OBJECT_LIST_FIELD_KEYS.keys())
)


def merge_qualification_documents(bid_id: str, documents: list[dict]) -> dict:
    """documents(같은 bid_id의 qualifications JSON들)를 병합해 bid_table UPDATE에
    바로 쓸 수 있는 dict를 반환한다. 자격요건 18개 컬럼 + merge_conflicts +
    extraction_evidence + extraction_meta 키를 전부 포함한다(값 없으면 None).
    """
    if not documents:
        raise ValueError(f"병합할 문서가 없음: bid_id={bid_id}")

    mismatched = [d for d in documents if d.get("bid_id") != bid_id]
    for d in mismatched:
        logger.warning(
            "문서의 bid_id가 조회 대상과 다름 — 이 문서는 병합에서 제외: expected=%s "
            "actual=%s document_id=%s",
            bid_id, d.get("bid_id"), d.get("document_id"),
        )
    documents = [d for d in documents if d.get("bid_id") == bid_id]
    if not documents:
        raise ValueError(f"bid_id가 일치하는 문서가 하나도 없음: bid_id={bid_id}")

    merged: dict = {}
    merge_conflicts: dict = {}

    for field in SCALAR_FIELDS:
        per_doc_values = [(d["document_id"], d.get(field)) for d in documents]
        value, conflict = _merge_scalar_field(field, per_doc_values)
        merged[field] = value
        merge_conflicts.update(conflict)

    per_doc_region_names = [(d["document_id"], d.get("region_limit_names")) for d in documents]
    merged["region_limit_names"] = _merge_string_list_field(per_doc_region_names) or None

    per_doc_certs = [(d["document_id"], d.get("required_certs")) for d in documents]
    merged["required_certs"] = _merge_string_list_field(per_doc_certs) or None

    for field, key_fields in OBJECT_LIST_FIELD_KEYS.items():
        per_doc_items = [(d["document_id"], d.get(field)) for d in documents]
        value, conflict = _merge_object_list_field(bid_id, field, per_doc_items, key_fields)
        merged[field] = value or None
        merge_conflicts.update(conflict)

    per_doc_licenses = [(d["document_id"], d.get("required_licenses")) for d in documents]
    merged["required_licenses"] = _merge_required_licenses(bid_id, per_doc_licenses) or None

    merged["extraction_evidence"] = _merge_evidence(documents)
    merged["extraction_meta"] = _merge_meta(documents)
    merged["merge_conflicts"] = merge_conflicts or None

    return merged


def _merge_scalar_field(field_name: str, per_doc_values: list[tuple[str, object]]) -> tuple[object, dict]:
    """non-null 값이 하나면 그대로 채택. 서로 다른 non-null 값이 2개 이상이면
    (boolean true/false 포함) 진짜 충돌 — document_id 오름차순(문서 번호가 빠른
    쪽)에서 가장 먼저 나온 값을 대표값으로 결정적으로 채택하고, 전체 후보를
    merge_conflicts에 남겨 사람이 검토하게 한다."""
    non_null = [(doc_id, v) for doc_id, v in per_doc_values if v is not None]
    if not non_null:
        return None, {}

    distinct_values = {v for _, v in non_null}
    if len(distinct_values) == 1:
        return non_null[0][1], {}

    non_null_sorted = sorted(non_null, key=lambda pair: pair[0])
    conflict_entries = [{"document_id": doc_id, "value": v} for doc_id, v in non_null_sorted]
    return non_null_sorted[0][1], {field_name: conflict_entries}


def _merge_string_list_field(per_doc_items: list[tuple[str, list | None]]) -> list:
    """region_limit_names/required_certs 전용 — 문자열 값 합집합(순서 보존, 중복 제거).
    문자열 자체가 곧 키라 값 충돌 개념이 없다(다르면 그냥 다른 항목)."""
    seen: list = []
    for _, items in per_doc_items:
        for v in (items or []):
            if v not in seen:
                seen.append(v)
    return seen


def _merge_object_list_field(
    bid_id: str,
    field_name: str,
    per_doc_items: list[tuple[str, list | None]],
    key_fields: tuple[str, ...] | None,
) -> tuple[list, dict]:
    """딕셔너리 리스트 필드의 합집합 + (key_fields가 있을 때) 값 충돌 감지.

    key_fields가 있으면: 같은 키의 항목이 문서마다 다른 값(나머지 필드)으로
    나오면 merge_conflicts에 기록하고, 대표값은 먼저 나온 문서 것을 유지.
    key_fields가 None이면: 항목 전체를 키로 써서 완전동일 항목만 하나로
    합치고, 다른 항목은 그냥 별개 항목으로 합집합에 추가한다(충돌 없음 —
    내부 스키마를 몰라 부분 키를 정할 근거가 없는 필드용, 예: item_codes).

    타입 방어: 이 필드는 LLM 스키마상 dict 리스트이지만, 스키마 위반으로
    dict가 아닌 항목(예: str)이 섞여 들어올 수 있다(2026-07-13 실전 장애 —
    required_certs가 이 경로로 잘못 분류돼 str 항목에 AttributeError 발생,
    현재는 required_certs 자체는 문자열 리스트 경로로 정정됨). 이런 항목은
    드랍하지 않고 str(item)을 키로 써서 그대로 합집합에 포함하며 WARNING을
    남긴다.
    """
    merged: dict = {}
    conflicts: list = []
    for document_id, items in per_doc_items:
        for item in (items or []):
            if not isinstance(item, dict):
                logger.warning(
                    "%s 필드에 dict가 아닌 항목 발견(스키마 위반) — 드랍하지 않고 "
                    "str(item)을 키로 처리: bid_id=%s document_id=%s 값=%r",
                    field_name, bid_id, document_id, item,
                )
                key = str(item)
            else:
                key = tuple(item.get(k) for k in key_fields) if key_fields else _stable_key(item)
            if key not in merged:
                merged[key] = (document_id, item)
            elif key_fields and isinstance(item, dict) and item != merged[key][1]:
                prev_document_id, prev_item = merged[key]
                conflicts.append({
                    "key": dict(zip(key_fields, key)),
                    "values": [
                        {"document_id": prev_document_id, "value": prev_item},
                        {"document_id": document_id, "value": item},
                    ],
                })
    merged_list = [item for _, item in merged.values()]
    return merged_list, ({field_name: conflicts} if conflicts else {})


def _stable_key(item: dict) -> tuple:
    return tuple(sorted(item.items(), key=lambda kv: kv[0]))


def _merge_required_licenses(bid_id: str, per_doc_items: list[tuple[str, list | None]]) -> list:
    """or_group은 문서 내부에서만 의미 있는 지역 번호다(같은 문서 안 "A 또는 B"
    대안 그룹을 가리킴). 두 문서의 required_licenses를 그대로 합치면 서로 다른
    문서의 대안 그룹이 우연히 같은 or_group 번호를 써서 실제로는 무관한 두
    라이선스가 "대안 관계"로 잘못 묶일 수 있다. 이를 막기 위해 두 번째 문서부터
    지금까지 나온 최댓값만큼 or_group에 offset을 더해 네임스페이스를 분리한다.

    ⚠ 가설: 2026-07-12 기준 실제로 2개 이상 문서에서 required_licenses가 동시에
    채워진 충돌 샘플을 확보하지 못해 검증되지 않은 로직이다. 발생 시 WARNING
    로그로 병합 전 or_group 값들을 남겨 나중에 실데이터로 가설을 검증한다.
    """
    contributing = [(doc_id, items) for doc_id, items in per_doc_items if items]
    if len(contributing) <= 1:
        return list(contributing[0][1]) if contributing else []

    logger.warning(
        "required_licenses가 2개 이상 문서에서 동시에 채워짐 — or_group 재번호 "
        "가설 발동(미검증 로직, 결과를 반드시 검토할 것): bid_id=%s 문서별_or_group=%s",
        bid_id,
        {doc_id: [item.get("or_group") for item in items] for doc_id, items in contributing},
    )

    seen: dict = {}
    offset = 0
    for doc_id, items in contributing:
        shifted = []
        for item in items:
            new_item = dict(item)
            if item.get("or_group") is not None:
                new_item["or_group"] = item["or_group"] + offset
            shifted.append(new_item)
        for item in shifted:
            key = (item.get("name_raw"), item.get("code"))
            if key not in seen:
                seen[key] = item
        group_values = [i["or_group"] for i in shifted if i.get("or_group") is not None]
        if group_values:
            offset = max(offset, max(group_values))
    return list(seen.values())


def _merge_evidence(documents: list[dict]) -> dict | None:
    """문서 레벨 evidence 리스트([{field,page,snippet}])를 필드별로 그룹핑하며
    각 항목에 document_id를 주입한다 → {field: [{document_id,page,snippet}, ...]}.
    같은 필드에 여러 문서가 evidence를 가지면 dedupe 없이 전부 이어붙인다 —
    merge_conflicts 조사 시 어느 문서에서 어떤 근거로 그 값이 나왔는지 전부 필요.
    """
    grouped: dict[str, list[dict]] = {}
    for doc in documents:
        for entry in (doc.get("evidence") or []):
            grouped.setdefault(entry["field"], []).append({
                "document_id": doc["document_id"],
                "page": entry.get("page"),
                "snippet": entry.get("snippet"),
            })
    return grouped or None


def _merge_meta(documents: list[dict]) -> dict:
    """extraction_meta 조립.

    not_found: 모든 문서의 not_found 교집합(한 문서라도 찾았으면 최종 not_found
    에서 제외).

    demoted_values/dropped_evidence: 소스 JSON의 _meta는 정수 카운트뿐이라
    bid_table 컬럼 주석이 기대하는 "필드명 리스트"(demoted_fields)를 만들 수
    없다 — 카운트만으로는 어떤 필드가 강등됐는지 알 수 없기 때문이다. 이름을
    억지로 demoted_fields로 바꾸지 않고 원본 키(demoted_values)를 문서별로
    그대로 보존한다. 컬럼 주석과의 이 불일치는 알려진 갭으로 남긴다.

    model/extracted_at: 모든 소스 JSON 샘플에 없어 조작하지 않는다. extracted_at
    은 병합 대상 문서들의 S3 LastModified 중 최댓값으로 근사하고,
    "extracted_at_source": "s3_last_modified" 표식을 반드시 남겨 이 값을 실제
    LLM 추출 시각으로 오해하지 않게 한다. model은 아예 생략한다(대체할 근거 없음).
    """
    not_found_sets = [set(doc.get("not_found") or []) for doc in documents]
    final_not_found = sorted(set.intersection(*not_found_sets)) if not_found_sets else []

    per_document = []
    last_modified_values = []
    for doc in documents:
        meta = doc.get("_meta") or {}
        per_document.append({
            "document_id": doc["document_id"],
            "dropped_evidence": meta.get("dropped_evidence"),
            "demoted_values": meta.get("demoted_values"),
        })
        if doc.get("s3_last_modified"):
            last_modified_values.append(doc["s3_last_modified"])

    result: dict = {
        "not_found": final_not_found,
        "per_document": per_document,
    }
    if last_modified_values:
        result["extracted_at"] = max(last_modified_values)
        result["extracted_at_source"] = "s3_last_modified"
    return result


def determine_qual_status(
    found_file_count: int,
    expected_file_count: int,
    has_failed_attachment: bool,
) -> str:
    """qual_status 전이 판정. bid_table.qual_status는 DB enum이 아니라 VARCHAR
    + 애플리케이션 규약(pending/merged/partial/failed, 2026-07-12 확인)이다.

    - found <= 0: pending 유지(호출부는 이 경우 UPDATE 자체를 하지 않아야 함 —
      아직 아무 파일도 안 왔다는 뜻이라 갱신할 값이 없음).
    - found >= expected: merged. found > expected(이상 케이스, expected_file_count
      가 stale할 수 있음)도 merged로 처리하되 호출부가 이 case를 별도로 로그해야
      한다(이 함수는 개수 비교만 하고 원인 로깅은 책임지지 않음).
    - 0 < found < expected 이고 has_failed_attachment=True: failed(영구 결손).
      ⚠ has_failed_attachment는 bid_attachments.status='failed' 존재 여부를
      호출부가 읽어서 넘겨야 하는데, 2026-07-12 기준 이 파이프라인의 어떤
      handler도 status를 'failed'로 갱신하지 않는다(1~2단계 조사로 확인) —
      즉 이 분기는 코드상 존재하지만 지금은 항상 False가 들어와 미발동 상태다.
      나중에 실패 갱신 로직이 붙으면 자동으로 살아난다.
    - 나머지(0 < found < expected, 실패 없음): partial — 계속 대기.
    """
    if found_file_count <= 0:
        return "pending"
    if found_file_count >= expected_file_count:
        return "merged"
    if has_failed_attachment:
        return "failed"
    return "partial"
