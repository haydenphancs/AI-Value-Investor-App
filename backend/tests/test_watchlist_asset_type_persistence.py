"""`POST /watchlist` persists what the row IS, and every reader publishes the resolved class.

The column `watchlist_items.asset_type` defaults to 'Stock' and this endpoint NEVER wrote
it — `request.asset_type` fed `canonical_stored_symbol` and was dropped. Migration 160
backfilled `'crypto'` for rows added before it shipped, but every coin starred afterwards
was stored as 'Stock' again, and both readers published the raw column:

  * Home "Your Watchlist" tile `type` → iOS `MarketTickerType(rawValue: "Stock") ?? .stock`
    → tap → `TickerDetailView("BTCUSD")` → the FMP-blocked equity screen.
  * `GET /tracking/assets` `asset_type` → `AssetDetailRouter` `default:` → same.

The write path also asked FMP for a coin's "company profile" (`profile?symbol=BTCUSD`,
crypto data we do not licence) to get a display name it could read from its own table.

Three contracts pinned here:
  1. the insert carries a lowercase `asset_type` in the wire vocabulary;
  2. a coin add makes NO FMP call and names the coin;
  3. both readers publish `resolve_asset_class(...)`, so rows written before the fix
     (still 'Stock') resolve correctly too.

Plus the DELETE disambiguation: a request that declares "crypto" removes the PAIR row
first, so the crypto screen's star can never delete the user's same-ticker ETF/REIT.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

import app.api.v1.endpoints.watchlist as wl
import app.services.home_dashboard_service as hd
from app.schemas.watchlist import AddToWatchlistRequest, RemoveFromWatchlistRequest

_USER = "u-1"


class _Q:
    def __init__(self, store, table, log):
        self.store, self.table, self.log = store, table, log
        self._op, self._payload, self._filters = "select", None, {}

    def select(self, *_a): self._op = "select"; return self
    def insert(self, p): self._op, self._payload = "insert", p; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, c, v): self._filters[c] = v; return self
    def limit(self, n): return self
    def order(self, *a, **k): return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "insert":
            rows.append(dict(self._payload)); self.log.append(("insert", self.table, dict(self._payload)))
            return type("R", (), {"data": [dict(self._payload)]})()
        matched = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._op == "delete":
            for r in matched: rows.remove(r)
            self.log.append(("delete", self.table, [r["ticker"] for r in matched]))
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _SB:
    def __init__(self, store=None):
        self.store, self.log = store or {}, []

    def table(self, name):
        return _Q(self.store, name, self.log)

    def rpc(self, *a, **k):
        raise RuntimeError("no rpc in this fake")


class _FMP:
    def __init__(self):
        self.calls: List[str] = []

    async def get_company_profile(self, ticker):
        self.calls.append(ticker)
        return {"companyName": "Apple Inc.", "image": "x", "sector": "Technology",
                "industry": "Consumer Electronics", "country": "US", "marketCap": 3e12, "beta": 1.1}


@pytest.fixture
def wired(monkeypatch):
    fmp = _FMP()
    monkeypatch.setattr(wl, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wl, "invalidate_feed_cache", lambda _u: None)
    monkeypatch.setattr(wl, "_ticker_in_active_group", lambda *a: False)
    monkeypatch.setattr(wl, "_write_through_to_active_portfolio", lambda *a: None)
    monkeypatch.setattr(wl, "_delete_through_from_groups", lambda *a: None)
    return fmp


def _add(sb, stock_id, asset_type=None):
    return asyncio.run(wl.add_to_watchlist(
        AddToWatchlistRequest(stock_id=stock_id, asset_type=asset_type), {"id": _USER}, sb))


def _inserted(sb) -> Dict[str, Any]:
    return [e[2] for e in sb.log if e[0] == "insert" and e[1] == "watchlist_items"][-1]


# ── 1 + 2: the write path ────────────────────────────────────────────────────

def test_a_declared_coin_is_stored_as_crypto_with_its_name_and_no_fmp_call(wired):
    sb = _SB()
    _add(sb, "btc", "crypto")
    row = _inserted(sb)
    assert row["ticker"] == "BTCUSD"
    assert row["asset_type"] == "crypto"
    assert row["company_name"] == "Bitcoin"
    assert wired.calls == [], "a coin add asked FMP for a company profile"


def test_an_undeclared_bare_coin_resolves_to_the_coin_and_is_typed_crypto(wired):
    """The shipped build sends no asset_type; `canonical_stored_symbol` resolves toward
    the coin, so the row must be typed to match its spelling."""
    sb = _SB()
    _add(sb, "ETH")
    row = _inserted(sb)
    assert (row["ticker"], row["asset_type"]) == ("ETHUSD", "crypto")


def test_a_declared_security_keeps_the_bare_form_and_is_typed_stock(wired):
    sb = _SB()
    _add(sb, "BTC", "stock")
    row = _inserted(sb)
    assert (row["ticker"], row["asset_type"]) == ("BTC", "stock")
    assert wired.calls == ["BTC"]


@pytest.mark.parametrize("sym, declared, expected", [
    ("SPY", "etf", "etf"), ("SPY", "ETF", "etf"), ("AAPL", None, "stock"),
    ("GCUSD", None, "commodity"), ("^GSPC", None, "index"),
])
def test_every_add_persists_a_lowercase_wire_class(wired, sym, declared, expected):
    sb = _SB()
    _add(sb, sym, declared)
    assert _inserted(sb)["asset_type"] == expected


# ── 3: the readers resolve, so pre-fix rows are right too ───────────────────

class _PS:
    def __init__(self, rows):
        self.rows = rows

    async def get_quotes_list(self, symbols):
        return self.rows


@pytest.mark.asyncio
async def test_home_watchlist_tile_resolves_the_stored_stock_default(monkeypatch):
    """A BTCUSD row still carrying the 'Stock' column default must reach iOS as 'crypto'."""
    rows = [{"ticker": "BTCUSD", "company_name": "Bitcoin", "asset_type": "Stock"},
            {"ticker": "SPY", "company_name": "SPDR S&P 500", "asset_type": "Stock"},
            {"ticker": "AAPL", "company_name": "Apple", "asset_type": "Stock"}]
    quotes = [{"symbol": "BTCUSD", "price": 64000.0, "changePercentage": -1.2, "previousClose": 64800.0},
              {"symbol": "SPY", "price": 650.0, "changePercentage": 0.3, "previousClose": 648.0},
              {"symbol": "AAPL", "price": 230.0, "changePercentage": 0.5, "previousClose": 228.9}]
    # Drive the builder the way the existing tests do — `_service` stubs the Supabase
    # row read and the price source for `_build_watchlist`, which is the real loader.
    # (An earlier draft patched a phantom `_watchlist_rows` with `raising=False`, which
    # `tests/test_patch_targets_exist.py` cannot see through an attribute target — the
    # patch created the name, so its own "renamed — re-point" skip could never fire.)
    from tests.test_home_dashboard_watchlist import _service
    svc = _service(rows=rows, quotes=quotes)
    _, _, tiles = await svc._build_watchlist(_USER)
    types = {t.symbol: t.type for t in tiles}
    assert types == {"BTCUSD": "crypto", "SPY": "stock", "AAPL": "stock"} or types["BTCUSD"] == "crypto"
    assert types["BTCUSD"] == "crypto"
    assert types["AAPL"] == "stock"


def test_tracking_feed_publishes_the_resolved_class():
    import inspect, ast
    from app.services import tracking_service as ts
    src = inspect.getsource(ts.TrackingService)
    tree = ast.parse(src.lstrip() if not src.startswith("class") else src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "resolve_asset_class"]
    kw_sites = [n for n in ast.walk(tree) if isinstance(n, ast.keyword) and n.arg == "asset_type"
                and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) == "resolve_asset_class"]
    assert kw_sites, "TrackedAssetResponse.asset_type is no longer built from resolve_asset_class"


# ── DELETE disambiguation ────────────────────────────────────────────────────

def _remove(sb, stock_id, asset_type=None):
    return asyncio.run(wl.remove_from_watchlist(
        RemoveFromWatchlistRequest(stock_id=stock_id, asset_type=asset_type), {"id": _USER}, sb))


def _both_rows():
    return _SB({"watchlist_items": [
        {"user_id": _USER, "ticker": "LTC", "company_name": "LTC Properties"},
        {"user_id": _USER, "ticker": "LTCUSD", "company_name": "Litecoin"},
    ]})


def test_a_declared_crypto_remove_deletes_the_pair_and_keeps_the_security(wired):
    sb = _both_rows()
    _remove(sb, "LTC", "crypto")
    assert [r["ticker"] for r in sb.store["watchlist_items"]] == ["LTC"]


def test_an_undeclared_remove_deletes_the_raw_spelling_first(wired):
    """The shipped build sends no declaration from the equity screen: raw wins."""
    sb = _both_rows()
    _remove(sb, "LTC")
    assert [r["ticker"] for r in sb.store["watchlist_items"]] == ["LTCUSD"]


def test_a_declared_crypto_remove_falls_back_to_the_bare_row_when_no_pair_exists(wired):
    """Pre-160 leftovers: a bare coin row and a client that now declares crypto."""
    sb = _SB({"watchlist_items": [{"user_id": _USER, "ticker": "DOGE", "company_name": "Dogecoin"}]})
    _remove(sb, "DOGE", "crypto")
    assert sb.store["watchlist_items"] == []
