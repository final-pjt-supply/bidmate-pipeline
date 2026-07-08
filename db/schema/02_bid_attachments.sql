-- 네이밍 규칙: API camelCase → snake_case 기계적 변환. API 원본명은 각 컬럼 주석 참조.
CREATE TABLE bid_attachments (
    file_id       VARCHAR(80) PRIMARY KEY,
                  -- 내부: {bid_id}_doc{seq:02d}. 불투명 식별자 — 절대 split하지 말 것
    bid_ntce_no   VARCHAR(40) NOT NULL,   -- API: bidNtceNo
    bid_ntce_ord  VARCHAR(10) NOT NULL,   -- API: bidNtceOrd
    bid_id        VARCHAR(60) GENERATED ALWAYS AS (bid_ntce_no || '_' || bid_ntce_ord) STORED,
                  -- 내부: 조회 편의용 파생 컬럼. INSERT 시 직접 넣지 않음(자동 계산). split 금지
    file_seq      INT NOT NULL,           -- 내부: 공고 내 파일 순번(수집 시 부여)
    file_url      TEXT,                   -- API: ntceSpecDocUrl1~10 (추정) — 번호 필드에서 분리
    s3_key        TEXT,                   -- 내부: raw 단계 key ({biz_div}/... 파티션 구조)
    status        VARCHAR(30) DEFAULT 'pending',
                  -- 내부: 병합 시점 S3 대조 스냅샷 (collected/text_extracted/llm_extracted/failed)
    updated_at    TIMESTAMP DEFAULT NOW(),  -- 내부: 마지막 병합 시각. KST naive
    UNIQUE (bid_ntce_no, bid_ntce_ord, file_seq),
    FOREIGN KEY (bid_ntce_no, bid_ntce_ord)
        REFERENCES bid_table (bid_ntce_no, bid_ntce_ord)
);

CREATE INDEX idx_att_bid ON bid_attachments (bid_ntce_no, bid_ntce_ord);
CREATE INDEX idx_att_bid_id ON bid_attachments (bid_id);