# -*- coding: utf-8 -*-
"""qual_merge 단위테스트 — 순수 병합 로직(I/O 없음)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import qual_merge  # noqa: E402


def _doc(num, **fields):
    """테스트용 doc dict. evidence/not_found/_meta 기본값 포함."""
    base = {f: None for f in qual_merge.DOMAIN_FIELDS}
    base.update({"document_id": f"doc{num:02d}", "evidence": [], "not_found": [], "_meta": {}})
    base.update(fields)
    return base


def test_detail_field_picks_longest_serialized():
    # required_licenses(list): 원소 많은(더 자세한) doc 채택
    a = _doc(1, required_licenses=[{"or_group": 1, "name_raw": "전기공사업", "code": None}])
    b = _doc(2, required_licenses=[
        {"or_group": 1, "name_raw": "전기공사업 등록", "code": None},
        {"or_group": 2, "name_raw": "정보통신공사업", "code": None},
    ])
    value, conflict = qual_merge.merge_independent_field("required_licenses", [a, b])
    assert value == b["required_licenses"]
    assert conflict is None  # detail 필드는 충돌 기록하지 않음


def test_scalar_all_same_no_conflict():
    a = _doc(1, joint_venture_allowed=True)
    b = _doc(2, joint_venture_allowed=True)
    value, conflict = qual_merge.merge_independent_field("joint_venture_allowed", [a, b])
    assert value is True
    assert conflict is None


def test_scalar_conflict_prefers_evidence_then_records():
    # doc1: True(근거 없음), doc2: False(근거 있음) -> evidence 있는 doc2 채택 + 충돌 기록
    a = _doc(1, joint_venture_allowed=True)
    b = _doc(2, joint_venture_allowed=False,
             evidence=[{"field": "joint_venture_allowed", "page": 5, "snippet": "공동계약 불가"}])
    value, conflict = qual_merge.merge_independent_field("joint_venture_allowed", [a, b])
    assert value is False
    assert conflict["field"] == "joint_venture_allowed"
    assert conflict["chosen"] is False
    assert {c["doc"] for c in conflict["candidates"]} == {"doc01", "doc02"}


def test_scalar_conflict_no_evidence_falls_back_to_doc_order():
    a = _doc(1, company_size_limit="no_large")
    b = _doc(2, company_size_limit="sme_only")
    value, conflict = qual_merge.merge_independent_field("company_size_limit", [a, b])
    assert value == "no_large"  # 근거 동률 -> 낮은 doc 번호
    assert conflict is not None


def test_all_missing_returns_none():
    a = _doc(1)
    b = _doc(2)
    value, conflict = qual_merge.merge_independent_field("credit_rating_req", [a, b])
    assert value is None
    assert conflict is None


def test_bool_false_is_not_missing():
    a = _doc(1, subcontract_allowed=False)
    value, conflict = qual_merge.merge_independent_field("subcontract_allowed", [a])
    assert value is False
    assert conflict is None


def test_group_subset_consistent_no_conflict():
    # doc1: type만, doc2: type+names+basis. 겹치는 type 값이 일치 ->
    # 완성도 차이일 뿐 충돌 아님. 가장 충실한 doc2에서 세트로 채택.
    a = _doc(1, region_limit_type="hq_location")
    b = _doc(2, region_limit_type="hq_location",
             region_limit_names=["경기도"], region_basis="입찰공고일 전일부터")
    values, conflict = qual_merge.merge_group(qual_merge.REGION_GROUP, [a, b])
    assert values == {
        "region_limit_type": "hq_location",
        "region_limit_names": ["경기도"],
        "region_basis": "입찰공고일 전일부터",
    }
    assert conflict is None  # 부분집합-일치는 충돌 아님


def test_group_conflicting_overlap_records_conflict():
    # 두 doc이 region_limit_type에 서로 다른 non-null 값 -> 진짜 충돌
    a = _doc(1, region_limit_type="hq_location", region_limit_names=["서울"])
    b = _doc(2, region_limit_type="none")
    values, conflict = qual_merge.merge_group(qual_merge.REGION_GROUP, [a, b])
    assert values["region_limit_type"] == "hq_location"  # non-null 2개인 doc1 채택
    assert values["region_limit_names"] == ["서울"]
    assert conflict is not None
    assert conflict["group"] == list(qual_merge.REGION_GROUP)
    assert conflict["chosen_doc"] == "doc01"


def test_group_no_conflict_when_single_source():
    a = _doc(1, tech_weight=80, price_weight=20)
    b = _doc(2)  # 배점 그룹 전부 null
    values, conflict = qual_merge.merge_group(qual_merge.WEIGHT_GROUP, [a, b])
    assert values == {"tech_weight": 80, "price_weight": 20}
    assert conflict is None


def test_group_all_missing_returns_nulls():
    a = _doc(1)
    b = _doc(2)
    values, conflict = qual_merge.merge_group(qual_merge.CUTLINE_GROUP, [a, b])
    assert values == {"award_cutline_type": None, "award_cutline_value": None}
    assert conflict is None


def test_merge_docs_full_shape():
    a = _doc(1, company_size_limit="no_large",
             evidence=[{"field": "company_size_limit", "page": 1, "snippet": "대기업 불가"}],
             not_found=["tech_weight"], _meta={"dropped_evidence": 1, "demoted_values": 0})
    a["_s3_key"] = "qualifications/backfill/biz_div=x/.../BID_A/BID_A_doc01.json"
    b = _doc(2, region_limit_type="hq_location", region_limit_names=["서울"],
             evidence=[{"field": "region_limit_type", "page": 2, "snippet": "서울 소재"}],
             not_found=["price_weight"], _meta={"dropped_evidence": 0, "demoted_values": 2})
    b["_s3_key"] = "qualifications/backfill/biz_div=x/.../BID_A/BID_A_doc02.json"
    out = qual_merge.merge_docs([a, b])

    # values: 18개 키 전부 존재
    assert set(out["values"]) == set(qual_merge.DOMAIN_FIELDS)
    assert out["values"]["company_size_limit"] == "no_large"
    assert out["values"]["region_limit_type"] == "hq_location"
    assert out["values"]["region_limit_names"] == ["서울"]
    # evidence 합침
    assert len(out["evidence"]) == 2
    # meta
    assert out["meta"]["merged_doc_count"] == 2
    assert out["meta"]["dropped_evidence_sum"] == 1
    assert out["meta"]["demoted_values_sum"] == 2
    assert set(out["meta"]["not_found_union"]) == {"tech_weight", "price_weight"}
    assert out["meta"]["source_document_ids"] == ["doc01", "doc02"]
    assert out["meta"]["source_s3_keys"] == [a["_s3_key"], b["_s3_key"]]


def test_merge_docs_all_null_docs():
    out = qual_merge.merge_docs([_doc(1), _doc(2)])
    assert all(v is None for v in out["values"].values())
    assert out["conflicts"] == []
    assert out["evidence"] == []
