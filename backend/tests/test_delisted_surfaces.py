"""Every surface carries the "Delisted" fields — and only for a CONFIRMED delisting.

The wire contract (owner D1-D4, 2026-11-13): four keys, all null unless
`listing_status_service` confirms a delisting, then all set together — `listing_status`
("delisted"), `delisted_date`, `delisted_exchange`, `last_trade_date`. Carried on the core and
full overviews, `GET /stocks/{t}`, `GET /stocks/{t}/quote`, the Tracking feed, the Home
watchlist tiles and the chat stock card. Existing fields keep their meaning: the price is the
last close, `change_known` stays false, `market_status` stays the clock's.

ROKU is the live case (Fox Corp acquired Roku; NASDAQ, delisted 2026-11-09, last trade
2026-11-06 at $160.52). Each surface is driven for real with its upstreams stubbed; the
listing service's own FMP client is the counting fake from `test_listing_status_service`.
The registry is published for the REAL clock's session, because the surfaces pass no `now`.
"""

from __future__ import annotations

import logging
import math
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

import app.services.stock_overview_service as sos
import app.services.tracking_service as ts
from app.api.v1.endpoints import stocks
from app.schemas.listing_status import LISTING_FIELDS
from app.services import listing_status_service as ls
from app.services import session_pricing as sp
from app.services.agents import chat_tools
from app.services.chat_service import ChatService
from app.services.price_service import PriceService
from app.services.stock_overview_service import StockOverviewService
from app.services.tracking_service import TrackingService
from app.utils.market_hours import previous_trading_day, session_trading_date
from _price_fakes import PriceFromFMPFake
from tests.test_listing_status_service import ROKU_PROFILE, _FMP, _profile, _settle

ROKU_WIRE = {"listing_status": "delisted", "delisted_date": "2026-11-09",
             "delisted_exchange": "NASDAQ", "last_trade_date": "2026-11-06"}
NULL_WIRE = {k: None for k in LISTING_FIELDS}
OBK_PROFILE: Dict[str, Any] = {
    "symbol": "OBK", "companyName": "Origin Bancorp, Inc.", "exchange": "NYSE", "price": 52.47,
    "change": 0.15, "changePercentage": 0.2867, "volume": 232332, "averageVolume": 164802,
    "marketCap": 1621538127, "range": "32.125-55.57", "isActivelyTrading": True,
    "isEtf": False, "isFund": False,
}


def _publish_current(traded=("AAPL", "OBK")):
    """A full-size session the REAL clock reads as current (the surfaces pass no `now`)."""
    session = previous_trading_day(session_trading_date())
    filler = [{"symbol": f"Z{i:05d}", "close": 10.0, "volume": 100} for i in range(sp._MIN_PRICED_SYMBOLS)]
    assert sp.publish_priced_session(session, filler + [
        {"symbol": s, "close": 50.0, "volume": 1_000} for s in traded
    ])


@pytest.fixture
def listing_fmp(monkeypatch):
    f = _FMP()
    monkeypatch.setattr(ls, "get_fmp_client", lambda: f)
    return f


@pytest_asyncio.fixture(autouse=True)
async def _drain_background_loads():
    yield
    await _settle()


def _wire_of(obj) -> Dict[str, Any]:
    d = obj if isinstance(obj, dict) else obj.model_dump(mode="json")
    return {k: d.get(k) for k in LISTING_FIELDS}


# ── the core overview ─────────────────────────────────────────────────────────

class _OverviewFMP:
    def __init__(self, profile):
        self._profile = profile

    async def get_stock_price_quote(self, ticker):
        return PriceService._from_profile(dict(self._profile))

    async def get_company_profile(self, ticker):
        return dict(self._profile)

    def __getattr__(self, name):
        async def _forbidden(*a, **k):
            raise AssertionError(f"core path must not call fmp.{name}()")
        return _forbidden


def _core_service(profile) -> StockOverviewService:
    sos._cache.clear()
    svc = StockOverviewService()
    svc.fmp = _OverviewFMP(profile)  # type: ignore[assignment]
    svc.price = PriceFromFMPFake(svc.fmp)
    return svc


