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

It also allows a DELIBERATE REDEFINITION — a later migration that `DROP INDEX`es the name
before recreating it. That is not a silent no-op: the DROP frees the name, so the CREATE
really runs. Correcting an index definition has to be expressible, and forbidding it outright
is what pushed 117's repair (121) into inventing a second name for the same concept. What
stays banned is the original bug: a second, DIFFERENT definition that just assumes
`IF NOT EXISTS` will do something.

Migration 146 is the case this allowance exists for: 143 shipped
`uq_whale_trades_group_ticker_action_date` as a PARTIAL index, which `ON CONFLICT (cols)`
cannot infer, so every whale-trade upsert failed 42P10 in production. 146 drops the predicate
under the same name.
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

# `DROP INDEX [CONCURRENTLY] [IF EXISTS] [schema.]name`
_DROP_INDEX = re.compile(
    r"DROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?"
    r"(?:\w+\.)?(?P<name>\w+)",
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


def _redefinitions_backed_by_a_drop() -> dict[str, set[str]]:
    """index name -> {files that DROP it before recreating it}.

    Order matters: the DROP has to come BEFORE the CREATE in the same file, or the name is
    still taken when the CREATE runs and the `IF NOT EXISTS` no-op is back.
    """
    backed: dict[str, set[str]] = defaultdict(set)
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = _strip_sql_comments(path.read_text())
        drops = {m.group("name").lower(): m.start() for m in _DROP_INDEX.finditer(sql)}
        for m in _CREATE_INDEX.finditer(sql):
            name = m.group("name").lower()
            if name in drops and drops[name] < m.start():
                backed[name].add(path.name)
    return backed


def test_no_index_name_is_bound_to_two_different_definitions():
    """The silent no-op. See the module docstring."""
    backed = _redefinitions_backed_by_a_drop()
    offenders = []
    for name, decls in sorted(_declarations().items()):
        definitions = {d for d, _f in decls}
        if len(definitions) > 1:
            # A deliberate redefinition frees the name first. Every file beyond the ORIGINAL
            # declaration must do so — one that does not is the silent no-op this test exists
            # for, even when a sibling file happens to drop correctly.
            files = sorted({f for _d, f in decls})
            unbacked = [f for f in files[1:] if f not in backed.get(name, set())]
            if not unbacked:
                continue
            where = ", ".join(files)
            detail = " | ".join(sorted(definitions))
            offenders.append(
                f"{name}: {len(definitions)} definitions in [{where}] -> {detail}"
                f"  (no DROP INDEX before the CREATE in: {', '.join(unbacked)})"
            )
    assert not offenders, (
        "these index names are declared with DIFFERENT definitions. Because every CREATE INDEX "
        "is `IF NOT EXISTS`, only the FIRST one to run exists — the others are silent no-ops "
        "that apply cleanly and create nothing. To redefine an index on purpose, `DROP INDEX "
        "IF EXISTS` it FIRST in the same file (see migration 146):\n  " + "\n  ".join(offenders)
    )


def test_a_redefinition_without_a_drop_is_still_caught():
    """Anti-vacuity for the allowance above: it must not have disabled the whole test.

    Builds the 117-shaped bug in memory — the same name, two different definitions, no DROP —
    and asserts the offender logic still flags it.
    """
    decls = {"idx_fake": {("unique public.t (a, b)", "900_a.sql"),
                          ("public.t (b)", "901_b.sql")}}
    backed: dict[str, set[str]] = {}
    offenders = []
    for name, d in decls.items():
        definitions = {x for x, _f in d}
        files = sorted({f for _x, f in d})
        unbacked = [f for f in files[1:] if f not in backed.get(name, set())]
        if len(definitions) > 1 and unbacked:
            offenders.append(name)
    assert offenders == ["idx_fake"]


def test_the_drop_must_precede_the_create():
    """A DROP placed AFTER the CREATE frees nothing — the CREATE still no-ops."""
    import tempfile

    sql_ok = ("DROP INDEX IF EXISTS public.idx_z;\n"
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_z ON public.t (a);\n")
    sql_bad = ("CREATE UNIQUE INDEX IF NOT EXISTS idx_z ON public.t (a);\n"
               "DROP INDEX IF EXISTS public.idx_z;\n")
    for sql, expected in ((sql_ok, True), (sql_bad, False)):
        drops = {m.group("name").lower(): m.start() for m in _DROP_INDEX.finditer(sql)}
        create = _CREATE_INDEX.search(sql)
        assert create is not None
        got = "idx_z" in drops and drops["idx_z"] < create.start()
        assert got is expected, f"ordering check wrong for:\n{sql}"


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
