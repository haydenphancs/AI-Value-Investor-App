"""The holdings-card sparkline fetch: a calendar-aware 2-3 session window (2026-10-08).

Tracking's per-ticker sparkline used to call `fetch_chart_data(fmp, t, "1D")`, which asks
FMP for 3 calendar days PLUS a 7-day indicator warm-up — ~10 days of 5-minute bars per
ticker, per cold build — and then keeps only the newest day. `fetch_sparkline_bars` asks
for `sparkline_window(today)`: from TWO trading days back to today (ET).

Why two hops, pinned below with a calendar the code does not know about: pre-market the
card draws the previous session, so one hop is enough on a normal day — but a closure
missing from `US_MARKET_HOLIDAYS` and the learned `_OBSERVED_CLOSURES` makes that one hop
land on a day with no bars, and the card would go blank until the next session.

Also pinned: the detail chart (`fetch_chart_data`) keeps its warm-up; the extracted
`_fetch_intraday_bars` behaves exactly like the branch it came from; the crypto gate holds;
and Tracking still PINS an empty answer on both sources (the CoinGecko un-pin was cut).

Hermetic: every FMP client is an inline fake; `_OBSERVED_CLOSURES` is monkeypatched,
never mutated.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from app.services import chart_helper
from app.services import tracking_service as tsvc
from app.services.chart_helper import fetch_sparkline_bars, sparkline_window
from app.services.tracking_service import TrackingService
from app.utils import market_hours as mh
from app.utils.market_hours import ET, is_trading_day, previous_trading_day


# ── the window ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "today,expected_from",
    [
        (date(2026, 10, 7), "2026-10-05"),    # Wed → Mon (a plain week)
        (date(2026, 9, 8), "2026-09-03"),     # Tue after Labor Day → Thu
        (date(2026, 10, 10), "2026-10-08"),   # Saturday → Thu (Fri is the last session)
        (date(2026, 10, 11), "2026-10-08"),   # Sunday → Thu
        (date(2026, 4, 6), "2026-04-01"),     # Mon after Good Friday → Wed
        (date(2026, 1, 20), "2026-01-15"),    # Tue after MLK Day → Thu
        (date(2026, 11, 27), "2026-11-24"),   # Fri after Thanksgiving (half day) → Tue
        (date(2026, 1, 2), "2025-12-30"),     # across the year end and New Year's Day
        (date(2026, 12, 28), "2026-12-23"),   # Mon after Christmas Friday → Wed
    ],
)
def test_known_calendar_days(today, expected_from):
    assert sparkline_window(today) == (expected_from, today.isoformat())


def test_every_day_of_2026_holds_two_real_sessions_before_today():
    day = date(2026, 1, 1)
    while day.year == 2026:
        start_s, end_s = sparkline_window(day)
        start, end = date.fromisoformat(start_s), date.fromisoformat(end_s)
        assert end == day
        assert start < end
        assert (end - start).days <= 7, f"{day}: window {start}..{end} is wider than needed"
        assert is_trading_day(start), f"{day}: the window starts on a closed day {start}"
        # Two sessions strictly before today are inside the window.
        sessions_before = [
            start + timedelta(days=i) for i in range((end - start).days)
            if is_trading_day(start + timedelta(days=i))
        ]
        assert len(sessions_before) >= 2, f"{day}: only {sessions_before} before today"
        assert previous_trading_day(day) > start
        day += timedelta(days=1)


def test_a_learned_closure_is_stepped_over(monkeypatch):
    """A closure the close ingest learned (`register_market_closure`) moves the window
    back a day, like a holiday."""
    monkeypatch.setattr(mh, "_OBSERVED_CLOSURES", {(2026, 10, 9)})
    assert sparkline_window(date(2026, 10, 12)) == ("2026-10-07", "2026-10-12")


# ── the fetch ─────────────────────────────────────────────────────────────────


class _WindowFMP:
    """An FMP fake that HONOURS from/to, like the real endpoint, and records the kwargs.

    ``bars_by_day``: {"YYYY-MM-DD": [(HH:MM, close), ...]}.
    """

    def __init__(self, bars_by_day: Dict[str, List[tuple]]):
        self.bars_by_day = bars_by_day
        self.calls: List[tuple] = []

    async def get_intraday_prices(self, ticker, **kwargs):
        self.calls.append((ticker, dict(kwargs)))
        lo, hi = kwargs.get("from_date") or "0000-00-00", kwargs.get("to_date")
        rows = []
        for day, bars in self.bars_by_day.items():
            if lo <= day <= hi:
                for hhmm, close in bars:
                    rows.append({"date": f"{day} {hhmm}:00", "open": close, "high": close,
                                 "low": close, "close": close, "volume": 100})
        rows.reverse()                    # FMP answers newest first
        return rows

    async def get_historical_prices(self, *a, **kw):
        raise AssertionError("a sparkline must never reach the EOD endpoint")


_SESSION = [("09:30", 10.0), ("12:00", 11.0), ("15:55", 12.0)]


@pytest.mark.asyncio
async def test_a_closure_the_calendar_does_not_know_still_leaves_a_session():
    """Friday 2026-10-09 closed for a reason no table knows; Monday 10-12 pre-market.
    One hop (from = Fri) would ask for a day with no bars and draw nothing."""
    assert is_trading_day(date(2026, 10, 9)), "precondition: the calendar must NOT know"
    fmp = _WindowFMP({"2026-10-07": _SESSION, "2026-10-08": _SESSION})
    bars = await fetch_sparkline_bars(fmp, "NVDA", today=date(2026, 10, 12))
    assert bars, "two hops must still reach Thursday's session"
    assert {b["date"][:10] for b in bars} == {"2026-10-08"}
    (_, kwargs), = fmp.calls
    assert kwargs["from_date"] == "2026-10-08" and kwargs["to_date"] == "2026-10-12"


@pytest.mark.asyncio
async def test_the_fetch_asks_for_the_window_at_five_minutes_without_warm_up():
    fmp = _WindowFMP({"2026-10-06": _SESSION})
    await fetch_sparkline_bars(fmp, "NVDA", today=date(2026, 10, 7))
    (ticker, kwargs), = fmp.calls
    assert ticker == "NVDA"
    assert kwargs == {"interval": "5min", "from_date": "2026-10-05", "to_date": "2026-10-07"}


@pytest.mark.asyncio
async def test_today_defaults_to_the_et_date():
    fmp = _WindowFMP({})
    before = datetime.now(ET).date()
    await fetch_sparkline_bars(fmp, "NVDA")
    after = datetime.now(ET).date()
    (_, kwargs), = fmp.calls
    assert kwargs["to_date"] in {before.isoformat(), after.isoformat()}
    assert (kwargs["from_date"], kwargs["to_date"]) in {sparkline_window(before),
                                                         sparkline_window(after)}


_DEFAULT = object()


class _SessionFMP:
    """Serves one fixed session spanning pre-market to after-hours (unsorted), or
    ``raw`` verbatim when given."""

    def __init__(self, raw: Any = _DEFAULT):
        self.calls: List[tuple] = []
        self.raw = raw

    async def get_intraday_prices(self, ticker, **kwargs):
        self.calls.append((ticker, dict(kwargs)))
        if self.raw is not _DEFAULT:
            return self.raw
        return [
            {"date": "2026-10-06 19:55:00", "close": 5.0},
            {"date": "2026-10-06 16:00:00", "close": 4.0},
            {"date": "2026-10-06 09:30:00", "close": 2.0},
            {"date": "2026-10-06 15:55:00", "close": 3.0},
            {"date": "2026-10-06 04:00:00", "close": 1.0},
            {"date": "2026-10-06 12:00:00", "close": float("nan")},   # dropped
            {"date": None, "close": 9.0},                               # dropped
        ]


@pytest.mark.asyncio
async def test_extended_is_forwarded_only_when_asked_and_the_bell_filter_applies():
    regular = _SessionFMP()
    rows = await fetch_sparkline_bars(regular, "NVDA", today=date(2026, 10, 7))
    assert "extended" not in regular.calls[0][1]
    assert [r["date"][11:16] for r in rows] == ["09:30", "15:55"]

    extended = _SessionFMP()
    rows = await fetch_sparkline_bars(extended, "NVDA", extended_hours=True,
                                      today=date(2026, 10, 7))
    assert extended.calls[0][1].get("extended") is True
    assert [r["date"][11:16] for r in rows] == ["04:00", "09:30", "15:55", "16:00", "19:55"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, {"Error Message": "limit"}, "garbage", 0])
async def test_a_non_list_answer_is_an_empty_series(raw):
    fake = _SessionFMP(raw=raw)
    assert await fetch_sparkline_bars(fake, "NVDA", today=date(2026, 10, 7)) == []
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_an_empty_answer_is_an_empty_series():
    assert await fetch_sparkline_bars(_SessionFMP(raw=[]), "NVDA",
                                      today=date(2026, 10, 7)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("extended", [False, True])
async def test_the_extracted_intraday_branch_matches_the_detail_chart(extended):
    """Same FMP answer → the same rows from both entry points; only the window differs."""
    via_detail = await chart_helper.fetch_chart_data(_SessionFMP(), "NVDA", "1D",
                                                     extended_hours=extended)
    via_card = await fetch_sparkline_bars(_SessionFMP(), "NVDA", extended_hours=extended,
                                          today=date(2026, 10, 7))
    assert via_detail == via_card and via_card


@pytest.mark.asyncio
async def test_the_detail_chart_keeps_its_indicator_warm_up():
    """Only the card's window changed: `fetch_chart_data("1D")` still asks 3 + 7 days."""
    fake = _SessionFMP()
    before = datetime.now(timezone.utc).date()
    await chart_helper.fetch_chart_data(fake, "NVDA", "1D")
    after = datetime.now(timezone.utc).date()
    (_, kwargs), = fake.calls
    assert kwargs["interval"] == "5min"
    assert kwargs["from_date"] in {(before - timedelta(days=10)).isoformat(),
                                   (after - timedelta(days=10)).isoformat()}
    assert kwargs["to_date"] in {before.isoformat(), after.isoformat()}


