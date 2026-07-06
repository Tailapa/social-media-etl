#!/usr/bin/env python
"""Apply every `migrations/*.sql` file, in filename order, against
`SUPABASE_DB_URL`.

This connects directly via SQLAlchemy (NOT through
`app.database.sql_engine.execute_readonly_sql`, which deliberately refuses
DDL/mutating statements — that guard exists for AI-generated SQL, not admin
tooling like this script).

Usage: python scripts/run_migrations.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main() -> None:
    settings = get_settings()
    if not settings.supabase_db_url:
        raise SystemExit(
            "SUPABASE_DB_URL is not configured — set it in .env before running migrations."
        )

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        raise SystemExit(f"No migration files found in {MIGRATIONS_DIR}")

    engine = create_engine(settings.supabase_db_url)
    with engine.begin() as conn:
        for path in migration_files:
            logger.info("Applying migration", file=path.name)
            conn.execute(text(path.read_text(encoding="utf-8")))
            print(f"applied: {path.name}")

    print(f"Applied {len(migration_files)} migration file(s) successfully.")


if __name__ == "__main__":
    main()