@pytest.mark.asyncio
async def test_core_carries_the_four_fields_for_roku(listing_fmp):
    _publish_current()
    resp = await _core_service(ROKU_PROFILE).get_overview_core("ROKU", chart_range="3M")
    assert _wire_of(resp) == ROKU_WIRE
    assert resp.current_price == 160.52, "the price is the last close, unchanged"
    assert resp.change_known is False and resp.market_status is not None
    assert list(resp.model_dump())[-4:] == list(LISTING_FIELDS), "appended last"


@pytest.mark.asyncio
async def test_core_for_a_trading_ticker_is_all_null_with_no_list_call(listing_fmp):
    _publish_current()
    resp = await _core_service(OBK_PROFILE).get_overview_core("OBK", chart_range="3M")
    assert _wire_of(resp) == NULL_WIRE
    assert listing_fmp.page_calls == [] and listing_fmp.eod_calls == []


@pytest.mark.asyncio
async def test_core_on_a_cold_registry_is_all_null(listing_fmp):
    resp = await _core_service(ROKU_PROFILE).get_overview_core("ROKU", chart_range="3M")
    assert _wire_of(resp) == NULL_WIRE and listing_fmp.page_calls == []


# ── the full overview ─────────────────────────────────────────────────────────

def _stub_full_overview(monkeypatch, svc, *, fund_profile, fresh_quote):
    from tests.test_stock_overview_event_loop import _neutralise_upstreams

    async def _fundamentals(ticker):
        return {"profile": dict(fund_profile)}

    async def _volatile(ticker, chart_range, interval, extended_hours, **kwargs):
        return {"quote": fresh_quote, "chart_data": []}

    async def _ohl(ticker, volatile=None, chart_data=None):
        return {}

    async def _empty_list():
        return []

    class _NoSnapshot:
        async def _none(self, ticker):
            return None
        get_profitability_snapshot = _none
        get_growth_snapshot = _none
        get_valuation_snapshot = _none
        get_health_snapshot = _none
        get_ownership_snapshot = _none

    for module_name, factory in (
        ("profitability_snapshot_service", "get_profitability_snapshot_service"),
        ("growth_snapshot_service", "get_growth_snapshot_service"),
        ("valuation_snapshot_service", "get_valuation_snapshot_service"),
        ("health_snapshot_service", "get_health_snapshot_service"),
        ("ownership_snapshot_service", "get_ownership_snapshot_service"),
    ):
        module = __import__(f"app.services.{module_name}", fromlist=[factory])
        monkeypatch.setattr(module, factory, lambda: _NoSnapshot())
    _neutralise_upstreams(monkeypatch, svc)
    monkeypatch.setattr(svc, "_get_fundamentals", _fundamentals)
    monkeypatch.setattr(svc, "_get_volatile", _volatile)
    monkeypatch.setattr(svc, "_get_session_ohl", _ohl)
    monkeypatch.setattr(svc, "_upsert_company_profile_db", lambda ticker, payload: None)
    movers = sos.get_market_movers_service()
    monkeypatch.setattr(movers, "get_sector_performance", _empty_list)
    monkeypatch.setattr(movers, "get_industry_performance", _empty_list)

    async def _no_related(ticker):
        return []
    monkeypatch.setattr(svc, "_build_related_tickers", _no_related)


@pytest.mark.asyncio
async def test_full_overview_labels_from_the_fresh_quote(monkeypatch, listing_fmp):
    _publish_current()
    sos._cache.clear()
    svc = StockOverviewService()
    fresh = PriceService._from_profile(dict(ROKU_PROFILE))
    # The bundle profile says ACTIVE (it follows the daily close cycle) — it must not matter.
    stale = {**ROKU_PROFILE, "isActivelyTrading": True, "ipoDate": ""}
    _stub_full_overview(monkeypatch, svc, fund_profile=stale, fresh_quote=fresh)
    resp = await svc.get_overview("ROKU", "3M", None, False)
    assert _wire_of(resp) == ROKU_WIRE
    assert resp.current_price == 160.52 and resp.change_known is False
    assert "isActivelyTrading" in fresh and fresh == PriceService._from_profile(dict(ROKU_PROFILE)), \
        "the shared quote row is never mutated"


