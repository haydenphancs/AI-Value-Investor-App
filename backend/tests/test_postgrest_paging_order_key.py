"""Every paged read must order on a key that is actually unique.

`app/utils/postgrest_paging.fetch_all_rows` states the precondition in its own module
docstring — "ORDER MATTERS … it must be unique-ish (a primary key, or a key plus a
tiebreaker)" — and three of the first six call sites violated it within a day of the helper
landing (`order_by="whale_id"` twice, `order_by="date"` once).

Why it is not cosmetic: OFFSET paging without a total order is not a stable window.
Postgres orders each page independently, and a tie group straddling the boundary can be
split so a row appears on BOTH pages or on NEITHER. A duplicate is usually absorbed by a
set; a DROPPED row is not — it silently under-counts, which is the exact defect the pager
was written to end (`whale_holdings` → "N funds adding" short by one → the Accumulation
card falls under `_WHALE_MIN_FUNDS` and vanishes).

This guard reads the two facts from source: the `(table, order_by)` pair at each call site,
and the single-column PRIMARY KEY / UNIQUE constraints in `schema_snapshot.sql`.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_SNAPSHOT = _BACKEND / "database" / "schema_snapshot.sql"


def _single_column_unique_columns() -> dict:
    """{table: {column, …}} for every SINGLE-column PK / UNIQUE constraint in the dump."""
    sql = _SNAPSHOT.read_text(encoding="utf-8", errors="replace")
    # `ALTER TABLE ONLY public.x ADD CONSTRAINT x_pkey PRIMARY KEY (id);`
    pat = re.compile(
        r"ALTER TABLE ONLY (?:public\.)?(\w+)\s*\n?\s*ADD CONSTRAINT \w+ "
        r"(?:PRIMARY KEY|UNIQUE) \(([^)]+)\);",
        re.MULTILINE,
    )
    out: dict = {}
    for table, cols in pat.findall(sql):
        parts = [c.strip() for c in cols.split(",")]
        if len(parts) == 1:
            out.setdefault(table, set()).add(parts[0])
    return out


_NOT_COLUMNS = {"constraint", "primary", "unique", "foreign", "check", "exclude"}
_ALTER_HEAD = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?\"?(\w+)\"?", re.IGNORECASE)
# Every `ADD [COLUMN] [IF NOT EXISTS] name` clause of a statement (the repo also adds several
# columns in one ALTER), never a constraint's ADD, plus `RENAME COLUMN a TO b`.
_ALTER_ADD = re.compile(r"(?:^|,)\s*ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?",
                        re.IGNORECASE)
_ALTER_RENAME = re.compile(r"RENAME\s+COLUMN\s+\"?\w+\"?\s+TO\s+\"?(\w+)\"?", re.IGNORECASE)


def _migration_columns(sql_texts) -> dict:
    """{table: {column, …}} that the migrations' ALTER TABLE statements add or rename to."""
    out: dict = {}
    for raw in sql_texts:
        text = re.sub(r"--[^\n]*", "", raw)
        for stmt in text.split(";"):
            m = _ALTER_HEAD.match(stmt)
            if not m:
                continue
            body = stmt[m.end():]
            names = [n for n in _ALTER_ADD.findall(body) + _ALTER_RENAME.findall(body)
                     if n.lower() not in _NOT_COLUMNS]
            out.setdefault(m.group(1), set()).update(names)
    return out


def _table_columns() -> dict:
    """{table: {column, …}} from the dump's CREATE TABLE blocks plus every migration's
    `ADD COLUMN` (the dump can trail the newest migrations)."""
    sql = _SNAPSHOT.read_text(encoding="utf-8", errors="replace")
    out: dict = {}
    for table, body in re.findall(r"^CREATE TABLE (?:public\.)?(\w+) \((.*?)^\);", sql,
                                  re.MULTILINE | re.DOTALL):
        # A CREATE TABLE body's inline `CONSTRAINT …` lines name no column.
        out.setdefault(table, set()).update(
            c for c in (m.group(1) for m in re.finditer(r"^\s+\"?(\w+)\"?\s+\S", body,
                                                        re.MULTILINE))
            if c.lower() not in _NOT_COLUMNS)
    migrations = sorted((_BACKEND / "database" / "migrations").glob("*.sql"))
    for table, cols in _migration_columns(
            m.read_text(encoding="utf-8", errors="replace") for m in migrations).items():
        out.setdefault(table, set()).update(cols)
    return out


