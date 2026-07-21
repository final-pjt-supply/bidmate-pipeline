# Alembic — BidMate API 서버 마이그레이션

## 소유권 모델 (중요)

이 Alembic은 **API 서버가 소유한 테이블만** 관리한다.

| 테이블 | 소유 | 스키마 관리 방식 |
| --- | --- | --- |
| `bid_table`, `bid_attachments` | 파이프라인 팀 | `db/schema/*.sql` (SSOT) + `apply_schema_to_rds.py` + 런타임 컬럼 가드 |
| `company`, `match_results`, ... (앞으로) | API 서버 팀 | **이 Alembic** |

`env.py`의 `include_object`가 `bid_table`/`bid_attachments`를 autogenerate에서 제외한다.
이유: 우리 `Bid` ORM은 그 테이블 컬럼을 일부만 매핑(읽기 전용)했으므로, 관리 대상에
넣으면 autogenerate가 매핑 안 한 컬럼을 DROP하려 든다. 파이프라인 테이블은 절대
Alembic으로 변경하지 않는다.

## 접속 설정

DB URL은 `app.config`(리포 루트 `.env` / 환경변수)에서 읽는다. `alembic.ini`에
접속정보를 하드코딩하지 않는다.

로컬 docker(db/docker-compose.yml) 대상 실행 예:
```
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=bidding_agent \
POSTGRES_USER=bidding_agent POSTGRES_PASSWORD=bidding_agent POSTGRES_SSLMODE= \
python -m alembic upgrade head
```
(이 개발 PC는 호스트 Postgres가 5432를 점유해 docker를 5434로 띄웠다 — 그 경우 PORT=5434.)

## 자주 쓰는 명령
```
python -m alembic current                         # 현재 리비전
python -m alembic history                         # 리비전 이력
python -m alembic revision -m "설명"              # 빈 리비전 생성(수동 작성)
python -m alembic revision --autogenerate -m "설명"  # 모델 diff로 자동 생성
python -m alembic upgrade head                     # 최신까지 적용
python -m alembic downgrade -1                      # 한 단계 되돌림
```

## ⚠ 운영 RDS baseline stamp (팀 협의 후 1회)

운영 RDS에는 이미 테이블이 있으므로 `upgrade`로 재생성하면 안 된다. 최초 1회만
baseline을 '적용됨'으로 표시한다(실제 DDL 실행 없음):
```
# 터널로 운영 RDS에 연결된 상태에서(팀 협의 후):
python -m alembic stamp 0001_baseline
```
이는 운영 DB에 `alembic_version` 테이블을 만드는 쓰기다. 파이프라인 팀과 협의 후
수행한다. 이후 새 테이블 마이그레이션은 `0001_baseline` 위에 쌓인다.
