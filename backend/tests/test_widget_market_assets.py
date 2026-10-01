"""The Market tile's grid — the Home Market Pulse, on the Home Screen.

WHAT CHANGED (2026-09-30)
-------------------------
The Market tile used to render a biggest-mover list over the swept universe, which
answered a question nobody asked of a "Market" tile. It now mirrors Home's Market Pulse:
S&P 500, Nasdaq, Dow, Russell 2000, Gold ETFs, then Bitcoin — `market_assets`. The legacy
`headline_mover` / `runners_up` are still SENT (installed builds render them) and still
z-ranked; the new tile ignores them.

WHAT THIS FILE PINS
-------------------
* the list cannot drift from Home: the equities ARE `home_dashboard_service._PULSE_SYMBOLS`
  (imported, not copied), in its order, with its names, and Bitcoin last;
* every number is honest: a change stamped for another session is withheld (the "Nasdaq
  0.00%" screenshot), a non-finite one too, and Bitcoin's unmeasured change is None — never
  the 0.0 the pulse keeps on the wire for its own shipped builds;
* Bitcoin comes from the pulse's OWN 600 s tile (`get_crypto_pulse_tile`), never a fresh
  CoinGecko quote (the Basic plan's monthly budget), and a slow or failing tile costs the
  grid one cell, not the payload;
* a payload with nothing to draw (no assets, no brief) is not pinned in the 60 s cache;
* a failed universe read still yields the grid from ONE quote call.

Hermetic. `HomeDashboardService`'s caches are CLASS-level, so they are only touched
through `monkeypatch.setitem` (restored after each test); `_crypto_tile` imports
`get_home_dashboard_service` inside the function, so it is patched on its SOURCE module.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import date

import pytest

import app.services.home_dashboard_service as home
from app.schemas.home_dashboard import MarketPulseItemResponse
from app.schemas.widget import WidgetMoverPayload
from app.services import widget_movers_service as wm
from app.services.widget_movers_service import (
    _MARKET_ASSET_SHORT_LABELS,
    _market_payload_is_cacheable,
    build_market_assets,
)

LIVE = date(2026, 9, 29)          # a Tuesday
LIVE_ISO = LIVE.isoformat()
PRIOR_ISO = "2026-09-28"

_PULSE_ORDER = [c["symbol"] for c in home._PULSE_SYMBOLS]


def _row(sym, chg, price=100.0, stamp=LIVE_ISO):
    r = {"symbol": sym, "price": price, "changePercentage": chg}
    if stamp is not None:
        r["changeSession"] = stamp
    return r


def _pulse_rows(**overrides):
    rows = {
        "SPY": _row("SPY", -0.18, 651.2),
        "ONEQ": _row("ONEQ", 0.12, 80.4),
        "DIA": _row("DIA", -0.31, 462.0),
        "IWM": _row("IWM", 0.75, 241.3),
        "GLD": _row("GLD", 1.02, 352.7),
    }
    rows.update(overrides)
    return rows


def _btc(change=1.234, known=True, price=112345.678):
    return MarketPulseItemResponse(
        symbol="BTCUSD", name="Bitcoin", type="crypto", price=price,
        change_percent=change, change_known=known, spark=[],
    )


# ── the list is Home's list ───────────────────────────────────────────


def test_the_grid_is_the_pulse_in_the_pulses_order_with_bitcoin_last():
    # A dict in REVERSE order: the output order must come from the pulse list, not the rows.
    rows = dict(reversed(list(_pulse_rows().items())))
    assets = build_market_assets(rows, LIVE_ISO, _btc())
    assert [a.symbol for a in assets] == _PULSE_ORDER + ["BTCUSD"]
    assert _PULSE_ORDER == ["SPY", "ONEQ", "DIA", "IWM", "GLD"]


def test_labels_are_the_pulses_own_names():
    assets = build_market_assets(_pulse_rows(), LIVE_ISO, _btc())
    names = {c["symbol"]: c["name"] for c in home._PULSE_SYMBOLS}
    for a in assets[:-1]:
        assert a.label == names[a.symbol], "the widget renamed an asset Home calls otherwise"
    assert assets[-1].label == "Bitcoin"


def test_short_labels_are_the_cramped_grid_names():
    assets = build_market_assets(_pulse_rows(), LIVE_ISO, _btc())
    assert [a.short_label for a in assets] == [
        "S&P 500", "Nasdaq", "Dow", "Russell 2000", "Gold", "Bitcoin",
    ]


def test_every_pulse_asset_has_a_short_label_and_none_claims_to_be_a_fund():
    """A pulse symbol added without a short label falls back to "…ETF" in a cell sized for
    "Gold". And a short label is drawn with NO price, so it must not say "ETF" either — it
    is the plain market name; the honest fund name is `label`, shown beside the price."""
    for sym in _PULSE_ORDER + ["BTCUSD"]:
        assert _MARKET_ASSET_SHORT_LABELS.get(sym), f"{sym} has no short label"
    assert not any("ETF" in v for v in _MARKET_ASSET_SHORT_LABELS.values())


def test_only_bitcoin_is_a_rolling_24h_move():
    assets = build_market_assets(_pulse_rows(), LIVE_ISO, _btc())
    assert [a.rolling_24h for a in assets] == [False] * 5 + [True]


def test_every_asset_carries_its_class_from_the_pulse_config():
    """`asset_type` is what a tap-through opens: an ETF detail screen for SPY..GLD, the
    crypto screen for Bitcoin. Server-owned, from the pulse's own `type` — the client must
    never infer "stock" from a bare symbol (SPY as a stock is the wrong screen)."""
    assets = build_market_assets(_pulse_rows(), LIVE_ISO, _btc())
    types = {c["symbol"]: c["type"] for c in home._PULSE_SYMBOLS}
    assert [a.asset_type for a in assets[:-1]] == [types[a.symbol] for a in assets[:-1]]
    assert all(t == "etf" for t in types.values())
    assert assets[-1].asset_type == "crypto"


def test_the_legacy_band_marks_its_proxies_as_etfs():
    band = wm.build_market_context(_pulse_rows(), [], sector_available=False)
    assert band is not None and band.indices
    assert {i.asset_type for i in band.indices} == {"etf"}


@pytest.mark.parametrize(
    "symbol, quote, expected",
    [
        ("VOO", {"isEtf": True, "isFund": False}, "etf"),
        ("AAPL", {"isEtf": False, "isFund": False}, "stock"),
        # No classification on the row at all ⇒ unknown, never a guessed "stock".
        ("AAPL", {"price": 1.0}, None),
        ("AAPL", {}, None),
        ("BTCUSD", {"isEtf": False}, "crypto"),
    ],
)
def test_a_holdings_class_comes_from_the_quotes_own_flags(symbol, quote, expected):
    assert wm._quote_asset_type(symbol, quote) == expected


# ── honest numbers ────────────────────────────────────────────────────


def test_a_change_stamped_for_another_session_is_withheld_but_the_price_is_kept():
    """The screenshot: pre-market, ONEQ drifted a sub-cent off Tuesday's stored close and
    was stamped WEDNESDAY with ~0.00%, beside SPY's real Tuesday −0.18% under "Tue close"."""
    rows = _pulse_rows(ONEQ=_row("ONEQ", 0.0004, 80.4, stamp="2026-09-30"),
                       DIA=_row("DIA", -0.31, 462.0, stamp=PRIOR_ISO))
    by = {a.symbol: a for a in build_market_assets(rows, LIVE_ISO)}
    assert by["ONEQ"].change_percent is None and by["ONEQ"].price == 80.4
    assert by["DIA"].change_percent is None and by["DIA"].price == 462.0
    assert by["SPY"].change_percent == -0.18, "a same-session row must survive the gate"


