"""The Assets tab's pre-market / after-hours print (2026-10-30).

The row's own price is the screener's regular-session price — it still reports Friday's
close at 07:00 Monday — and FMP's real-time `aftermarket-quote` is outside the licence. The
print comes from the licensed `extended=true` 5-minute bars instead:

  * `market_hours.last_regular_session` — the session the print is measured AFTER, on New
    York's clock (pinned across the 2026-11-01 EDT→EST switch);
  * `chart_helper.fetch_extended_bars` — the window, from that session through today (ET);
  * `chart_helper.latest_extended_print` — the newest bar after the last regular one,
    named by its OWN ET stamp, half-day aware;
  * `TrackingService._get_extended_prints` — no call in the regular session, none for a
    row that has no extended session, a session-tagged cache, failures not cached;
  * the merge — the move is measured from the row's own price, all three fields or none.

No network: every FMP client is an inline fake, and the clocks are injected.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.services.tracking_service as ts
from app.services.chart_helper import fetch_extended_bars, latest_extended_print
from app.services.tracking_service import TrackingService
from app.utils.market_hours import ET, last_regular_session


def _at(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def _utc(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_caches():
    ts._extended_cache.clear()
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    yield
    ts._extended_cache.clear()
    ts._feed_cache.clear()
    ts._feed_inflight.clear()


# ── 1. the session the print is measured after ───────────────────────────────


@pytest.mark.parametrize(
    "now,expected",
    [
        (_at(2026, 10, 29, 5, 0), date(2026, 10, 28)),    # Thu pre-market → Wed
        (_at(2026, 10, 29, 12, 0), date(2026, 10, 29)),   # Thu regular → Thu
        (_at(2026, 10, 29, 17, 0), date(2026, 10, 29)),   # Thu after hours → Thu
        (_at(2026, 10, 29, 22, 0), date(2026, 10, 29)),   # Thu night → Thu
        (_at(2026, 10, 30, 2, 0), date(2026, 10, 29)),    # Fri 02:00 → still Thu
        (_at(2026, 10, 31, 11, 0), date(2026, 10, 30)),   # Saturday → Fri
        (_at(2026, 11, 2, 5, 0), date(2026, 10, 30)),     # Mon pre-market → Fri
        (_at(2026, 11, 26, 12, 0), date(2026, 11, 25)),   # Thanksgiving → Wed
        (_at(2026, 11, 27, 14, 0), date(2026, 11, 27)),   # Black Friday after its 13:00 bell
    ],
)
def test_last_regular_session(now, expected):
    assert last_regular_session(now) == expected


def test_the_session_is_read_on_new_yorks_clock_across_the_dst_switch():
    """14:00 UTC is 09:00 EST on Mon 2026-11-02 — pre-market, so the print is measured
    after FRIDAY. Read with summer's -4h it would be 10:00, the regular session. And
    13:45 UTC on Mon 2026-10-26 is 09:45 EDT — regular — where a fixed -5h says 08:45."""
    assert last_regular_session(_utc(2026, 11, 2, 14, 0)) == date(2026, 10, 30)
    assert last_regular_session(_utc(2026, 10, 26, 13, 45)) == date(2026, 10, 26)
    # A naive instant is UTC, the same convention as every other `market_hours` helper.
    assert last_regular_session(datetime(2026, 11, 2, 14, 0)) == date(2026, 10, 30)


# ── 2. the window ─────────────────────────────────────────────────────────────


class _RecordingFMP:
    """Records each `get_intraday_prices` call; answers ``rows`` (newest first, like FMP)."""

    def __init__(self, rows: Any = None, exc: Exception | None = None):
        self.rows = rows if rows is not None else []
        self.exc = exc
        self.calls: List[tuple] = []

    async def get_intraday_prices(self, ticker, **kwargs):
        self.calls.append((ticker, dict(kwargs)))
        if self.exc is not None:
            raise self.exc
        rows = self.rows(ticker) if callable(self.rows) else self.rows
        return list(reversed(rows)) if isinstance(rows, list) else rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now,window",
    [
        (_at(2026, 10, 29, 17, 0), ("2026-10-29", "2026-10-29")),    # after hours: today only
        (_at(2026, 11, 2, 5, 0), ("2026-10-30", "2026-11-02")),      # Mon pre-market: Fri..Mon
        (_at(2026, 10, 31, 11, 0), ("2026-10-30", "2026-10-31")),    # Saturday: Fri..Sat
        (_at(2026, 11, 26, 12, 0), ("2026-11-25", "2026-11-26")),    # Thanksgiving: Wed..Thu
        # 02:00 UTC Fri is 22:00 EDT Thu: "today" is New York's date, not UTC's.
        (_utc(2026, 10, 30, 2, 0), ("2026-10-29", "2026-10-29")),
        (datetime(2026, 10, 29, 21, 0), ("2026-10-29", "2026-10-29")),   # naive = UTC
    ],
)
async def test_the_fetch_asks_for_the_last_session_through_today_with_extended_bars(now, window):
    fmp = _RecordingFMP()
    await fetch_extended_bars(fmp, "AAPL", now=now)
    (ticker, kwargs), = fmp.calls
    assert ticker == "AAPL"
    assert kwargs == {"interval": "5min", "from_date": window[0], "to_date": window[1],
                      "extended": True}


@pytest.mark.asyncio
async def test_the_fetch_keeps_the_extended_bars_oldest_first():
    """The regular-hours filter must NOT run: those bars are the whole point."""
    rows = [{"date": "2026-10-29 15:55:00", "close": 3.0},
            {"date": "2026-10-29 19:55:00", "close": 5.0},
            {"date": "2026-10-29 04:00:00", "close": 1.0}]
    out = await fetch_extended_bars(_RecordingFMP(rows), "AAPL", now=_at(2026, 10, 29, 20, 30))
    assert [r["date"][11:16] for r in out] == ["04:00", "15:55", "19:55"]


# ── 3. the print ──────────────────────────────────────────────────────────────


def _bars(day: str, *points: tuple) -> List[Dict[str, Any]]:
    return [{"date": f"{day} {hhmm}:00", "close": close} for hhmm, close in points]


def test_after_hours_is_the_newest_bar_after_the_bell():
    bars = _bars("2026-10-29", ("04:00", 1.0), ("09:30", 2.0), ("15:55", 3.0),
                 ("16:00", 4.0), ("19:55", 5.0))
    assert latest_extended_print(bars) == {"price": 5.0, "session": "after_hours"}


def test_pre_market_is_named_by_its_own_stamp():
    bars = (_bars("2026-10-30", ("09:30", 2.0), ("15:55", 3.0), ("17:00", 4.0))
            + _bars("2026-11-02", ("04:05", 6.0), ("09:25", 7.0)))
    assert latest_extended_print(bars) == {"price": 7.0, "session": "pre_market"}


def test_before_any_pre_market_trade_the_after_hours_print_still_says_after_hours():
    """05:00 Monday, nothing has traded yet today: Friday's after-hours print is the
    newest, and it is labelled for what it is, not for the clock."""
    bars = _bars("2026-10-30", ("09:30", 2.0), ("15:55", 3.0), ("19:55", 4.0))
    assert latest_extended_print(bars) == {"price": 4.0, "session": "after_hours"}


def test_a_half_day_closes_at_13_00():
    """`_filter_regular_hours` would call 13:00-16:00 regular; the half-day bell does not."""
    bars = _bars("2026-11-27", ("12:55", 7.0), ("13:00", 8.0), ("15:30", 9.0))
    assert latest_extended_print(bars) == {"price": 9.0, "session": "after_hours"}


@pytest.mark.parametrize(
    "bars",
    [
        _bars("2026-10-29", ("09:30", 2.0), ("15:55", 3.0)),    # nothing after the bell
        _bars("2026-10-29", ("04:00", 1.0), ("08:00", 2.0)),    # no regular bar to measure from
        [],
        None,
        "garbage",
    ],
)
def test_no_print(bars):
    assert latest_extended_print(bars) is None


def test_unusable_rows_after_the_bell_are_skipped_not_fatal():
    bars = _bars("2026-10-29", ("15:55", 3.0), ("16:30", 4.0)) + [
        {"date": "2026-10-29 17:00:00", "close": float("nan")},
        {"date": "2026-10-29 17:05:00", "close": 0},
        "not a row",
        {"date": None, "close": 9.0},
    ]
    assert latest_extended_print(bars) == {"price": 4.0, "session": "after_hours"}


# ── 4. the service pass ──────────────────────────────────────────────────────


_AFTER_HOURS = _at(2026, 10, 29, 17, 0)
_CLOSED = _at(2026, 10, 29, 20, 5)
_PRE_MARKET = _at(2026, 10, 30, 5, 0)
_REGULAR = _at(2026, 10, 29, 12, 0)

_SESSION = _bars("2026-10-29", ("09:30", 100.0), ("15:55", 101.0), ("17:00", 102.5))


def _svc(fmp) -> TrackingService:
    svc = TrackingService.__new__(TrackingService)
    svc.fmp = fmp
    return svc


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock for the cache, scoped to `tracking_service` only —
    patching `time.monotonic` itself would move the event loop's clock too."""
    value = [1_000.0]
    monkeypatch.setattr(ts, "_time", SimpleNamespace(monotonic=lambda: value[0]))
    return value


