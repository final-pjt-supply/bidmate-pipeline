"""OpenDataLoader 기반 추출기 회귀 테스트 (이슈 #21).

parsing.text_extractor.test_table_extraction과 동일한 샘플·기대값으로,
opendataloader_extractor가 우리 인터페이스를 통해서도 5개 숫자를 보존하는지 확인한다.
"""
from pathlib import Path

from parsing.opendataloader_extractor import extract_text

SAMPLE_PDF = (
    Path(__file__).parent.parent / "data" / "sample"
    / "(공고서변환) 폴리스짐 체육관 바닥개선 공사.pdf"
)

EXPECTED_VALUES = [
    "3,336,700",
    "20,516,747",
    "4,408,713",
    "4,626,676",
    "2,134,745",
]


def test_insurance_table_values_preserved():
    result = extract_text(str(SAMPLE_PDF))
    full_text = "\n".join(result["pages"].values())

    missing = [v for v in EXPECTED_VALUES if v not in full_text]
    assert not missing, f"누락된 값: {missing}"


if __name__ == "__main__":
    test_insurance_table_values_preserved()
    print(f"[테스트 통과] 보험료 표 {len(EXPECTED_VALUES)}개 값 모두 보존됨 (OpenDataLoader)")