@pytest.mark.parametrize("stamp", [None, "", "garbage"])
def test_an_unstamped_row_fails_open(stamp):
    """The profile fallback carries no stamp; the movers keep such rows, so does the grid."""
    rows = _pulse_rows(SPY=_row("SPY", -0.18, 651.2, stamp=stamp))
    by = {a.symbol: a for a in build_market_assets(rows, LIVE_ISO)}
    assert by["SPY"].change_percent == -0.18


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "n/a"])
def test_an_unreadable_change_is_none_never_zero(bad):
    by = {a.symbol: a for a in build_market_assets(_pulse_rows(IWM=_row("IWM", bad, 241.3)), LIVE_ISO)}
    assert by["IWM"].change_percent is None
    assert by["IWM"].price == 241.3


def test_an_asset_with_neither_price_nor_change_is_omitted_and_the_rest_keep_order():
    rows = _pulse_rows(
        DIA=_row("DIA", None, None),
        GLD=_row("GLD", float("nan"), 0.0),          # a 0 price is no price
    )
    del rows["IWM"]                                   # no row at all
    assert [a.symbol for a in build_market_assets(rows, LIVE_ISO)] == ["SPY", "ONEQ"]


def test_negative_zero_is_not_a_red_zero():
    by = {a.symbol: a for a in build_market_assets(_pulse_rows(SPY=_row("SPY", -0.0001)), LIVE_ISO)}
    assert by["SPY"].change_percent == 0.0
    assert math.copysign(1.0, by["SPY"].change_percent) == 1.0, "-0.00% would render red"


