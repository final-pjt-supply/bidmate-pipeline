#!/usr/bin/env python3
"""나라장터 입찰공고 원본 레코드를 큐레이션 스키마로 변환한다."""

from datetime import datetime

DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
ATTACHMENT_RANGE = range(1, 11)


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


FIELD_MAP = {
    # 식별자
    "bid_ntce_no": ("bidNtceNo", _txt),
    "bid_ntce_ord": ("bidNtceOrd", _txt),
    "unty_ntce_no": ("untyNtceNo", _txt),
    # 공고·분류
    "bid_ntce_nm": ("bidNtceNm", _txt),
    "ntce_kind_nm": ("ntceKindNm", _txt),
    "srvce_div_nm": ("srvceDivNm", _txt),
    "large_clsfc_nm": ("pubPrcrmntLrgClsfcNm", _txt),
    "mid_clsfc_nm": ("pubPrcrmntMidClsfcNm", _txt),
    "prcrmnt_clsfc_nm": ("pubPrcrmntClsfcNm", _txt),
    "prcrmnt_clsfc_no": ("pubPrcrmntClsfcNo", _txt),
    # 기관
    "dminstt_nm": ("dminsttNm", _txt),
    "dminstt_cd": ("dminsttCd", _txt),
    "ntce_instt_nm": ("ntceInsttNm", _txt),
    "ntce_instt_cd": ("ntceInsttCd", _txt),
    "ofcl_nm": ("ntceInsttOfclNm", _txt),
    "ofcl_tel": ("ntceInsttOfclTelNo", _txt),
    # 금액
    "asign_bdgt_amt": ("asignBdgtAmt", _int),
    "presmpt_prce": ("presmptPrce", _int),
    "vat": ("VAT", _int),
    # 일정
    "bid_ntce_dt": ("bidNtceDt", _dt),
    "bid_begin_dt": ("bidBeginDt", _dt),
    "bid_clse_dt": ("bidClseDt", _dt),
    "openg_dt": ("opengDt", _dt),
    "qlfct_rgst_dt": ("bidQlfctRgstDt", _dt),
    # 계약·평가 방식
    "cntrct_mthd_nm": ("cntrctCnclsMthdNm", _txt),
    "sucsfbid_mthd_nm": ("sucsfbidMthdNm", _txt),
    "bid_methd_nm": ("bidMethdNm", _txt),
    "lwlt_rate": ("sucsfbidLwltRate", _num),
    "tech_evl_rt": ("techAbltEvlRt", _num),
    "prce_evl_rt": ("bidPrceEvlRt", _num),
    # 자격 제한
    "indstryty_lmt_yn": ("indstrytyLmtYn", _bool),
    "intrbid_yn": ("intrbidYn", _bool),
    "re_ntce_yn": ("reNtceYn", _bool),
    "info_biz_yn": ("infoBizYn", _bool),
    # URL·변경
    "bid_ntce_url": ("bidNtceUrl", _txt),
    "chg_ntce_rsn": ("chgNtceRsn", _txt),
}


def _attachments(record):
    attachments = []
    for index in ATTACHMENT_RANGE:
        file_url = _txt(record.get(f"ntceSpecDocUrl{index}"))
        file_nm = _txt(record.get(f"ntceSpecFileNm{index}"))
        if file_url or file_nm:
            attachments.append({"file_nm": file_nm, "file_url": file_url})
    return attachments


def to_curated(record, biz_div, collected_at=None):
    """원본 레코드 1건을 FIELD_DICTIONARY.md의 39개 필드로 변환한다."""
    curated = {field: caster(record.get(source)) for field, (source, caster) in FIELD_MAP.items()}
    curated["attachments"] = _attachments(record)
    curated["src_biz_div"] = biz_div
    curated["collected_at"] = (collected_at or datetime.now()).isoformat(sep=" ", timespec="seconds")
    return curated
