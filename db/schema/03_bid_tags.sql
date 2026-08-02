-- 공고 품목 태그(#97).
--
-- bid_table에 컬럼을 붙이지 않고 따로 둔다. bid_table은 파이프라인 팀 소유이고
-- 매칭 로직이 어떤 컬럼을 참조하는지 확정할 수 없어, 컬럼 추가는 영향 범위를
-- 예측하기 어렵다. 조회는 LEFT JOIN 한 번이면 된다.
--
-- 공고당 한 줄이다. 다중 태그는 쓰지 않는다 - 두 태그에 걸치는지 실제로 재봤고
-- 물품 1.5% / 용역 3.9%였는데 그마저 "스피커외 38종" 같은 묶음 발주였다
-- (clustering/10_multilabel_check.py).
--
-- 이력은 남기지 않는다. 태그가 바뀌는 건 대부분 추정 -> 정답 방향이라
-- 옛 값을 남길 값어치가 적다. 필요해지면 bid_tags_history를 따로 붙인다.

CREATE TABLE IF NOT EXISTS bid_tags (
    bid_id      varchar(100) PRIMARY KEY,
    tag         varchar(50)  NOT NULL,
    -- 무엇으로 정했는지. 정확도가 다르므로 통계에서 구분할 수 있어야 한다.
    --   cnstty_column      공사 main_cnstty_nm 컬럼 (발주기관 입력값)
    --   code               조달청 세부품명번호/업종코드 매핑 (정답)
    --   model_thng         물품 모델 (Macro F1 0.813)
    --   model_servc        용역 모델 (Macro F1 0.840)
    --   model_frgcpt       외자에 물품 모델 적용 (정확도 미측정 - 정답 라벨 없음)
    --   model_*_low        위와 같으나 신뢰도가 임계값 미만
    --   none               태그를 정할 근거가 없음(제목 없음, 공종명 없음)
    source      varchar(30)  NOT NULL,
    -- 모델 추론일 때만 값이 있다. 1위와 2위 태그의 점수차이며 확률이 아니다.
    -- 모델별로 척도가 달라 업종을 넘어 비교하면 안 된다.
    confidence  numeric(6,4),
    created_at  timestamptz  NOT NULL DEFAULT NOW(),
    updated_at  timestamptz  NOT NULL DEFAULT NOW()
);

-- 통계·필터가 태그로 집계하므로 태그 기준 조회가 주 패턴이다.
CREATE INDEX IF NOT EXISTS idx_bid_tags_tag ON bid_tags (tag);
-- 임계값을 바꿔 재판정할 때 source+confidence로 훑는다.
CREATE INDEX IF NOT EXISTS idx_bid_tags_source ON bid_tags (source);
