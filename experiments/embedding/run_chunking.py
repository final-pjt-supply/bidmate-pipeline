# -*- coding: utf-8 -*-
"""experiments/embedding/data/의 extracted JSON 전체를 embedding/chunker.py로 청킹하고
결과를 experiments/embedding/chunks/all_chunks.json에 저장 + 통계를 출력한다.

청킹 관련 로그는 embedding.chunker의 logging을 그대로 쓴다 — 콘솔엔 INFO 레벨(문서
단위 요약)까지 출력하고, WARNING 레벨(표 잘림/잔재 드랍/조항 인식 실패)은 메모리에도
모아서(_WarningCollector) 드랍 목록 등을 전/후 비교 보고에 쓴다. 별도 통계 파일
없이 로그가 유일한 출처 — chunker.py 쪽 로그 레벨 원칙 주석 참고.

data/의 각 문서는 pages 리스트(문서 원 페이지 단위)로 나뉘어 있는데,
chunker.chunk()는 페이지 경계를 모르는 통짜 텍스트 하나를 받는 함수라
페이지 texts를 그대로 이어붙여서 넘긴다(페이지 구분자를 새로 만들지 않음 —
chunker의 섹션 인식은 원래 페이지 경계와 무관하게 "1. 2. 3." 같은 조항
번호로 하기 때문에 이어붙이기만 해도 된다).

실행(리포 루트에서):
    python experiments/embedding/run_chunking.py
"""
import json
import logging
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from embedding.chunker import chunk  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"
OUT_DIR = Path(__file__).parent / "chunks"
OUT_PATH = OUT_DIR / "all_chunks.json"


class _WarningCollector(logging.Handler):
    """WARNING 이상 로그 레코드를 메모리에 모은다 — 드랍 목록 등 리포트용 추출에 사용."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def setup_logging() -> _WarningCollector:
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

    collector = _WarningCollector()

    chunker_logger = logging.getLogger("embedding.chunker")
    chunker_logger.setLevel(logging.DEBUG)  # 핸들러별 레벨로 필터링하니 로거 자체는 낮게 열어둠
    chunker_logger.addHandler(console)
    chunker_logger.addHandler(collector)
    chunker_logger.propagate = False

    return collector


def load_document_text(path: Path) -> tuple[str, str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    bid_id = data.get("bid_id", "")
    document_id = data.get("document_id", "")
    text = "\n\n".join(p["text"] for p in data.get("pages", []))
    return text, bid_id, document_id


def main() -> None:
    collector = setup_logging()
    logger = logging.getLogger(__name__)
    logging.getLogger().setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(console)
    logger.propagate = False

    files = sorted(DATA_DIR.rglob("*.json"))
    logger.info("입력 문서 수: %d", len(files))

    all_chunks = []
    per_doc_counts = []

    for path in files:
        text, bid_id, document_id = load_document_text(path)
        source = f"{bid_id}_{document_id}"
        doc_chunks = chunk(text, source=source)
        per_doc_counts.append(len(doc_chunks))
        for c in doc_chunks:
            c["bid_id"] = bid_id
            c["document_id"] = document_id
        all_chunks.extend(doc_chunks)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8")

    chunk_lengths = [len(c["text"]) for c in all_chunks]
    truncated = [c for c in all_chunks if c.get("truncated")]
    dropped_records = [r for r in collector.records if getattr(r, "event", None) == "chunk_dropped"]
    violation_records = [r for r in collector.records if getattr(r, "event", None) == "invariant_violation"]

    logger.info("총 청크 수: %d", len(all_chunks))
    logger.info("저장 위치: %s", OUT_PATH)
    logger.info("드랍된 청크: %d건 (WARNING 로그 event=chunk_dropped에서 추출)", len(dropped_records))
    logger.info("불변식 위반(비-truncated 청크가 MAX_CHARS 초과): %d건", len(violation_records))
    logger.info("문서당 청크 수 - 중앙값=%s 최대=%s 최소=%s",
                statistics.median(per_doc_counts), max(per_doc_counts), min(per_doc_counts))
    logger.info("청크 길이(문자) - 중앙값=%.0f 최대=%s 최소=%s",
                statistics.median(chunk_lengths), max(chunk_lengths), min(chunk_lengths))
    logger.info("truncated 청크: %d건", len(truncated))

    print()
    print("=== 드랍된 청크 목록 (WARNING 로그 추출, 눈검사용) ===")
    for r in dropped_records:
        print(f"  [{r.source}] {r.dropped_text!r}")

    print()
    print("=== 불변식 위반 목록 (있으면 버그) ===")
    for r in violation_records:
        print(f"  [{r.source}] chunk_idx={r.chunk_idx} length={r.length}자")

    print()
    print("=== truncated 청크 목록 ===")
    for c in truncated:
        print(
            f"  {c['bid_id']}_{c['document_id']} chunk_idx={c['chunk_idx']} "
            f"original={c['original_chars']}자 kept={c['kept_chars']}자"
        )

    print()
    print("=== 최장 청크 상위 5개 (1,125자 사례 등 병합 결과 확인용) ===")
    for c in sorted(all_chunks, key=lambda c: len(c["text"]), reverse=True)[:5]:
        print(
            f"  {c['bid_id']}_{c['document_id']} chunk_idx={c['chunk_idx']} "
            f"type={c['type']} length={len(c['text'])}자 truncated={c.get('truncated', False)}"
        )


if __name__ == "__main__":
    main()
