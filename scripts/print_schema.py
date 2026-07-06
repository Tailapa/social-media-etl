#!/usr/bin/env python
"""Print the database schema documentation used to ground the AI assistant's
SQL generation prompt (`app.database.SCHEMA_DESCRIPTION`), and the list of
tables known to the SQL safety validator (`app.models.db.orm.KNOWN_TABLES`).

Useful when auditing that `migrations/*.sql` and the AI's schema grounding
haven't drifted apart.

Usage: python scripts/print_schema.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SCHEMA_DESCRIPTION
from app.models.db.orm import KNOWN_TABLES


def main() -> None:
    print(SCHEMA_DESCRIPTION)
    print()
    print(f"Known tables ({len(KNOWN_TABLES)}):")
    for table in sorted(KNOWN_TABLES):
        print(f"  - {table}")


if __name__ == "__main__":
    main()
