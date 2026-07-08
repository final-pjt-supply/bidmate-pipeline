# -*- coding: utf-8 -*-
"""LLM 추출 오염 회귀 체크 — 4유형(물품/용역/공사/외자) 실제 샘플로 extractor.extract()를
돌려 few-shot이 "창작한 값 조합"이 실제 대상 문서의 출력에 새는지 확인한다.

마커는 반드시 few-shot 안에서만 성립하는 구체적인 값 조합(코드 번호·퍼센트+문맥 전체
등을 통째로)으로만 잡는다. 실존 가능한 지명·업종명·법령명을 단독으로 마커에 넣으면
오탐이 난다 — 실제로 "전북특별자치도"를 단독 마커로 썼다가, 반월지구 용역 샘플이
진짜로 전북특별자치도 소재라서 정상 추출인데 오염으로 오판한 사례가 있었다(2026-07-07).
그래서 아래 마커는 전부 few-shot에만 존재하는 구체적 조합 전체를 쓴다.

실행(리포 루트에서):
    cd pipeline/realtime/scripts && python check_llm_contamination.py
(.env의 NVIDIA_API_KEY/LLM_BASE_URL/LLM_MODEL이 필요하고, 실제 API 호출이 발생한다 — 과금 대상)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

_ENV_PATH = Path(__file__).parent.parent.parent.parent / ".env"
if _ENV_PATH.exists():
    import os

    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        if _k in ("NVIDIA_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
            os.environ.setdefault(_k, _v)

from extractors.llm import extractor  # noqa: E402

SAMPLES_DIR = Path(__file__).parent.parent / "tests" / "output"

TARGETS = {
    "goods": "입찰공고서(국가_규격가격동시_1468_제조공급_공동불가).extracted.json",
    "service": "공고문_반월지구 취약지역생활여건개조사업 건설폐기물처리 용역.extracted.json",
    "construction": "공고문_시설공사 견적체출 안내공고(제물포고등학교).extracted.json",
    "foreign": "[공고문] 2026년 외자물품 국제운송 수출입 통관 대행 용역.extracted.json",
}

# 4종 모두 실제 원문에 "공동계약/공동도급 불가·불허"가 명시돼 있어 정답은 전부 False.
EXPECTED_JOINT_VENTURE_ALLOWED = False

# few-shot이 "창작한" 구체적 값 조합만 마커로 쓴다(실존 가능한 지명/업종명/법령명 단독 금지).
CROSS_TYPE_MARKERS = {
    "goods": [
        "세부품명번호: 4321000001",
        "낙찰하한율 88% 이상인 자 중 최저가 입찰자",
    ],
    "service": [
        "정보보호전문서비스기업 인증 보유 업체(가점 아님, 필수)",
        "정보시스템 구축 용역 실적(단일계약 기준 3억원 이상)",
    ],
    "construction": [
        "전기공사업법 제4조에 의한 전기공사업 등록업체",
        "철도신호분야(구분:시공) 필수기술인력(중급 1인, 초급 1인 이상)",
        "본점 소재지가 전북특별자치도에 입찰공고일 기준 90일 이상 소재한 업체",
    ],
    "foreign": [
        "대외무역법에 따른 무역업 고유번호를 보유한 업체",
        "신용평가등급 B등급(또는 이에 상응하는 등급) 이상 보유 업체",
    ],
}


def find_leaks(result: dict, own_type: str) -> list[str]:
    raw = json.dumps(result, ensure_ascii=False)
    leaks = []
    for other_type, markers in CROSS_TYPE_MARKERS.items():
        if other_type == own_type:
            continue
        for marker in markers:
            if marker in raw:
                leaks.append(f"{other_type}:{marker}")
    return leaks


def main() -> None:
    all_ok = True
    for label, filename in TARGETS.items():
        doc = json.load(open(SAMPLES_DIR / filename, encoding="utf-8"))
        result = extractor.extract(doc["pages"])

        leaks = find_leaks(result, label)
        jv = result.get("joint_venture_allowed")
        jv_ok = jv is EXPECTED_JOINT_VENTURE_ALLOWED

        ok = not leaks and jv_ok
        all_ok = all_ok and ok
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {label:14} jv={jv!s:6} leaks={leaks or '없음'}")

    print()
    print("전체 통과" if all_ok else "일부 실패 — 위 FAIL 항목 확인")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
