"""Migration hygiene: an index name must mean exactly one thing.

Every `CREATE INDEX` in `backend/database/migrations/` is written `IF NOT EXISTS`, because
migrations must be safe to re-run. That guard has a failure mode nobody sees:

    CREATE UNIQUE INDEX IF NOT EXISTS idx_x ON t (a, b);   -- created
    CREATE        INDEX IF NOT EXISTS idx_x ON t (b);      -- NAME TAKEN -> silent no-op

The second statement does not error, does not warn, and leaves nothing behind to notice. The
migration applies cleanly, reports success, and the index simply does not exist. **Idempotency
guards HIDE name collisions rather than surfacing them.**

This shipped: migration 117 declared both the unique `(environment, transaction_id)` dedup key
and the `(transaction_id)` lookup index as `idx_credit_purchases_txn`, so the lookup index that
`IAPService.user_id_for_transaction` needs — on the App Store REFUND webhook path — was never
created. Caught only by querying `pg_indexes` on the live database after the fact. Migration 121
repairs it; this test is what makes the next one fail in CI instead.

Deliberately allows the SAME name with the SAME definition across files: re-declaring an
identical index is a legitimate idempotency pattern. Only a name bound to two different
definitions is a bug.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"

# `CREATE [UNIQUE] INDEX [CONCURRENTLY] [IF NOT EXISTS] name ON [ONLY] table (cols) [WHERE ...]`
_CREATE_INDEX = re.compile(
    r"CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>\w+)\s+ON\s+(?:ONLY\s+)?(?P<rest>[^;]+);",
    re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    """Drop `-- …` lines and trailing comments.

    Not optional: several migration headers QUOTE their own DDL to explain a fix (117's header
    quotes the very statements below it), and a naive scan reads documentation as declarations —
    which would make this test fail on prose.
    """
    out = []
    for raw in sql.splitlines():
        if raw.strip().startswith("--"):
            continue
        out.append(re.sub(r"--.*$", "", raw))
    return "\n".join(out)


def _declarations() -> dict[str, set[tuple[str, str]]]:
    """index name -> {(normalised definition, source file)}."""
    found: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        for m in _CREATE_INDEX.finditer(_strip_sql_comments(path.read_text())):
            definition = re.sub(r"\s+", " ", m.group("rest")).strip().lower()
            if m.group("unique"):
                definition = "unique " + definition
            found[m.group("name").lower()].add((definition, path.name))
    return found


def test_the_scanner_actually_finds_indexes():
    """Anti-vacuity. If the regex rots, every assertion below passes on an empty dict — the
    exact way a source-scan guard goes quietly blind."""
    decls = _declarations()
    assert len(decls) > 50, f"only {len(decls)} indexes found — the CREATE INDEX regex has rotted"
    assert "idx_credit_purchases_env_txn" in decls
    assert "idx_credit_purchases_txn_lookup" in decls


def test_no_index_name_is_bound_to_two_different_definitions():
    """The silent no-op. See the module docstring."""
    offenders = []
    for name, decls in sorted(_declarations().items()):
        definitions = {d for d, _f in decls}
        if len(definitions) > 1:
            where = ", ".join(sorted(f"{f}" for _d, f in decls))
            detail = " | ".join(sorted(definitions))
            offenders.append(f"{name}: {len(definitions)} definitions in [{where}] -> {detail}")
    assert not offenders, (
        "these index names are declared with DIFFERENT definitions. Because every CREATE INDEX "
        "is `IF NOT EXISTS`, only the FIRST one to run exists — the others are silent no-ops "
        "that apply cleanly and create nothing:\n  " + "\n  ".join(offenders)
    )


def test_the_collision_that_shipped_is_fixed():
    """Pins the specific pair from migration 117, so the fix cannot be undone by a merge."""
    decls = _declarations()
    env_txn = {d for d, _f in decls["idx_credit_purchases_env_txn"]}
    lookup = {d for d, _f in decls["idx_credit_purchases_txn_lookup"]}
    assert any("unique" in d and "environment" in d for d in env_txn), \
        "the dedup key must stay UNIQUE on (environment, transaction_id)"
    assert all("unique" not in d for d in lookup), \
        "the lookup index must NOT be unique — a user may buy several packs"
    assert not (env_txn & lookup), "the two indexes are the same declaration again"
