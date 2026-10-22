"""Every column `IndustryDossier.to_db_row()` writes exists in the database — in the schema
snapshot, or added by a migration in `database/migrations/` (2026-10-22).

A `to_db_row` key with no column fails EVERY industry_dossier upsert (PostgREST refuses an
unknown column), and the quarterly recompute logs that at ERROR and settles with
`rows_upserted 0` — the stale rows stay, and nothing else notices. This catches the code
half landing without its migration at review time.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.industry_dossier_service import IndustryDossier

_BACKEND = Path(__file__).resolve().parents[1]
_SNAPSHOT = _BACKEND / "database" / "schema_snapshot.sql"
_MIGRATIONS = _BACKEND / "database" / "migrations"
_MIGRATION_203 = _MIGRATIONS / "203_industry_dossier_growth_window_and_concentration_basis.sql"


def _snapshot_columns() -> set:
    text = _SNAPSHOT.read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE public\.industry_dossier \((.*?)\n\);", text, re.S)
    assert m, "industry_dossier not found in schema_snapshot.sql"
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.upper().startswith("CONSTRAINT"):
            continue
        cols.add(line.split()[0])
    return cols


def _migration_added_columns() -> set:
    added = set()
    pattern = re.compile(
        r"ALTER\s+TABLE\s+(?:public\.)?industry_dossier\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)",
        re.I,
    )
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))   # comments never count
        added.update(pattern.findall(sql))
    return added


def test_every_dossier_row_key_is_a_column_or_a_migration_add():
    row = IndustryDossier(current_tam=1.0, future_tam=1.0, current_year="2024", future_year="2029",
                          source_label="x").to_db_row()
    known = _snapshot_columns() | _migration_added_columns()
    missing = sorted(set(row) - known)
    assert not missing, (
        f"to_db_row writes {missing}, which neither schema_snapshot.sql nor any migration adds — "
        "every industry_dossier upsert would fail"
    )


def test_the_guard_sees_the_203_columns():
    """Not vacuous: the two 2026-10-22 columns come ONLY from migration 203 until the owner
    applies it and the snapshot is re-dumped."""
    added = _migration_added_columns()
    assert {"cagr_start_year", "concentration_basis"} <= added | _snapshot_columns()
    assert "cagr_start_year" in added and "concentration_basis" in added


def test_migration_203_is_idempotent_text():
    sql = re.sub(r"--[^\n]*", "", _MIGRATION_203.read_text(encoding="utf-8"))
    adds = re.findall(r"ADD\s+COLUMN\s+(IF\s+NOT\s+EXISTS\s+)?(\w+)", sql, re.I)
    assert adds and all(guard for guard, _ in adds), "every ADD COLUMN must be IF NOT EXISTS"
    updates = re.findall(r"UPDATE\s+public\.industry_dossier\s+SET\s+(\w+)\s*=.*?WHERE\s+(\w+)\s+IS\s+NULL",
                         sql, re.I | re.S)
    assert len(updates) == 2 and all(col == where for col, where in updates), \
        "each backfill must touch only rows where its own column IS NULL"
    assert "DROP " not in sql.upper() and "DELETE " not in sql.upper()
