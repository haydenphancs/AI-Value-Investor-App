"""`.limit(50_000)` is not a bigger read — PostgREST clamps it to ~1,000 rows.

The clamp is measured in this repo twice (`market_movers_service`: "verified:
`.range(0, 49999)` still returns 1,000"; `news_cache_service`: "PostgREST also clamps a
large `.limit()` server-side"). Five call sites relied on a `.limit()` to lift it, each
with a comment describing the cap it was not lifting — and because the read SUCCEEDS the
truncation was invisible (found 2026-09-12):

* `price_alert_service` — active rules past row 1,000 never evaluated, and `.order("id")`
  made it the SAME users' alerts every cycle.
* `signals_service` — "N funds adding" under-counted (45 whales × up to 30 holdings).
* `competitor_intel_service` / `ip_intel_service` / `hydrate_hedge_fund_flow` — the
  "top watchlisted tickers" universe computed from an arbitrary unordered first page.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.utils.postgrest_paging import MAX_PAGES, PAGE_SIZE, fetch_all_rows

_BACKEND = Path(__file__).resolve().parents[1]


class _FakeQuery:
    """Models PostgREST: serves at most `cap` rows per request, whatever is asked."""

    def __init__(self, rows, cap, log):
        self.rows, self.cap, self.log = rows, cap, log
        self._order = None
        self._range = (0, 10 ** 9)

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def execute(self):
        assert self._order is not None, "an unordered paged read can overlap or skip rows"
        col, desc = self._order
        rows = sorted(self.rows, key=lambda r: r[col], reverse=desc)
        a, b = self._range
        page = rows[a:b + 1][: self.cap]          # the server-side clamp
        self.log.append((a, b, len(page)))
        return type("R", (), {"data": page})()


def _source(rows, cap=PAGE_SIZE):
    log = []
    return (lambda: _FakeQuery(rows, cap, log)), log


# ── the helper ──────────────────────────────────────────────────────────────────────


def test_it_reads_every_row_past_the_server_cap():
    rows = [{"id": i, "ticker": f"T{i}"} for i in range(2_500)]
    build, log = _source(rows)
    out = fetch_all_rows(build, order_by="id", what="test")
    assert len(out) == 2_500, f"only {len(out)} rows — the cap was not paged past"
    assert [r["id"] for r in out] == list(range(2_500)), "pages overlapped or skipped"
    assert len(log) == 3, log


def test_a_single_short_page_costs_one_request():
    build, log = _source([{"id": i} for i in range(10)])
    assert len(fetch_all_rows(build, order_by="id", what="test")) == 10
    assert len(log) == 1


def test_an_exactly_full_page_still_probes_for_more():
    """The boundary that an `if len(batch) < page: break` gets right and `<=` does not."""
    build, log = _source([{"id": i} for i in range(PAGE_SIZE)])
    assert len(fetch_all_rows(build, order_by="id", what="test")) == PAGE_SIZE
    assert len(log) == 2, "a full final page must be followed by one empty probe"


def test_it_refuses_to_spin_forever_and_says_so(caplog):
    class _Endless:
        def order(self, *a, **k):
            return self

        def range(self, a, b):
            self._n = b - a + 1
            return self

        def execute(self):
            return type("R", (), {"data": [{"id": 1}] * self._n})()

    with caplog.at_level("WARNING", logger="app.utils.postgrest_paging"):
        out = fetch_all_rows(lambda: _Endless(), order_by="id", what="runaway", max_pages=3)
    assert len(out) == 3 * PAGE_SIZE
    assert any("capped, not complete" in r.getMessage() for r in caplog.records), (
        "a silently truncated read is the exact bug this helper exists to end"
    )


def test_the_backstop_is_generous_enough_for_every_caller():
    assert MAX_PAGES * PAGE_SIZE >= 200_000


# ── no call site relies on a .limit() to lift the cap any more ──────────────────────

_SITES = [
    "app/services/price_alert_service.py",
    "app/services/signals_service.py",
    "app/services/competitor_intel_service.py",
    "app/services/ip_intel_service.py",
    "scripts/hydrate_hedge_fund_flow.py",
]


@pytest.mark.parametrize("rel", _SITES)
def test_the_site_pages_instead_of_asking_for_a_big_limit(rel):
    src = (_BACKEND / rel).read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"#.*$", "", l) for l in src.splitlines())   # the fix EXPLAINS the old limit
    assert "fetch_all_rows(" in code, f"{rel} no longer pages its capped read"
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "limit" and node.args):
            arg = node.args[0]
            value = arg.value if isinstance(arg, ast.Constant) else None
            assert value is None or value <= PAGE_SIZE, (
                f"{rel} asks PostgREST for {value} rows; the server clamps to {PAGE_SIZE} "
                "and answers 200, so the truncation is silent"
            )


def test_the_detector_would_catch_a_regression():
    """Anti-vacuity control for the scan above."""
    tree = ast.parse('q.select("x").limit(50_000).execute()')
    found = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "limit"]
    assert found and found[0].args[0].value == 50_000