@pytest.mark.asyncio
async def test_a_coin_is_routed_to_coingecko_never_fmp(monkeypatch):
    seen: List[tuple] = []

    async def fake_crypto(symbol, range_code, resolved_interval):
        seen.append((symbol, range_code, resolved_interval))
        return [{"date": "2026-10-06 12:00:00", "close": 1.0}]

    monkeypatch.setattr(chart_helper, "_fetch_crypto_chart_data", fake_crypto)
    monkeypatch.setattr(chart_helper.settings, "CRYPTO_PRICE_SOURCE", "coingecko")
    fmp = _SessionFMP()
    rows = await fetch_sparkline_bars(fmp, "BTCUSD", today=date(2026, 10, 7))
    assert seen == [("BTCUSD", "1D", "5min")]
    assert fmp.calls == [], "FMP 402s every crypto pair"
    assert rows == [{"date": "2026-10-06 12:00:00", "close": 1.0}]


@pytest.mark.asyncio
async def test_the_fmp_kill_switch_still_sends_a_coin_to_fmp(monkeypatch):
    async def fake_crypto(*a, **k):
        raise AssertionError("CRYPTO_PRICE_SOURCE=fmp must restore the FMP path")

    monkeypatch.setattr(chart_helper, "_fetch_crypto_chart_data", fake_crypto)
    monkeypatch.setattr(chart_helper.settings, "CRYPTO_PRICE_SOURCE", "fmp")
    fmp = _SessionFMP()
    await fetch_sparkline_bars(fmp, "BTCUSD", extended_hours=True, today=date(2026, 10, 7))
    assert [c[0] for c in fmp.calls] == ["BTCUSD"]


