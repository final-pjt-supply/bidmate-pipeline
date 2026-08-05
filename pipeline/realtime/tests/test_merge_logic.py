# -*- coding: utf-8 -*-
"""merge/logic.py 단위테스트 — DB/S3 없이 순수 함수만 검증."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from merge import logic  # noqa: E402


def _doc(document_id: str, **fields) -> dict:
    base = {
        "bid_id": "R25BK00000000_000",
        "document_id": document_id,
        "evidence": [],
        "not_found": [],
        "_meta": {"dropped_evidence": 0, "demoted_values": 0},
    }
    base.update(fields)
    return base


def test_single_document_passthrough():
    doc = _doc("doc01", credit_rating_req=True, region_limit_type="hq_location")
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc])
    assert result["credit_rating_req"] is True
    assert result["region_limit_type"] == "hq_location"
    assert result["merge_conflicts"] is None


def test_scalar_non_null_wins_over_null():
    doc1 = _doc("doc01", credit_rating_req=None)
    doc2 = _doc("doc02", credit_rating_req=True)
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["credit_rating_req"] is True
    assert result["merge_conflicts"] is None


def test_scalar_conflict_boolean_true_vs_false_is_recorded():
    doc1 = _doc("doc01", joint_venture_allowed=True)
    doc2 = _doc("doc02", joint_venture_allowed=False)
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    # doc_id 오름차순(doc01 우선)으로 결정적 채택
    assert result["joint_venture_allowed"] is True
    assert result["merge_conflicts"]["joint_venture_allowed"] == [
        {"document_id": "doc01", "value": True},
        {"document_id": "doc02", "value": False},
    ]


def test_scalar_conflict_string_values():
    doc1 = _doc("doc01", company_size_limit="sme_only")
    doc2 = _doc("doc02", company_size_limit="none")
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["company_size_limit"] == "sme_only"
    assert "company_size_limit" in result["merge_conflicts"]


def test_region_limit_names_union_dedupe():
    doc1 = _doc("doc01", region_limit_names=["경기도", "서울"])
    doc2 = _doc("doc02", region_limit_names=["서울", "인천"])
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["region_limit_names"] == ["경기도", "서울", "인천"]


def test_item_codes_dedupe_exact_and_union_distinct():
    doc1 = _doc("doc01", item_codes=[{"type": "CPC", "code": "51312"}])
    doc2 = _doc("doc02", item_codes=[{"type": "CPC", "code": "51312"}, {"type": "CPC", "code": "99999"}])
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert sorted(result["item_codes"], key=lambda i: i["code"]) == [
        {"type": "CPC", "code": "51312"},
        {"type": "CPC", "code": "99999"},
    ]
    assert result["merge_conflicts"] is None


def test_capacity_reqs_same_key_different_value_is_conflict():
    doc1 = _doc("doc01", capacity_reqs=[{"name": "장비A", "value": 2, "unit": "대"}])
    doc2 = _doc("doc02", capacity_reqs=[{"name": "장비A", "value": 3, "unit": "대"}])
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["capacity_reqs"] == [{"name": "장비A", "value": 2, "unit": "대"}]
    assert result["merge_conflicts"]["capacity_reqs"][0]["key"] == {"name": "장비A"}


def test_required_certs_is_string_list_union_dedupe():
    doc1 = _doc("doc01", required_certs=["중소기업·소상공인확인서", "KC 인증"])
    doc2 = _doc("doc02", required_certs=["KC 인증", "직접생산확인증명서"])
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["required_certs"] == ["중소기업·소상공인확인서", "KC 인증", "직접생산확인증명서"]
    assert result["merge_conflicts"] is None


def test_object_list_field_non_dict_item_kept_and_warns(caplog):
    doc1 = _doc("doc01", item_codes=["업종코드 1468"])
    doc2 = _doc("doc02", item_codes=[{"type": "CPC", "code": "51312"}])
    with caplog.at_level("WARNING"):
        result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert "업종코드 1468" in result["item_codes"]
    assert {"type": "CPC", "code": "51312"} in result["item_codes"]
    assert any("dict가 아닌 항목" in r.message for r in caplog.records)


def test_required_licenses_single_document_untouched():
    doc = _doc("doc01", required_licenses=[{"or_group": 1, "name_raw": "토목공사업", "code": None}])
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc])
    assert result["required_licenses"] == [{"or_group": 1, "name_raw": "토목공사업", "code": None}]


def test_required_licenses_multi_document_offsets_or_group_and_warns(caplog):
    doc1 = _doc("doc01", required_licenses=[{"or_group": 1, "name_raw": "토목공사업", "code": None}])
    doc2 = _doc("doc02", required_licenses=[{"or_group": 1, "name_raw": "전기공사업", "code": None}])
    with caplog.at_level("WARNING"):
        result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])

    licenses = {lic["name_raw"]: lic["or_group"] for lic in result["required_licenses"]}
    assert licenses["토목공사업"] == 1
    assert licenses["전기공사업"] == 2  # doc02는 offset(1) 적용돼 재번호됨
    assert any("or_group 재번호 가설 발동" in r.message for r in caplog.records)


def test_required_licenses_duplicate_across_documents_deduped():
    doc1 = _doc("doc01", required_licenses=[{"or_group": 1, "name_raw": "토목공사업", "code": None}])
    doc2 = _doc("doc02", required_licenses=[{"or_group": 1, "name_raw": "토목공사업", "code": None}])
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert len(result["required_licenses"]) == 1


def test_evidence_grouped_by_field_with_document_id_injected():
    doc1 = _doc("doc01", evidence=[{"field": "credit_rating_req", "page": 4, "snippet": "..."}])
    doc2 = _doc("doc02", evidence=[{"field": "credit_rating_req", "page": 1, "snippet": "..2"}])
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["extraction_evidence"]["credit_rating_req"] == [
        {"document_id": "doc01", "page": 4, "snippet": "..."},
        {"document_id": "doc02", "page": 1, "snippet": "..2"},
    ]


def test_not_found_is_intersection_across_documents():
    doc1 = _doc("doc01", not_found=["performance_reqs", "capacity_reqs"])
    doc2 = _doc("doc02", not_found=["performance_reqs"])  # capacity_reqs는 doc02가 찾음
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["extraction_meta"]["not_found"] == ["performance_reqs"]


def test_meta_preserves_original_counts_without_renaming():
    doc = _doc("doc01", _meta={"dropped_evidence": 5, "demoted_values": 6})
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc])
    assert result["extraction_meta"]["per_document"] == [
        {"document_id": "doc01", "dropped_evidence": 5, "demoted_values": 6},
    ]
    assert "demoted_fields" not in result["extraction_meta"]
    assert "model" not in result["extraction_meta"]


def test_extracted_at_approximated_from_s3_last_modified_and_flagged():
    doc1 = _doc("doc01", s3_last_modified="2026-07-09T05:32:44+00:00")
    doc2 = _doc("doc02", s3_last_modified="2026-07-09T07:00:00+00:00")
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result["extraction_meta"]["extracted_at"] == "2026-07-09T07:00:00+00:00"
    assert result["extraction_meta"]["extracted_at_source"] == "s3_last_modified"


def test_extracted_at_absent_when_no_last_modified_provided():
    doc = _doc("doc01")
    result = logic.merge_qualification_documents("R25BK00000000_000", [doc])
    assert "extracted_at" not in result["extraction_meta"]


def test_mismatched_bid_id_document_excluded_with_warning(caplog):
    doc1 = _doc("doc01")
    doc2 = _doc("doc02")
    doc2["bid_id"] = "OTHER_BID_000"
    with caplog.at_level("WARNING"):
        result = logic.merge_qualification_documents("R25BK00000000_000", [doc1, doc2])
    assert result is not None
    assert any("bid_id가 조회 대상과 다름" in r.message for r in caplog.records)


def test_no_documents_raises():
    with pytest.raises(ValueError):
        logic.merge_qualification_documents("R25BK00000000_000", [])


@pytest.mark.parametrize(
    "found,expected,has_failed,want",
    [
        (0, 1, False, "pending"),
        (1, 1, False, "merged"),
        (2, 1, False, "merged"),  # 이상 케이스(found > expected)도 merged
        (1, 2, False, "partial"),
        (1, 2, True, "failed"),
        # expected=0은 "완료"가 아니라 "아직 개수를 모름"이다. 압축 첨부는 수집
        # 단계에서 0으로 잡히고 다운로드 뒤에야 정정되는데, 그 사이에 merged로
        # 확정되면 병합 배치가 다시 뽑지 않아 되돌릴 수 없다.
        (1, 0, False, "pending"),
        (3, 0, True, "pending"),
    ],
)
def test_determine_qual_status(found, expected, has_failed, want):
    assert logic.determine_qual_status(found, expected, has_failed) == want