def _call_sites():
    """[(file, lineno, table, order_by, then_by, function)] for every `fetch_all_rows(...)` /
    `fetch_all_rows_concurrent(...)` in app/ + scripts/."""
    sites = []
    for root in ("app", "scripts"):
        for path in sorted((_BACKEND / root).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:                                    # pragma: no cover
                continue
            # Module-level `TABLE = "price_alerts"` style constants, so a call site that
            # names its table through a constant still resolves.
            consts = {
                t.id: n.value.value
                for n in tree.body
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
                for t in n.targets
                if isinstance(t, ast.Name)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                # The parallel sibling pages by OFFSET too, so the same precondition holds.
                if name not in ("fetch_all_rows", "fetch_all_rows_concurrent"):
                    continue
                order_by = then_by = None
                for kw in node.keywords:
                    if kw.arg == "order_by" and isinstance(kw.value, ast.Constant):
                        order_by = kw.value.value
                    # `then_by` (fetch_all_rows only): a unique tiebreaker after a non-unique key.
                    if kw.arg == "then_by" and isinstance(kw.value, ast.Constant):
                        then_by = kw.value.value
                # The table name is the argument of the `.table("X")` call inside the
                # query builder this site passes as `build_query`.
                table = None
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "table"
                        and sub.args
                    ):
                        arg = sub.args[0]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            table = arg.value
                        elif isinstance(arg, ast.Name):
                            table = consts.get(arg.id)
                        if table:
                            break
                sites.append((path.relative_to(_BACKEND).as_posix(), node.lineno, table, order_by,
                              then_by, name))
    return sites


def test_the_scan_finds_the_real_call_sites():
    """Anti-vacuity: an empty scan would make every assertion below trivially true."""
    sites = _call_sites()
    assert len(sites) >= 6, f"only {len(sites)} fetch_all_rows call sites found — scan is broken"
    files = {s[0] for s in sites}
    assert any("signals_service" in f for f in files)
    assert any("price_alert_service" in f for f in files)
    # The concurrent sibling's call site (the movers close map) is scanned too.
    assert ("market_close_snapshot", "symbol") in {(s[2], s[3]) for s in sites}
    # A tiebroken site is scanned with its tiebreaker (the IAP sweep's newest-first re-check).
    assert ("subscriptions", "updated_at", "id") in {(s[2], s[3], s[4]) for s in sites}


def test_the_snapshot_parse_finds_real_primary_keys():
    """Anti-vacuity: an empty constraint map would make the ordering test unfalsifiable."""
    uniq = _single_column_unique_columns()
    if not uniq:
        pytest.skip("schema_snapshot.sql carries no ALTER TABLE constraints")
    assert len(uniq) >= 60, f"only {len(uniq)} tables parsed from the dump"
    assert "id" in uniq.get("whale_holdings", set())
    assert "id" in uniq.get("sector_benchmarks", set())
    # The column the bug used must NOT look unique, or the guard proves nothing.
    assert "whale_id" not in uniq.get("whale_holdings", set())


def test_the_column_parse_finds_real_columns():
    """Anti-vacuity for the tiebreaker check below: the dump's columns plus migrations' adds."""
    cols = _table_columns()
    assert len(cols) >= 60, f"only {len(cols)} tables' columns parsed"
    assert {"id", "user_id", "updated_at", "status"} <= cols.get("subscriptions", set())
    assert "max_period_end" in cols.get("subscriptions", set()), "migration 198's ADD COLUMN"
    assert "updatd_at" not in cols.get("subscriptions", set())
    # A constraint's ADD, or an inline CONSTRAINT line, is not a column.
    assert not {"constraint", "CONSTRAINT", "primary", "unique", "check"} & set().union(*cols.values())


def test_the_migration_parse_reads_every_clause_and_nothing_else():
    """The migration half alone (the dump's union would hide a parser that drops columns the dump
    already has): every ADD of a multi-column ALTER, a RENAME target, never a commented-out ADD or
    a constraint's ADD (review round 6 — three regressions of the parser stayed green)."""
    synthetic = _migration_columns(["""
        -- ALTER TABLE public.t ADD COLUMN in_a_comment TEXT;
        ALTER TABLE IF EXISTS ONLY public."t" ADD COLUMN IF NOT EXISTS a TEXT,
            ADD COLUMN b INT, ADD c BOOLEAN,  -- ADD COLUMN trailing_comment TEXT
            ADD CONSTRAINT t_a_key UNIQUE (a);
        alter table t rename column a to a2;
        ALTER TABLE public.t ADD PRIMARY KEY (b);
    """])
    assert synthetic == {"t": {"a", "b", "c", "a2"}}
    real = _migration_columns(m.read_text(encoding="utf-8", errors="replace") for m in sorted(
        (_BACKEND / "database" / "migrations").glob("*.sql")))
    # Multi-ADD statements of the real migrations (039, 041, 064) and one single-ADD (198).
    assert {"ticker_report_data", "overall_score", "fair_value_estimate", "is_refunded",
            "credits_charged", "pdf_status", "pdf_generated_at"} <= real.get("research_reports", set())
    assert {"transaction_id", "max_period_end"} <= real.get("subscriptions", set())


def test_every_paged_read_orders_on_a_unique_column():
    uniq = _single_column_unique_columns()
    if not uniq:
        pytest.skip("schema_snapshot.sql carries no ALTER TABLE constraints")
    cols = _table_columns()
    bad = []
    for path, line, table, order_by, then_by, fn in _call_sites():
        assert order_by, f"{path}:{line} — fetch_all_rows without a literal order_by"
        assert table, f"{path}:{line} — could not resolve the .table(\"…\") for this read"
        # Only the serial helper takes a tiebreaker (the concurrent one would raise TypeError).
        assert not (then_by and fn != "fetch_all_rows"), f"{path}:{line} — then_by on {fn}"
        if table not in uniq:
            continue          # table absent from the dump (a view, or a newer table)
        # Total when the key is unique, or when a unique tiebreaker follows a REAL column (a
        # tiebreaker would otherwise hide a misspelled key, which only a unique key used to catch).
        missing = [c for c in (order_by, then_by) if then_by and c not in cols.get(table, set())]
        if missing:
            bad.append(f"{path}:{line} pages {table} on {missing!r}, which is not a column")
        elif order_by not in uniq[table] and then_by not in uniq[table]:
            bad.append(f"{path}:{line} pages {table} on {order_by!r}"
                       + (f" then {then_by!r}" if then_by else "") + ", which is not unique")
    assert not bad, (
        "OFFSET paging on a non-unique sort key can skip or duplicate rows across the "
        "page boundary:\n  " + "\n  ".join(bad)
    )
