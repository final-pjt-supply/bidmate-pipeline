"""Curated JSON에서 나라장터 첨부문서 URL을 추출하고 실제 파일을 저장한다.

이 스크립트는 파이프라인의 2번 단계만 수행한다.
1. raw_json.py 가 만든 curated JSON 파일/폴더를 읽는다.
2. 각 공고의 attachments 배열에서 첨부문서 URL과 메타데이터를 추출한다.
3. 메타데이터 JSON을 저장하고 첨부문서를 downloads 폴더에 내려받는다.
"""

import argparse
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

import requests

# raw_json.py 와 동일한 데이터 루트. 수집기가 이 아래에 curated/ 를 만든다.
BASE_DIR = Path("/Users/oloqlq/Desktop/bidding")
CURATED_DIR = BASE_DIR / "curated"


def JSON_파일목록_찾기(input_path: Path) -> List[Path]:
    """입력 경로가 파일이면 그 파일만, 폴더면 하위 JSON 파일을 모두 반환한다."""
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.rglob("*.json"))
    raise FileNotFoundError(f"curated JSON 경로를 찾을 수 없습니다: {input_path}")


def JSON_읽기(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def 공고목록_꺼내기(payload: Any) -> List[Dict[str, Any]]:
    """curated JSON(레코드 리스트)에서 공고 목록을 꺼낸다."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def 공고_ID_만들기(record: Dict[str, Any], fallback_index: int) -> str:
    bid_no = str(record.get("bid_ntce_no") or f"no-bid-no-{fallback_index}")
    bid_ord = str(record.get("bid_ntce_ord") or "000")
    return f"{bid_no}-{bid_ord}"


def 파일명_정리(name: str, fallback: str) -> str:
    cleaned = (name or "").strip() or fallback
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:180] or fallback


def 확장자_추정(file_name: str, content_type: Optional[str], url: str) -> str:
    suffix = Path(file_name).suffix
    if suffix:
        return suffix

    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed

    url_suffix = Path(urlparse(url).path).suffix
    return url_suffix or ".bin"


def 첨부메타_추출(record: Dict[str, Any], src_file: Path, notice_index: int) -> List[Dict[str, Any]]:
    """curated 공고 1건의 attachments 배열에서 첨부문서 메타데이터를 추출한다."""
    notice_id = 공고_ID_만들기(record, notice_index)
    base_meta = {
        "noticeId": notice_id,
        "업무구분": record.get("src_biz_div") or "미분류",
        "bidNtceNo": record.get("bid_ntce_no"),
        "bidNtceOrd": record.get("bid_ntce_ord"),
        "bidNtceNm": record.get("bid_ntce_nm"),
        "dminsttCd": record.get("dminstt_cd"),
        "dminsttNm": record.get("dminstt_nm"),
        "ntceInsttCd": record.get("ntce_instt_cd"),
        "ntceInsttNm": record.get("ntce_instt_nm"),
        "bidNtceDt": record.get("bid_ntce_dt"),
        "srcJsonPath": str(src_file),
    }

    files: List[Dict[str, Any]] = []
    for seq, attachment in enumerate(record.get("attachments") or [], start=1):
        file_url = str(attachment.get("file_url") or "").strip()
        file_nm = str(attachment.get("file_nm") or "").strip()
        if not file_url and not file_nm:
            continue

        files.append(
            {
                **base_meta,
                "fileId": f"{notice_id}-{seq}",
                "fileSeq": str(seq),
                "fileKind": attachment.get("kind") or "공고첨부",
                "fileName": file_nm,
                "fileUrl": file_url,
            }
        )

    return files


def curated_JSON에서_공고_읽기(input_path: Path, notice_limit: int) -> List[Dict[str, Any]]:
    notices: List[Dict[str, Any]] = []
    for src_file in JSON_파일목록_찾기(input_path):
        payload = JSON_읽기(src_file)
        for record in 공고목록_꺼내기(payload):
            copied = record.copy()
            copied["_srcJsonFile"] = src_file
            notices.append(copied)
            if notice_limit and len(notices) >= notice_limit:  # 0이면 전체
                return notices
    return notices


def 문서파일_다운로드(
    session: requests.Session,
    metadata: Dict[str, Any],
    download_dir: Path,
    timeout: int,
) -> Dict[str, Any]:
    file_url = str(metadata.get("fileUrl") or "").strip()
    if not file_url:
        return {
            "downloadStatus": "skipped",
            "downloadPath": "",
            "downloadSize": 0,
            "contentType": "",
            "downloadError": "fileUrl이 비어 있습니다.",
        }

    response = session.get(file_url, stream=True, timeout=timeout)
    response.raise_for_status()

    업무구분 = 파일명_정리(str(metadata.get("업무구분") or "미분류"), "미분류")
    bid_no = 파일명_정리(str(metadata.get("bidNtceNo") or metadata.get("noticeId")), "공고번호없음")
    file_seq = 파일명_정리(str(metadata.get("fileSeq") or "unknown"), "unknown")
    original_name = str(metadata.get("fileName") or "")
    ext = 확장자_추정(original_name, response.headers.get("Content-Type"), file_url)
    safe_name = 파일명_정리(original_name, f"{bid_no}_{file_seq}{ext}")
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}{ext}"

    target_dir = download_dir / 업무구분 / bid_no
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name

    total = 0
    with target_path.open("wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            file.write(chunk)
            total += len(chunk)

    return {
        "downloadStatus": "success",
        "downloadPath": str(target_path),
        "downloadSize": total,
        "contentType": response.headers.get("Content-Type", ""),
        "downloadError": "",
    }


def 메타데이터_저장(metadata: Sequence[Dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(list(metadata), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def 파이프라인_실행(args: argparse.Namespace) -> None:
    input_path = Path(args.raw_path)
    download_dir = Path(args.download_dir)
    metadata_path = Path(args.metadata_path)

    notices = curated_JSON에서_공고_읽기(input_path, args.notice_limit)
    limit_label = args.notice_limit if args.notice_limit else "전체"
    print(f"[시작] curated 공고={len(notices)}건, 제한={limit_label}")

    metadata: List[Dict[str, Any]] = []
    for index, record in enumerate(notices, start=1):
        src_file = record.pop("_srcJsonFile")
        metadata.extend(첨부메타_추출(record, src_file, index))

    extracted_at = datetime.now(timezone.utc).isoformat()
    for file_meta in metadata:
        file_meta["extractedAt"] = extracted_at

    print(f"[추출] 첨부문서 메타데이터={len(metadata)}건")

    session = requests.Session()
    success = 0
    failed = 0
    skipped = 0

    for file_meta in metadata:
        label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
        try:
            result = 문서파일_다운로드(session, file_meta, download_dir, args.timeout)
            file_meta.update(result)
            if result["downloadStatus"] == "success":
                success += 1
                print(f"[성공] {label} -> {result['downloadPath']}")
            else:
                skipped += 1
                print(f"[건너뜀] {label}: {result['downloadError']}")
        except Exception as exc:
            failed += 1
            file_meta.update(
                {
                    "downloadStatus": "failed",
                    "downloadPath": "",
                    "downloadSize": 0,
                    "contentType": "",
                    "downloadError": str(exc)[:1000],
                }
            )
            print(f"[실패] {label}: {exc}")

    메타데이터_저장(metadata, metadata_path)
    print(f"[완료] 메타데이터 저장={metadata_path}")
    print(f"[완료] 다운로드 성공={success}건, 실패={failed}건, 건너뜀={skipped}건")


def 인자_만들기() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="curated JSON 첨부문서 추출/다운로드 전용 도구")
    parser.add_argument("--raw-path", default=str(CURATED_DIR),
                        help="curated JSON 파일 또는 폴더 (기본: BASE_DIR/curated)")
    parser.add_argument("--notice-limit", type=int, default=0, help="처리할 공고 개수 (0이면 전체)")
    parser.add_argument("--metadata-path", default=str(BASE_DIR / "metadata" / "bid_files.json"),
                        help="첨부문서 메타데이터 저장 경로")
    parser.add_argument("--download-dir", default=str(BASE_DIR / "downloads"),
                        help="첨부문서 다운로드 폴더")
    parser.add_argument("--timeout", type=int, default=60, help="파일 다운로드 제한 시간 초")
    return parser


def main() -> None:
    args = 인자_만들기().parse_args()
    try:
        파이프라인_실행(args)
    except Exception as exc:
        raise SystemExit(f"실패: {exc}") from None


if __name__ == "__main__":
    main()