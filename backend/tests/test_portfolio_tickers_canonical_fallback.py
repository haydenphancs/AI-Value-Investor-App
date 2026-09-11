"""The four holdings/membership writers must resolve RAW then CANONICAL, like their siblings.

Migration 160 stores a coin in the PAIR form ("BTCUSD") and leaves the bare form ("BTC")
to mean the listed security (Grayscale Bitcoin Mini Trust ETF). `DELETE /watchlist` and
`PUT /tracking/holdings/{ticker}` were taught to try the raw spelling first and the
canonical one second. Four siblings were not — the "fixed 1 of N copies" shape:

  * `PUT /portfolios/{id}/tickers`   — filters the request against `watchlist_items` by the
    RAW spelling, then DELETEs every row for the portfolio and reinserts the survivors. A
    client still saying "BTC" for the coin (stale local state, the shipped build) had its
    BTCUSD position and its hand-entered `shares` silently deleted.
  * `PUT /portfolios/{id}/holdings`  — `.eq("ticker", ticker)` one spelling; 0 rows, 200 OK.
  * `DELETE /tracking/holdings/{t}`  — same, and reported "Holding cleared" on zero rows.
  * `PUT /tracking/assets/holdings`  — same, counted as not-updated.

Raw first, canonical second, NEVER both at once: a user can legitimately hold the coin AND
the security, and only one row per spelling can exist.

No network / Supabase — a fake store that models the unique key on portfolio_items.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import app.api.v1.endpoints.portfolios as pf
import app.api.v1.endpoints.tracking as tr

_USER = "u-1"
_PID = "p1"


class _Q:
    def __init__(self, store, table, log):
        self.store, self.table, self.log = store, table, log
        self._op, self._payload = "select", None
        self._filters, self._in = {}, None
        self._limit = None

    def select(self, *_a): self._op = "select"; return self
    def insert(self, p): self._op, self._payload = "insert", p; return self
    def update(self, p): self._op, self._payload = "update", p; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, c, v): self._filters[c] = v; return self
    def in_(self, c, vals): self._in = (c, list(vals)); return self
    def limit(self, n): self._limit = n; return self
    def order(self, *_a, **_k): return self

    def _matched(self, rows):
        out = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._in:
            col, vals = self._in
            out = [r for r in out if r.get(col) in vals]
        return out

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                if self.table == "portfolio_items":
                    # portfolio_items_portfolio_id_ticker_key — a duplicate is a 23505.
                    if any(r["portfolio_id"] == p["portfolio_id"] and r["ticker"] == p["ticker"]
                           for r in rows):
                        raise RuntimeError(f"23505 duplicate key {p['ticker']}")
                rows.append(dict(p))
            self.log.append(("insert", self.table, [p["ticker"] for p in payload
                                                     if "ticker" in p]))
            return type("R", (), {"data": [dict(p) for p in payload]})()
        matched = self._matched(rows)
        if self._op == "delete":
            for r in matched:
                rows.remove(r)
            self.log.append(("delete", self.table, len(matched)))
            return type("R", (), {"data": [dict(r) for r in matched]})()
        if self._op == "update":
            for r in matched:
                r.update(self._payload)
            self.log.append(("update", self.table, [r.get("ticker") for r in matched]))
            return type("R", (), {"data": [dict(r) for r in matched]})()
        if self._limit is not None:
            matched = matched[: self._limit]
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _SB:
    def __init__(self, store):
        self.store, self.log = store, []

    def table(self, name):
        return _Q(self.store, name, self.log)


def _store(watchlist, items):
    return {
        "portfolios": [{
            "id": _PID, "user_id": _USER, "name": "Holdings", "is_active": True,
            "sort_order": 0, "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }],
        "watchlist_items": [{"user_id": _USER, "ticker": t, "shares": None,
                             "market_value": None} for t in watchlist],
        "portfolio_items": [dict(i, portfolio_id=_PID) for i in items],
    }


def _items(store):
    return [(r["ticker"], r.get("shares")) for r in store["portfolio_items"]]


@pytest.fixture(autouse=True)
def _no_feed_cache(monkeypatch):
    monkeypatch.setattr(pf, "invalidate_feed_cache", lambda _uid: None)


# ── PUT /portfolios/{id}/tickers ─────────────────────────────────────────────

def _set_tickers(sb, tickers):
    return asyncio.run(pf.set_portfolio_tickers(
        _PID, pf.SetTickersRequest(tickers=tickers), {"id": _USER}, sb))


def test_a_stale_client_saying_btc_keeps_the_btcusd_position_and_its_shares():
    """THE defect: the request says BTC, the watchlist row is BTCUSD, and the destructive
    delete+reinsert used to drop the position with its 0.5 shares."""
    store = _store(["BTCUSD", "AAPL"], [{"ticker": "BTCUSD", "shares": 0.5, "market_value": 40000.0}])
    _set_tickers(_SB(store), ["BTC", "AAPL"])
    assert _items(store) == [("BTCUSD", 0.5), ("AAPL", None)]


def test_both_spellings_in_one_request_insert_the_pair_exactly_once():
    """`["BTC", "BTCUSD"]` with only the pair stored must not violate the unique key."""
    store = _store(["BTCUSD"], [])
    _set_tickers(_SB(store), ["BTC", "BTCUSD"])
    assert _items(store) == [("BTCUSD", None)]


def test_a_declared_etf_holder_keeps_the_bare_form():
    """Raw wins when it exists: the user who picked the Grayscale ETF keeps the ETF."""
    store = _store(["BTC"], [])
    _set_tickers(_SB(store), ["BTC"])
    assert _items(store) == [("BTC", None)]


def test_a_user_holding_both_assets_gets_exactly_what_they_asked_for():
    """Coin AND security on the watchlist: "BTC" means the security, nothing is inferred."""
    store = _store(["BTC", "BTCUSD"], [])
    _set_tickers(_SB(store), ["BTC"])
    assert _items(store) == [("BTC", None)]


def test_a_symbol_on_neither_spelling_is_dropped_with_a_warning(caplog):
    store = _store(["AAPL"], [])
    with caplog.at_level(logging.WARNING, logger=pf.logger.name):
        _set_tickers(_SB(store), ["AAPL", "ZZZZ"])
    assert _items(store) == [("AAPL", None)]
    assert any("ZZZZ" in r.getMessage() for r in caplog.records), \
        "a silently dropped ticker is exactly the failure this guards against"


# ── PUT /portfolios/{id}/holdings ────────────────────────────────────────────

def test_portfolio_holdings_update_reaches_the_canonical_row():
    store = _store(["BTCUSD"], [{"ticker": "BTCUSD", "shares": None, "market_value": None}])
    asyncio.run(pf.set_portfolio_holdings(
        _PID,
        pf.SetPortfolioHoldingsRequest(items=[pf.HoldingItem(ticker="btc", shares=0.25)]),
        {"id": _USER}, _SB(store)))
    assert _items(store) == [("BTCUSD", 0.25)]


def test_portfolio_holdings_update_prefers_the_raw_row_when_both_exist():
    store = _store(["BTC", "BTCUSD"], [
        {"ticker": "BTC", "shares": None, "market_value": None},
        {"ticker": "BTCUSD", "shares": None, "market_value": None},
    ])
    asyncio.run(pf.set_portfolio_holdings(
        _PID,
        pf.SetPortfolioHoldingsRequest(items=[pf.HoldingItem(ticker="BTC", shares=10)]),
        {"id": _USER}, _SB(store)))
    assert _items(store) == [("BTC", 10), ("BTCUSD", None)]


# ── DELETE /tracking/holdings/{ticker} ───────────────────────────────────────

def test_tracking_delete_holding_clears_the_canonical_row():
    store = _store(["BTCUSD"], [])
    store["watchlist_items"][0].update({"shares": 2.0, "market_value": 100.0})
    out = asyncio.run(tr.delete_holding("btc", {"id": _USER}, _SB(store)))
    assert store["watchlist_items"][0]["shares"] is None
    assert store["watchlist_items"][0]["market_value"] is None
    assert isinstance(out, dict) and "message" in out


def test_tracking_delete_holding_on_an_unknown_ticker_is_not_a_silent_success():
    """Zero rows matched used to return 200 "Holding cleared"."""
    store = _store(["AAPL"], [])
    out = asyncio.run(tr.delete_holding("ZZZZ", {"id": _USER}, _SB(store)))
    status = getattr(out, "status_code", 200)
    assert status == 404, f"expected a 404, got {status}: {out}"


# ── PUT /tracking/assets/holdings ────────────────────────────────────────────

def test_tracking_bulk_update_reaches_the_canonical_row():
    store = _store(["BTCUSD", "AAPL"], [])
    out = asyncio.run(tr.bulk_update_holdings(
        [tr.BulkHoldingUpdateItem(ticker="btc", shares=1.5),
         tr.BulkHoldingUpdateItem(ticker="AAPL", shares=3)],
        {"id": _USER}, _SB(store)))
    by = {r["ticker"]: r["shares"] for r in store["watchlist_items"]}
    assert by == {"BTCUSD": 1.5, "AAPL": 3}
    assert out["updated"] == 2


def test_tracking_bulk_update_never_touches_both_spellings():
    store = _store(["BTC", "BTCUSD"], [])
    asyncio.run(tr.bulk_update_holdings(
        [tr.BulkHoldingUpdateItem(ticker="BTC", shares=7)], {"id": _USER}, _SB(store)))
    by = {r["ticker"]: r["shares"] for r in store["watchlist_items"]}
    assert by == {"BTC": 7, "BTCUSD": None}
