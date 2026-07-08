-- ============================================================
-- bid_table — 입찰공고 기본정보(v0.1) + LLM 자격요건 추출 결과(v0.2) 통합 테이블
--
-- 실행: 병합 파이프라인 배포 시, 아직 미실행.
--
-- v0.2 설계 배경(변경분 이력):
--   원래 v0.2는 ALTER 문 형태로 작성될 예정이었으나 별도 파일로 저장되기 전에
--   유실됐고, 이 테이블 자체가 아직 한 번도 생성/실행된 적이 없어(DB 미구축)
--   ALTER 대신 완성형 CREATE TABLE로 다시 정리했다. v0.2가 추가하는 내용은
--   pipeline/realtime/src/extractors/llm/schema.py의 LLM 추출 스키마(18필드 +
--   evidence + not_found)를 그대로 컬럼화한 것이며, 그중 아래 3가지는 실제
--   라이브 테스트에서 드러난 문제를 반영해 스키마와 함께 조정됐다:
--     1) award_cutline_type을 VARCHAR(20)으로 잡음 — 'lowest_price'(12자)를
--        enum에 추가하면서 기존에 더 짧게 잡았을 길이로는 INSERT가 실패하기 때문.
--     2) extraction_evidence의 실제 저장 형태를 필드별 그룹핑으로 명시.
--     3) extraction_meta(JSONB) 컬럼 신규 추가 — 추출 과정의 신뢰도 메타데이터.
--
-- TODO: v0.1(기본 공고정보) 컬럼 정의를 저장소/문서 어디서도 찾지 못했다.
--       db/schema.sql의 bid_qualifications가 참조하는 bid_announcements 테이블도
--       정의(CREATE TABLE)가 없는 상태다 — 아마 이 테이블이 그 후속/대체 설계로
--       보이지만 확신할 수 없어 자격요건(v0.2) 부분만 우선 만들어둔다.
--       기본 공고정보 컬럼(공고번호·공고명·발주기관·마감일시·추정가격 등)은
--       v0.1 설계가 확정되면 이 CREATE TABLE에 병합할 것.
-- ============================================================
CREATE TABLE bid_table (
    id                      SERIAL          PRIMARY KEY,

    -- ------------------------------------------------------------
    -- 식별자 (파이프라인 출력의 bid_id/document_id 그대로)
    -- ------------------------------------------------------------
    bid_id                  VARCHAR(40)     NOT NULL,
    document_id             VARCHAR(20)     NOT NULL,

    -- ------------------------------------------------------------
    -- TODO: v0.1 기본 공고정보 컬럼 (설계 확정 후 병합)
    -- 예상 후보: bid_ntce_no, bid_ntce_nm, ntce_instt_nm, bid_close_dt,
    --            opening_dt, presmpt_prce, indstryty_lmt_yn, pq_eval_yn 등
    -- ------------------------------------------------------------

    -- ------------------------------------------------------------
    -- v0.2 — LLM 자격요건 추출 결과 (extractors/llm/schema.py SCHEMA와 1:1 대응)
    -- ------------------------------------------------------------
    company_size_limit      VARCHAR(20),    -- 'sme_only'|'small_only'|'no_large'|'no_conglomerate'|'none'|NULL
    direct_production_req   BOOLEAN,        -- 직접생산확인증명서 요구 여부
    credit_rating_req       BOOLEAN,        -- 신용평가등급 요구 여부
    required_licenses       JSONB,          -- [{or_group, name_raw, code}] — or_group 같으면 OR, 다르면 AND
    item_codes              JSONB,          -- [{type, code}] — 세부품명번호/업종코드 등
    region_limit_type       VARCHAR(20),    -- 'hq_location'|'none'|NULL
    region_limit_names      JSONB,          -- ["<지역명>", ...]
    region_basis            TEXT,           -- 지역 제한 판단 기준(원문 그대로)
    performance_reqs        JSONB,          -- [{category, basis, value, unit, scope_raw}]
    capacity_reqs           JSONB,          -- [{name, value, unit}]
    personnel_reqs          JSONB,          -- [{field, grade, count}]
    required_certs          JSONB,          -- ["<인증명>", ...]

    -- award_cutline_type: 'score'(적격심사 점수제) | 'rate'(낙찰하한율 비율제) |
    -- 'lowest_price'(최저가낙찰 — 점수/비율 커트라인 자체가 없는 방식).
    -- VARCHAR(20): 'lowest_price'가 12자라 기존에 더 짧게 잡았으면 INSERT 실패함.
    award_cutline_type      VARCHAR(20),
    -- award_cutline_type이 'lowest_price'이면 정해진 수치가 없는 방식이므로
    -- award_cutline_value는 NULL이 정상(오류 아님).
    award_cutline_value     NUMERIC(6, 3),

    tech_weight             NUMERIC(5, 2),  -- 기술(이행능력) 평가 배점 비중
    price_weight            NUMERIC(5, 2),  -- 가격 평가 배점 비중
    joint_venture_allowed   BOOLEAN,        -- 공동수급(컨소시엄) 허용 여부
    subcontract_allowed     BOOLEAN,        -- 하도급 허용 여부

    not_found               JSONB,          -- ["<필드명>", ...] — 문서가 명시적으로 부재를 확인해준 필드

    -- extraction_evidence: LLM 원본 출력은 [{field, page, snippet}] 평면 리스트지만,
    -- 저장 시에는 필드명으로 그룹핑한 {필드명: [{document_id, page, snippet}]} 형태로
    -- 변환해서 넣는다(적재 파이프라인이 리스트 -> 필드별 그룹핑 변환을 담당해야 함).
    -- 예: {"joint_venture_allowed": [{"document_id": "doc01", "page": 2, "snippet": "공동계약 불가"}]}
    extraction_evidence     JSONB,

    -- extraction_meta: 추출 신뢰도/이력 메타데이터.
    --   demoted_fields  — evidence 그라운딩 실패로 값을 null로 강등한 필드명 목록
    --   dropped_evidence — 원본 문서에서 확인 안 돼 드랍된 evidence 항목 개수
    --   not_found        — 위 not_found 컬럼과 동일 값(메타 블록 안에도 중복 보관 —
    --                       감사/추적 시 extraction_meta 하나만 봐도 되게 하기 위함)
    --   model            — 추출에 사용한 LLM 모델 ID(예: "qwen/qwen3-next-80b-a3b-instruct")
    --   extracted_at     — 추출 완료 시각(ISO 8601)
    extraction_meta         JSONB,

    created_at              TIMESTAMP       DEFAULT NOW(),
    updated_at              TIMESTAMP       DEFAULT NOW(),

    CONSTRAINT uq_bid_table_bid_document UNIQUE (bid_id, document_id)
);

CREATE INDEX idx_bid_table_bid_id            ON bid_table (bid_id);
CREATE INDEX idx_bid_table_company_size      ON bid_table (company_size_limit);
CREATE INDEX idx_bid_table_award_cutline     ON bid_table (award_cutline_type);
