"""압축 첨부 해제와 처리 불가 첨부(DRM) 판별 규칙.

I/O가 전혀 없는 순수 규칙만 둔다 — 다운로드/업로드는 json_file_download_daily.py가
맡고 여기서는 "이 바이트가 무엇인가", "이 zip에서 무엇을 꺼낼 것인가"만 정한다.
attachment_rules.py가 다운로드 규칙을 I/O와 분리해 둔 것과 같은 이유(단위 테스트).
"""

import zipfile
from typing import Any

from attachment_rules import DOC_EXT_PRIORITY, split_ext


ZIP_MAGIC = b"PK\x03\x04"

# 발주기관 DRM으로 잠긴 첨부. 확장자는 .hwp인데 내용이 OLE가 아니라 벤더 컨테이너라
# 추출기가 "Not an OLE2 Compound Binary File"로 죽고, 3회 재시도 후 DLQ에 영구 적재된다.
# 복호화 키가 기관 DRM 서버에 있어 우리 쪽에서 열 방법이 없으므로 아예 안 받는다.
# 현재는 SoftCamp(SCDSA004)만 실물로 확인됐다. 다른 벤더가 나오면 여기에 추가할 것.
DRM_MAGICS = (b"SCDS",)

# 첫 바이트 판별에 필요한 최소 길이. DRM_MAGICS/ZIP_MAGIC 중 가장 긴 것보다 넉넉하게.
PEEK_BYTES = 8

# zip bomb·비정상 첨부 방어. 실측 첨부 최대가 7MB대라 넉넉히 잡아도 정상 파일은 안 걸린다.
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_MEMBERS = 50

# 압축 안의 압축은 풀지 않는다(깊이 1단계). 실물에서 본 적이 없고, 허용하면 재귀 상한과
# 채번 규칙(_docNN_MM)이 같이 복잡해진다. 발견되면 manifest에 사유가 남으므로 그때 판단한다.
_MACOS_JUNK = ("__MACOSX/", ".DS_Store")


def detect_payload_kind(head: bytes, file_name: str) -> str:
    """받은 바이트의 앞부분과 파일명으로 처리 방식을 정한다: "drm" | "zip" | "plain".

    ⚠️ 매직바이트만으로 zip을 판정하면 안 된다 — HWPX도 PK\\x03\\x04로 시작하는 zip이다.
    그래서 확장자가 .zip일 때만 압축으로 취급한다. 확장자가 hwpx/hwp/pdf면 내용이
    zip이어도 그대로 올려서 기존 추출 경로(라우터가 매직바이트로 재판별)를 태운다.
    """
    if head.startswith(DRM_MAGICS):
        return "drm"

    _, ext = split_ext(file_name)
    if ext == "zip" and head.startswith(ZIP_MAGIC):
        return "zip"
    return "plain"


def decode_member_name(info: zipfile.ZipInfo) -> str:
    """zip 멤버 이름을 사람이 읽을 수 있는 형태로 되돌린다.

    나라장터 zip은 대부분 UTF-8 플래그(0x800) 없이 cp949로 이름을 담는다. zipfile은
    플래그가 없으면 cp437로 디코딩하므로 한글이 통째로 깨진다. cp437로 되돌린 뒤
    cp949로 다시 읽어 복원한다. 복원이 안 되면 원본을 그대로 쓴다(키 생성 단계의
    safe_key_part가 어차피 위험 문자를 걸러낸다).
    """
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _is_junk(name: str) -> bool:
    return any(part in name for part in _MACOS_JUNK) or name.endswith("/")


def select_zip_members(infos: list[zipfile.ZipInfo]) -> list[dict[str, Any]]:
    """zip 안에서 실제로 적재할 멤버만 고른다.

    적용 규칙은 attachment_rules.apply_dedup()과 같은 계열이다:
    1. 지원 확장자(hwpx/hwp/pdf)가 아니면 제외 — 추출 파이프라인이 못 다룬다.
    2. 같은 stem이 여러 확장자로 들어 있으면 hwpx > hwp > pdf 중 하나만.
    이 규칙을 여기서도 지켜야 expected_file_count(= 실제 적재 개수)가 어긋나지 않는다.

    반환 순서는 zip 안의 순서를 따르고, 각 원소에 1부터의 memberNo를 붙인다.
    이 번호가 S3 키의 `_docNN_MM`이 되므로 같은 zip을 다시 풀어도 같은 키가 나온다.
    """
    candidates = []
    for info in infos:
        if info.is_dir():
            continue
        name = decode_member_name(info)
        if _is_junk(name):
            continue
        base = name.rsplit("/", 1)[-1]
        stem, ext = split_ext(base)
        if not stem or ext not in DOC_EXT_PRIORITY:
            continue
        candidates.append({"info": info, "name": base, "stem": stem, "ext": ext})

    best: dict[str, int] = {}
    for c in candidates:
        pri = DOC_EXT_PRIORITY.index(c["ext"])
        best[c["stem"]] = min(best.get(c["stem"], pri), pri)

    selected = []
    for c in candidates:
        if DOC_EXT_PRIORITY.index(c["ext"]) > best[c["stem"]]:
            continue
        selected.append(c)
        if len(selected) >= MAX_MEMBERS:
            break

    for number, member in enumerate(selected, start=1):
        member["memberNo"] = number
    return selected


def guard_archive(infos: list[zipfile.ZipInfo]) -> str | None:
    """압축 해제 전 안전 점검. 문제가 있으면 사유 문자열, 없으면 None.

    호출부는 사유를 manifest에 남기고 해제를 포기한다(첨부 하나 때문에 run 전체를
    실패시키지 않는다).
    """
    total = sum(i.file_size for i in infos)
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        return f"압축 해제 총량 상한 초과: {total:,}바이트 > {MAX_TOTAL_UNCOMPRESSED_BYTES:,}"
    oversized = next((i for i in infos if i.file_size > MAX_MEMBER_BYTES), None)
    if oversized is not None:
        return f"단일 멤버 상한 초과: {oversized.file_size:,}바이트"
    return None