def test_numbers_are_rounded_for_the_wire_and_json_safe():
    assets = build_market_assets(
        _pulse_rows(SPY=_row("SPY", -0.18456, 651.2345), GLD=_row("GLD", float("inf"), 352.7)),
        LIVE_ISO, _btc(),
    )
    by = {a.symbol: a for a in assets}
    assert by["SPY"].change_percent == -0.18 and by["SPY"].price == 651.23
    assert by["BTCUSD"].change_percent == 1.23 and by["BTCUSD"].price == 112345.68
    blob = json.dumps([a.model_dump(mode="json") for a in assets], allow_nan=False)
    assert "NaN" not in blob and "Infinity" not in blob


# ── Bitcoin ───────────────────────────────────────────────────────────


def test_an_unmeasured_bitcoin_change_is_none_not_the_pulses_wire_zero():
    """The pulse ships `change_percent=0.0` with `change_known=False` for its own installed
    builds. The grid must not turn that into a green +0.00%."""
    btc = build_market_assets({}, LIVE_ISO, _btc(change=0.0, known=False))[-1]
    assert btc.symbol == "BTCUSD"
    assert btc.change_percent is None
    assert btc.price == 112345.68


def test_bitcoin_is_never_session_gated():
    """Its change is a rolling 24 h move with no session; a stamp check would blank it every
    weekend and overnight."""
    btc = build_market_assets({}, "2026-09-25", _btc())[-1]
    assert btc.change_percent == 1.23 and btc.rolling_24h is True


def test_no_bitcoin_tile_means_no_bitcoin_cell():
    assert [a.symbol for a in build_market_assets(_pulse_rows(), LIVE_ISO, None)] == _PULSE_ORDER


def test_a_bitcoin_tile_with_nothing_readable_is_omitted():
    assert build_market_assets({}, LIVE_ISO, _btc(change=0.0, known=False, price=0.0)) == []


# ── cacheability ──────────────────────────────────────────────────────


def _market_payload(**kw) -> WidgetMoverPayload:
    return WidgetMoverPayload(mode="market", as_of="2026-09-29T18:14:00Z",
                              market_session="regular", **kw)


def test_a_payload_with_nothing_to_draw_is_not_cacheable():
    from app.schemas.widget import WidgetMarketBriefResponse

    assert _market_payload_is_cacheable(_market_payload()) is False
    assert _market_payload_is_cacheable(
        _market_payload(market_assets=build_market_assets(_pulse_rows(), LIVE_ISO))
    ) is True
    assert _market_payload_is_cacheable(
        _market_payload(market_brief=WidgetMarketBriefResponse(headline="Stocks drift"))
    ) is True


@pytest.mark.asyncio
async def test_an_empty_market_payload_is_rebuilt_by_the_next_caller(monkeypatch):
    """Pinning a blank Market tile for 60 s would serve it to every WidgetKit wake in the
    window. The next caller retries instead."""
    svc = wm.WidgetMoversService()
    builds = {"n": 0}

    async def _build_market():
        builds["n"] += 1
        return _market_payload()

    monkeypatch.setattr(svc, "_build_market", _build_market)
    await svc.get_market_mover()
    await svc.get_market_mover()
    assert builds["n"] == 2
    assert "market" not in svc._cache