@pytest.mark.asyncio
async def test_no_call_at_all_in_the_regular_session(clock):
    fmp = _RecordingFMP(_SESSION)
    assert await _svc(fmp)._get_extended_prints(["AAPL"], {}, now=_REGULAR) == {}
    assert fmp.calls == []


@pytest.mark.asyncio
async def test_only_rows_with_an_extended_session_cost_a_call(clock):
    """A coin trades round the clock; an index, a commodity code and FX have no licensed
    bars. Only the equity and the ETF are fetched — and a bare BTC is the ETF."""
    fmp = _RecordingFMP(_SESSION)
    out = await _svc(fmp)._get_extended_prints(
        ["AAPL", "SPY", "BTCUSD", "^GSPC", "GCUSD", "EURUSD", "BTC"],
        {"SPY": "etf", "BTCUSD": "crypto", "GCUSD": "commodity"},
        now=_AFTER_HOURS,
    )
    assert sorted(c[0] for c in fmp.calls) == ["AAPL", "BTC", "SPY"]
    assert out["AAPL"] == {"price": 102.5, "session": "after_hours"}
    assert set(out) == {"AAPL", "BTC", "SPY"}


@pytest.mark.asyncio
async def test_the_cache_holds_within_a_phase_and_flips_at_the_boundary(clock):
    fmp = _RecordingFMP(_SESSION)
    svc = _svc(fmp)
    await svc._get_extended_prints(["AAPL"], {}, now=_AFTER_HOURS)
    await svc._get_extended_prints(["AAPL"], {}, now=_at(2026, 10, 29, 17, 0))
    assert len(fmp.calls) == 1, "a second build inside the TTL is a hit"

    # 20:00 ET: same last session, new phase — re-read once for the final print.
    await svc._get_extended_prints(["AAPL"], {}, now=_CLOSED)
    assert len(fmp.calls) == 2
    # 04:00 ET: pre-market, a fresh read even though the last session is unchanged.
    await svc._get_extended_prints(["AAPL"], {}, now=_PRE_MARKET)
    assert len(fmp.calls) == 3


