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


def _call_sites():
    """[(file, lineno, table, order_by)] for every `fetch_all_rows(...)` in app/ + scripts/."""
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
                if name != "fetch_all_rows":
                    continue
                order_by = None
                for kw in node.keywords:
                    if kw.arg == "order_by" and isinstance(kw.value, ast.Constant):
                        order_by = kw.value.value
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
                sites.append((path.relative_to(_BACKEND).as_posix(), node.lineno, table, order_by))
    return sites


def test_the_scan_finds_the_real_call_sites():
    """Anti-vacuity: an empty scan would make every assertion below trivially true."""
    sites = _call_sites()
    assert len(sites) >= 6, f"only {len(sites)} fetch_all_rows call sites found — scan is broken"
    files = {s[0] for s in sites}
    assert any("signals_service" in f for f in files)
    assert any("price_alert_service" in f for f in files)


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


def test_every_paged_read_orders_on_a_unique_column():
    uniq = _single_column_unique_columns()
    if not uniq:
        pytest.skip("schema_snapshot.sql carries no ALTER TABLE constraints")
    bad = []
    for path, line, table, order_by in _call_sites():
        assert order_by, f"{path}:{line} — fetch_all_rows without a literal order_by"
        assert table, f"{path}:{line} — could not resolve the .table(\"…\") for this read"
        if table not in uniq:
            continue          # table absent from the dump (a view, or a newer table)
        if order_by not in uniq[table]:
            bad.append(f"{path}:{line} pages {table} on {order_by!r}, which is not unique")
    assert not bad, (
        "OFFSET paging on a non-unique sort key can skip or duplicate rows across the "
        "page boundary:\n  " + "\n  ".join(bad)
    )