@pytest.mark.asyncio
async def test_a_market_payload_with_assets_is_cached(monkeypatch):
    svc = wm.WidgetMoversService()
    builds = {"n": 0}

    async def _build_market():
        builds["n"] += 1
        return _market_payload(market_assets=build_market_assets(_pulse_rows(), LIVE_ISO))

    monkeypatch.setattr(svc, "_build_market", _build_market)
    await svc.get_market_mover()
    await svc.get_market_mover()
    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_concurrent_callers_still_share_one_uncacheable_build(monkeypatch):
    """`cacheable` decides only whether the NEXT caller reuses it — joiners of THIS build
    must still be deduped, or an outage multiplies upstream calls by the herd size."""
    svc = wm.WidgetMoversService()
    builds = {"n": 0}
    gate = asyncio.Event()

    async def _build_market():
        builds["n"] += 1
        await gate.wait()
        return _market_payload()

    monkeypatch.setattr(svc, "_build_market", _build_market)
    tasks = [asyncio.create_task(svc.get_market_mover()) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)
    assert builds["n"] == 1
    assert svc._inflight == {}


# ── the crypto tile read ──────────────────────────────────────────────


class _Home:
    def __init__(self, tile=None, raises=None, delay=0.0):
        self.tile, self.raises, self.delay, self.calls = tile, raises, delay, 0

    async def get_crypto_pulse_tile(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return self.tile


@pytest.mark.asyncio
async def test_the_crypto_tile_comes_from_the_home_pulse(monkeypatch):
    fake = _Home(tile=_btc())
    monkeypatch.setattr(home, "get_home_dashboard_service", lambda: fake)
    tile = await wm.WidgetMoversService()._crypto_tile()
    assert tile.symbol == "BTCUSD" and fake.calls == 1


@pytest.mark.asyncio
async def test_a_failing_crypto_tile_is_omitted_not_raised(monkeypatch):
    monkeypatch.setattr(home, "get_home_dashboard_service",
                        lambda: _Home(raises=RuntimeError("coingecko 429")))
    assert await wm.WidgetMoversService()._crypto_tile() is None


@pytest.mark.asyncio
async def test_a_slow_crypto_tile_is_omitted_after_the_timeout(monkeypatch):
    """A hung upstream must not hold the whole Market payload (and every caller deduped
    behind it) for the 30 s httpx timeout."""
    monkeypatch.setattr(wm, "_CRYPTO_TILE_TIMEOUT_SECONDS", 0.05)
    slow = _Home(tile=_btc(), delay=0.3)
    monkeypatch.setattr(home, "get_home_dashboard_service", lambda: slow)
    started = time.monotonic()
    assert await wm.WidgetMoversService()._crypto_tile() is None
    assert time.monotonic() - started < 0.25, "the build waited for the slow tile"
    # SHIELDED: the fetch is not cancelled by the timeout — it finishes and fills the
    # shared cache for the next build. Let it settle so no task outlives the loop.
    await asyncio.sleep(0.35)
    assert slow.calls == 1


@pytest.mark.asyncio
async def test_the_home_wrapper_serves_the_pulses_own_600s_cache(monkeypatch):
    """`get_crypto_pulse_tile` is a public door onto the SAME class-level cache — a second
    surface must never become a second CoinGecko consumer."""
    svc = home.HomeDashboardService.__new__(home.HomeDashboardService)
    fetches = {"n": 0}

    async def _fetch_pulse_item(self, cfg, _now):
        fetches["n"] += 1
        return _btc(change=2.5)

    monkeypatch.setattr(home.HomeDashboardService, "_fetch_pulse_item", _fetch_pulse_item)

    # Fresh cache entry → served without a fetch.
    monkeypatch.setitem(home.HomeDashboardService._cache, home._CRYPTO_PULSE_CACHE_KEY,
                        (time.time(), _btc(change=1.0)))
    tile = await svc.get_crypto_pulse_tile()
    assert tile.change_percent == 1.0 and fetches["n"] == 0

    # Expired → ONE fetch, then cached again for the next surface.
    monkeypatch.setitem(home.HomeDashboardService._cache, home._CRYPTO_PULSE_CACHE_KEY,
                        (time.time() - home._CRYPTO_PULSE_TTL_SECONDS - 1, _btc(change=1.0)))
    tile = await svc.get_crypto_pulse_tile()
    again = await svc.get_crypto_pulse_tile()
    assert tile.change_percent == 2.5 and again.change_percent == 2.5
    assert fetches["n"] == 1


# ── the market BUILD, end to end over stubs ───────────────────────────


_UNIVERSE_QUOTES = {
    "NVDA": {"symbol": "NVDA", "name": "NVIDIA", "price": 130.0, "changePercentage": -3.1,
             "changeSession": LIVE_ISO},
    "AMD": {"symbol": "AMD", "name": "AMD", "price": 150.0, "changePercentage": 2.2,
            "changeSession": LIVE_ISO},
}


def _market_build(monkeypatch, *, universe=("NVDA", "AMD"), quotes=None, home_fake=None,
                  brief_at="2026-09-29T18:14:00Z"):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: LIVE)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: LIVE)
    monkeypatch.setattr(wm, "session_phase", lambda now=None: "regular")
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Live 2:14 PM ET")

    svc = wm.WidgetMoversService()
    all_quotes = dict(_pulse_rows(ONEQ=_row("ONEQ", 0.0003, 80.4, stamp="2026-09-30")))
    all_quotes.update(quotes if quotes is not None else _UNIVERSE_QUOTES)
    seen = {"quote_calls": 0, "rank_kwargs": None, "fetch_sessions": []}

    async def _swept_universe():
        return list(universe)

    async def _quotes(symbols):
        seen["quote_calls"] += 1
        return {s: all_quotes[s] for s in symbols if s in all_quotes}

    async def _fetch_market_context(session_day):
        seen["fetch_sessions"].append(session_day)
        return wm._MarketContext(
            sector_available=True,
            sector_changes=[("Energy", 0.8), ("Technology", -1.1)],
            sector_dates={"energy": LIVE_ISO, "technology": LIVE_ISO},
        )

    async def _empty_map(tickers):
        return {}

    async def _none(*_a, **_k):
        return None

    real_rank = svc._rank_and_read

    async def _rank_and_read(tickers, **kwargs):
        seen["rank_kwargs"] = kwargs
        return await real_rank(tickers, **kwargs)

    class _Vol:
        async def get_sigmas_bulk(self, symbols):
            return {s: 0.02 for s in symbols}

    class _News:
        async def get_cards(self, scopes):
            if wm.MARKET_SCOPE in scopes:
                return {wm.MARKET_SCOPE: {"headline": "Chips drag, energy lifts",
                                          "sentiment": "Neutral", "generated_at": brief_at}}
            return {}

    monkeypatch.setattr(svc, "_swept_universe", _swept_universe)
    monkeypatch.setattr(svc, "_quotes", _quotes)
    monkeypatch.setattr(svc, "_fetch_market_context", _fetch_market_context)
    monkeypatch.setattr(svc, "_industries", _empty_map)
    monkeypatch.setattr(svc, "_industry_for_one", _none)
    monkeypatch.setattr(svc, "_head_grades", _none)
    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Vol())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    home_fake = home_fake if home_fake is not None else _Home(tile=_btc())
    monkeypatch.setattr(home, "get_home_dashboard_service", lambda: home_fake)
    return svc, seen