@pytest.mark.asyncio
async def test_the_ttl_is_60_s_in_a_live_session_and_15_min_when_closed(clock):
    fmp = _RecordingFMP(_SESSION)
    svc = _svc(fmp)
    await svc._get_extended_prints(["AAPL"], {}, now=_AFTER_HOURS)
    clock[0] += ts.EXTENDED_PRINT_TTL + 1
    await svc._get_extended_prints(["AAPL"], {}, now=_AFTER_HOURS)
    assert len(fmp.calls) == 2, "after hours, a print older than 60 s is re-read"

    await svc._get_extended_prints(["AAPL"], {}, now=_CLOSED)
    assert len(fmp.calls) == 3
    clock[0] += ts.EXTENDED_PRINT_TTL + 1
    await svc._get_extended_prints(["AAPL"], {}, now=_CLOSED)
    assert len(fmp.calls) == 3, "overnight the print cannot move: no re-read inside 15 min"
    clock[0] += ts.EXTENDED_PRINT_CLOSED_TTL
    await svc._get_extended_prints(["AAPL"], {}, now=_CLOSED)
    assert len(fmp.calls) == 4


@pytest.mark.asyncio
async def test_an_empty_answer_is_pinned(clock):
    """No trade after the bell is an answer, not a failure: cached like the sparkline's."""
    fmp = _RecordingFMP([])
    svc = _svc(fmp)
    assert await svc._get_extended_prints(["AAPL"], {}, now=_AFTER_HOURS) == {}
    assert await svc._get_extended_prints(["AAPL"], {}, now=_AFTER_HOURS) == {}
    assert len(fmp.calls) == 1