@pytest.mark.asyncio
async def test_full_overview_never_labels_from_the_bundle_profile(monkeypatch, listing_fmp):
    """The other direction: an INACTIVE bundle profile beside a fresh quote that trades."""
    _publish_current()
    sos._cache.clear()
    svc = StockOverviewService()
    fresh = {k: v for k, v in PriceService._from_profile(dict(ROKU_PROFILE)).items()
             if k != "isActivelyTrading"}
    _stub_full_overview(monkeypatch, svc, fund_profile={**ROKU_PROFILE, "ipoDate": ""},
                        fresh_quote=fresh)
    resp = await svc.get_overview("ROKU", "3M", None, False)
    assert _wire_of(resp) == NULL_WIRE
    assert listing_fmp.page_calls == []


def test_the_full_builder_stays_sync_and_spreads_the_listing():
    quote = PriceService._from_profile(dict(ROKU_PROFILE))
    listing = ls.DelistedListing("ROKU", date(2026, 11, 9), "NASDAQ", date(2026, 11, 6))
    resp = StockOverviewService()._build_full_response(
        "ROKU", {"profile": {"sector": "Technology", **ROKU_PROFILE}},
        {"quote": quote, "chart_data": []}, "1D", "5min", False, listing=listing,
    )
    assert _wire_of(resp) == ROKU_WIRE
    plain = StockOverviewService()._build_full_response(
        "ROKU", {"profile": dict(ROKU_PROFILE)}, {"quote": quote, "chart_data": []},
        "1D", "5min", False,
    )
    assert _wire_of(plain) == NULL_WIRE


# ── GET /stocks/{t} and GET /stocks/{t}/quote ─────────────────────────────────

class _DetailFMP:
    def __init__(self, profile):
        self._profile = profile

    async def get_company_profile(self, ticker):
        return dict(self._profile)

    async def get_shares_float(self, ticker):
        return {}

    async def get_analyst_estimates(self, ticker, period="annual", limit=5):
        return []

    async def get_institutional_ownership_summary(self, ticker):
        return []

    async def get_income_statement(self, ticker, period="quarter", limit=4):
        return []


def _wire_endpoints(monkeypatch, profile):
    async def _no_short_interest(ticker):
        return {}

    class _Price:
        async def get_quote(self, ticker):
            return PriceService._from_profile(dict(profile))

    class _Svc:
        async def quote_valuation(self, ticker, **kw):
            return {}

    monkeypatch.setattr(stocks, "get_fmp_client", lambda: _DetailFMP(profile))
    monkeypatch.setattr(stocks, "get_short_interest", _no_short_interest)
    monkeypatch.setattr(stocks, "price_source", lambda owner=None: _Price())
    monkeypatch.setattr(stocks, "get_stock_overview_service", lambda: _Svc())


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["get_stock_details", "get_stock_quote"])
async def test_the_raw_endpoints_carry_the_fields_for_roku(monkeypatch, listing_fmp, route):
    _publish_current()
    _wire_endpoints(monkeypatch, ROKU_PROFILE)
    resp = await getattr(stocks, route)("ROKU")
    assert _wire_of(resp) == ROKU_WIRE
    assert resp["price"] == 160.52


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["get_stock_details", "get_stock_quote"])
async def test_the_raw_endpoints_always_carry_the_keys_as_null(monkeypatch, listing_fmp, route):
    """Present-as-null for a trading ticker: the iOS fallback decodes nil either way."""
    _publish_current()
    _wire_endpoints(monkeypatch, OBK_PROFILE)
    resp = await getattr(stocks, route)("OBK")
    assert all(k in resp for k in LISTING_FIELDS)
    assert _wire_of(resp) == NULL_WIRE
    assert listing_fmp.page_calls == []


# ── the Tracking feed ─────────────────────────────────────────────────────────

class _FakeTable:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeSupabase:
    def __init__(self, watchlist): self._w = watchlist
    def table(self, name):
        return _FakeTable(self._w if name == "watchlist_items" else [])