@pytest.mark.asyncio
async def test_the_market_build_carries_the_grid_and_the_legacy_movers(monkeypatch):
    svc, seen = _market_build(monkeypatch)
    p = await svc._build_market()

    assert p.mode == "market"
    assert [a.symbol for a in p.market_assets] == _PULSE_ORDER + ["BTCUSD"]
    assert seen["quote_calls"] == 1, "the pulse equities must ride the universe batch"
    # Installed builds still render these, z-ranked over the universe.
    assert p.headline_mover is not None and p.headline_mover.ticker == "NVDA"
    assert [m.ticker for m in p.runners_up] == ["AMD"]
    assert "basis" not in seen["rank_kwargs"], "market mode must keep the default z axis"
    assert seen["rank_kwargs"].get("phase") == "regular"
    assert p.market_brief is not None and p.market_brief.headline == "Chips drag, energy lifts"
    assert p.market_context is not None and p.market_context.breadth_total == 2


@pytest.mark.asyncio
async def test_the_market_payload_never_carries_the_holdings_fields(monkeypatch):
    svc, _ = _market_build(monkeypatch)
    p = await svc._build_market()
    assert p.group_name is None
    assert p.holdings_count is None
    assert (p.up_count, p.down_count, p.flat_count) == (None, None, None)
    assert p.top_gainers == [] and p.top_losers == []


