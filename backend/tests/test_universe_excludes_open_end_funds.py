"""F4 (2026-09-11, Railway): open-end mutual funds must not reach a movers ranking.

Measured on the live screener sweep (>$50M, NASDAQ/NYSE/AMEX, actively trading): without
`isFund=false` page 0 was 10,000 rows of which 3,719 were open-end funds (`GOLDX` = Gabelli
Gold Fund, `isFund: true`, `volume: 0`, one NAV print a day) plus a second 1,648-row page;
with it the whole universe is one 7,116-row page. The widget's prior-session WARNING named
GOLDX every cycle, and — worse — a watchlisted fund served through the `/stable/profile`
fallback arrives UNSTAMPED (no `changeSession`), so without an explicit `isFund` refusal it
would be RANKED, not dropped.

Three layers, each pinned here:
  1. the universe sweep asks the screener for `isFund=false`;
  2. every quote-shaped row carries `isEtf` / `isFund` (both sources have the flags);
  3. the widget refuses `isFund` rows before ranking.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import pytest

import app.services.price_service as ps_module
import app.services.widget_movers_service as wm
from app.integrations.fmp import FMPClient
from app.services.price_service import PriceService


# ── 1. the sweep ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_universe_sweep_asks_the_screener_to_exclude_funds(monkeypatch):
    seen: List[Dict[str, Any]] = []

    class _FMP:
        async def get_company_screener(self, **kw):
            seen.append(kw)
            return []

    monkeypatch.setattr(ps_module, "get_fmp_client", lambda: _FMP())
    await PriceService()._fetch_universe_pages()
    assert seen and seen[0].get("is_fund") is False, seen
    assert seen[0].get("actively_trading") is True  # the existing filters stay


@pytest.mark.asyncio
async def test_screener_wrapper_sends_isfund_only_when_asked(monkeypatch):
    calls: List[Dict[str, Any]] = []

    async def _fake_request(self, endpoint, params=None, **_):
        calls.append({"endpoint": endpoint, "params": dict(params or {})})
        return []

    monkeypatch.setattr(FMPClient, "_make_request", _fake_request)
    client = FMPClient.__new__(FMPClient)
    await client.get_company_screener(limit=10)
    await client.get_company_screener(limit=10, is_fund=False)
    await client.get_company_screener(limit=10, is_fund=True, is_etf=False)
    assert "isFund" not in calls[0]["params"] and "isEtf" not in calls[0]["params"]
    assert calls[1]["params"]["isFund"] == "false"
    assert calls[2]["params"]["isFund"] == "true" and calls[2]["params"]["isEtf"] == "false"
    assert all(c["endpoint"] == "company-screener" for c in calls)


# ── 2. the row flags ────────────────────────────────────────────────────────────────


def test_every_quote_row_carries_the_fund_and_etf_flags():
    prof = PriceService._from_profile({"symbol": "GOLDX", "price": 53.1, "isFund": True, "isEtf": False})
    assert prof["isFund"] is True and prof["isEtf"] is False
    scr = PriceService._from_screener({"symbol": "GLD", "price": 250.0, "isEtf": True}, None)
    assert scr["isEtf"] is True and scr["isFund"] is False
    plain = PriceService._from_profile({"symbol": "AAPL", "price": 190.0})
    assert plain["isFund"] is False and plain["isEtf"] is False, "absent flags read as a company"
    assert PriceService._shape(
        symbol="X", name=None, price=None, previous_close=None, change=None, change_pct=None,
        volume=None, avg_volume=None, market_cap=None, exchange=None,
    )["isFund"] is False


# ── 3. the widget ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_widget_refuses_a_fund_row_before_ranking(monkeypatch, caplog):
    quotes = {
        # UNSTAMPED, as the profile fallback serves it — would otherwise rank, not drop.
        "GOLDX": {"symbol": "GOLDX", "name": "Gabelli Gold Fund", "price": 53.0,
                  "changePercentage": -2.0, "previousClose": 54.08, "marketCap": 1.2e9,
                  "isFund": True, "isEtf": False},
        "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "price": 130.0, "changePercentage": 1.5,
                 "previousClose": 128.08, "marketCap": 3.1e12, "isFund": False, "isEtf": False,
                 "changeSession": "2026-09-11"},
    }
    svc = wm.WidgetMoversService.__new__(wm.WidgetMoversService)

    async def _quotes(symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    class _Vol:
        async def get_sigmas_bulk(self, symbols):
            return {s: 0.02 for s in symbols}

    class _News:
        async def get_cards(self, tickers):
            return {}

    monkeypatch.setattr(svc, "_quotes", _quotes, raising=False)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Vol())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    with caplog.at_level(logging.INFO, logger="app.services.widget_movers_service"):
        ranked, _cards, _news_ok, _idx = await svc._rank_and_read(["GOLDX", "NVDA"])
    tickers = [m.ticker for m in ranked]
    assert "GOLDX" not in tickers, "an open-end fund was ranked as a mover"
    assert tickers == ["NVDA"]
    assert any("open-end fund row(s) excluded" in r.getMessage() and "GOLDX" in r.getMessage()
               for r in caplog.records if r.levelno == logging.INFO)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], \
        "the fund must be refused quietly, not dropped as a prior-session WARNING"


# ── F46: a 24/7 asset's rolling change is not an equity-session move ────────────────


def _m(ticker, session=None, change=1.0):
    from app.services.widget_movers_service import rank_movers
    return rank_movers([{
        "ticker": ticker, "change_percent": change, "price": 100.0,
        "company_name": ticker, "market_cap": 1e12, "sigma_daily": 0.02,
        "open": 99.0, "previous_close": 99.0, "change_session": session,
    }])[0]


def test_a_crypto_pair_never_sets_the_batch_session():
    """Stamping it forward (today) was the first attempt and it AGED OUT every
    legitimately Friday-stamped equity at Monday pre-market — the exact mis-drop
    `drop_prior_session_movers` exists to prevent."""
    equity = _m("NVDA", "2026-09-11")
    pair = _m("BTCUSD", "2026-09-14")        # even if something stamps it
    assert wm.newest_session([equity, pair]).isoformat() == "2026-09-11", (
        "a 24/7 row dragged the batch session forward and would strand the equities"
    )


def test_a_crypto_pair_is_never_dropped_as_a_prior_session_row():
    equity = _m("NVDA", "2026-09-11")
    # BOTH shapes: unstamped (what CoinGecko quotes actually carry) AND stamped with an
    # older date (what a cached row, or any future writer, could carry). The second is
    # what makes the exemption load-bearing — without it a stale-looking stamp on a 24/7
    # asset drops it from a tile it legitimately belongs on.
    pair = _m("BTCUSD", None)
    stamped_pair = _m("ETHUSD", "2026-09-08")
    current, stale = wm.drop_prior_session_movers([equity, pair, stamped_pair])
    assert {m.ticker for m in current} == {"NVDA", "BTCUSD", "ETHUSD"}
    assert stale == []
    # …and a genuinely stale EQUITY is still dropped (anti-vacuity control).
    old_equity = _m("GOLDX2", "2026-09-08")
    current, stale = wm.drop_prior_session_movers([equity, old_equity])
    assert [m.ticker for m in stale] == ["GOLDX2"]


def test_a_crypto_head_never_moves_the_tiles_session_date():
    """The 24/7 wording is a PER-ROW concern; the date is not.

    `_session_of` also returns the date every detector is gated on — `_classified_today_news`
    keys on it, `attribute(..., earnings_row=)` keys on it, and iOS re-derives the tile's
    "Fri close" label from it. Returning the LIVE session because the head happened to be a
    crypto pair moved all three: on a Monday pre-market, with the screener still reporting
    Friday's close, the equity runners narrated Friday's −4.8% as "…today", the news lookup
    was keyed on Monday and matched nothing (printing the confident negative "No company
    news today"), and a Friday move was presented as Monday's — verbatim the cross-session
    bug `_session_of` was written to kill.
    """
    from datetime import date

    live = date(2026, 9, 14)                      # Monday
    friday = date(2026, 9, 11)
    pair_head = [_m("BTCUSD", None), _m("NVDA", "2026-09-11")]
    d, iso, word = wm.WidgetMoversService._session_of(pair_head, live)
    assert d == friday and iso == friday.isoformat(), (
        "a crypto head dragged the tile's session date to the live session — every "
        "equity-only detector is then asked about the wrong day"
    )
    assert word.startswith("on "), (
        "the tile-level word describes the equity session the numbers came from"
    )
    # An EQUITY head is unchanged.
    equity_head = [_m("NVDA", "2026-09-11"), _m("BTCUSD", None)]
    d2, _iso2, word2 = wm.WidgetMoversService._session_of(equity_head, live)
    assert (d2, word2) == (friday, word)
    # …and with no stale equity stamp at all it is still the live session (control).
    d3, _iso3, word3 = wm.WidgetMoversService._session_of([_m("BTCUSD", None)], live)
    assert (d3, word3) == (live, "today")


def test_a_round_the_clock_row_is_narrated_today_whatever_the_tile_session_is():
    """The word IS overridden — just on the row, in `_build_mover`, not on the tile."""
    import ast
    import inspect
    import re

    src = inspect.getsource(wm)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "_build_mover")
    body = ast.get_source_segment(src, fn)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())
    assert "_is_round_the_clock(m.ticker)" in code, (
        "a crypto row's rolling 24-hour move is narrated with the equity session's word "
        "— 'Up 6.1% on Fri' about an asset that has no Friday close"
    )
    assert 'session_word = "today"' in code
    # It must happen BEFORE the reason is built, or the override is inert.
    assert code.index("_is_round_the_clock(m.ticker)") < code.index("deterministic_reason(")


def test_the_round_the_clock_predicate_is_source_based():
    assert wm._is_round_the_clock("BTCUSD") is True
    assert wm._is_round_the_clock("BTC") is False, "bare BTC is the Grayscale ETF"
    assert wm._is_round_the_clock("NVDA") is False
    assert wm._is_round_the_clock("") is False


# ── the market band must not headline the tile it is the yardstick for ───────────────


@pytest.mark.asyncio
async def test_the_market_band_symbols_are_never_ranked_as_movers(monkeypatch):
    """`market_change` IS `index_rows[MARKET_INDEX_SYMBOL]["changePercentage"]`, so a SPY
    headline attributes SPY's move to itself: "The market fell 1.6% today; SPY moved with
    it." The whole band is also already drawn as its own row above the headline, so any of
    them headlining prints the same number twice.

    The batch comment claimed this already ("excluded from ranking below; an index is not a
    'mover' the widget can attribute") and it held only for the symbols APPENDED for
    quoting — a user who watchlists SPY put it straight into `symbols`. In portfolio mode
    it is systematic rather than rare: z = |r|/σ, and σ is smaller for the index proxy than
    for its constituents, so on a market-driven day with no idiosyncratic news it has the
    highest z of a small group BY CONSTRUCTION.
    """
    quotes = {
        "SPY": {"symbol": "SPY", "name": "S&P 500 ETF", "price": 651.0,
                "changePercentage": -1.6, "previousClose": 661.6, "marketCap": 6.0e11,
                "isFund": False, "isEtf": True},
        "ONEQ": {"symbol": "ONEQ", "name": "Nasdaq Comp ETF", "price": 80.0,
                 "changePercentage": -1.9, "previousClose": 81.6, "marketCap": 8.0e9,
                 "isFund": False, "isEtf": True},
        "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "price": 130.0,
                 "changePercentage": -0.4, "previousClose": 130.5, "marketCap": 3.1e12,
                 "isFund": False, "isEtf": False},
    }
    svc = wm.WidgetMoversService.__new__(wm.WidgetMoversService)

    async def _quotes(symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    class _Vol:
        async def get_sigmas_bulk(self, symbols):
            # The index proxy is the LEAST volatile, which is what hands it the top z.
            return {s: (0.004 if s in ("SPY", "ONEQ") else 0.02) for s in symbols}

    class _News:
        async def get_cards(self, tickers):
            return {}

    monkeypatch.setattr(svc, "_quotes", _quotes, raising=False)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Vol())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())

    ranked, _cards, _ok, index_rows = await svc._rank_and_read(["SPY", "ONEQ", "NVDA"])
    tickers = [m.ticker for m in ranked]
    assert "SPY" not in tickers, (
        "the widget ranked the very instrument its market leg is computed from — the "
        "headline attributes the market's move to itself"
    )
    assert "ONEQ" not in tickers, "the whole band is drawn above the headline already"
    assert tickers == ["NVDA"], tickers
    # …and the band is still QUOTED, so the market row and the context keep working.
    assert "SPY" in index_rows and index_rows["SPY"]["changePercentage"] == -1.6, (
        "excluding the band from RANKING must not stop it being READ — the market leg and "
        "the index row both come from these quotes"
    )


@pytest.mark.asyncio
async def test_an_ordinary_etf_is_still_rankable(monkeypatch):
    """Control. The exclusion is the market BAND, not ETFs — GLD and TQQQ are ordinary
    tradable assets and ranking them is correct. A guard that rejected every ETF would
    pass the test above while quietly emptying the tile."""
    quotes = {
        "GLD": {"symbol": "GLD", "name": "SPDR Gold", "price": 250.0,
                "changePercentage": 3.1, "previousClose": 242.5, "marketCap": 7.0e10,
                "isFund": False, "isEtf": True},
        "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "price": 130.0,
                 "changePercentage": 0.2, "previousClose": 129.7, "marketCap": 3.1e12,
                 "isFund": False, "isEtf": False},
    }
    svc = wm.WidgetMoversService.__new__(wm.WidgetMoversService)

    async def _quotes(symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    class _Vol:
        async def get_sigmas_bulk(self, symbols):
            return {s: 0.02 for s in symbols}

    class _News:
        async def get_cards(self, tickers):
            return {}

    monkeypatch.setattr(svc, "_quotes", _quotes, raising=False)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Vol())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())

    ranked, *_ = await svc._rank_and_read(["GLD", "NVDA"])
    assert "GLD" in [m.ticker for m in ranked], "an ordinary ETF must still be rankable"

