-- 네이밍 규칙: API camelCase → snake_case 기계적 변환. API 원본명은 각 컬럼 주석 참조.
-- 규약: 모든 TIMESTAMP는 KST naive. boolean류는 API 'Y'/'N' 변환 필요.
CREATE TABLE bid_table (
    -- 식별
    bid_ntce_no        VARCHAR(40) NOT NULL,   -- API: bidNtceNo
    bid_ntce_ord       VARCHAR(10) NOT NULL,   -- API: bidNtceOrd
    bid_id             VARCHAR(60) GENERATED ALWAYS AS (bid_ntce_no || '_' || bid_ntce_ord) STORED,
                       -- 내부: 조회용 파생 컬럼. split 금지(성분은 bid_ntce_no/ord 사용)
    bid_category       VARCHAR(10) NOT NULL,   -- 내부: thng/servc/cnstwk/frgcpt (수집 시 결정)
    -- 기본
    bid_ntce_nm        TEXT,                   -- API: bidNtceNm
    ntce_instt_cd      VARCHAR(10),            -- API: ntceInsttCd
    ntce_instt_nm      VARCHAR(200),           -- API: ntceInsttNm
    dminstt_cd         VARCHAR(10),            -- API: dminsttCd
    dminstt_nm         VARCHAR(200),           -- API: dminsttNm
    re_ntce_yn         BOOLEAN,                -- API: reNtceYn ('Y'/'N' 변환)
    intrbid_yn         BOOLEAN,                -- API: intrbidYn ('Y'/'N' 변환)
    -- 일정
    bid_ntce_dt        TIMESTAMP,              -- API: bidNtceDt
    bid_clse_dt        TIMESTAMP,              -- API: bidClseDt
    openg_dt           TIMESTAMP,              -- API: opengDt
    bid_qlfct_rgst_dt  TIMESTAMP,              -- API: bidQlfctRgstDt
    rgst_dt            TIMESTAMP,              -- API: rgstDt
    chg_dt             TIMESTAMP,              -- API: chgDt
    -- 금액
    presmpt_prce       BIGINT,                 -- API: presmptPrce
    bdgt_amt           BIGINT,                 -- API: asignBdgtAmt
    vat                BIGINT,                 -- API: VAT (추정)
    govsply_amt        BIGINT,                 -- API: govsplyAmt (추정)
    -- 방식
    cntrct_cncls_mthd_nm  VARCHAR(50),         -- API: cntrctCnclsMthdNm
    sucsfbid_mthd_cd      VARCHAR(20),         -- API: sucsfbidMthdCd
    sucsfbid_mthd_nm      VARCHAR(200),        -- API: sucsfbidMthdNm
    sucsfbid_lwlt_rate    NUMERIC(7,3),        -- API: sucsfbidLwltRate
    bid_methd_nm          VARCHAR(50),         -- API: bidMethdNm
    pq_eval_yn            BOOLEAN,             -- API: pqEvalYn (추정, 'Y'/'N' 변환)
    dsgnt_cmpt_yn         BOOLEAN,             -- API: dsgntCmptYn (추정, 'Y'/'N' 변환)
    bid_prtcpt_lmt_yn     BOOLEAN,             -- API: bidPrtcptLmtYn ('Y'/'N' 변환)
    rbid_permsn_yn        BOOLEAN,             -- API: rbidPermsnYn ('Y'/'N' 변환)
    -- 지역/공동수급
    cnstrtsite_rgn_nm          VARCHAR(100),   -- API: cnstrtsiteRgnNm (추정)
    rgn_duty_jntcontrct_yn     BOOLEAN,        -- API: rgnDutyJntcontrctYn (추정, 'Y'/'N' 변환)
    rgn_duty_jntcontrct_rt     NUMERIC(5,2),   -- API: rgnDutyJntcontrctRt (추정)
    jntcontrct_duty_rgns       JSONB,          -- 내부: API RgnNm1~3 묶음
    cmmn_spldmd_methd_cd       VARCHAR(20),    -- API: cmmnSpldmdMethdCd
    cmmn_spldmd_methd_nm       VARCHAR(100),   -- API: cmmnSpldmdMethdNm
    cmmn_spldmd_agrmnt_clse_dt TIMESTAMP,      -- API: cmmnSpldmdAgrmntClseDt (추정)
    -- 업종
    main_cnstty_nm             VARCHAR(100),   -- API: mainCnsttyNm
    main_cnstty_presmpt_prce   BIGINT,         -- API: mainCnsttyPresmptPrce (추정)
    indstryty_lmt_yn           BOOLEAN,        -- API: indstrytyLmtYn ('Y'/'N' 변환)
    cnstty_share_rates         JSONB,          -- 내부: API 문자열 "[토목공사업^100]" 파싱
    subsi_cnstty               JSONB,          -- 내부: API subsiCnsttyNm1~9 + EvlRt1~9 묶음
    -- 링크/참조
    bid_ntce_dtl_url   TEXT,                   -- API: bidNtceDtlUrl
    unty_ntce_no       VARCHAR(40),            -- API: untyNtceNo
    -- 자격요건 (v0.2, LLM 추출 — 전부 내부)
    company_size_limit    VARCHAR(20),  -- sme_only/small_only/no_large/no_conglomerate/none
    direct_production_req BOOLEAN,
    credit_rating_req     BOOLEAN,
    required_licenses     JSONB,        -- [{or_group, name_raw, code}]
    item_codes            JSONB,        -- [{type, code}]
    region_limit_type     VARCHAR(20),  -- hq_location/none
    region_limit_names    JSONB,
    region_basis          VARCHAR(200),
    performance_reqs      JSONB,        -- [{category, basis, value, unit, scope_raw}]
    capacity_reqs         JSONB,        -- [{name, value, unit}]
    personnel_reqs        JSONB,        -- [{field, grade, count}]
    required_certs        JSONB,
    award_cutline_type    VARCHAR(20),  -- score/rate/lowest_price (lowest_price면 value NULL 정상)
    award_cutline_value   NUMERIC(7,3),
    tech_weight           NUMERIC(5,2),
    price_weight          NUMERIC(5,2),
    joint_venture_allowed BOOLEAN,
    subcontract_allowed   BOOLEAN,
    -- 병합 운영 (내부)
    merge_conflicts       JSONB,
    extraction_evidence   JSONB,        -- {필드명: [{document_id, page, snippet}]}
    extraction_meta       JSONB,        -- {demoted_fields, dropped_evidence, not_found, model, extracted_at}
    is_human_verified     BOOLEAN DEFAULT FALSE,
    merged_at             TIMESTAMP,
    -- 파이프라인 (내부)
    expected_file_count   INT DEFAULT 0,  -- 처리 대상 파일 수(다운로드 성공+지원 형식만)
    raw_s3_key            TEXT,
    qual_status           VARCHAR(20) DEFAULT 'pending',  -- pending/merged/partial/failed
    created_at            TIMESTAMP DEFAULT NOW(),
    updated_at            TIMESTAMP DEFAULT NOW(),  -- UPDATE 시 코드에서 명시적 갱신 필요
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord)
);

CREATE UNIQUE INDEX idx_bid_table_bid_id ON bid_table (bid_id);
CREATE INDEX idx_bid_table_clse ON bid_table (bid_clse_dt);
CREATE INDEX idx_bid_table_cat_clse ON bid_table (bid_category, bid_clse_dt);