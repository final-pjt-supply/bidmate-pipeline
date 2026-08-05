"""압축 첨부 해제와 처리 불가 첨부(DRM) 판별 규칙.

I/O가 전혀 없는 순수 규칙만 둔다 — 다운로드/업로드는 json_file_download_daily.py가
맡고 여기서는 "이 바이트가 무엇인가", "이 zip에서 무엇을 꺼낼 것인가"만 정한다.
attachment_rules.py가 다운로드 규칙을 I/O와 분리해 둔 것과 같은 이유(단위 테스트).
"""

import zipfile
from typing import Any

from attachment_rules import DOC_EXT_PRIORITY, split_ext


ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PDF_MAGIC = b"%PDF"
XML_PROLOG = b"<?xml"
BOM = b"\xef\xbb\xbf"

# 발주기관 DRM으로 잠긴 첨부. 확장자는 .hwp인데 내용이 OLE가 아니라 벤더 컨테이너라
# 추출기가 "Not an OLE2 Compound Binary File"로 죽고, 3회 재시도 후 DLQ에 영구 적재된다.
# 복호화 키가 기관 DRM 서버에 있어 우리 쪽에서 열 방법이 없으므로 아예 안 받는다.
# 현재는 SoftCamp(SCDSA004)만 실물로 확인됐다. 다른 벤더가 나오면 여기에 추가할 것.
DRM_MAGICS = (b"SCDS",)

# 첫 바이트 판별에 필요한 최소 길이. DRM_MAGICS/ZIP_MAGIC 중 가장 긴 것보다 넉넉하게.
PEEK_BYTES = 8

# 멤버 내용 판별용. HWPML은 `<?xml …?>` 뒤에 DOCTYPE이 오고 그다음 <HWPML>이라
# 앞 8바이트로는 부족하다(라우터의 _HWPML_PROBE와 같은 이유).
MEMBER_PROBE_BYTES = 4096

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


def detect_member_kind(head: bytes) -> str | None:
    """압축에서 꺼낸 내용물이 추출 파이프라인이 읽을 수 있는 것인지 판정한다.

    읽을 수 있으면 형식 이름, 아니면 None. 확장자는 보지 않는다 — 압축 안에는
    이름과 내용이 어긋난 파일이 흔하다(실측: 이름은 .hwp인데 내용은 HWPML).
    최종 판정은 어차피 추출 Lambda의 router가 같은 매직바이트로 다시 한다.

    아는 형식만 통과시킨다. 모르는 것을 일단 올려두면 추출 단계에서 결정적으로
    실패해 3회 재시도 후 DLQ에 영구 적재되고, expected_file_count에는 잡혀 있어
    공고가 partial로 고착된다(2026-08-05 HWPML 유입 때 실제로 겪음).
    """
    if head.startswith(DRM_MAGICS):
        return None
    if head.startswith(OLE_MAGIC):
        return "hwp"
    if head.startswith(ZIP_MAGIC):
        return "hwpx"
    if head.startswith(PDF_MAGIC):
        return "pdf"
    probe = head[len(BOM):] if head.startswith(BOM) else head
    if probe.startswith(XML_PROLOG) and b"<HWPML" in probe:
        return "hwpml"
    return None


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


def select_zip_members(archive: zipfile.ZipFile) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """zip 안에서 실제로 적재할 멤버를 고른다. 반환은 (적재분, 제외분).

    세 단계로 거른다:
    1. 지원 확장자(hwpx/hwp/pdf)가 아니면 제외 — 추출 파이프라인이 못 다룬다.
    2. 같은 stem이 여러 확장자로 들어 있으면 hwpx > hwp > pdf 중 하나만
       (attachment_rules.apply_dedup()과 같은 규칙).
    3. **내용 검사** — 앞부분 매직바이트가 아는 형식이 아니면 제외.
       확장자가 맞아도 내용이 DRM이거나 정체불명이면 여기서 걸러진다.

    3번이 없으면 압축 바깥 첨부에만 검사가 걸리고 안쪽은 무사통과가 된다.
    실제로 그 구멍으로 HWPML이 들어와 공고당 3~4개씩 영구 실패했다(2026-08-05).

    제외분도 사유와 함께 돌려준다 — 호출부가 manifest에 남겨야 "이 공고에 문서가
    있었지만 못 받았다"가 추적된다(다운로드 단계의 dedupDropped와 같은 관례).

    적재분에는 1부터의 memberNo를 붙인다. 이 번호가 S3 키의 `_docNN_MM`이 되므로
    같은 zip을 다시 풀어도 같은 키가 나온다.
    """
    candidates, rejected = [], []
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = decode_member_name(info)
        if _is_junk(name):
            continue
        base = name.rsplit("/", 1)[-1]
        stem, ext = split_ext(base)
        if not stem or ext not in DOC_EXT_PRIORITY:
            rejected.append({"name": base, "reason": f"지원하지 않는 확장자({ext or '없음'})"})
            continue
        candidates.append({"info": info, "name": base, "stem": stem, "ext": ext})

    best: dict[str, int] = {}
    for c in candidates:
        pri = DOC_EXT_PRIORITY.index(c["ext"])
        best[c["stem"]] = min(best.get(c["stem"], pri), pri)

    selected = []
    for c in candidates:
        if DOC_EXT_PRIORITY.index(c["ext"]) > best[c["stem"]]:
            rejected.append({
                "name": c["name"],
                "reason": f"같은 이름의 {DOC_EXT_PRIORITY[best[c['stem']]]} 문서를 우선 적재",
            })
            continue

        with archive.open(c["info"]) as fh:
            head = fh.read(MEMBER_PROBE_BYTES)
        kind = detect_member_kind(head)
        if kind is None:
            rejected.append({
                "name": c["name"],
                "reason": f"추출할 수 없는 내용(DRM이거나 미지원 형식, 선두 {head[:8]!r})",
            })
            continue

        c["kind"] = kind
        selected.append(c)
        if len(selected) >= MAX_MEMBERS:
            break

    for number, member in enumerate(selected, start=1):
        member["memberNo"] = number
    return selected, rejected


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