# ── Tracking's use of it ──────────────────────────────────────────────────────


def test_tracking_no_longer_imports_the_detail_fetcher():
    """A stale test patch of `tracking_service.fetch_chart_data` must fail loudly
    (`test_patch_targets_exist`), not silently patch a name nothing calls."""
    assert not hasattr(tsvc, "fetch_chart_data")
    assert tsvc.fetch_sparkline_bars is chart_helper.fetch_sparkline_bars


@pytest.mark.asyncio
async def test_tracking_draws_the_newest_session_from_the_window(monkeypatch):
    """End to end through the REAL `fetch_sparkline_bars`: the card asks for the window
    and keeps the newest day's bars."""
    tsvc._sparkline_cache.clear()
    today = datetime.now(ET).date()
    older, _ = sparkline_window(today)                 # two sessions back
    newer = previous_trading_day(today).isoformat()    # the newest session in the fake
    fmp = _WindowFMP({older: [("09:30", 50.0), ("12:00", 51.0)],
                      newer: [("09:30", 20.0), ("12:00", 21.0), ("15:55", 22.0)]})
    svc = TrackingService.__new__(TrackingService)
    svc.fmp = fmp
    try:
        out = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    finally:
        tsvc._sparkline_cache.clear()
    assert out["ORCL"][0] == [20.0, 21.0, 22.0]
    (_, kwargs), = fmp.calls
    assert (kwargs["from_date"], kwargs["to_date"]) in {sparkline_window(today),
                                                         sparkline_window(datetime.now(ET).date())}
    assert "extended" not in kwargs


