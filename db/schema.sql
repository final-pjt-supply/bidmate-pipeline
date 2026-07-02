-- ============================================================
-- 입찰 자격요건 테이블 (PDF LLM 추출)
-- ============================================================
CREATE TABLE bid_qualifications (
    id                      SERIAL          PRIMARY KEY,
    bid_ntce_no             VARCHAR(40)     NOT NULL REFERENCES bid_announcements(bid_ntce_no),

    -- 기업 규모 제한
    company_size_limit      VARCHAR(50),    -- '중소기업', '중견기업이하', '대기업제외', '제한없음'

    -- 업종/면허
    required_licenses       JSONB,          -- ["전기공사업", "소프트웨어사업자"]

    -- 기술인력
    required_personnel      JSONB,          -- [{"field": "철도신호", "grade": "중급", "count": 1}]

    -- 실적 기준
    perf_min_amt            BIGINT,         -- 최소 실적 금액 (원)
    perf_max_amt            BIGINT,         -- 최대 실적 금액 (원, 상한 있는 경우)
    perf_period_years       INTEGER,        -- 실적 인정 기간 (년)
    perf_type               VARCHAR(200),   -- 실적 유형 ("일반철도신호공사" 등)

    -- 지역 제한
    region_limit            VARCHAR(100),   -- "전북특별자치도"
    region_min_days         INTEGER,        -- 본점 소재 최소 기간 (일)

    -- 커트라인
    eval_cutline            NUMERIC(5, 2),  -- 적격심사 커트라인 점수

    -- 기타
    work_period             VARCHAR(100),   -- "착공일로부터 40일"
    warranty_rate           NUMERIC(5, 2),  -- 하자보증률 (%)
    warranty_months         INTEGER,        -- 하자기간 (월)
    required_certs          JSONB,          -- ["GS인증", "ISO 27001"]
    other_requirements      JSONB,          -- 기타 요건 자유형식

    created_at              TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uq_bid_qual UNIQUE (bid_ntce_no)
);

-- ============================================================
-- 사내 기본 정보 (단일 레코드)
-- ============================================================
CREATE TABLE company_profile (
    id              SERIAL          PRIMARY KEY,
    company_name    VARCHAR(200)    NOT NULL,
    company_size    VARCHAR(50)     NOT NULL,   -- '중소기업', '중견기업', '대기업'
    hq_region       VARCHAR(100)    NOT NULL,   -- 본점 소재지 ("서울특별시")
    hq_since        DATE            NOT NULL,   -- 현 소재지 등록일 (지역 제한 90일 계산용)
    updated_at      TIMESTAMP       DEFAULT NOW()
);

-- ============================================================
-- 사내 보유 면허/업종
-- ============================================================
CREATE TABLE company_licenses (
    id              SERIAL          PRIMARY KEY,
    license_name    VARCHAR(200)    NOT NULL,   -- "전기공사업", "소프트웨어사업자"
    license_no      VARCHAR(100),               -- 등록번호
    issued_date     DATE,
    expired_date    DATE,                       -- NULL이면 영구
    is_active       BOOLEAN         DEFAULT TRUE
);

-- ============================================================
-- 사내 보유 인증
-- ============================================================
CREATE TABLE company_certifications (
    id              SERIAL          PRIMARY KEY,
    cert_name       VARCHAR(200)    NOT NULL,   -- "GS인증", "ISO 27001"
    cert_no         VARCHAR(100),
    issued_date     DATE,
    expired_date    DATE,
    is_active       BOOLEAN         DEFAULT TRUE
);

-- ============================================================
-- 사내 보유 실적
-- ============================================================
CREATE TABLE company_performances (
    id                  SERIAL          PRIMARY KEY,
    project_name        VARCHAR(500)    NOT NULL,   -- 사업명
    client_name         VARCHAR(200),               -- 발주처
    contract_amount     BIGINT          NOT NULL,   -- 계약금액 (원)
    project_type        VARCHAR(200),               -- "일반철도신호공사", "소프트웨어개발"
    contract_date       DATE,                       -- 계약일
    completion_date     DATE,                       -- 준공/완료일
    is_joint_contract   BOOLEAN         DEFAULT FALSE,
    joint_share_rate    NUMERIC(5, 2)               -- 공동수급 지분율 (%)
);

-- ============================================================
-- 사내 보유 기술인력
-- ============================================================
CREATE TABLE company_personnel (
    id              SERIAL          PRIMARY KEY,
    name            VARCHAR(100)    NOT NULL,
    field           VARCHAR(200)    NOT NULL,   -- "철도신호", "소프트웨어"
    grade           VARCHAR(50)     NOT NULL,   -- "고급", "중급", "초급"
    cert_no         VARCHAR(100),               -- 자격증 번호
    cert_issued_date DATE,
    is_active       BOOLEAN         DEFAULT TRUE
);

-- ============================================================
-- 인덱스
-- ============================================================
CREATE INDEX idx_bid_ann_close_dt     ON bid_announcements (bid_close_dt);
CREATE INDEX idx_bid_ann_presmpt_prce ON bid_announcements (presmpt_prce);
CREATE INDEX idx_bid_ann_indstryty    ON bid_announcements (indstryty_lmt_yn);
CREATE INDEX idx_bid_ann_pq           ON bid_announcements (pq_eval_yn);
CREATE INDEX idx_bid_qual_company_size ON bid_qualifications (company_size_limit);
CREATE INDEX idx_bid_qual_region      ON bid_qualifications (region_limit);
CREATE INDEX idx_perf_completion      ON company_performances (completion_date);
CREATE INDEX idx_perf_type            ON company_performances (project_type);
