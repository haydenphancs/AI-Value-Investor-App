"""`asset_type` on every writer and on the degraded feed row — the E3 residue.

TestFlight 1.0 (7): a Dogecoin watchlist row opened the EQUITY detail screen. The routing
itself was fixed server-side (`tracking_service` publishes `resolve_asset_class`, migration
160 canonicalised the bare rows), but three side doors on the same AssetRow → router path
stayed open. Each is pinned here:

  1. `POST /tracking/holdings` wrote `request.asset_type or "Stock"` — an undeclared
     holdings post for an existing ('DOGEUSD', 'crypto') row UPSERTED the capitalised
     column default over the migrated value. It now writes a declared wire class, else a
     SPECIFIC derived class, else omits the key (an omitted key leaves the column alone).
     It also no longer asks FMP for a coin's company profile (crypto data is outside the
     Order Form and never carries a sector).
  2. `PUT /tracking/holdings/{ticker}` persisted the client's string verbatim.
  3. The feed's per-row `except` fallback built a `TrackedAssetResponse` WITHOUT
     `asset_type`, so the wire value was `null`, iOS defaulted it to "stock", and a stored
     'etf'/'index'/'commodity' row whose enrichment threw was routed to the equity screen
     on that refresh — the AST scan in `test_watchlist_asset_type_persistence.py` passed
     on the ONE constructor that had the keyword.

Hermetic: in-memory Supabase/FMP fakes copied from `test_watchlist_row_cap.py` and the
full-build harness from `test_tracking_feed_inflight.py`.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
from pathlib import Path
from typing import Dict, List

import pytest

import app.api.v1.endpoints.tracking as tr
import app.api.v1.endpoints.watchlist as wl
import app.services.tracking_service as ts
from app.schemas.tracking import AddHoldingRequest, UpdateHoldingRequest
from app.services.asset_class import WIRE_CLASSES, stored_asset_type
from app.services.tracking_service import TrackingService

from test_tracking_feed_inflight import _FakeSupabase

_USER = "u-writers"
_REPO = Path(__file__).resolve().parents[2]


# ── fakes ─────────────────────────────────────────────────────────────────────


class _Q:
    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self._op, self._payload, self._filters = "select", None, {}

    def select(self, *_a, **_k): self._op = "select"; return self
    def insert(self, p): self._op, self._payload = "insert", p; return self
    def upsert(self, p, **_k): self._op, self._payload = "upsert", p; return self
    def update(self, p): self._op, self._payload = "update", p; return self
    def eq(self, c, v): self._filters[c] = v; return self
    def neq(self, *_a): return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self

    def execute(self):
        rows = self.sb.store.setdefault(self.table, [])
        if self._op in ("insert", "upsert", "update"):
            # Mirror the one NOT NULL column without a default on `watchlist_items`: a fresh
            # insert/upsert that omits `company_name` fails in Postgres, so it fails here.
            if self._op in ("insert", "upsert") and self.table == "watchlist_items":
                exists = any(r.get("ticker") == self._payload.get("ticker") for r in rows)
                if not exists and not self._payload.get("company_name"):
                    raise RuntimeError('null value in column "company_name" violates not-null constraint')
            self.sb.writes.append((self._op, self.table, dict(self._payload)))
            if self._op == "update":
                matched = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
                for r in matched:
                    r.update(self._payload)
                return type("R", (), {"data": [dict(r) for r in matched]})()
            rows.append(dict(self._payload))
            return type("R", (), {"data": [dict(self._payload)]})()
        matched = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        return type("R", (), {"data": [dict(r) for r in matched], "count": len(matched)})()


class _SB:
    def __init__(self, rows=()):
        self.store = {"watchlist_items": [dict(r, user_id=_USER, id=i) for i, r in enumerate(rows)]}
        self.writes: List[tuple] = []

    def table(self, name):
        return _Q(self, name)


class _FMP:
    def __init__(self):
        self.calls: List[str] = []

    async def get_company_profile(self, ticker):
        self.calls.append(ticker)
        return {"companyName": f"{ticker} Inc.", "sector": "Technology", "country": "US"}


@pytest.fixture
def wired(monkeypatch):
    fmp = _FMP()
    monkeypatch.setattr(tr, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wl, "get_fmp_client", lambda: fmp)

    class _NoPrice:
        async def get_quote(self, _symbol):
            return {}
    monkeypatch.setattr(tr, "price_source", lambda *a, **k: _NoPrice())
    return fmp


def _post_holding(sb, ticker, asset_type=None):
    kw = {"asset_type": asset_type} if asset_type is not None else {}
    return asyncio.run(tr.add_holding(
        AddHoldingRequest(ticker=ticker, shares=1.0, market_value=None, **kw), {"id": _USER}, sb))


def _last_write(sb):
    assert sb.writes, "nothing was written"
    return sb.writes[-1][2]


# ── 1. POST /tracking/holdings ────────────────────────────────────────────────


def test_an_undeclared_coin_is_stored_as_the_pair_typed_crypto_without_an_fmp_profile(wired):
    sb = _SB()
    resp = _post_holding(sb, "DOGE")
    assert not hasattr(resp, "status_code") or resp.status_code < 400, resp
    row = _last_write(sb)
    assert row["ticker"] == "DOGEUSD"
    assert row["asset_type"] == "crypto"
    assert wired.calls == [], "a coin has no FMP company profile — the call is outside the Order Form"
    # `company_name` is NOT NULL and no profile names a coin: the display-name map must.
    assert row["company_name"] == "Dogecoin", row


def test_a_declared_coin_name_from_the_client_is_kept(wired):
    sb = _SB()
    asyncio.run(tr.add_holding(
        AddHoldingRequest(ticker="BTC", shares=1.0, market_value=None, asset_type="crypto",
                          company_name="My Bitcoin"), {"id": _USER}, sb))
    row = _last_write(sb)
    assert row["ticker"] == "BTCUSD" and row["company_name"] == "My Bitcoin"


def test_an_undeclared_equity_omits_the_key_so_an_upsert_leaves_the_column_alone(wired):
    sb = _SB()
    _post_holding(sb, "AAPL")
    row = _last_write(sb)
    assert "asset_type" not in row, (
        "a derived 'stock' is the column default — writing it would clobber a stored "
        "'crypto'/'etf' on a re-add"
    )
    assert wired.calls == ["AAPL"]


def test_a_declared_security_stays_bare_and_lowercase(wired):
    sb = _SB()
    _post_holding(sb, "BTC", asset_type="Stock")   # the Grayscale ETF, not the coin
    row = _last_write(sb)
    assert row["ticker"] == "BTC"
    assert row["asset_type"] == "stock"


@pytest.mark.parametrize("declared, expected", [("etf", "etf"), ("ETF ", "etf"), ("index", "index")])
def test_a_declared_wire_class_is_persisted_normalised(wired, declared, expected):
    sb = _SB()
    _post_holding(sb, "SPY", asset_type=declared)
    assert _last_write(sb)["asset_type"] == expected


def test_a_garbage_declaration_never_reaches_the_column(wired):
    sb = _SB()
    _post_holding(sb, "AAPL", asset_type="equity")
    assert "asset_type" not in _last_write(sb)


def _code(src: str) -> str:
    """Comment-stripped — the explanatory comment beside the fix quotes the old line."""
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def test_the_holdings_insert_never_writes_the_capitalised_default():
    src = _code(inspect.getsource(tr.add_holding))
    assert 'or "Stock"' not in src, "the default-writing line is back"
    assert "stored_asset_type(" in src
    # And the request schema no longer declares every post a security by default.
    assert AddHoldingRequest(ticker="X", shares=1.0).asset_type is None


# ── 2. PUT /tracking/holdings/{ticker} ────────────────────────────────────────


def test_the_holdings_update_normalises_the_declared_class(wired):
    sb = _SB(rows=[{"ticker": "DOGEUSD", "asset_type": "crypto", "shares": 1.0}])
    asyncio.run(tr.update_holding(
        "DOGEUSD", UpdateHoldingRequest(shares=2.0, asset_type="Crypto"), {"id": _USER}, sb))
    row = _last_write(sb)
    assert row["asset_type"] == "crypto"


def test_the_holdings_update_refuses_a_garbage_class_instead_of_dropping_it(wired):
    """It used to persist the string verbatim; then a first fix silently dropped it and, when
    it was the only field, answered "Nothing to update." to a request that did send one."""
    sb = _SB(rows=[{"ticker": "AAPL", "asset_type": "stock", "shares": 1.0}])
    resp = asyncio.run(tr.update_holding(
        "AAPL", UpdateHoldingRequest(shares=2.0, asset_type="Equity"), {"id": _USER}, sb))
    assert getattr(resp, "status_code", None) == 400
    body = resp.body.decode()
    assert '"INVALID_INPUT"' in body and "crypto" in body and "etf" in body, body
    assert sb.writes == [], "a refused request must write nothing — not even the other fields"


# ── 3. the pure rule ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("symbol, declared, expected", [
    ("DOGEUSD", None, "crypto"),      # derived, specific → written
    ("^GSPC", None, "index"),
    ("GCUSD", None, "commodity"),
    ("AAPL", None, None),             # derived stock == column default → omitted
    ("BTC", None, None),              # bare coin = the listed security → omitted
    ("BTC", "crypto", "crypto"),      # declared wins
    ("AAPL", "ETF", "etf"),
    ("AAPL", "equity", None),         # unknown vocabulary → omitted
    ("AAPL", "  stock ", "stock"),
])
def test_stored_asset_type_table(symbol, declared, expected):
    assert stored_asset_type(symbol, declared) == expected


def test_wire_classes_match_the_ios_router_vocabulary():
    """`WIRE_CLASSES` is what iOS `MarketTickerType` switches on; a value written that the
    router cannot decode falls to its symbol fallback silently."""
    swift = (_REPO / "frontend/ios/ios/Models/HomeModels.swift").read_text()
    start = swift.index("enum MarketTickerType")
    body = swift[swift.index("{", start):]
    body = body[: body.index("static func resolve")]
    cases = set(re.findall(r"^\s*case (\w+)", body, re.M))
    assert cases == set(WIRE_CLASSES), (cases, WIRE_CLASSES)
    assert wl._WIRE_CLASSES is WIRE_CLASSES, "watchlist.py must use the hoisted constant"


# ── 4. the degraded feed row ──────────────────────────────────────────────────


def test_both_feed_row_constructors_carry_the_resolved_class():
    """The AST scan in test_watchlist_asset_type_persistence.py passed with ONE site."""
    tree = ast.parse(inspect.getsource(ts.TrackingService))
    sites = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "TrackedAssetResponse"]
    assert len(sites) == 2, f"expected the enriched + fallback constructors, found {len(sites)}"
    for call in sites:
        kw = {k.arg: k.value for k in call.keywords}
        assert "asset_type" in kw, "a TrackedAssetResponse without asset_type ships null"
        assert isinstance(kw["asset_type"], ast.Call)
        assert getattr(kw["asset_type"].func, "id", None) == "resolve_asset_class"


@pytest.mark.asyncio
async def test_a_row_whose_enrichment_throws_still_carries_its_class(monkeypatch, caplog):
    """Driven: a stored 'etf' row whose enrichment RAISES hits the per-row `except` and
    must still go out typed 'etf' — it used to go out `null` → "stock".

    The raise has to be real: a non-numeric price is folded to None by `_finite_or_none`
    on the NORMAL path (that row degrades without ever reaching the `except`), which is
    how a first version of this test passed while pinning nothing. A quote row that is
    not a dict makes `quote.get(...)` raise inside the `try`; the fallback's own log line
    proves the path taken."""
    watchlist = [{"id": 1, "ticker": "SPY", "company_name": "SPDR S&P 500", "asset_type": "etf"},
                 {"id": 2, "ticker": "AAPL", "company_name": "Apple", "asset_type": "stock"}]
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def _quotes(self, tickers):
        return {"SPY": ["not", "a", "dict"], "AAPL": {"price": 190.0, "previousClose": 189.0}}
    async def _nothing(self, *a, **k): return {}
    async def _no_list(self, *a, **k): return []
    async def _backfill(self, user_id, watchlist): return None
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _nothing),
                     ("_get_earnings_alerts", _no_list), ("_get_whale_trade_alerts", _no_list),
                     ("_get_analyst_rating_alerts", _no_list),
                     ("_get_insider_transaction_alerts", _no_list),
                     ("_backfill_classification", _backfill)]:
        monkeypatch.setattr(TrackingService, name, fn)
    ts._feed_cache.clear(); ts._feed_inflight.clear()

    with caplog.at_level("ERROR", logger="app.services.tracking_service"):
        feed = await TrackingService().get_tracking_feed(_USER)

    assert any("Failed to enrich ticker SPY" in r.getMessage() for r in caplog.records), (
        "VACUOUS: the SPY row never reached the per-row except"
    )
    by = {a.ticker: a for a in feed.assets}
    assert set(by) == {"SPY", "AAPL"}, "every watchlist row must stay in the feed"
    assert by["AAPL"].price_known is True and by["AAPL"].asset_type == "stock"
    assert by["SPY"].price_known is False
    assert by["SPY"].asset_type == "etf", "the degraded row must keep its class"
