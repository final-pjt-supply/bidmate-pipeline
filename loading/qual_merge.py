# -*- coding: utf-8 -*-
"""자격요건 docNN JSON들을 bid_id 단위로 병합하는 순수 로직(I/O 없음).

필드 타입별 채택 규칙(스펙 §4):
- 텍스트/리스트/객체: 직렬화 길이가 가장 긴(가장 자세한) 값
- enum/bool/number: 후보가 다르면 evidence 있는 doc 우선 -> doc 번호 순, 충돌 기록
- 연관 필드(지역/배점/낙찰하한)는 그룹 단위로 한 doc에서 통째로 채택(merge_group).
"""
import json
import re

# 18개 도메인 필드(schema.py의 REQUIRED_FIELDS에서 evidence/not_found 제외)
DOMAIN_FIELDS = (
    "company_size_limit", "direct_production_req", "credit_rating_req",
    "required_licenses", "item_codes", "region_limit_type", "region_limit_names",
    "region_basis", "performance_reqs", "capacity_reqs", "personnel_reqs",
    "required_certs", "award_cutline_type", "award_cutline_value",
    "tech_weight", "price_weight", "joint_venture_allowed", "subcontract_allowed",
)

# 세트로 묶어 병합하는 연관 필드(Task 2에서 사용)
REGION_GROUP = ("region_limit_type", "region_limit_names", "region_basis")
WEIGHT_GROUP = ("tech_weight", "price_weight")
CUTLINE_GROUP = ("award_cutline_type", "award_cutline_value")
FIELD_GROUPS = (REGION_GROUP, WEIGHT_GROUP, CUTLINE_GROUP)
_GROUPED = frozenset(f for g in FIELD_GROUPS for f in g)

# 그룹에 속하지 않는 독립 필드
_INDEPENDENT = tuple(f for f in DOMAIN_FIELDS if f not in _GROUPED)
# 독립 필드 중 "가장 자세한 값" 규칙(리스트/객체)
INDEPENDENT_DETAIL_FIELDS = frozenset({
    "required_licenses", "item_codes", "performance_reqs",
    "capacity_reqs", "personnel_reqs", "required_certs",
})
# 독립 필드 중 스칼라(enum/bool) 규칙
INDEPENDENT_SCALAR_FIELDS = frozenset(f for f in _INDEPENDENT if f not in INDEPENDENT_DETAIL_FIELDS)


def _serialized_len(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _doc_num(doc: dict) -> int:
    """'doc03' -> 3. 숫자만 추출."""
    digits = re.sub(r"\D", "", doc.get("document_id", "") or "")
    return int(digits) if digits else 0


def _has_evidence(doc: dict, field: str) -> bool:
    return any(
        isinstance(e, dict) and e.get("field") == field
        for e in (doc.get("evidence") or [])
    )


def _is_missing(value) -> bool:
    """null 또는 빈 리스트는 '값 없음'으로 취급."""
    return value is None or (isinstance(value, list) and len(value) == 0)


def merge_independent_field(field: str, docs: list[dict]) -> tuple[object, dict | None]:
    """독립 필드 하나를 병합한다. (채택값, 충돌기록 or None) 반환."""
    candidates = [d for d in docs if not _is_missing(d.get(field))]
    if not candidates:
        return None, None

    if field in INDEPENDENT_DETAIL_FIELDS:
        # 가장 자세한 값: 직렬화 길이 최대, 동률이면 낮은 doc 번호
        best = max(candidates, key=lambda d: (_serialized_len(d.get(field)), -_doc_num(d)))
        return best.get(field), None

    # 스칼라: 후보값이 모두 같으면 그대로
    distinct = {json.dumps(d.get(field), ensure_ascii=False) for d in candidates}
    if len(distinct) == 1:
        return candidates[0].get(field), None

    # 충돌: evidence 있는 doc 우선 -> 낮은 doc 번호
    best = min(candidates, key=lambda d: (0 if _has_evidence(d, field) else 1, _doc_num(d)))
    conflict = {
        "field": field,
        "chosen": best.get(field),
        "candidates": [{"doc": d.get("document_id"), "value": d.get(field)} for d in candidates],
        "reason": "evidence_priority_then_doc_order",
    }
    return best.get(field), conflict


def _group_nonnull_count(doc: dict, fields: tuple[str, ...]) -> int:
    return sum(0 if _is_missing(doc.get(f)) else 1 for f in fields)


def _group_field_conflict(fields: tuple[str, ...], candidates: list[dict]) -> bool:
    """그룹 내 '같은 필드'에 서로 다른 non-null 값을 준 후보가 있으면 True.

    부분집합-일치(어떤 doc이 값을 덜 채웠을 뿐 겹치는 값은 동일)는 충돌이 아니다 —
    문서별 추출 완성도 차이는 기본 상황이므로 충돌 지표에서 배제한다.
    """
    for f in fields:
        distinct = {
            json.dumps(d.get(f), ensure_ascii=False)
            for d in candidates if not _is_missing(d.get(f))
        }
        if len(distinct) > 1:
            return True
    return False


def merge_group(fields: tuple[str, ...], docs: list[dict]) -> tuple[dict, dict | None]:
    """연관 필드 그룹을 한 doc에서 통째로 채택한다. ({필드:값}, 충돌 or None).

    충돌은 '겹치는 non-null 필드 값이 상이할 때만' 기록(완성도 차이는 충돌 아님).
    """
    candidates = [d for d in docs if _group_nonnull_count(d, fields) > 0]
    if not candidates:
        return {f: None for f in fields}, None

    # 가장 충실한 doc: non-null 필드 수 최대 -> 그룹 직렬화 길이 최대 -> 낮은 doc 번호
    def _key(d):
        group_values = {f: d.get(f) for f in fields}
        return (_group_nonnull_count(d, fields), _serialized_len(group_values), -_doc_num(d))

    best = max(candidates, key=_key)
    values = {f: best.get(f) for f in fields}

    conflict = None
    if _group_field_conflict(fields, candidates):
        conflict = {
            "group": list(fields),
            "chosen_doc": best.get("document_id"),
            "chosen": values,
            "candidates": [
                {"doc": d.get("document_id"), "values": {f: d.get(f) for f in fields}}
                for d in candidates
            ],
            "reason": "conflicting_field_value_in_group",
        }
    return values, conflict


def merge_docs(docs: list[dict]) -> dict:
    """한 bid_id의 docNN JSON 리스트를 병합해 values/evidence/conflicts/meta를 만든다."""
    docs = sorted(docs, key=_doc_num)
    values: dict = {}
    conflicts: list = []

    for field in _INDEPENDENT:
        value, conflict = merge_independent_field(field, docs)
        values[field] = value
        if conflict is not None:
            conflicts.append(conflict)

    for group in FIELD_GROUPS:
        group_values, conflict = merge_group(group, docs)
        values.update(group_values)
        if conflict is not None:
            conflicts.append(conflict)

    evidence = [e for d in docs for e in (d.get("evidence") or [])]
    not_found_union = sorted({nf for d in docs for nf in (d.get("not_found") or [])})
    meta = {
        "merged_doc_count": len(docs),
        "source_s3_keys": [d.get("_s3_key") for d in docs],
        "source_document_ids": [d.get("document_id") for d in docs],
        "dropped_evidence_sum": sum((d.get("_meta") or {}).get("dropped_evidence", 0) for d in docs),
        "demoted_values_sum": sum((d.get("_meta") or {}).get("demoted_values", 0) for d in docs),
        "not_found_union": not_found_union,
    }
    return {"values": values, "evidence": evidence, "conflicts": conflicts, "meta": meta}