@pytest.mark.asyncio
async def test_an_off_session_index_row_is_gated_in_the_grid_and_the_band(monkeypatch):
    """ONEQ is stamped Wednesday inside a Tuesday payload: both surfaces withhold its
    change. The band is where "Nasdaq 0.00%" was actually drawn."""
    svc, _ = _market_build(monkeypatch)
    p = await svc._build_market()

    grid = {a.symbol: a for a in p.market_assets}
    assert grid["ONEQ"].change_percent is None and grid["ONEQ"].price == 80.4
    band = {i.symbol: i for i in p.market_context.indices}
    assert band["ONEQ"].change_percent is None, "the band printed a cross-session 0.00%"
    assert band["SPY"].change_percent == -0.18


@pytest.mark.asyncio
async def test_a_failing_bitcoin_tile_costs_one_cell_not_the_payload(monkeypatch):
    svc, _ = _market_build(monkeypatch, home_fake=_Home(raises=RuntimeError("boom")))
    p = await svc._build_market()
    assert [a.symbol for a in p.market_assets] == _PULSE_ORDER
    assert p.headline_mover is not None


@pytest.mark.asyncio
async def test_a_failed_universe_read_still_draws_the_grid_from_one_quote(monkeypatch):
    """`_rank_and_read([])` used to return before the batch quote that carries the band —
    so a universe outage also blanked the indices. They do not depend on the universe."""
    svc, seen = _market_build(monkeypatch, universe=())
    p = await svc._build_market()
    assert seen["quote_calls"] == 1
    assert p.headline_mover is None and p.runners_up == []
    assert [a.symbol for a in p.market_assets] == _PULSE_ORDER + ["BTCUSD"]
    assert p.market_context is not None and p.market_context.indices


@pytest.mark.asyncio
async def test_a_premarket_universe_outage_dates_the_grid_by_its_own_stamps(monkeypatch):
    """Pre-market Wednesday, universe read failed: nothing ranked, so `_session_of` fell back
    to the LIVE session (Wed) while every pulse row is still stamped Tuesday — and the grid
    then withheld every change, drawing bare prices under "Pre-market". The index rows' own
    stamps must date the payload instead."""
    svc, _ = _market_build(monkeypatch, universe=())
    wed = date(2026, 9, 30)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: wed)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: wed)
    monkeypatch.setattr(wm, "session_phase", lambda now=None: "premarket")
    p = await svc._build_market()
    assert p.session_date == LIVE_ISO, "dated by the Tuesday stamps, not the live Wednesday"
    assert p.session_label == "Tue close"
    spy = next(a for a in p.market_assets if a.symbol == "SPY")
    assert spy.change_percent == -0.18, "a same-session change must not be withheld"
    # ONEQ is stamped Wednesday in this fixture — the one row that disagrees stays gated.
    oneq = next(a for a in p.market_assets if a.symbol == "ONEQ")
    assert oneq.change_percent is None and oneq.price == 80.4


def test_stamp_carriers_ignore_unstamped_and_malformed_rows():
    carriers = wm._stamp_only_movers({
        "SPY": {"changeSession": "2026-09-29"},
        "DIA": {"price": 1.0},
        "BAD": "not-a-row",
    })
    assert [(m.ticker, m.change_session) for m in carriers] == [("SPY", "2026-09-29")]
    assert wm._stamp_only_movers({}) == []


@pytest.mark.asyncio
async def test_an_off_session_brief_is_dropped_but_the_grid_still_caches(monkeypatch):
    svc, _ = _market_build(monkeypatch, brief_at="2026-09-26T18:14:00Z")
    p = await svc._build_market()
    assert p.market_brief is None
    assert _market_payload_is_cacheable(p) is True
