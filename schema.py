#!/usr/bin/env python3
"""나라장터 입찰공고 원본(API 113필드) → 큐레이션 스키마 변환.
- 첨부 20컬럼(ntceSpecDocUrl/FileNm 1~10) → attachments 배열로 접기
- 금액 → int, 일시 → 'YYYY-MM-DD HH:MM:SS', 율 → float, Yn → bool 캐스팅
- 빈 문자열/None 은 모두 None 으로 정규화
"""
from datetime import datetime


# ── 값 변환 헬퍼 ──────────────────────────────────────────────
def _txt(v):
    """문자열 정규화: 공백 제거, 빈값은 None."""
    if v is None:
        return None
    v = str(v).strip()
    return v or None

def _int(v):
    """금액 등 정수. 숫자가 아니면 None."""
    v = _txt(v)
    return int(v) if v and v.lstrip("-").isdigit() else None

def _num(v):
    """율(rate) 등 실수. 변환 불가면 None."""
    v = _txt(v)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None

DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")

def parse_dt(v):
    """일시 문자열 → datetime. 비었거나 모르는 포맷이면 None. (마감일 필터 등에서 재사용)"""
    v = _txt(v)
    if not v:
        return None
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None

def _dt(v):
    """일시 → 'YYYY-MM-DD HH:MM:SS'. 모르는 포맷은 원본 문자열 유지."""
    d = parse_dt(v)
    return d.isoformat(sep=" ") if d else _txt(v)

def _bool(v):
    """Y / N / 빈값 → True / False / None."""
    v = _txt(v)
    return True if v == "Y" else False if v == "N" else None


# ── 정제필드 → (원본필드, 변환함수) ──────────────────────────
FIELD_MAP = {
    # 식별자 (bid_ntce_no + bid_ntce_ord 가 복합 PK)
    "bid_ntce_no":      ("bidNtceNo", _txt),
    "bid_ntce_ord":     ("bidNtceOrd", _txt),
    "unty_ntce_no":     ("untyNtceNo", _txt),
    # 공고·분류 (전문검색/임베딩)
    "bid_ntce_nm":      ("bidNtceNm", _txt),
    "ntce_kind_nm":     ("ntceKindNm", _txt),
    "srvce_div_nm":     ("srvceDivNm", _txt),
    "large_clsfc_nm":   ("pubPrcrmntLrgClsfcNm", _txt),
    "mid_clsfc_nm":     ("pubPrcrmntMidClsfcNm", _txt),
    "prcrmnt_clsfc_nm": ("pubPrcrmntClsfcNm", _txt),
    "prcrmnt_clsfc_no": ("pubPrcrmntClsfcNo", _txt),
    # 기관 (발주처 가중치)
    "dminstt_nm":       ("dminsttNm", _txt),
    "dminstt_cd":       ("dminsttCd", _txt),
    "ntce_instt_nm":    ("ntceInsttNm", _txt),
    "ntce_instt_cd":    ("ntceInsttCd", _txt),
    "ofcl_nm":          ("ntceInsttOfclNm", _txt),
    "ofcl_tel":         ("ntceInsttOfclTelNo", _txt),
    # 금액 (예산 매칭/필터)
    "asign_bdgt_amt":   ("asignBdgtAmt", _int),
    "presmpt_prce":     ("presmptPrce", _int),
    "vat":              ("VAT", _int),
    # 일정 (필터/정렬/마감)
    "bid_ntce_dt":      ("bidNtceDt", _dt),
    "bid_begin_dt":     ("bidBeginDt", _dt),
    "bid_clse_dt":      ("bidClseDt", _dt),
    "openg_dt":         ("opengDt", _dt),
    "qlfct_rgst_dt":    ("bidQlfctRgstDt", _dt),
    # 계약·평가 방식
    "cntrct_mthd_nm":   ("cntrctCnclsMthdNm", _txt),
    "sucsfbid_mthd_nm": ("sucsfbidMthdNm", _txt),
    "bid_methd_nm":     ("bidMethdNm", _txt),
    "lwlt_rate":        ("sucsfbidLwltRate", _num),
    "tech_evl_rt":      ("techAbltEvlRt", _num),
    "prce_evl_rt":      ("bidPrceEvlRt", _num),
    # 자격 제한 (Go/No-Go)
    "indstryty_lmt_yn": ("indstrytyLmtYn", _bool),
    "intrbid_yn":       ("intrbidYn", _bool),
    "re_ntce_yn":       ("reNtceYn", _bool),
    "info_biz_yn":      ("infoBizYn", _bool),
    # URL·변경
    "bid_ntce_url":     ("bidNtceUrl", _txt),
    "chg_ntce_rsn":     ("chgNtceRsn", _txt),
}

MAX_ATTACH = 10


def _attachments(rec):
    """ntceSpecDocUrl1~10 + ntceSpecFileNm1~10 → [{file_nm, file_url}, ...]"""
    out = []
    for i in range(1, MAX_ATTACH + 1):
        url = _txt(rec.get(f"ntceSpecDocUrl{i}"))
        nm = _txt(rec.get(f"ntceSpecFileNm{i}"))
        if url or nm:
            out.append({"file_nm": nm, "file_url": url})
    return out


def to_curated(rec, biz_div, collected_at=None):
    """원본 레코드 1건 → 큐레이션 dict 1건.
    biz_div: 업무구분 키('servc' 등), collected_at: 수집 시각(datetime, 없으면 now)."""
    row = {field: fn(rec.get(src)) for field, (src, fn) in FIELD_MAP.items()}
    row["attachments"] = _attachments(rec)
    row["src_biz_div"] = biz_div
    row["collected_at"] = (collected_at or datetime.now()).isoformat(sep=" ", timespec="seconds")
    return row