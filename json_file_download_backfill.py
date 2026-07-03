"""Curated JSON에서 나라장터 첨부문서 URL을 추출하고 파일로 저장한다."""


import argparse
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import requests


BASE_DIR = Path("/Users/oloqlq/Desktop/bidding")
CURATED_DIR = BASE_DIR / "curated"
CHUNK_SIZE = 1024 * 256
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*]')
MULTISPACE = re.compile(r"\s+")


def json_files(input_path: Path):
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.rglob("*.json"))
    raise FileNotFoundError(f"curated JSON 경로를 찾을 수 없습니다: {input_path}")


def safe_name(value: Any, fallback: str) -> str:
    name = str(value or "").strip() or fallback
    name = INVALID_FILENAME.sub("_", name)
    name = MULTISPACE.sub(" ", name).strip()
    return name[:180] or fallback


def guess_ext(file_name: str, content_type: str, url: str) -> str:
    if Path(file_name).suffix:
        return Path(file_name).suffix

    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed

    return Path(urlparse(url).path).suffix or ".bin"


def iter_notices(input_path: Path, notice_limit: int):
    count = 0
    for src_file in json_files(input_path):
        with src_file.open("r", encoding="utf-8-sig") as file:
            payload = json.load(file)

        records = payload if isinstance(payload, list) else [payload]
        for record in records:
            if not isinstance(record, dict):
                continue
            count += 1
            yield count, src_file, record
            if notice_limit and count >= notice_limit:
                return


def build_metadata(index: int, src_file: Path, record: dict[str, Any], extracted_at: str):
    notice_id = f"{record.get('bid_ntce_no') or f'no-bid-no-{index}'}-{record.get('bid_ntce_ord') or '000'}"
    base = {
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
        "extractedAt": extracted_at,
    }

    files = []
    for seq, attachment in enumerate(record.get("attachments") or [], start=1):
        file_url = str(attachment.get("file_url") or "").strip()
        file_name = str(attachment.get("file_nm") or "").strip()
        if file_url or file_name:
            files.append(
                {
                    **base,
                    "fileId": f"{notice_id}-{seq}",
                    "fileSeq": str(seq),
                    "fileKind": attachment.get("kind") or "공고첨부",
                    "fileName": file_name,
                    "fileUrl": file_url,
                }
            )
    return files


def download_file(session: requests.Session, metadata: dict[str, Any], download_dir: Path, timeout: int):
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

    biz_div = safe_name(metadata.get("업무구분"), "미분류")
    bid_no = safe_name(metadata.get("bidNtceNo") or metadata.get("noticeId"), "공고번호없음")
    file_seq = safe_name(metadata.get("fileSeq"), "unknown")
    original_name = str(metadata.get("fileName") or "")
    ext = guess_ext(original_name, response.headers.get("Content-Type", ""), file_url)
    filename = safe_name(original_name, f"{bid_no}_{file_seq}{ext}")
    if not Path(filename).suffix:
        filename = f"{filename}{ext}"

    target_dir = download_dir / biz_div / bid_no
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    total = 0
    with target_path.open("wb") as file:
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                file.write(chunk)
                total += len(chunk)

    return {
        "downloadStatus": "success",
        "downloadPath": str(target_path),
        "downloadSize": total,
        "contentType": response.headers.get("Content-Type", ""),
        "downloadError": "",
    }


def run(args: argparse.Namespace) -> None:
    input_path = Path(args.raw_path)
    download_dir = Path(args.download_dir)
    metadata_path = Path(args.metadata_path)
    extracted_at = datetime.now(timezone.utc).isoformat()

    metadata = []
    notice_count = 0
    for index, src_file, record in iter_notices(input_path, args.notice_limit):
        notice_count = index
        metadata.extend(build_metadata(index, src_file, record, extracted_at))

    limit_label = args.notice_limit if args.notice_limit else "전체"
    print(f"[시작] curated 공고={notice_count}건, 제한={limit_label}")
    print(f"[추출] 첨부문서 메타데이터={len(metadata)}건")

    success = failed = skipped = 0
    with requests.Session() as session:
        for file_meta in metadata:
            label = f"{file_meta.get('bidNtceNo')}/{file_meta.get('fileSeq')}"
            try:
                result = download_file(session, file_meta, download_dir, args.timeout)
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

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[완료] 메타데이터 저장={metadata_path}")
    print(f"[완료] 다운로드 성공={success}건, 실패={failed}건, 건너뜀={skipped}건")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="curated JSON 첨부문서 추출/다운로드 전용 도구")
    parser.add_argument("--raw-path", default=str(CURATED_DIR), help="curated JSON 파일 또는 폴더")
    parser.add_argument("--notice-limit", type=int, default=0, help="처리할 공고 개수 (0이면 전체)")
    parser.add_argument(
        "--metadata-path",
        default=str(BASE_DIR / "metadata" / "bid_files.json"),
        help="첨부문서 메타데이터 저장 경로",
    )
    parser.add_argument("--download-dir", default=str(BASE_DIR / "downloads"), help="첨부문서 다운로드 폴더")
    parser.add_argument("--timeout", type=int, default=60, help="파일 다운로드 제한 시간 초")
    return parser.parse_args()


def main() -> None:
    try:
        run(parse_args())
    except Exception as exc:
        raise SystemExit(f"실패: {exc}") from None


if __name__ == "__main__":
    main()