@pytest.mark.asyncio
async def test_tracking_still_pins_an_empty_fmp_answer(monkeypatch):
    tsvc._sparkline_cache.clear()
    calls: List[str] = []

    async def fake(fmp, ticker, extended_hours=False):
        calls.append(ticker)
        return []

    monkeypatch.setattr(tsvc, "fetch_sparkline_bars", fake)
    try:
        svc = TrackingService.__new__(TrackingService)
        svc.fmp = object()
        first = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
        second = await svc._get_all_sparklines(["ORCL"], {"ORCL": "stock"})
    finally:
        tsvc._sparkline_cache.clear()
    assert calls == ["ORCL"], "an FMP failure RAISES; its [] is a real empty and stays pinned"
    assert first["ORCL"] == second["ORCL"] == ([], 0.0, 1.0)


@pytest.mark.asyncio
async def test_tracking_still_pins_an_empty_coingecko_answer(monkeypatch):
    """The un-pin was CUT from this change (a /search outage would re-resolve every
    30 s per user against the 100K/month quota). Pinned so it is not reintroduced
    by accident."""
    tsvc._sparkline_cache.clear()
    cg_calls: List[str] = []

    async def fake_cg_history(self, symbol, days, *, intraday=False):
        cg_calls.append(symbol)
        return []

    async def no_fmp(*a, **k):
        raise AssertionError("a coin must not reach the FMP sparkline fetch")

    monkeypatch.setattr("app.services.crypto_service.CryptoService._cg_history",
                        fake_cg_history)
    monkeypatch.setattr(tsvc, "fetch_sparkline_bars", no_fmp)
    monkeypatch.setattr(tsvc.settings, "CRYPTO_PRICE_SOURCE", "coingecko")
    try:
        svc = TrackingService.__new__(TrackingService)
        svc.fmp = object()
        await svc._get_all_sparklines(["BTCUSD"], {"BTCUSD": "crypto"})
        await svc._get_all_sparklines(["BTCUSD"], {"BTCUSD": "crypto"})
    finally:
        tsvc._sparkline_cache.clear()
    assert cg_calls == ["BTC"]
