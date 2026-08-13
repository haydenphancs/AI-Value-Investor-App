"""`_seed_default_portfolio` must survive a concurrent seed instead of 500-ing.

`GET /portfolios` seeds a portfolio literally named "Holdings" the first time a user
has none. `ContentView` mounts every tab at launch, so `TrackingViewModel.init` and
the Home fetch can both hit that route inside the same window — and
`POST /users/me/claim-guest-data` can move a guest "Holdings" over at the same moment.
`portfolios_user_id_name_key UNIQUE (user_id, name)` then makes the loser's bare
`.insert(...).execute().data[0]` raise 23505, which surfaced as an unhandled 500 on a
read-only endpoint.

The fix adopts the winner. What it must NOT become is a generic error-swallower, so
both re-raise paths are pinned here, and so is the fact that the dropped items are
recovered by the pre-existing `_backfill_lone_empty_portfolio` heal rather than lost.
"""
from __future__ import annotations

import pytest

import app.api.v1.endpoints.portfolios as pf


class _Violation(Exception):
    """Shaped like postgrest's unique-violation APIError."""

    def __init__(self, constraint="portfolios_user_id_name_key"):
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.code = "23505"


class _Q:
    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self._op = "select"
        self._payload = None
        self._eq = {}

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, payload, *a, **k):
        self._op = "insert"
        self._payload = payload
        return self

    def upsert(self, payload, *a, **k):
        self._op = "upsert"
        self._payload = payload
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self.sb.ops.append((self.table, self._op))
        if self._op == "insert":
            self.sb.inserts.append((self.table, self._payload))
            exc = self.sb.insert_errors.get(self.table)
            if exc is not None:
                self.sb.insert_errors[self.table] = None  # fire once
                raise exc
        return type("R", (), {"data": self.sb.rows.get((self.table, self._op), [])})()


class _SB:
    def __init__(self, rows=None, insert_errors=None):
        self.rows = rows or {}
        self.insert_errors = dict(insert_errors or {})
        self.ops: list[tuple[str, str]] = []
        self.inserts: list[tuple[str, object]] = []

    def table(self, name):
        return _Q(self, name)

    def count(self, table, op):
        return sum(1 for t, o in self.ops if t == table and o == op)


_WATCHLIST = [{"ticker": "NVDA", "added_at": "2026-08-01", "shares": 3,
               "market_value": 100.0}]


# ── non-vacuity ──────────────────────────────────────────────────────────────

def test_the_fake_really_raises_on_the_seed_insert():
    """If the injected error stopped firing, every test below would pass for free."""
    sb = _SB(insert_errors={"portfolios": _Violation()})
    with pytest.raises(_Violation):
        sb.table("portfolios").insert({"name": "Holdings"}).execute()


# ── the race ─────────────────────────────────────────────────────────────────

def test_seed_adopts_the_winner_instead_of_raising():
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "select"): [{"id": "winner"}],
        },
        insert_errors={"portfolios": _Violation()},
    )

    pf._seed_default_portfolio(sb, "u1")  # must not raise

    assert sb.count("portfolios", "select") == 1, "did not re-read to find the winner"


def test_seed_inserts_no_items_when_it_loses_the_race():
    """The winner owns the items.

    Writing them here would collide on portfolio_items_portfolio_id_ticker_key, and
    if the winner seeded an EMPTY group the pre-existing
    `_backfill_lone_empty_portfolio` heal fills it on the next GET /portfolios — so
    there is no second repair path to maintain.
    """
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "select"): [{"id": "winner"}],
        },
        insert_errors={"portfolios": _Violation()},
    )

    pf._seed_default_portfolio(sb, "u1")

    assert sb.count("portfolio_items", "insert") == 0


def test_the_existing_backfill_heal_recovers_the_dropped_items():
    """Proves the items are RECOVERABLE, not lost — the premise of the test above."""
    class _Empty:
        id = "winner"
        tickers: list = []

    sb = _SB(rows={("watchlist_items", "select"): _WATCHLIST})
    captured = {}

    def _refetch(_sb, _uid):
        captured["refetched"] = True
        return ["healed"]

    original = pf._fetch_user_portfolios
    pf._fetch_user_portfolios = _refetch
    try:
        out = pf._backfill_lone_empty_portfolio(sb, "u1", [_Empty()])
    finally:
        pf._fetch_user_portfolios = original

    assert captured.get("refetched"), "the heal did not run"
    assert out == ["healed"]
    assert sb.count("portfolio_items", "upsert") == 1, \
        "the watchlist ticker was not written into the empty group"


def test_the_happy_path_is_unchanged():
    """Negative control: no race → the seed still creates the group AND its items."""
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "insert"): [{"id": "new"}],
        }
    )

    pf._seed_default_portfolio(sb, "u1")

    assert sb.count("portfolios", "insert") == 1
    assert sb.count("portfolio_items", "insert") == 1
    assert sb.count("portfolios", "select") == 0, "no need to re-read when we won"


# ── the two re-raise controls ────────────────────────────────────────────────

def test_a_non_unique_error_is_re_raised():
    """This must not become a generic swallower."""
    sb = _SB(
        rows={("watchlist_items", "select"): _WATCHLIST},
        insert_errors={"portfolios": RuntimeError("relation is being vacuumed")},
    )

    with pytest.raises(RuntimeError):
        pf._seed_default_portfolio(sb, "u1")


def test_a_23505_with_no_holdings_row_is_re_raised():
    """The violation was on some OTHER constraint — swallowing it would hide a bug."""
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "select"): [],  # no winner to adopt
        },
        insert_errors={"portfolios": _Violation("some_other_constraint")},
    )

    with pytest.raises(Exception) as excinfo:
        pf._seed_default_portfolio(sb, "u1")
    assert "portfolios_user_id_name_key" in str(excinfo.value)


def test_an_empty_insert_result_also_adopts_rather_than_indexerrors():
    """The bare `.data[0]` also IndexError'd when PostgREST returned no row.

    Same 500, different trigger; the `if not inserted:` guard covers both.
    """
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "insert"): [],       # insert succeeded, returned nothing
            ("portfolios", "select"): [{"id": "winner"}],
        }
    )

    pf._seed_default_portfolio(sb, "u1")  # must not raise
    assert sb.count("portfolio_items", "insert") == 0
