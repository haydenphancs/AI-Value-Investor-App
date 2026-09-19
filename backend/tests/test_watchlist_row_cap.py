"""`WATCHLIST_MAX_ITEMS` is enforced on BOTH insert paths, before the FMP call (F15-3).

`POST /watchlist` had no per-user row cap, and every add costs one FMP profile call, so a
scripted free account could grow a 2,000-row watchlist that the Tracking feed then fanned
out over on every refresh. `POST /tracking/holdings` upserts the SAME table, so a cap on
one route alone is bypassable through the other.

Pinned here:
  * at the cap, both routes answer 400 INVALID_INPUT, write nothing and make NO FMP call;
  * one under the cap still inserts; a re-add / holdings edit of a ticker already on the
    list is never refused (counted excluding that ticker);
  * `<= 0` disables; a count read that fails (or returns no count) fails OPEN and is
    logged — the cap is an abuse bound, not a correctness invariant.

No network / Supabase / FMP.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import pytest

import app.api.v1.endpoints.tracking as tr
import app.api.v1.endpoints.watchlist as wl
import app.services.tracking_service as ts
from app.config import settings
from app.schemas.tracking import AddHoldingRequest
from app.schemas.watchlist import AddToWatchlistRequest

_USER = "u-cap"


class _Q:
    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self._op, self._payload, self._filters, self._neq = "select", None, {}, None
        self._count = None

    def select(self, *_a, **k): self._op = "select"; self._count = k.get("count"); return self
    def insert(self, p): self._op, self._payload = "insert", p; return self
    def upsert(self, p, **_k): self._op, self._payload = "upsert", p; return self
    def eq(self, c, v): self._filters[c] = v; return self
    def neq(self, c, v): self._neq = (c, v); return self
    def limit(self, *_a): return self
    def order(self, *_a, **_k): return self

    def execute(self):
        if self.sb.count_error is not None and self._count == "exact":
            raise self.sb.count_error
        rows = self.sb.store.setdefault(self.table, [])
        if self._op in ("insert", "upsert"):
            self.sb.writes.append((self._op, self.table, dict(self._payload)))
            rows.append(dict(self._payload))
            return type("R", (), {"data": [dict(self._payload)]})()
        matched = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._neq:
            c, v = self._neq
            matched = [r for r in matched if r.get(c) != v]
        count = None if self.sb.count_none else len(matched)
        return type("R", (), {"data": [dict(r) for r in matched], "count": count})()


class _SB:
    def __init__(self, tickers=(), *, count_error=None, count_none=False):
        self.store = {"watchlist_items": [{"id": i, "user_id": _USER, "ticker": t}
                                          for i, t in enumerate(tickers)]}
        self.writes: List[tuple] = []
        self.count_error = count_error
        self.count_none = count_none

    def table(self, name):
        return _Q(self, name)


class _FMP:
    def __init__(self):
        self.calls: List[str] = []

    async def get_company_profile(self, ticker):
        self.calls.append(ticker)
        return {"companyName": f"{ticker} Inc.", "image": None}


@pytest.fixture
def cap3(monkeypatch):
    monkeypatch.setattr(settings, "WATCHLIST_MAX_ITEMS", 3)


@pytest.fixture
def wired(monkeypatch):
    fmp = _FMP()
    monkeypatch.setattr(wl, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(tr, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wl, "invalidate_feed_cache", lambda _u: None)
    monkeypatch.setattr(wl, "_ticker_in_active_group", lambda *a: False)
    monkeypatch.setattr(wl, "_write_through_to_active_portfolio", lambda *a: None)

    class _NoPrice:
        async def get_quote(self, _symbol):
            return {}

    # `add_holding` prices the holding through the price service singleton, not `fmp`;
    # left unstubbed it reaches the hermeticity guard and is swallowed by the handler's
    # own except — green for the wrong reason.
    monkeypatch.setattr(tr, "price_source", lambda *a, **k: _NoPrice())
    return fmp


def _post_watchlist(sb, ticker, asset_type=None):
    return asyncio.run(wl.add_to_watchlist(
        AddToWatchlistRequest(stock_id=ticker, asset_type=asset_type), {"id": _USER}, sb))


def _post_holding(sb, ticker, shares=1.0, asset_type=None):
    kw = {"asset_type": asset_type} if asset_type is not None else {}
    return asyncio.run(tr.add_holding(
        AddHoldingRequest(ticker=ticker, shares=shares, market_value=None, **kw), {"id": _USER}, sb))


def _status(resp):
    return getattr(resp, "status_code", 201 if isinstance(resp, dict) else None)


# ── the helper ───────────────────────────────────────────────────────────────

def test_the_fake_counts_rows_excluding_the_neq_ticker():
    sb = _SB(["A", "B", "C"])
    r = sb.table("watchlist_items").select("id", count="exact").eq("user_id", _USER).neq("ticker", "A").execute()
    assert r.count == 2


def test_full_at_the_cap_and_not_one_under(cap3):
    assert ts.watchlist_is_full(_SB(["A", "B", "C"]), _USER, "D") is True
    assert ts.watchlist_is_full(_SB(["A", "B"]), _USER, "D") is False
    assert ts.watchlist_is_full(_SB([]), _USER, "D") is False


def test_a_ticker_already_on_the_list_is_never_refused_at_the_cap(cap3):
    """Counted EXCLUDING the ticker, so a re-add / holdings edit of an existing row passes."""
    assert ts.watchlist_is_full(_SB(["A", "B", "C"]), _USER, "C") is False


@pytest.mark.parametrize("cap", [0, -1, None])
def test_a_non_positive_cap_disables_the_check(monkeypatch, cap):
    monkeypatch.setattr(settings, "WATCHLIST_MAX_ITEMS", cap)
    assert ts.watchlist_is_full(_SB([f"T{i}" for i in range(5000)]), _USER, "NEW") is False


def test_a_failed_count_read_fails_open_and_is_logged(cap3, caplog):
    with caplog.at_level(logging.WARNING):
        assert ts.watchlist_is_full(_SB(["A", "B", "C"], count_error=RuntimeError("520")), _USER, "D") is False
    assert any("cap not enforced" in r.getMessage() and _USER in r.getMessage() for r in caplog.records)


def test_a_missing_count_fails_open_and_is_logged(cap3, caplog):
    with caplog.at_level(logging.WARNING):
        assert ts.watchlist_is_full(_SB(["A", "B", "C"], count_none=True), _USER, "D") is False
    assert any("no count" in r.getMessage() for r in caplog.records)


# ── POST /watchlist ──────────────────────────────────────────────────────────

def test_post_watchlist_refuses_at_the_cap_with_no_write_and_no_fmp_call(cap3, wired):
    sb = _SB(["A", "B", "C"])
    resp = _post_watchlist(sb, "D")
    assert _status(resp) == 400
    body = resp.body.decode()
    assert '"INVALID_INPUT"' in body and "full" in body and '"max":3' in body.replace(" ", "")
    assert sb.writes == [], "nothing may be written at the cap"
    assert wired.calls == [], "the refusal must not cost the FMP profile call it exists to bound"


def test_post_watchlist_one_under_the_cap_still_inserts(cap3, wired):
    sb = _SB(["A", "B"])
    resp = _post_watchlist(sb, "D")
    assert _status(resp) == 201
    assert [w[2]["ticker"] for w in sb.writes] == ["D"]
    assert wired.calls == ["D"]


def test_post_watchlist_re_add_at_the_cap_takes_the_converge_path_not_the_refusal(cap3, wired, monkeypatch):
    """A ticker already on the list at the cap: the duplicate/converge branch runs FIRST and
    reports success — the cap never sees it."""
    mirrored = []
    monkeypatch.setattr(wl, "_write_through_to_active_portfolio", lambda _sb, _u, t: mirrored.append(t))
    sb = _SB(["A", "B", "C"])
    resp = _post_watchlist(sb, "C")
    assert isinstance(resp, dict) and resp["ticker"] == "C"
    assert mirrored == ["C"] and sb.writes == []


def test_post_watchlist_cap_counts_the_canonical_spelling(cap3, wired):
    """A coin declared crypto is stored as the pair; the exclusion must use that spelling.

    DISCRIMINATING (W2 vacuity-2-3: the old case posted ETH against [A, B, BTCUSD], and
    neither "ETH" nor "ETHUSD" was on the list, so raw-vs-canonical exclusion refused
    either way). The row on the list is BTCUSD and the request says "BTC"/crypto: only
    the CANONICAL exclusion counts 2 and lets the re-add through.
    """
    sb = _SB(["A", "B", "BTCUSD"])
    assert _status(_post_watchlist(sb, "BTC", "crypto")) != 400, "canonical spelling excluded → not full"
    # Control: a coin NOT on the list is refused at the cap under either spelling.
    assert _status(_post_watchlist(_SB(["A", "B", "BTCUSD"]), "ETH", "crypto")) == 400


def test_post_holdings_cap_counts_the_canonical_spelling(cap3, wired):
    """The holdings door has no converge-before-cap step, so the exclusion spelling is
    load-bearing there: editing shares on an existing BTCUSD row by sending "BTC"/crypto
    at the cap was refused as 'watchlist full' under a raw exclusion."""
    sb = _SB(["A", "B", "BTCUSD"])
    resp = _post_holding(sb, "BTC", shares=2.0, asset_type="crypto")
    assert _status(resp) != 400, resp
    assert [(w[0], w[2]["ticker"]) for w in sb.writes] == [("upsert", "BTCUSD")], sb.writes
    assert _status(_post_holding(_SB(["A", "B", "BTCUSD"]), "ETH", asset_type="crypto")) == 400


# ── POST /tracking/holdings (the second door) ────────────────────────────────

def test_post_holdings_refuses_at_the_cap_with_no_write_and_no_fmp_call(cap3, wired):
    sb = _SB(["A", "B", "C"])
    resp = _post_holding(sb, "D")
    assert _status(resp) == 400
    assert '"INVALID_INPUT"' in resp.body.decode()
    assert sb.writes == []
    assert wired.calls == []


def test_post_holdings_edit_of_an_existing_ticker_at_the_cap_is_allowed(cap3, wired):
    sb = _SB(["A", "B", "C"])
    resp = _post_holding(sb, "C", shares=5.0)
    assert _status(resp) != 400
    assert [w[0] for w in sb.writes] == ["upsert"]


def test_post_holdings_one_under_the_cap_upserts(cap3, wired):
    sb = _SB(["A", "B"])
    resp = _post_holding(sb, "D")
    assert _status(resp) != 400
    assert [w[2]["ticker"] for w in sb.writes] == ["D"]


# ── source guard: both doors, before the FMP call ────────────────────────────

def _order_in(module, handler: str) -> List[str]:
    import ast, inspect
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(module)))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == handler)
    names = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "to_thread" and n.args:
                names.append(getattr(n.args[0], "id", None))
            else:
                names.append(getattr(f, "id", None) or getattr(f, "attr", None))
    return names


@pytest.mark.parametrize("module,handler", [(wl, "add_to_watchlist"), (tr, "add_holding")])
def test_both_insert_paths_check_the_cap_before_the_fmp_profile_call(module, handler):
    order = _order_in(module, handler)
    assert "watchlist_is_full" in order, f"{handler} lost the row-cap check — the other door is open"
    assert "get_company_profile" in order
    assert order.index("watchlist_is_full") < order.index("get_company_profile"), (
        "the cap must be checked BEFORE the FMP profile call, or the refusal costs the call"
    )