@pytest.mark.asyncio
async def test_a_failure_costs_the_row_its_print_and_is_retried(clock, caplog):
    fmp = _RecordingFMP(exc=RuntimeError("FMP down"))
    svc = _svc(fmp)
    assert await svc._get_extended_prints(["AAPL"], {}, now=_AFTER_HOURS) == {}
    assert await svc._get_extended_prints(["AAPL"], {}, now=_AFTER_HOURS) == {}
    assert len(fmp.calls) == 2, "a failed read is never cached"
    assert any("Extended-hours print for AAPL failed" in r.getMessage() for r in caplog.records)


def test_the_cache_stays_bounded(monkeypatch):
    monkeypatch.setattr(ts, "_EXTENDED_CACHE_SWEEP_AT", 3)
    for t in ("A", "B", "C", "D", "E"):
        ts._extended_cache_set(t, "tag", None)
    assert len(ts._extended_cache) <= 3
    assert "E" in ts._extended_cache, "the newest write survives the sweep"


# ── 5. the merge ─────────────────────────────────────────────────────────────


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


async def _feed_with(monkeypatch, quotes: Dict[str, Dict], prints: Dict[str, Dict]):
    watchlist = [{"id": i, "ticker": t, "company_name": t, "asset_type": "stock",
                  "sector": "Technology"} for i, t in enumerate(["AAPL", "MSFT", "NVDA"])]
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def _quotes(self, tickers): return quotes
    async def _nothing(self, *a, **k): return {}
    async def _no_list(self, *a, **k): return []
    async def _backfill(self, user_id, wl): return None
    async def _prints(self, tickers, asset_types=None): return prints
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _nothing),
                     ("_get_earnings_alerts", _no_list), ("_get_whale_trade_alerts", _no_list),
                     ("_get_analyst_rating_alerts", _no_list),
                     ("_get_insider_transaction_alerts", _no_list),
                     ("_backfill_classification", _backfill),
                     ("_get_extended_prints", _prints)]:
        monkeypatch.setattr(TrackingService, name, fn)
    feed = await TrackingService().get_tracking_feed("u-extended")
    return {a.ticker: a for a in feed.assets}


@pytest.mark.asyncio
async def test_the_move_is_measured_from_the_rows_own_price(monkeypatch):
    rows = await _feed_with(
        monkeypatch,
        quotes={"AAPL": {"price": 348.91, "changePercentage": 1.48},
                "MSFT": {"price": 400.0, "changePercentage": -0.5}},
        prints={"AAPL": {"price": 349.2, "session": "after_hours"},
                "MSFT": {"price": 398.0, "session": "pre_market"}},
    )
    aapl, msft = rows["AAPL"], rows["MSFT"]
    assert (aapl.price, aapl.change_percent) == (348.91, 1.48), "the regular price is untouched"
    assert (aapl.extended_price, aapl.extended_change_percent, aapl.extended_session) == (
        349.2, 0.08, "after_hours")
    assert (msft.extended_price, msft.extended_change_percent, msft.extended_session) == (
        398.0, -0.5, "pre_market")


@pytest.mark.asyncio
async def test_no_print_or_no_price_means_all_three_null(monkeypatch):
    """NVDA has a print but no quote: there is nothing to measure it from, so the row
    carries no half-filled chip. MSFT has a quote but no print."""
    rows = await _feed_with(
        monkeypatch,
        quotes={"AAPL": {"price": 348.91, "changePercentage": 1.48},
                "MSFT": {"price": 400.0, "changePercentage": -0.5}},
        prints={"NVDA": {"price": 190.0, "session": "after_hours"}},
    )
    for ticker in ("MSFT", "NVDA"):
        row = rows[ticker]
        assert (row.extended_price, row.extended_change_percent, row.extended_session) == (
            None, None, None), ticker


@pytest.mark.asyncio
async def test_an_unchanged_print_is_a_positive_zero(monkeypatch):
    """Swift reads -0.0 >= 0 as up but prints "-0.00": the same guard as change_percent."""
    rows = await _feed_with(
        monkeypatch,
        quotes={"AAPL": {"price": 100.0, "changePercentage": 1.0}},
        prints={"AAPL": {"price": 99.9999, "session": "after_hours"}},
    )
    move = rows["AAPL"].extended_change_percent
    assert move == 0.0 and math.copysign(1.0, move) == 1.0
