-- 규약: 모든 TIMESTAMP는 KST naive. boolean류는 API 'Y'/'N' 변환 필요.
CREATE TABLE bid_table (
    -- 식별
    bid_ntce_no        VARCHAR(40) NOT NULL,
    bid_ntce_ord       VARCHAR(10) NOT NULL,
    bid_id             VARCHAR(60) GENERATED ALWAYS AS (bid_ntce_no || '_' || bid_ntce_ord) STORED,
                       -- 조회용 파생 컬럼. split 금지(성분은 bid_ntce_no/ord 사용)
    bid_category       VARCHAR(10) NOT NULL,  -- thng/servc/cnstwk/frgcpt
    -- 기본
    bid_ntce_nm        TEXT,
    ntce_instt_cd      VARCHAR(10),
    ntce_instt_nm      VARCHAR(200),
    dminstt_cd         VARCHAR(10),
    dminstt_nm         VARCHAR(200),
    re_ntce_yn         BOOLEAN,
    intrbid_yn         BOOLEAN,
    -- 일정
    bid_ntce_dt        TIMESTAMP,
    bid_clse_dt        TIMESTAMP,
    openg_dt           TIMESTAMP,
    bid_qlfct_rgst_dt  TIMESTAMP,
    rgst_dt            TIMESTAMP,
    chg_dt             TIMESTAMP,
    -- 금액
    presmpt_prce       BIGINT,
    asignBdgtAmt       BIGINT,
    vat                BIGINT,
    govsply_amt        BIGINT,
    -- 방식
    cntrct_cncls_mthd_nm  VARCHAR(50),
    sucsfbid_mthd_cd      VARCHAR(20),
    sucsfbid_mthd_nm      VARCHAR(200),
    sucsfbid_lwlt_rate    NUMERIC(7,3),
    bid_methd_nm          VARCHAR(50),
    pq_eval_yn            BOOLEAN,
    dsgnt_cmpt_yn         BOOLEAN,
    bid_prtcpt_lmt_yn     BOOLEAN,
    rbid_permsn_yn        BOOLEAN,
    -- 지역/공동수급
    cnstrtsite_rgn_nm          VARCHAR(100),
    rgn_duty_jntcontrct_yn     BOOLEAN,
    rgn_duty_jntcontrct_rt     NUMERIC(5,2),
    jntcontrct_duty_rgns       JSONB,
    cmmn_spldmd_methd_cd       VARCHAR(20),
    cmmn_spldmd_methd_nm       VARCHAR(100),
    cmmn_spldmd_agrmnt_clse_dt TIMESTAMP,
    -- 업종
    main_cnstty_nm             VARCHAR(100),
    main_cnstty_presmpt_prce   BIGINT,
    indstryty_lmt_yn           BOOLEAN,
    cnstty_share_rates         JSONB,
    subsi_cnstty               JSONB,
    -- 링크/참조
    bid_ntce_dtl_url   TEXT,
    unty_ntce_no       VARCHAR(40),
    -- 자격요건 (v0.2, LLM 추출)
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
    -- 병합 운영
    merge_conflicts       JSONB,
    extraction_evidence   JSONB,        -- {필드명: [{document_id, page, snippet}]}
    extraction_meta       JSONB,        -- {demoted_fields, dropped_evidence, not_found, model, extracted_at}
    is_human_verified     BOOLEAN DEFAULT FALSE,
    merged_at             TIMESTAMP,
    -- 파이프라인
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
