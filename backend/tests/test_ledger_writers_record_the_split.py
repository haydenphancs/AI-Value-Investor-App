"""Every writer of `credit_transactions` records the two-pool split (F11-5).

`refund_credits` reverses the RECORDED split of the matched debit (§9b.2), so a ledger row
written without `granted_delta` / `purchased_delta` is a row the refund matcher cannot judge
— it falls through to the "unknown split" branch meant for the genuine pre-117 rows. Migration
166 kept a (0,0) escape in its CHECK constraint open on the grounds that
`add_credit_transaction` (via `CreditService.log_transaction`) "still inserts without the
split columns today". That function had NO callers; migration 171 drops it and this file keeps
the door shut:

  1. No Python under app/ or scripts/ calls the `add_credit_transaction` RPC, and
     `CreditService.log_transaction` does not come back.
  2. Every `INSERT INTO public.credit_transactions` in the LIVE schema (`schema_snapshot.sql`)
     names both split columns. The one exception — the body of `add_credit_transaction`
     itself — is tolerated only while the snapshot pre-dates 171 (the user applies
     migrations by hand, then re-dumps); once the function is gone from the dump the
     tolerance is dead code and the assertion is absolute.
  3. Every migration from 140 on (139 rewrote the last short-list writer) names both split
     columns in any ledger INSERT — so a future "quick" writer cannot land with the DEFAULT
     laundering it into a (0,0) row.

Comment lines are stripped before matching (testing.md rule 1).
"""
from __future__ import annotations

import pathlib
import re

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_SNAPSHOT = _BACKEND / "database" / "schema_snapshot.sql"
_MIGRATIONS = _BACKEND / "database" / "migrations"
_DROP_MIGRATION = _MIGRATIONS / "171_drop_dead_ledger_door.sql"

_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(?:public\.)?credit_transactions\s*\(([^)]*)\)", re.I | re.S
)


def _strip_sql_comments(text: str) -> str:
    return "\n".join(ln.split("--", 1)[0] for ln in text.splitlines())


def _strip_py_comments(text: str) -> str:
    return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())


def _ledger_inserts(sql: str):
    """(column list, position) for every ledger INSERT in `sql`, comments stripped."""
    clean = _strip_sql_comments(sql)
    return [(m.group(1), m.start()) for m in _INSERT_RE.finditer(clean)]


def _names_the_split(columns: str) -> bool:
    cols = {c.strip().lower() for c in columns.split(",")}
    return {"granted_delta", "purchased_delta"} <= cols


# ── 1. The Python door stays shut ─────────────────────────────────────────────


def _python_sources():
    for root in (_BACKEND / "app", _BACKEND / "scripts"):
        yield from root.rglob("*.py")


def test_no_python_calls_the_dead_ledger_rpc():
    offenders = [
        str(p.relative_to(_BACKEND))
        for p in _python_sources()
        if "add_credit_transaction" in _strip_py_comments(p.read_text(errors="ignore"))
    ]
    assert offenders == [], (
        f"{offenders} call add_credit_transaction — dropped in 171; ledger rows are written "
        "ONLY by the 101/118 RPCs (spend_credits / refund_credits), which record the split"
    )


def test_credit_service_has_no_log_transaction():
    src = _strip_py_comments((_BACKEND / "app" / "services" / "credit_service.py").read_text())
    assert "def log_transaction" not in src


def test_the_scanner_sees_a_real_call():
    """Anti-vacuity: the detector matches the exact string a caller would need."""
    assert "add_credit_transaction" in _strip_py_comments(
        'self.supabase.rpc("add_credit_transaction", {...})  # a caller'
    )
    assert "add_credit_transaction" not in _strip_py_comments("# add_credit_transaction")


# ── 2. The live schema ────────────────────────────────────────────────────────


def test_every_live_ledger_insert_records_the_split():
    sql = _SNAPSHOT.read_text()
    clean = _strip_sql_comments(sql)
    inserts = _ledger_inserts(sql)
    assert len(inserts) >= 8, "the snapshot detector found almost nothing — regex drift?"

    dead_fn = re.search(r"CREATE FUNCTION public\.add_credit_transaction\(", clean)
    tolerated_span = None
    if dead_fn:
        # The dump pre-dates 171. Tolerate the dead function's own body ONLY while the
        # migration that drops it exists and really drops it.
        assert _DROP_MIGRATION.exists(), (
            "schema_snapshot.sql still defines add_credit_transaction and no migration drops it"
        )
        assert re.search(r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+public\.add_credit_transaction",
                         _strip_sql_comments(_DROP_MIGRATION.read_text()), re.I)
        end = clean.find("$$;", dead_fn.end())
        tolerated_span = (dead_fn.start(), end)

    short = []
    for columns, pos in inserts:
        if tolerated_span and tolerated_span[0] <= pos <= tolerated_span[1]:
            continue
        if not _names_the_split(columns):
            short.append(columns.strip())
    assert short == [], f"live ledger INSERTs without the split columns: {short}"


# ── 3. Migrations from 140 on ─────────────────────────────────────────────────


def _migrations_from(n: int):
    for p in sorted(_MIGRATIONS.glob("*.sql")):
        m = re.match(r"^(\d{3})_", p.name)
        if m and int(m.group(1)) >= n:
            yield p


@pytest.mark.parametrize("path", list(_migrations_from(140)), ids=lambda p: p.name)
def test_every_ledger_insert_in_a_recent_migration_records_the_split(path):
    short = [cols.strip() for cols, _ in _ledger_inserts(path.read_text()) if not _names_the_split(cols)]
    assert short == [], f"{path.name}: ledger INSERT without granted_delta/purchased_delta: {short}"


def test_the_migration_detector_is_not_vacuous():
    sample = (
        "-- INSERT INTO public.credit_transactions (user_id, delta) -- comment, ignored\n"
        "INSERT INTO public.credit_transactions (user_id, delta, reason)\n"
        "VALUES (1, 2, 'x');\n"
        "INSERT INTO public.credit_transactions\n"
        "    (user_id, delta, granted_delta, purchased_delta, reason)\nVALUES (...);\n"
    )
    found = _ledger_inserts(sample)
    assert len(found) == 2
    assert [_names_the_split(c) for c, _ in found] == [False, True]
