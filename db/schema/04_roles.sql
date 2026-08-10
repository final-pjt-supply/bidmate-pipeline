-- 데이터베이스 롤과 권한 (운영 RDS 실측 덤프, 2026-08-08 / PostgreSQL 18.3)
--
-- 왜 이 파일이 필요한가
--   롤과 GRANT를 운영 DB에 직접 적용해 왔다. 레포에 정의가 없어 DB를
--   재구축하면 조용히 사라지고, "누가 무엇을 읽을 수 있는가"를 코드로
--   확인할 방법도 없었다. 01~03(테이블)과 같은 자리에 둔다.
--
-- 적용
--   db/apply_schema_to_rds.py 가 db/schema/*.sql 을 파일명 순서로 적용한다.
--   01~03 이후에 실행돼야 GRANT 대상이 존재한다.
--
-- 비밀번호는 이 파일에 넣지 않는다
--   CREATE ROLE 은 비밀번호 없이 만들고, 실제 값은 배포 시 별도로 설정한다.
--     ALTER ROLE dev_ro     WITH PASSWORD '...';
--     ALTER ROLE grafana_ro WITH PASSWORD '...';
--   grafana_ro 비밀번호는 Grafana 컨테이너 환경변수 POSTGRES_RO_PASSWORD 로
--   주입된다(bidmate-frontend/deploy/grafana/provisioning/datasources/postgres.yaml).
--
-- ── 설계 원칙 ────────────────────────────────────────────────────
--   1. 쓰기 가능한 롤은 bidmaster 하나뿐. 조회용 자격증명이 유출돼도
--      데이터가 변조되지 않는다.
--   2. 읽기 롤은 용도별로 범위가 다르다. dev_ro 는 전체, grafana_ro 는
--      대시보드에 필요한 것만.
--   3. grafana_ro 가 개인정보 테이블을 읽어야 할 때는 테이블 전체가 아니라
--      **컬럼 단위**로 연다. companies 8개 컬럼 중 집계에 필요한 3개만 주고
--      name·biz_reg_no·email·cognito_sub 는 주지 않는다.
--   4. 읽기 롤에는 스키마 USAGE 만(CREATE 없음).
--   5. dev_ro 에만 기본 권한을 걸어 신규 테이블 GRANT 누락을 막는다.
--      grafana_ro 는 일부러 제외 — 새 테이블이 자동으로 열리면 안 된다.

BEGIN;

-- ── 롤 ─────────────────────────────────────────────────────────
-- 이미 있으면 넘어간다. 비밀번호는 건드리지 않는다.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dev_ro') THEN
        CREATE ROLE dev_ro LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        CREATE ROLE grafana_ro LOGIN;
    END IF;
END
$$;

-- 조회 전용 속성은 CREATE ROLE 의 기본값이라 따로 걸지 않는다
-- (NOSUPERUSER · NOCREATEDB · NOCREATEROLE · NOREPLICATION).
--
-- ALTER ROLE ... NOSUPERUSER 로 못박고 싶어도 RDS 에서는 안 된다 —
-- bidmaster 는 rds_superuser 멤버일 뿐 SUPERUSER 속성이 없어서
-- "permission denied to alter role / Only roles with the SUPERUSER
--  attribute may change the SUPERUSER attribute" 로 거부된다.
-- 실측 확인(2026-08-08): dev_ro·grafana_ro 모두 위 4개 속성이 모두 꺼져 있다.

-- ── 스키마 ─────────────────────────────────────────────────────
-- USAGE 만. CREATE 를 주면 읽기 롤이 자기 테이블을 만들 수 있게 된다.

GRANT USAGE ON SCHEMA public TO dev_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;

REVOKE CREATE ON SCHEMA public FROM dev_ro;
REVOKE CREATE ON SCHEMA public FROM grafana_ro;

-- ── dev_ro : 전체 읽기 ──────────────────────────────────────────
-- 개발자 조회용. 실측 기준 테이블 37개 + matview 2개(bid_stats,
-- institution_parent)에 SELECT.

GRANT SELECT ON ALL TABLES IN SCHEMA public TO dev_ro;

-- 앞으로 bidmaster 가 만드는 테이블·뷰·matview 에도 자동 부여.
-- 이 한 줄이 "신규 테이블에 GRANT 를 깜빡하는" 사고를 구조적으로 막는다.
ALTER DEFAULT PRIVILEGES FOR ROLE bidmaster IN SCHEMA public
    GRANT SELECT ON TABLES TO dev_ro;

-- ── grafana_ro : 대시보드에 필요한 만큼만 ───────────────────────
--
-- 테이블 단위 — 개인정보가 없는 공고·매칭 데이터.
--   bid_table     72개 컬럼 전부 (공고 원문·자격요건)
--   match_results 11개 컬럼 전부 (회사×공고 판정 결과. company_id 는 있으나
--                                 회사를 식별할 이름·연락처는 없다)

GRANT SELECT ON bid_table     TO grafana_ro;
GRANT SELECT ON match_results TO grafana_ro;

-- 컬럼 단위 — companies 는 개인정보 테이블이라 통째로 열지 않는다.
--
--   전체 8개 컬럼: id, name, biz_reg_no, email, cognito_sub,
--                  created_at, updated_at, deleted_at
--   그중 3개만 부여: id, created_at, deleted_at
--   주지 않는 것:    name(회사명) · biz_reg_no(사업자등록번호) ·
--                    email · cognito_sub(인증 식별자) · updated_at
--
-- 대시보드(bidmate-frontend/deploy/grafana/dashboards/service.json,
-- "BidFriend 회원 지표" 5패널)가 필요한 건 가입·탈퇴 건수 집계뿐이고
-- 실제로 이 3개 컬럼만 쓴다. 컬럼 권한이면 대시보드 쿼리를 고치지 않고도
-- (FROM companies 그대로) 개인정보 컬럼 접근만 차단된다.
--
-- 뷰로 감싸는 방법도 있지만 컬럼 권한이 낫다 — 뷰는 원본을 직접 조회하는
-- 우회 경로가 남지만, 컬럼 권한은 SELECT name FROM companies 를 그 자리에서
-- 막는다.

GRANT SELECT (id, created_at, deleted_at) ON companies TO grafana_ro;

COMMIT;


-- ─────────────────────────────────────────────────────────────────
-- 확인 쿼리
-- ─────────────────────────────────────────────────────────────────
--
-- 롤별 테이블 단위 권한 요약:
--   SELECT grantee, privilege_type, count(*)
--     FROM information_schema.role_table_grants
--    WHERE grantee IN ('bidmaster','dev_ro','grafana_ro')
--    GROUP BY 1,2 ORDER BY 1,2;
--
-- grafana_ro 가 읽을 수 있는 컬럼 전부 (컬럼 권한은 위 쿼리에 안 잡힌다):
--   SELECT table_name, string_agg(column_name, ', ' ORDER BY column_name)
--     FROM information_schema.column_privileges
--    WHERE grantee='grafana_ro' AND privilege_type='SELECT'
--    GROUP BY 1 ORDER BY 1;
--
-- 개인정보 컬럼이 정말 막혔는지 (모두 false 여야 한다):
--   SELECT has_column_privilege('grafana_ro','companies',c,'SELECT')
--     FROM unnest(ARRAY['name','biz_reg_no','email','cognito_sub']) c;
--
-- matview 는 information_schema 에 안 나온다. 따로 본다:
--   SELECT cl.relname, cl.relacl FROM pg_class cl
--     JOIN pg_namespace n ON n.oid=cl.relnamespace
--    WHERE n.nspname='public' AND cl.relkind='m';
--
-- 기본 권한:
--   SELECT pg_get_userbyid(defaclrole), defaclobjtype, defaclacl
--     FROM pg_default_acl;


-- ─────────────────────────────────────────────────────────────────
-- 주의 — has_table_privilege 만 보면 컬럼 권한을 놓친다
-- ─────────────────────────────────────────────────────────────────
--
-- has_table_privilege('grafana_ro','companies','SELECT') 는 false 를 낸다.
-- 테이블 단위 권한이 없기 때문이다. 그런데 실제로는
-- SELECT count(*) FROM companies 가 동작한다 — 컬럼 권한이 하나라도 있으면
-- count(*) 는 허용되기 때문이다.
--
-- 권한 점검 시 information_schema.role_table_grants(테이블 단위)만 보면
-- "권한 없음"으로 오판한다. column_privileges 를 함께 봐야 한다.