@pytest.fixture
def _tracking_caches():
    for c in (ts._feed_cache, ts._feed_inflight, ts._sparkline_cache, ts._insider_cache,
              ts._extended_cache):
        c.clear()
    yield
    for c in (ts._feed_cache, ts._feed_inflight, ts._sparkline_cache, ts._insider_cache,
              ts._extended_cache):
        c.clear()


def _stub_tracking(monkeypatch, quotes: Dict[str, Dict[str, Any]], tickers):
    watchlist = [{"id": i, "ticker": t, "company_name": t, "asset_type": "stock",
                  "sector": "Tech"} for i, t in enumerate(tickers)]
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def _quotes(self, tickers): return dict(quotes)
    async def _spark(self, tickers, asset_types=None): return {}
    async def _empty(self, tickers): return []
    async def _insider(self, tickers, asset_types=None): return []
    async def _backfill(self, user_id, watchlist): return None
    async def _extended(self, tickers, asset_types=None): return {}
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _spark),
                     ("_get_earnings_alerts", _empty), ("_get_whale_trade_alerts", _empty),
                     ("_get_analyst_rating_alerts", _empty),
                     ("_get_insider_transaction_alerts", _insider),
                     ("_backfill_classification", _backfill),
                     ("_get_extended_prints", _extended)]:
        monkeypatch.setattr(TrackingService, name, fn)


