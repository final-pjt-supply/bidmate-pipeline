"""RDS PostgreSQL에 db/schema DDL 파일을 순서대로 적용한다.

Example:
    python db/apply_schema_to_rds.py --dry-run
    python db/apply_schema_to_rds.py --yes
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import find_dotenv, load_dotenv


DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
DEFAULT_PORT = "5432"
DEFAULT_DB_NAME = "bidmate-postgres"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply bid_table DDL files to RDS PostgreSQL.",
    )
    parser.add_argument(
        "--schema-dir",
        default=str(DEFAULT_SCHEMA_DIR),
        help="DDL SQL files directory. Defaults to db/schema.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print target and SQL files without applying DDL.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply DDL without interactive confirmation.",
    )
    return parser.parse_args()


def load_schema_files(schema_dir: Path) -> list[Path]:
    if not schema_dir.exists():
        raise FileNotFoundError(f"Schema directory not found: {schema_dir}")

    files = sorted(schema_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No .sql files found in: {schema_dir}")
    return files


def build_database_url() -> str:
    load_dotenv(find_dotenv(usecwd=True))

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    host = os.getenv("RDS_HOST")
    port = os.getenv("RDS_PORT") or DEFAULT_PORT
    db_name = os.getenv("RDS_DB_NAME") or DEFAULT_DB_NAME
    user = os.getenv("RDS_USER")
    password = os.getenv("RDS_PASSWORD")

    missing = [
        name
        for name, value in {
            "RDS_HOST": host,
            "RDS_USER": user,
            "RDS_PASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing RDS connection settings: "
            + ", ".join(missing)
            + ". Set DATABASE_URL or RDS_* values in .env.",
        )

    # DB 이름에 하이픈이 있어도 URL 경로에서는 인코딩된 문자열로 안전하게 전달된다.
    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{quote_plus(db_name)}"
    )


def describe_target(database_url: str) -> str:
    # 비밀번호는 로그에 남기지 않고, 어디에 적용되는지만 확인할 수 있게 마스킹한다.
    if "@" not in database_url:
        return database_url

    scheme_and_auth, host_and_db = database_url.rsplit("@", 1)
    scheme = scheme_and_auth.split("://", 1)[0]
    return f"{scheme}://***:***@{host_and_db}"


def confirm_apply(files: list[Path], target: str) -> None:
    print("DDL will be applied to:")
    print(f"  {target}")
    print("SQL files:")
    for file in files:
        print(f"  {file}")

    answer = input("Apply DDL now? Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        raise SystemExit("Canceled.")


def apply_schema(database_url: str, files: list[Path]) -> None:
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit(
            "psycopg is not installed. Run: python -m pip install -r requirement.txt",
        ) from exc

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            for file in files:
                print(f"applying={file}")
                cur.execute(file.read_text(encoding="utf-8"))
        conn.commit()


def main() -> None:
    args = parse_args()

    try:
        files = load_schema_files(Path(args.schema_dir))
        database_url = build_database_url()
    except (FileNotFoundError, ValueError) as exc:
        print(f"DDL configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    target = describe_target(database_url)
    if args.dry_run:
        print("DDL dry run")
        print(f"target={target}")
        for file in files:
            print(f"sql={file}")
        return

    if not args.yes:
        confirm_apply(files, target)

    apply_schema(database_url, files)
    print("DDL apply completed.")


if __name__ == "__main__":
    main()
