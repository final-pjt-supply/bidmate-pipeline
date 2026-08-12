#!/usr/bin/env python3
"""나라장터 입찰공고 원본 레코드를 큐레이션 스키마로 변환한다."""

import re
from datetime import datetime

DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
ATTACHMENT_RANGE = range(1, 11)   # ntceSpecDocUrl1~10 / ntceSpecFileNm1~10
RGN_DUTY_RANGE = range(1, 4)      # jntcontrctDutyRgnNm1~3
SUBSI_CNSTTY_RANGE = range(1, 10)  # subsiCnsttyNm1~9 / subsiCnsttyEvlRt1~9
SHRE_RATE_SPLIT = re.compile(r"[\^|;,]")


def _txt(value):
    """공백 문자열과 None을 모두 None으로 정규화한다."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _int(value):
    value = _txt(value)
    if value is None:
        return None
    value = value.replace(",", "")
    return int(value) if value.lstrip("-").isdigit() else None


def _num(value):
    value = _txt(value)
    if value is None:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def parse_dt(value):
    """일시 문자열을 datetime으로 변환한다. 알 수 없는 포맷은 None."""
    if isinstance(value, datetime):
        return value

    value = _txt(value)
    if value is None:
        return None

    for date_format in DATETIME_FORMATS:
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    return None


def _dt(value):
    parsed = parse_dt(value)
    return parsed.isoformat(sep=" ") if parsed else _txt(value)


def _bool(value):
    return {"Y": True, "N": False}.get(_txt(value))


def _bdgt_amt(record):
    """예산 금액. 업무구분(biz_div)마다 실제 원본 키가 다르게 관측된다: cnstwk는 bdgtAmt,
    thng/servc/frgcpt는 asignBdgtAmt(2026-07 확인, cnstwk 전건이 asignBdgtAmt만 보고 null이
    되던 버그의 원인). 우선순위 키에 "-" 같은 결측 플레이스홀더가 와도 다음 키로 폴백하도록,
    원문 존재 여부가 아니라 _int() 파싱 성공 여부로 채택한다(실측: thng/frgcpt에 "", "0",
    cnstwk엔 미확인이나 "-" 류 플레이스홀더가 흔한 편이라 방어).
    """
    for key in ("bdgtAmt", "asignBdgtAmt"):
        value = _int(record.get(key))
        if value is not None:
            return value
    return None


# 원본 camelCase → curated snake_case 1:1 매핑 (39개).
# 생성/주입/계산/묶음 필드(bid_id, bid_category, attachments, expected_file_count,
# raw_s3_key, jntcontrct_duty_rgn_nm, cnstty_accot_shre_rate_list, subsi_cnstty, bdgt_amt)는
# 여러 원본 필드나 외부 입력이 필요하므로 FIELD_MAP이 아니라 to_curated()에서 조립한다.
FIELD_MAP = {
    # 식별자
    "bid_ntce_no": ("bidNtceNo", _txt),
    "bid_ntce_ord": ("bidNtceOrd", _txt),
    # 공고·기관
    "bid_ntce_nm": ("bidNtceNm", _txt),
    "ntce_instt_cd": ("ntceInsttCd", _txt),
    "ntce_instt_nm": ("ntceInsttNm", _txt),
    "dminstt_cd": ("dminsttCd", _txt),
    "dminstt_nm": ("dminsttNm", _txt),
    # 공고종류(등록공고/재공고/취소공고/변경공고) — 취소공고는 같은 bidNtceNo에 차수만
    # 올려서 오므로, 이 값이 취소공고 식별의 유일한 근거다(#122).
    "ntce_kind_nm": ("ntceKindNm", _txt),
    # 플래그
    "re_ntce_yn": ("reNtceYn", _bool),
    "intrbid_yn": ("intrbidYn", _bool),
    # 일정
    "bid_ntce_dt": ("bidNtceDt", _dt),
    "bid_clse_dt": ("bidClseDt", _dt),
    "openg_dt": ("opengDt", _dt),
    "bid_qlfct_rgst_dt": ("bidQlfctRgstDt", _dt),
    "rgst_dt": ("rgstDt", _dt),
    "chg_dt": ("chgDt", _dt),
    # 금액
    "presmpt_prce": ("presmptPrce", _int),
    # bdgt_amt는 to_curated()에서 별도 조립(업무구분별로 실제 키가 다름 — 아래 _bdgt_amt 참고).
    "vat": ("VAT", _int),
    "govsply_amt": ("govsplyAmt", _int),
    # 계약·낙찰 방식
    "cntrct_cncls_mthd_nm": ("cntrctCnclsMthdNm", _txt),
    "sucsfbid_mthd_cd": ("sucsfbidMthdCd", _txt),
    "sucsfbid_mthd_nm": ("sucsfbidMthdNm", _txt),
    "sucsfbid_lwlt_rate": ("sucsfbidLwltRate", _num),
    "bid_methd_nm": ("bidMethdNm", _txt),
    # 심사·경쟁 유형
    "pq_eval_yn": ("pqEvalYn", _bool),
    "dsgnt_cmpt_yn": ("dsgntCmptYn", _bool),
    "bid_prtcpt_lmt_yn": ("bidPrtcptLmtYn", _bool),
    "rbid_permsn_yn": ("rbidPermsnYn", _bool),
    # 지역·공동도급
    "cnstrtsite_rgn_nm": ("cnstrtsiteRgnNm", _txt),
    "rgn_duty_jntcontrct_yn": ("rgnDutyJntcontrctYn", _bool),
    "rgn_duty_jntcontrct_rt": ("rgnDutyJntcontrctRt", _num),
    "cmmn_spldmd_methd_cd": ("cmmnSpldmdMethdCd", _txt),
    "cmmn_spldmd_methd_nm": ("cmmnSpldmdMethdNm", _txt),
    "cmmn_spldmd_agrmnt_clse_dt": ("cmmnSpldmdAgrmntClseDt", _dt),
    # 공종
    "main_cnstty_nm": ("mainCnsttyNm", _txt),
    "main_cnstty_presmpt_prce": ("mainCnsttyPresmptPrce", _int),
    "indstryty_lmt_yn": ("indstrytyLmtYn", _bool),
    # URL·통합번호
    "bid_ntce_dtl_url": ("bidNtceDtlUrl", _txt),
    "unty_ntce_no": ("untyNtceNo", _txt),
}


def _bid_id(bid_ntce_no, bid_ntce_ord):
    """{공고번호}_{차수}. 둘 중 하나라도 없으면 None."""
    if bid_ntce_no is None or bid_ntce_ord is None:
        return None
    return f"{bid_ntce_no}_{bid_ntce_ord}"


def _rgn_duty_list(record):
    """jntcontrctDutyRgnNm1~3 → 비어 있지 않은 지역명 배열."""
    values = (_txt(record.get(f"jntcontrctDutyRgnNm{i}")) for i in RGN_DUTY_RANGE)
    return [value for value in values if value]


def _subsi_cnstty(record):
    """subsiCnsttyNm{i} + subsiCnsttyEvlRt{i} → [{nm, evl_rt}] 배열 (부공종)."""
    items = []
    for i in SUBSI_CNSTTY_RANGE:
        nm = _txt(record.get(f"subsiCnsttyNm{i}"))
        evl_rt = _num(record.get(f"subsiCnsttyEvlRt{i}"))
        if nm or evl_rt is not None:
            items.append({"nm": nm, "evl_rt": evl_rt})
    return items


def _shre_rate_list(record):
    """cnsttyAccotShreRateList(공종지분) 파싱.

    원본이 이미 배열/객체면 그대로 보존하고, 문자열이면 대괄호를 전부 제거한 뒤 구분자
    (^ | ; ,)로 나눠 토큰 배열로 만든다. 공종이 여럿이면 그룹별로 대괄호가 따로 붙는다
    (실측: "[일반소방시설공사업(전기)^60.1],[일반소방시설공사업(기계)^39.9]") — 맨 앞/뒤
    대괄호만 벗기면(removeprefix/removesuffix) 가운데 그룹의 대괄호가 그대로 남는 버그가
    있었다. 대괄호는 구조 문자일 뿐 값에 의미 있게 등장하지 않는다고 보고 전부 제거한다.
    """
    value = record.get("cnsttyAccotShreRateList")
    if isinstance(value, list):
        return value
    text = _txt(value)
    if text is None:
        return []
    text = text.replace("[", "").replace("]", "")
    return [token.strip() for token in SHRE_RATE_SPLIT.split(text) if token.strip()]


# expected_file_count 전용 dedup 판정 규칙. attachment_rules.py의 DOC_EXT_PRIORITY와
# 반드시 동일하게 유지할 것 — 값을 바꾸면 두 곳 다 바꿔야 한다(#88, 순환 임포트
# 회피 때문에 import 대신 로직을 복제함. attachment_rules.py가 이미
# `from schema import parse_dt`로 이 모듈을 참조하고 있어 반대 방향 import는 순환이 됨).
_DOC_EXT_PRIORITY = ("hwpx", "hwp", "pdf")


def _split_ext(file_name):
    """attachment_rules.split_ext()와 동일 규칙(#88) — snake_case(file_nm) 입력용."""
    name = str(file_name or "").strip()
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        return stem.strip(), ext.strip().lower()
    return name, ""


def _effective_expected_count(attachments):
    """expected_file_count 계산 전용(#88). attachment_rules.apply_dedup()과 동일한 두 규칙
    (동일 URL 중복 제거, 같은 stem은 hwpx>hwp>pdf 우선순위 하나만)을 재현하되, 여기에 더해
    지원 확장자(pdf/hwp/hwpx)가 아닌 첨부는 아예 세지 않는다 — 추출 파이프라인이 원천적으로
    처리 못 하는 파일(zip/xlsx/htm 등)까지 "기대 문서 수"에 잡혀 병합 판정이 영원히
    partial/pending에 고착되는 문제(#88) 대응. attachments 리스트 자체는 이 함수가 건드리지
    않는다 — 다운로드 파이프라인(attachment_rules.build_file_metadata) 입력은 그대로 둔다.
    """
    best = {}
    for attachment in attachments:
        stem, ext = _split_ext(attachment.get("file_nm"))
        if stem and ext in _DOC_EXT_PRIORITY:
            pri = _DOC_EXT_PRIORITY.index(ext)
            best[stem] = min(best.get(stem, pri), pri)

    count = 0
    seen_urls = set()
    for attachment in attachments:
        url = str(attachment.get("file_url") or "").strip()
        if url and url in seen_urls:
            continue

        stem, ext = _split_ext(attachment.get("file_nm"))
        if stem and ext in _DOC_EXT_PRIORITY and _DOC_EXT_PRIORITY.index(ext) > best[stem]:
            continue

        if ext not in _DOC_EXT_PRIORITY:
            if url:
                seen_urls.add(url)
            continue

        if url:
            seen_urls.add(url)
        count += 1
    return count


def _attachments(record):
    attachments = []
    spec_urls = set()
    for index in ATTACHMENT_RANGE:
        file_url = _txt(record.get(f"ntceSpecDocUrl{index}"))
        file_nm = _txt(record.get(f"ntceSpecFileNm{index}"))
        if file_url or file_nm:
            attachments.append({"file_nm": file_nm, "file_url": file_url, "kind": "공고첨부"})
            if file_url:
                spec_urls.add(file_url)
    # 표준공고서(stdNtceDocUrl)도 다운로드 대상이라 첨부 배열에 흡수하되, 나라장터는
    # stdNtceDocUrl을 ntceSpecDocUrl 중 하나와 동일한 URL로 내려주는 경우가 대부분이다.
    # (실측: servc/thng/cnstwk 전 레코드에서 표준공고서 URL이 공고첨부 URL과 100% 동일)
    # 그대로 두면 같은 파일이 파일명 없는 표준공고서로 한 번 더 다운로드돼 _doc02.bin 등으로
    # 중복 적재되므로, 공고첨부에 이미 있는 URL이면 표준공고서를 추가하지 않는다.
    std_url = _txt(record.get("stdNtceDocUrl"))
    if std_url and std_url not in spec_urls:
        attachments.append({"file_nm": None, "file_url": std_url, "kind": "표준공고서"})
    return attachments


def to_curated(record, bid_category, raw_s3_key=None):
    """원본 레코드 1건을 FIELD_DICTIONARY.md의 48개 필드로 변환한다.

    bid_category: 출처 API 업무구분(thng/servc/cnstwk/frgcpt) — 수집기가 주입.
    raw_s3_key: 이 레코드의 원본 JSON이 저장된 S3 객체 키 — 수집기가 주입.
    """
    mapped = {field: caster(record.get(source)) for field, (source, caster) in FIELD_MAP.items()}
    mapped["bdgt_amt"] = _bdgt_amt(record)
    attachments = _attachments(record)

    return {
        # 식별자
        "bid_ntce_no": mapped["bid_ntce_no"],
        "bid_ntce_ord": mapped["bid_ntce_ord"],
        "bid_id": _bid_id(mapped["bid_ntce_no"], mapped["bid_ntce_ord"]),
        "bid_category": bid_category,
        # 공고·기관
        "bid_ntce_nm": mapped["bid_ntce_nm"],
        "ntce_instt_cd": mapped["ntce_instt_cd"],
        "ntce_instt_nm": mapped["ntce_instt_nm"],
        "dminstt_cd": mapped["dminstt_cd"],
        "dminstt_nm": mapped["dminstt_nm"],
        "ntce_kind_nm": mapped["ntce_kind_nm"],
        # 플래그
        "re_ntce_yn": mapped["re_ntce_yn"],
        "intrbid_yn": mapped["intrbid_yn"],
        # 일정
        "bid_ntce_dt": mapped["bid_ntce_dt"],
        "bid_clse_dt": mapped["bid_clse_dt"],
        "openg_dt": mapped["openg_dt"],
        "bid_qlfct_rgst_dt": mapped["bid_qlfct_rgst_dt"],
        "rgst_dt": mapped["rgst_dt"],
        "chg_dt": mapped["chg_dt"],
        # 금액
        "presmpt_prce": mapped["presmpt_prce"],
        "bdgt_amt": mapped["bdgt_amt"],
        "vat": mapped["vat"],
        "govsply_amt": mapped["govsply_amt"],
        # 계약·낙찰 방식
        "cntrct_cncls_mthd_nm": mapped["cntrct_cncls_mthd_nm"],
        "sucsfbid_mthd_cd": mapped["sucsfbid_mthd_cd"],
        "sucsfbid_mthd_nm": mapped["sucsfbid_mthd_nm"],
        "sucsfbid_lwlt_rate": mapped["sucsfbid_lwlt_rate"],
        "bid_methd_nm": mapped["bid_methd_nm"],
        # 심사·경쟁 유형
        "pq_eval_yn": mapped["pq_eval_yn"],
        "dsgnt_cmpt_yn": mapped["dsgnt_cmpt_yn"],
        "bid_prtcpt_lmt_yn": mapped["bid_prtcpt_lmt_yn"],
        "rbid_permsn_yn": mapped["rbid_permsn_yn"],
        # 지역·공동도급
        "cnstrtsite_rgn_nm": mapped["cnstrtsite_rgn_nm"],
        "rgn_duty_jntcontrct_yn": mapped["rgn_duty_jntcontrct_yn"],
        "rgn_duty_jntcontrct_rt": mapped["rgn_duty_jntcontrct_rt"],
        "jntcontrct_duty_rgn_nm": _rgn_duty_list(record),
        "cmmn_spldmd_methd_cd": mapped["cmmn_spldmd_methd_cd"],
        "cmmn_spldmd_methd_nm": mapped["cmmn_spldmd_methd_nm"],
        "cmmn_spldmd_agrmnt_clse_dt": mapped["cmmn_spldmd_agrmnt_clse_dt"],
        # 공종
        "main_cnstty_nm": mapped["main_cnstty_nm"],
        "main_cnstty_presmpt_prce": mapped["main_cnstty_presmpt_prce"],
        "indstryty_lmt_yn": mapped["indstryty_lmt_yn"],
        "cnstty_accot_shre_rate_list": _shre_rate_list(record),
        "subsi_cnstty": _subsi_cnstty(record),
        # URL·통합번호
        "bid_ntce_dtl_url": mapped["bid_ntce_dtl_url"],
        "unty_ntce_no": mapped["unty_ntce_no"],
        # 첨부·메타
        "attachments": attachments,
        "expected_file_count": _effective_expected_count(attachments),
        "raw_s3_key": raw_s3_key,
    }