@pytest.mark.asyncio
async def test_tracking_labels_roku_quietly(monkeypatch, listing_fmp, _tracking_caches, caplog):
    _publish_current()
    quotes = {"ROKU": PriceService._from_profile(dict(ROKU_PROFILE)),
              "AAPL": {"symbol": "AAPL", "price": 230.0, "changePercentage": 1.0}}
    _stub_tracking(monkeypatch, quotes, ["ROKU", "AAPL"])
    with caplog.at_level(logging.DEBUG, logger=ts.logger.name):
        feed = await TrackingService().get_tracking_feed("u-delisted")
    by = {a.ticker: a for a in feed.assets}
    assert _wire_of(by["ROKU"]) == ROKU_WIRE
    assert by["ROKU"].price == 160.52 and by["ROKU"].change_known is False
    assert _wire_of(by["AAPL"]) == NULL_WIRE
    roku_warnings = [r for r in caplog.records if r.name == ts.logger.name
                     and r.levelno >= logging.WARNING and "ROKU" in r.getMessage()]
    assert roku_warnings == [], "a confirmed delisting's missing change is not a data problem"
    assert any(r.levelno == logging.DEBUG and "ROKU" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_tracking_still_warns_on_a_real_data_problem(monkeypatch, listing_fmp, _tracking_caches, caplog):
    """A delisted row with a NON-FINITE price is still broken data: it warns (and is labelled)."""
    _publish_current()
    dds = {**PriceService._from_profile(_profile("DDS", active=False)), "price": float("nan")}
    _stub_tracking(monkeypatch, {"DDS": dds}, ["DDS"])
    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        feed = await TrackingService().get_tracking_feed("u-nan")
    assert feed.assets[0].price_known is False
    assert any("DDS" in r.getMessage() and "non-finite" in r.getMessage()
               for r in caplog.records if r.name == ts.logger.name)


@pytest.mark.asyncio
async def test_tracking_with_no_quote_for_the_symbol_makes_no_list_call(monkeypatch, listing_fmp, _tracking_caches):
    """The screener failed (`get_quotes` skips the per-symbol fallback): no quote, no flag,
    no label — and no list call."""
    _publish_current()
    _stub_tracking(monkeypatch, {}, ["ROKU"])
    feed = await TrackingService().get_tracking_feed("u-noquote")
    assert _wire_of(feed.assets[0]) == NULL_WIRE
    assert listing_fmp.page_calls == []


# ── the Home watchlist tiles ──────────────────────────────────────────────────

@pytest.fixture
def home_service(monkeypatch):
    import app.services.home_dashboard_service as mod
    from app.services.active_group_service import ActiveGroup
    from app.services.home_dashboard_service import HomeDashboardService

    def build(tickers, quotes):
        svc = object.__new__(HomeDashboardService)

        class _FakeFMP:
            async def get_batch_quotes_bulk(self, symbols):
                return [dict(quotes[s], symbol=s) for s in symbols if s in quotes]

        svc.fmp = _FakeFMP()
        svc.price = PriceFromFMPFake(svc.fmp)

        async def _group(_user_id):
            return ActiveGroup(id="g1", name="Holdings", tickers=list(tickers))

        async def _meta(_user_id, syms):
            return {s: {"company_name": s} for s in syms}

        monkeypatch.setattr(mod, "get_active_group", _group)
        monkeypatch.setattr(mod, "fetch_ticker_metadata", _meta)
        return svc

    return build


@pytest.mark.asyncio
async def test_home_tile_is_labelled_and_quiet(home_service, listing_fmp, caplog):
    import app.services.home_dashboard_service as mod

    _publish_current()
    svc = home_service(["ROKU", "AAPL"], {
        "ROKU": PriceService._from_profile(dict(ROKU_PROFILE)),
        "AAPL": {"price": 230.0, "changePercentage": 1.0, "previousClose": 227.7},
    })
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        _, _, tiles = await svc._build_watchlist("u1")
    by = {t.symbol: t for t in tiles}
    assert _wire_of(by["ROKU"]) == ROKU_WIRE and by["ROKU"].price == 160.52
    assert by["ROKU"].change_known is False
    assert _wire_of(by["AAPL"]) == NULL_WIRE
    assert not [r for r in caplog.records if r.name == mod.logger.name
                and r.levelno >= logging.WARNING and "ROKU" in r.getMessage()]


@pytest.mark.asyncio
async def test_home_inactive_but_unconfirmed_warns_with_the_true_reason(home_service, listing_fmp, caplog):
    import app.services.home_dashboard_service as mod

    _publish_current()
    svc = home_service(["GONE", "STALE"], {
        "GONE": PriceService._from_profile(_profile("GONE", active=False)),   # not on the list
        "STALE": {"price": 12.0, "changePercentage": None},                   # snapshot gap
    })
    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        _, _, tiles = await svc._build_watchlist("u1")
    assert all(_wire_of(t) == NULL_WIRE for t in tiles)
    msgs = [r.getMessage() for r in caplog.records if r.name == mod.logger.name]
    assert any("GONE is flagged inactive by FMP" in m for m in msgs), msgs
    assert any("STALE has no day change (stale/missing close snapshot)" in m for m in msgs), msgs
    assert not any("GONE has no day change (stale" in m for m in msgs)


def test_market_pulse_tiles_default_to_null():
    from app.schemas.home_dashboard import MarketPulseItemResponse

    tile = MarketPulseItemResponse(symbol="^GSPC", name="S&P 500", type="index", price=1.0,
                                   change_percent=0.0, spark=[])
    assert _wire_of(tile) == NULL_WIRE


# ── chat: the card, its context line and the tool result ──────────────────────

ROKU_LISTING = ls.DelistedListing("ROKU", date(2026, 11, 9), "NASDAQ", date(2026, 11, 6))


def test_the_widget_carries_the_fields_and_is_never_live():
    quote = PriceService._from_profile(dict(ROKU_PROFILE))
    w = ChatService._build_stock_widget("ROKU", quote, [], 0, True, currency="USD",
                                        listing=ROKU_LISTING)
    assert _wire_of(w) == ROKU_WIRE and w["is_market_open"] is False
    plain = ChatService._build_stock_widget("ROKU", quote, [], 0, True, currency="USD")
    assert _wire_of(plain) == NULL_WIRE and plain["is_market_open"] is True


def _line(**overrides):
    quote = PriceService._from_profile(dict(ROKU_PROFILE))
    w = ChatService._build_stock_widget("ROKU", quote, [], 0, False, currency="USD",
                                        listing=ROKU_LISTING)
    w.update(overrides)
    return ChatService._widget_grounding_line(w)


def test_the_context_line_says_delisted_last_close_never_live():
    line = _line()
    assert "DELISTED LISTING" in line and "LIVE QUOTE" not in line
    assert "(live)" not in line and "These are the current numbers" not in line
    assert "ROKU is delisted from NASDAQ on 2026-11-09" in line
    assert "$160.52 is its LAST CLOSE (the 2026-11-06 session)" in line
    assert "never call it live" in line and "give no reason" in line
    assert "FMP" not in line, "the data vendor is never named to the model"


@pytest.mark.parametrize("overrides, present, absent", [
    ({"delisted_exchange": None}, "ROKU is delisted on 2026-11-09", "from None"),
    ({"delisted_exchange": "   "}, "ROKU is delisted on 2026-11-09", "from  "),
    ({"last_trade_date": None}, "is its last stored close", "session)"),
    ({"last_trade_date": "garbage"}, "is its last stored close", "garbage"),
    ({"delisted_date": None}, "ROKU is delisted from NASDAQ, per", "on None"),
    ({"currency": "EUR"}, "160.52 EUR is its LAST CLOSE", "$160.52"),
])
def test_missing_fields_drop_their_clause(overrides, present, absent):
    line = _line(**overrides)
    assert present in line and absent not in line, line


def test_a_trading_widget_keeps_the_live_quote_line():
    quote = PriceService._from_profile(dict(OBK_PROFILE))
    w = ChatService._build_stock_widget("OBK", quote, [], 0, True, currency="USD")
    line = ChatService._widget_grounding_line(w)
    assert "LIVE QUOTE" in line and "(live)" in line and "DELISTED" not in line


def test_the_prompt_rules_name_the_delisted_line_as_a_price_source():
    for body in (ChatService._WEB_RESULTS_BODY, ChatService._WEB_RESULTS_BODY_GENERAL):
        assert "LIVE QUOTE line, a DELISTED LISTING line or a market-data tool result" in body


@pytest.mark.asyncio
async def test_the_card_fetch_labels_roku_and_forces_closed(monkeypatch, listing_fmp):
    _publish_current()
    monkeypatch.setattr("app.services.home_dashboard_service._market_status",
                        lambda: ("open", True))
    svc = ChatService.__new__(ChatService)
    svc.fmp = SimpleNamespace(
        get_stock_price_quote=AsyncMock(return_value=PriceService._from_profile(dict(ROKU_PROFILE))),
        get_historical_prices=AsyncMock(return_value=[]),
        get_company_profile=AsyncMock(return_value=dict(ROKU_PROFILE)),
    )
    svc.price = PriceFromFMPFake(svc.fmp)
    widget = await svc._fetch_stock_widget_data("ROKU")
    assert _wire_of(widget) == ROKU_WIRE
    assert widget["is_market_open"] is False, "a green Live dot beside a frozen last close"
    line = ChatService._widget_grounding_line(widget)
    assert "DELISTED LISTING" in line and "(live)" not in line

    # The same card through the TOOL path the model calls mid-stream carries a note.
    handlers = chat_tools.build_chat_tool_handlers(svc)
    result = await handlers["get_stock_chart_data"]({"ticker": "ROKU"})
    assert "delisted from NASDAQ on 2026-11-09" in result["note"]
    assert "last close (the 2026-11-06 session)" in result["note"]
    assert "FMP" not in result["note"] and "note" not in widget, "a copy, never the shared dict"


@pytest.mark.parametrize("result", [
    {"widget_type": "stock_chart", "ticker": "AAPL", "listing_status": None},
    {"error": "No quote data found for X"}, None, "x",
])
def test_the_tool_note_is_only_for_a_delisting(result):
    assert chat_tools._with_delisted_note(result) is result


def test_the_tool_note_with_missing_fields():
    note = chat_tools._with_delisted_note({"listing_status": "delisted"})["note"]
    assert note.startswith("This listing is delisted (a US Eastern calendar date).")
    assert "its last stored close" in note and "None" not in note


def test_the_capability_and_chip_wording_no_longer_promise_a_live_price():
    assert "live quote" not in chat_tools.TOOL_CAPABILITIES["get_stock_chart_data"]
    assert "live price" not in chat_tools._CHIP_SCOPE_BY_TOOL["get_stock_chart_data"]
    assert not math.isnan(0.0)
