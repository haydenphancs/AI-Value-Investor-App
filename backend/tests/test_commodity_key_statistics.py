"""Commodity Key Statistics — no permanently empty rows (follow-up to TestFlight 1.0 (9)).

Two defects, one per data source, both visible on the Simulator 2026-10-01:

* **Metal funds (GLD/SLV/PPLT/PALL).** The quote comes from `/stable/profile` via
  `price_service`, which has no `open`/`dayHigh`/`dayLow` since `/stable/quote` left the
  licence — so Open / Day High / Day Low read "—" on every metal screen, all day. The stock
  screen had already solved this; the commodity screen now calls the same
  `stock_overview_service.session_ohl_for` with its own FMP client.
* **FRED spot (WTI, Henry Hub).** One settled EIA price a day: an Open, a day range and a
  volume do not exist, and the 52-week band had no source at all. Five of ten rows were "—"
  forever. Those rows are now REPLACED with ones the series can fill — 50/200-day averages,
  a 52-week band from the settled prints, the settlement date ("As Of" — FRED runs ~5
  business days behind and nothing else on screen says so) and the source.

Hermetic: FMP is a fake on `svc.fmp`, FRED is `_fred_observations` stubbed, Tier 2 is a dict,
and the session clock is patched at the binding `stock_overview_service` reads.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.services import commodity_service as M
from app.services import stock_overview_service as sos
from tests._price_fakes import PriceFromFMPFake

TODAY = date.today()


# ── helpers ──────────────────────────────────────────────────────────────────


def _daily(n, *, end=TODAY, close=lambda i: 100.0 + i, ohlc=True):
    rows = []
    for i in range(n):
        d = (end - timedelta(days=n - 1 - i)).isoformat()
        c = close(i)
        row = {"date": d, "close": c}
        if ohlc:
            row.update({"open": c - 0.5, "high": c + 1.0, "low": c - 1.0, "volume": 1000})
        rows.append(row)
    return rows


def _stats(resp) -> dict:
    return {s.label: s.value for g in resp.key_statistics_groups for s in g.statistics}


def _isolate(monkeypatch):
    M._cache.clear()
    M._inflight.clear()
    sos._cache.clear()
    store: dict = {}
    monkeypatch.setattr(M.CommodityService, "_tier2_get", staticmethod(store.get))
    monkeypatch.setattr(
        M.CommodityService, "_tier2_put",
        staticmethod(lambda k, sym, cat, payload: store.__setitem__(k, payload)),
    )
    return store


def _clock(monkeypatch, phase=None, session=TODAY):
    monkeypatch.setattr(sos, "session_phase", lambda now=None: phase or sos.SESSION_AFTERHOURS)
    monkeypatch.setattr(sos, "session_trading_date", lambda now=None: session)


class _FMP:
    """Profile-shaped quote (no open/dayHigh/dayLow — the real shape since 2026-09-03)."""

    def __init__(self, session_row=None, raise_window=None):
        self.session_row = session_row
        self.raise_window = raise_window
        self.window_calls = 0
        self.full_calls = 0

    async def get_stock_price_quote(self, sym):
        return {"price": 382.76, "change": 1.92, "changePercentage": 0.5,
                "previousClose": 380.84, "volume": 5_800_000,
                "yearHigh": 509.70, "yearLow": 351.40}

    async def get_historical_prices(self, sym, f, t):
        span = (date.fromisoformat(str(t)[:10]) - date.fromisoformat(str(f)[:10])).days
        if span <= 10:
            self.window_calls += 1
            if self.raise_window:
                raise self.raise_window
            return [self.session_row] if self.session_row else []
        self.full_calls += 1
        return _daily(400)

    async def get_intraday_prices(self, *a, **k):
        return []


def _metal_svc(fmp):
    svc = M.CommodityService.__new__(M.CommodityService)
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    return svc


def _fred_svc(monkeypatch, observations):
    svc = M.CommodityService.__new__(M.CommodityService)
    svc.fmp = _FMP()
    svc.price = PriceFromFMPFake(svc.fmp)

    async def _obs(series_id):
        return list(observations)
    monkeypatch.setattr(svc, "_fred_observations", _obs)
    return svc


# ── the 52-week band from settled daily prints ───────────────────────────────


def test_trailing_year_range_covers_the_last_365_days_only():
    rows = _daily(500, close=lambda i: 50.0 if i < 100 else 100.0 + i)
    low, high = M._trailing_year_range(rows)
    # The 50.0 rows are > 365 days before the last row: outside the band.
    assert low == 100.0 + (500 - 1 - 365)
    assert high == 100.0 + 499


def test_a_series_shorter_than_a_year_has_no_52_week_band():
    """A half-year range under a "52-Week" label would be a false statement."""
    assert M._trailing_year_range(_daily(180)) == (None, None)
    assert M._trailing_year_range(_daily(1)) == (None, None)
    assert M._trailing_year_range([]) == (None, None)
    # Within the start gap (≤14 days short of a full year) still counts as a year.
    assert M._trailing_year_range(_daily(366 - 10)) != (None, None)


def test_malformed_and_non_positive_rows_are_skipped_not_counted():
    rows = _daily(400)
    rows[-5]["close"] = float("nan")
    rows[-6]["close"] = 0
    rows[-7]["close"] = -3.0
    rows[-8]["close"] = None
    rows[-9]["date"] = "not-a-date"
    rows.insert(10, "garbage")
    rows.insert(11, None)
    low, high = M._trailing_year_range(rows)
    assert low is not None and high is not None
    assert math.isfinite(low) and low > 0
    assert high == 100.0 + 399


def test_unsorted_input_is_sorted_by_date_first():
    rows = _daily(400)
    shuffled = rows[200:] + rows[:200]
    assert M._trailing_year_range(shuffled) == M._trailing_year_range(rows)


def test_a_one_day_spike_inside_the_window_is_part_of_the_range():
    """A real settlement outlier is data, not noise: WTI printed −$37 in 2020. Only
    non-positive / non-finite values are refused — a positive spike is kept."""
    rows = _daily(400)
    rows[-20]["close"] = 9999.0
    assert M._trailing_year_range(rows)[1] == 9999.0


def test_derived_bundle_carries_the_fred_rows_inputs():
    svc = M.CommodityService.__new__(M.CommodityService)
    d = svc._derive_from_history(_daily(400, ohlc=False), [])
    assert d["ma_50"] == pytest.approx(sum(100.0 + i for i in range(350, 400)) / 50)
    # close = 100 + i over 400 rows: the newest is 499; the oldest inside 365 days is i=34.
    assert d["high_52w"] == 499.0 and d["low_52w"] == 100.0 + (399 - 365)
    assert d["last_date"] == TODAY.isoformat()
    short = svc._derive_from_history(_daily(30, ohlc=False), [])
    assert short["ma_50"] is None and short["ma_200"] is None
    assert short["high_52w"] is None and short["low_52w"] is None


# ── the FRED rows ────────────────────────────────────────────────────────────


def _fmt(v, prefix="$", decimals=2):   # the build's own `_fmt` contract
    if not v:
        return "—"
    return f"{prefix}{v:,.{decimals}f}"


def _fred_rows(**over):
    kw = dict(price=96.16, prev_close=99.37, quote={"asOf": "2026-09-29"},
              derived={"ma_50": 90.0, "ma_200": 83.24, "high_52w": 110.0, "low_52w": 60.0,
                       "last_date": "2026-09-26"},
              price_per_unit="$96.16/bbl", fmt=_fmt)
    kw.update(over)
    groups = M.CommodityService._fred_key_statistics(**kw)
    return {s.label: s.value for g in groups for s in g.statistics}, groups


def test_fred_rows_are_ones_the_series_can_fill():
    stats, groups = _fred_rows()
    assert list(stats) == ["Price/Unit", "Previous Close", "50-Day Avg", "200-Day Avg",
                           "52-Week High", "52-Week Low", "As Of", "Source"]
    for impossible in ("Open", "Day High", "Day Low", "Volume", "Avg. Volume (30D)"):
        assert impossible not in stats, f"{impossible} cannot exist for a daily spot print"
    assert [len(g.statistics) for g in groups] == [4, 4], "the two columns stay even"
    assert stats["52-Week High"] == "$110.00" and stats["52-Week Low"] == "$60.00"
    assert stats["As Of"] == "Sep 29, 2026"
    assert stats["Source"] == "EIA daily spot"


def test_the_band_always_contains_the_live_price():
    """History is cached up to 12h, the quote 45s: a newer print can sit outside the
    band computed from the older rows. Price outside its own 52-week range is a contradiction."""
    stats, _ = _fred_rows(price=120.0)
    assert stats["52-Week High"] == "$120.00"
    stats, _ = _fred_rows(price=55.0)
    assert stats["52-Week Low"] == "$55.00"


def test_unknown_values_render_a_dash_never_zero_or_nan():
    stats, _ = _fred_rows(quote={}, derived={})
    assert stats["50-Day Avg"] == "—" and stats["200-Day Avg"] == "—"
    assert stats["52-Week High"] == "—" and stats["52-Week Low"] == "—"
    assert stats["As Of"] == "—"
    stats, _ = _fred_rows(derived={"high_52w": float("nan"), "low_52w": 60.0})
    assert stats["52-Week High"] == "—"
    assert all("nan" not in v.lower() and v != "$0.00" for v in stats.values())


def test_as_of_falls_back_to_the_last_settled_row_then_dashes():
    assert _fred_rows(quote={})[0]["As Of"] == "Sep 26, 2026"
    assert _fred_rows(quote={"asOf": "garbage"})[0]["As Of"] == "Sep 26, 2026"
    assert _fred_rows(quote={"asOf": None}, derived={"last_date": "x"})[0]["As Of"] == "—"


# ── end to end: FRED ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wti_screen_shows_filled_rows_and_makes_no_session_call(monkeypatch):
    _isolate(monkeypatch)
    obs = [(TODAY - timedelta(days=399 - i), 60.0 + i * 0.1) for i in range(400)]
    svc = _fred_svc(monkeypatch, obs)
    resp = await svc.get_commodity_detail("CLUSD", chart_range="3M")
    stats = _stats(resp)
    assert stats["52-Week High"] != "—" and stats["52-Week Low"] != "—"
    assert stats["50-Day Avg"] != "—" and stats["200-Day Avg"] != "—"
    assert stats["As Of"] == TODAY.strftime("%b %-d, %Y")
    assert "Open" not in stats and "Volume" not in stats
    assert svc.fmp.window_calls == 0, "a FRED series has no session to ask FMP about"


# ── end to end: metal funds ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gold_screen_fills_open_and_day_range_from_the_funds_session(monkeypatch):
    _isolate(monkeypatch)
    _clock(monkeypatch)
    row = {"date": TODAY.isoformat(), "open": 381.10, "high": 384.25, "low": 379.80,
           "close": 382.76, "volume": 5_800_000}
    fmp = _FMP(session_row=row)
    resp = await _metal_svc(fmp).get_commodity_detail("GCUSD", chart_range="3M")
    stats = _stats(resp)
    assert stats["Open"] == "$381.10"
    assert stats["Day High"] == "$384.25"
    assert stats["Day Low"] == "$379.80"
    assert stats["52-Week High"] == "$509.70"   # from the profile band, unchanged
    assert fmp.window_calls == 1


@pytest.mark.asyncio
async def test_the_shared_quote_row_is_not_mutated(monkeypatch):
    _isolate(monkeypatch)
    _clock(monkeypatch)
    row = {"date": TODAY.isoformat(), "open": 381.10, "high": 384.25, "low": 379.80,
           "close": 382.76}
    await _metal_svc(_FMP(session_row=row)).get_commodity_detail("GCUSD", chart_range="3M")
    cached_quote = M._cache_get(f"com:quote:{M._resolve_fmp_symbol('GC')}")
    assert cached_quote and "open" not in cached_quote and "dayHigh" not in cached_quote


@pytest.mark.asyncio
async def test_a_previous_sessions_row_is_never_shown_under_todays_labels(monkeypatch):
    _isolate(monkeypatch)
    _clock(monkeypatch)
    stale = {"date": (TODAY - timedelta(days=1)).isoformat(), "open": 1.0, "high": 2.0,
             "low": 0.5, "close": 1.5}
    resp = await _metal_svc(_FMP(session_row=stale)).get_commodity_detail("GCUSD")
    stats = _stats(resp)
    assert stats["Open"] == "—" and stats["Day High"] == "—" and stats["Day Low"] == "—"


@pytest.mark.asyncio
async def test_an_upstream_failure_degrades_to_dashes_and_is_not_cached(monkeypatch):
    _isolate(monkeypatch)
    _clock(monkeypatch)
    fmp = _FMP(raise_window=M.FMPUnavailableException("boom"))
    resp = await _metal_svc(fmp).get_commodity_detail("GCUSD")
    assert _stats(resp)["Open"] == "—"
    assert resp.current_price > 0, "three stat rows are not worth the screen"
    assert M._cache_get(f"com:ohl:{M._resolve_fmp_symbol('GC')}") is None, \
        "a failure must not be cached as an answer"


# ── the derived bundle's shape version ───────────────────────────────────────


@pytest.mark.asyncio
async def test_an_older_derived_bundle_is_not_read_back(monkeypatch):
    """A v1 bundle (no `high_52w`…) persisted by the previous build must not be served:
    `.get()` would render the new rows as "—" for up to 12h after the deploy."""
    store = _isolate(monkeypatch)
    sym = M._resolve_fmp_symbol("CL")
    store[f"{sym}:derived"] = {"ma_200": 1.0, "last_close": 1.0, "performance_periods": []}
    obs = [(TODAY - timedelta(days=399 - i), 60.0 + i * 0.1) for i in range(400)]
    svc = _fred_svc(monkeypatch, obs)
    derived = await svc._get_derived(sym)
    assert derived.get("high_52w") is not None, "the stale v1 bundle was served"
    persisted = [k for k in store if k.endswith(":derived") and M._DERIVED_SHAPE in k]
    assert persisted, f"no versioned derived row was written: {list(store)}"
