"""Tickers added to the watchlist reach the tab that renders them.

THE BUG, exactly as reported: first-run onboarding captured ORCL and PLUG. They showed up in
the Updates tab's filter pills and the Tracking tab said "No tickers yet".

Why. The Assets tab is a PORTFOLIO view — `TrackingViewModel.filteredAssets` intersects the
feed with `activePortfolio.tickers`, and a portfolio is "a user-named subset of the master
watchlist" (portfolios.py module docstring). The design's safety net is `_seed_default_portfolio`,
which populates a default "Holdings" portfolio FROM the watchlist on the first `GET /portfolios`.

That seed is one-shot and it fires at the wrong moment. `ContentView` mounts every tab at
launch, so `TrackingViewModel.init` issues `GET /portfolios` seconds BEFORE onboarding writes a
single ticker. The seed reads an empty watchlist, creates the portfolio with zero items, and
the `if not portfolios:` guard means it can never run again. Nothing reconciles the two tables
afterwards, so the watchlist has tickers, the portfolio is empty, and Assets is empty forever —
unfixable except by re-adding every ticker by hand through the Tracking search.

Two fixes, both pinned here:
  * DURABLE — `POST /watchlist` mirrors into the user's portfolio when they have exactly one.
  * REPAIR  — `GET /portfolios` backfills a lone empty portfolio from a non-empty watchlist,
    so installs already stuck heal themselves.

No network / Supabase.
"""
from __future__ import annotations

import pytest

import app.api.v1.endpoints.watchlist as wl


class _Q:
    def __init__(self, store, table, log):
        self.store, self.table, self.log = store, table, log
        self._op = "select"
        self._payload = None
        self._filters = {}
        self._conflict = None

    def select(self, *_a): self._op = "select"; return self
    def insert(self, p): self._op, self._payload = "insert", p; return self
    def eq(self, c, v): self._filters[c] = v; return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        self._op, self._payload, self._conflict = "upsert", payload, on_conflict
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "upsert":
            p = self._payload
            # Model portfolio_items_portfolio_id_ticker_key.
            dupe = any(
                r.get("portfolio_id") == p["portfolio_id"] and r.get("ticker") == p["ticker"]
                for r in rows
            )
            if not dupe:
                rows.append(dict(p))
                self.log.append(("upsert", self.table, p["ticker"]))
            else:
                self.log.append(("upsert-noop", self.table, p["ticker"]))
            return type("R", (), {"data": []})()
        matched = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _SB:
    def __init__(self, store):
        self.store, self.log = store, []

    def table(self, name):
        return _Q(self.store, name, self.log)


_USER = "guest-install-A"


def _store(portfolios, items=()):
    return {"portfolios": list(portfolios), "portfolio_items": list(items)}


# ---------------------------------------------------------------------------
# The durable write-through
# ---------------------------------------------------------------------------


def test_a_watchlist_add_reaches_the_users_only_portfolio():
    """THE reported symptom: onboarding adds ORCL, Assets stays empty."""
    store = _store([{"id": "p1", "user_id": _USER}])
    sb = _SB(store)

    wl._write_through_to_lone_portfolio(sb, _USER, "ORCL")

    assert [r["ticker"] for r in store["portfolio_items"]] == ["ORCL"]
    assert store["portfolio_items"][0]["portfolio_id"] == "p1"


def test_positions_increment_rather_than_collide():
    store = _store([{"id": "p1", "user_id": _USER}])
    sb = _SB(store)

    wl._write_through_to_lone_portfolio(sb, _USER, "ORCL")
    wl._write_through_to_lone_portfolio(sb, _USER, "PLUG")

    assert [(r["ticker"], r["position"]) for r in store["portfolio_items"]] == [
        ("ORCL", 0), ("PLUG", 1)
    ]


def test_re_adding_an_existing_ticker_is_a_no_op():
    """UNIQUE(portfolio_id, ticker) — a re-add must not raise, and must not reorder."""
    store = _store([{"id": "p1", "user_id": _USER}],
                   [{"portfolio_id": "p1", "ticker": "ORCL", "position": 0}])
    sb = _SB(store)

    wl._write_through_to_lone_portfolio(sb, _USER, "ORCL")

    assert len(store["portfolio_items"]) == 1
    assert store["portfolio_items"][0]["position"] == 0
    assert ("upsert-noop", "portfolio_items", "ORCL") in sb.log


def test_a_user_with_several_portfolios_is_left_alone():
    """They have made an explicit choice about where tickers go; the Tracking search already
    targets the ACTIVE portfolio. Silently also filing it under the first one would be wrong."""
    store = _store([{"id": "p1", "user_id": _USER}, {"id": "p2", "user_id": _USER}])
    sb = _SB(store)

    wl._write_through_to_lone_portfolio(sb, _USER, "ORCL")

    assert store["portfolio_items"] == []


def test_a_user_with_no_portfolio_is_left_to_the_seed():
    """`GET /portfolios` seeds one from the watchlist; inventing a second here would race it."""
    store = _store([])
    sb = _SB(store)

    wl._write_through_to_lone_portfolio(sb, _USER, "ORCL")

    assert store["portfolio_items"] == []
    assert store["portfolios"] == []


def test_another_users_portfolio_is_never_targeted():
    store = _store([{"id": "p-other", "user_id": "someone-else"}])
    sb = _SB(store)

    wl._write_through_to_lone_portfolio(sb, _USER, "ORCL")

    assert store["portfolio_items"] == [], "wrote into another user's portfolio"


def test_a_mirror_failure_never_breaks_the_add():
    """The watchlist row is already committed and IS the source of truth. Raising here would
    turn a successful add into a 500."""
    class _Boom:
        def table(self, _n):
            raise RuntimeError("supabase down")

    wl._write_through_to_lone_portfolio(_Boom(), _USER, "ORCL")  # must not raise


def test_the_add_endpoint_actually_calls_the_mirror():
    """A helper nobody calls is the original bug in a new place."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "app" / "api" / "v1" / "endpoints" / "watchlist.py").read_text(encoding="utf-8")
    assert "_write_through_to_lone_portfolio(supabase, user_id, ticker)" in src


# ---------------------------------------------------------------------------
# The repair path
# ---------------------------------------------------------------------------


def test_backfill_is_wired_into_the_portfolio_list():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "app" / "api" / "v1" / "endpoints" / "portfolios.py").read_text(encoding="utf-8")
    assert "_backfill_lone_empty_portfolio(supabase, user[\"id\"], portfolios)" in src, (
        "installs already stuck with an empty portfolio never heal"
    )


def test_backfill_only_touches_a_lone_empty_portfolio():
    """Narrow on purpose: a user with several portfolios, or one deliberately emptied among
    several, must not be re-populated on every list call."""
    import app.api.v1.endpoints.portfolios as pf

    class _P:
        def __init__(self, pid, tickers):
            self.id, self.tickers = pid, tickers

    sb = _SB(_store([]))
    two = [_P("p1", []), _P("p2", ["AAPL"])]
    assert pf._backfill_lone_empty_portfolio(sb, _USER, two) is two, "touched a multi-portfolio user"

    non_empty = [_P("p1", ["AAPL"])]
    assert pf._backfill_lone_empty_portfolio(sb, _USER, non_empty) is non_empty
