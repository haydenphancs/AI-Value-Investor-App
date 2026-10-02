"""The screener universe's freshness is SESSION-AWARE: 60 s, or 15 min inside a closed window.

WHY THIS EXISTS — review of the round-the-clock Home warmer, 2026-10-01.

The warmer (`home_dashboard_service.refresh_due_sections`, every 10 s, no market-hours gate)
forced `PriceService.refresh_universe()` once the entry was 45 s old: one full
`company-screener` page (~7,000 rows, the app's one large FMP response) about every 50 s,
around the clock — ~1,700 sweeps a day, most of them overnight and at weekends, when US
equity and ETF prices cannot move. The FMP contract is priced on bandwidth, not only on calls.

Now an entry whose stamp AND read both fall in `session_phase() == SESSION_CLOSED` stays fresh
for `_UNIVERSE_CLOSED_TTL` (900 s). That is honest, not merely cheap: the cached value is the
RAW screener rows, and every day change is computed at read time against the current close
map and clock. A stamp from any other phase keeps the 60 s rule, so the first pre-market
prices at 04:00 ET are never hidden behind a night-time sweep.

Hermetic: the screener fetch is a counting fake, the clock is a fake bound to
`price_service.time` only, and the close lookup is stubbed.
"""

from __future__ import annotations

import asyncio
import logging
import time as _real_time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest

import app.services.price_service as ps
from app.integrations.fmp import FMPUnavailableException
from app.services.price_service import PriceService

_ET = ZoneInfo("America/New_York")
_LOGGER = "app.services.price_service"


def _et(y: int, m: int, d: int, hh: int, mm: int = 0, ss: int = 0) -> float:
    return datetime(y, m, d, hh, mm, ss, tzinfo=_ET).timestamp()


# Wednesday 2026-09-30 21:00 ET — the closed window after a regular session.
WED_NIGHT = _et(2026, 9, 30, 21, 0)
# Wednesday 2026-09-30 11:00 ET — the regular session.
WED_11 = _et(2026, 9, 30, 11, 0)


# ── 1. the rule ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label, stamp, age, fresh", [
    # Closed → closed: 15 min.
    ("weeknight, 899 s", WED_NIGHT, 899, True),
    ("weeknight, 901 s", WED_NIGHT, 901, False),
    ("weeknight, 61 s", WED_NIGHT, 61, True),
    ("weeknight across midnight, 899 s", _et(2026, 9, 30, 23, 55), 899, True),
    # A phase change follows the 60 s rule.
    ("after-hours stamp read after 20:00, 61 s", _et(2026, 9, 30, 19, 59, 40), 61, False),
    ("after-hours stamp read after 20:00, 59 s", _et(2026, 9, 30, 19, 59, 40), 59, True),
    ("night stamp read in pre-market, 61 s", _et(2026, 10, 1, 3, 59, 40), 61, False),
    ("night stamp read in pre-market, 59 s", _et(2026, 10, 1, 3, 59, 40), 59, True),
    ("night stamp read at 04:05 (10 min, under 15)", _et(2026, 10, 1, 3, 55), 600, False),
    # Weekend.
    ("Saturday noon, 899 s", _et(2026, 10, 3, 12, 0), 899, True),
    ("Saturday noon, 901 s", _et(2026, 10, 3, 12, 0), 901, False),
    ("Friday night into Saturday, 899 s", _et(2026, 10, 2, 23, 55), 899, True),
    ("Sunday night into Monday pre-market, 899 s", _et(2026, 10, 5, 3, 50), 899, False),
    # Holidays: no session all day, 04:00-20:00 included.
    ("Thanksgiving noon, 899 s", _et(2026, 11, 26, 12, 0), 899, True),
    ("Labor Day 10:00, 899 s", _et(2026, 9, 7, 10, 0), 899, True),
    ("Labor Day 10:00, 901 s", _et(2026, 9, 7, 10, 0), 901, False),
    # Half-day (the day after Thanksgiving): CLOSED from its 13:00 close.
    ("half-day 13:05, 899 s", _et(2026, 11, 27, 13, 5), 899, True),
    ("half-day regular stamp read after 13:00, 61 s", _et(2026, 11, 27, 12, 59, 40), 61, False),
    # Control: 13:05 on an ordinary weekday is the regular session.
    ("ordinary weekday 13:05, 61 s", _et(2026, 9, 30, 13, 5), 61, False),
    # Every open phase keeps 60 s.
    ("regular session, 59 s", WED_11, 59, True),
    ("regular session, 61 s", WED_11, 61, False),
    ("pre-market, 61 s", _et(2026, 9, 30, 6, 0), 61, False),
    ("after hours, 61 s", _et(2026, 9, 30, 17, 0), 61, False),
])
def test_universe_freshness_is_session_aware(label, stamp, age, fresh):
    assert ps._universe_is_fresh(stamp, now=stamp + age) is fresh, label


def test_the_closed_ttl_needs_both_ends_closed():
    assert ps._UNIVERSE_TTL == 60 and ps._UNIVERSE_CLOSED_TTL == 900
    assert ps._universe_ttl_for(WED_NIGHT, WED_NIGHT + 10) == ps._UNIVERSE_CLOSED_TTL
    # Stamp in after hours, read in the closed window.
    assert ps._universe_ttl_for(_et(2026, 9, 30, 19, 59), WED_NIGHT) == ps._UNIVERSE_TTL
    # Stamp in the closed window, read in pre-market.
    assert ps._universe_ttl_for(_et(2026, 10, 1, 3, 59), _et(2026, 10, 1, 4, 0)) == ps._UNIVERSE_TTL
    # Both in session.
    assert ps._universe_ttl_for(WED_11, WED_11 + 10) == ps._UNIVERSE_TTL


@pytest.mark.parametrize("bad", [1e20, float("nan"), float("inf")])
def test_an_unreadable_stamp_gets_the_short_ttl_and_a_warning(bad, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    assert ps._universe_in_closed_window(bad, WED_NIGHT) is False
    assert ps._universe_ttl_for(bad, WED_NIGHT) == ps._UNIVERSE_TTL
    assert "unreadable" in caplog.text


# ── 2. `_get_universe` applies it ──────────────────────────────────────────────


class _Clock:
    """Stands in for the `time` module inside price_service only."""

    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return _real_time.monotonic()


class _Pages:
    """`_fetch_universe_pages` stand-in: counts sweeps; each sweep is a NEW row list."""

    def __init__(self, price: float = 101.0) -> None:
        self.calls = 0
        self.price = price
        self.fail: Optional[BaseException] = None
        self.empty = False

    async def __call__(self) -> List[Dict[str, Any]]:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        if self.empty:
            return []
        return [{"symbol": "AAPL", "price": self.price, "companyName": "Apple",
                 "volume": 1.0, "avgVolume": 1.0, "marketCap": 1e12}]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(ps, "_cache", {})
    monkeypatch.setattr(ps, "_inflight", {})
    clock = _Clock(WED_NIGHT)
    monkeypatch.setattr(ps, "time", clock)
    pages = _Pages()
    svc = PriceService()
    monkeypatch.setattr(svc, "_fetch_universe_pages", pages)
    return clock, pages, svc


@pytest.mark.asyncio
async def test_a_closed_window_reuses_one_sweep_for_fifteen_minutes(env):
    clock, pages, svc = env
    start = clock.t = WED_NIGHT
    first = await svc._get_universe()
    for age in (61, 300, 840, 899):
        clock.t = start + age
        assert await svc._get_universe() is first, f"re-swept at {age}s in a closed window"
    assert pages.calls == 1
    clock.t = start + 901
    second = await svc._get_universe()
    assert pages.calls == 2 and second is not first


@pytest.mark.asyncio
async def test_the_regular_session_keeps_the_sixty_second_rule(env):
    clock, pages, svc = env
    start = clock.t = WED_11
    first = await svc._get_universe()
    clock.t = start + 59
    assert await svc._get_universe() is first
    clock.t = start + 61
    await svc._get_universe()
    assert pages.calls == 2


@pytest.mark.asyncio
async def test_the_first_pre_market_read_is_never_held_back_by_the_night(env):
    clock, pages, svc = env
    clock.t = _et(2026, 10, 1, 3, 55)            # Thursday, the closed window
    night = await svc._get_universe()
    clock.t = _et(2026, 10, 1, 4, 0, 5)          # pre-market, 5 min later
    morning = await svc._get_universe()
    assert pages.calls == 2 and morning is not night


@pytest.mark.asyncio
async def test_an_after_hours_sweep_is_not_stretched_past_twenty_hundred(env):
    """The last after-hours sweep keeps 60 s; the first one taken after 20:00 lasts 15 min."""
    clock, pages, svc = env
    clock.t = _et(2026, 9, 30, 19, 59, 40)
    await svc._get_universe()
    clock.t = _et(2026, 9, 30, 20, 0, 41)        # 61 s later, now CLOSED
    closed_sweep = await svc._get_universe()
    assert pages.calls == 2
    clock.t = _et(2026, 9, 30, 20, 15, 40)       # 899 s after the closed-window sweep
    assert await svc._get_universe() is closed_sweep
    assert pages.calls == 2


class _SlowPages(_Pages):
    """A sweep that takes ``seconds`` of (fake) wall clock to land."""

    def __init__(self, clock: _Clock, seconds: float) -> None:
        super().__init__()
        self.clock = clock
        self.seconds = seconds

    async def __call__(self) -> List[Dict[str, Any]]:
        rows = await super().__call__()
        self.clock.t += self.seconds
        return rows


@pytest.mark.asyncio
async def test_a_sweep_is_stamped_when_it_was_requested_not_when_it_landed(env, monkeypatch):
    """A slow sweep's duration counts against its own TTL. Requested at 09:29:58 and landing
    35 s later, its prices are pre-open; a landing-time stamp (09:30:33) would let the Home
    grace pass them off as today's numbers."""
    clock, _, svc = env
    requested = clock.t = _et(2026, 10, 1, 9, 29, 58)
    slow = _SlowPages(clock, 35)
    monkeypatch.setattr(svc, "_fetch_universe_pages", slow)
    first = await svc._get_universe()
    assert ps._cache[ps._UNIVERSE_KEY][0] == requested
    clock.t = requested + 61                     # 26 s after it landed, 61 s after the request
    assert await svc._get_universe() is not first
    assert slow.calls == 2


@pytest.mark.asyncio
async def test_an_after_hours_request_that_lands_after_twenty_hundred_keeps_sixty_seconds(
    env, monkeypatch
):
    clock, _, svc = env
    clock.t = _et(2026, 9, 30, 19, 59, 50)
    slow = _SlowPages(clock, 20)                 # lands 20:00:10, CLOSED
    monkeypatch.setattr(svc, "_fetch_universe_pages", slow)
    first = await svc._get_universe()
    clock.t = _et(2026, 9, 30, 20, 0, 51)        # 61 s after the request
    assert await svc._get_universe() is not first, "an after-hours sweep got the 15-min TTL"
    assert slow.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("fmp 503"), "empty"])
async def test_a_stale_closed_window_sweep_is_never_served_when_the_resweep_fails(env, failure):
    """Degraded path: past 15 min the old rows are dropped, not served, and the failure is
    memoised (degraded key) exactly as in session."""
    clock, pages, svc = env
    start = clock.t = WED_NIGHT
    await svc._get_universe()
    clock.t = start + 901
    if failure == "empty":
        pages.empty = True
    else:
        pages.fail = failure
    with pytest.raises((FMPUnavailableException, RuntimeError)):
        await svc._get_universe()
    assert ps._UNIVERSE_KEY not in ps._cache, "a stale universe stayed servable"
    assert ps._UNIVERSE_DEGRADED_KEY in ps._cache


@pytest.mark.asyncio
async def test_the_cached_universe_holds_raw_rows_and_quotes_are_computed_at_read_time(
    env, monkeypatch,
):
    """Why 15 min is honest: the entry stores the screener's RAW rows (no change figure),
    and `get_quotes` derives the day change per read from the close map it reads then. A
    14-min-old closed-window sweep therefore quotes exactly what a fresh sweep of the same
    frozen price would, and follows a close map swapped in after the sweep."""
    clock, pages, svc = env
    snaps = {"AAPL": {"symbol": "AAPL", "close": 100.0, "previous_close": 99.0,
                      "trade_date": "2026-09-29"}}

    async def _closes(self_, symbols):
        return {s: snaps[s] for s in symbols if s in snaps}

    monkeypatch.setattr(PriceService, "get_close_snapshots", _closes)
    # The day-change math reads the session calendar with `now=None` (the wall clock);
    # route that to the fake clock so the expected figures below mean the same at any hour.
    for name in ("session_trading_date", "session_phase"):
        real = getattr(ps, name)
        monkeypatch.setattr(
            ps, name,
            lambda now=None, _real=real: _real(
                now if now is not None else datetime.fromtimestamp(clock.t, tz=timezone.utc)
            ),
        )
    start = clock.t = WED_NIGHT
    swept = await svc.get_quotes(["AAPL"])
    raw = ps._cache[ps._UNIVERSE_KEY][1]["AAPL"]
    assert "changePercentage" not in raw and "changesPercentage" not in raw

    clock.t = start + 840
    # The ingest lands Wednesday's official close between the sweep and this read.
    snaps["AAPL"] = {"symbol": "AAPL", "close": 100.8, "previous_close": 100.0,
                     "trade_date": "2026-09-30"}
    cached = await svc.get_quotes(["AAPL"])
    assert pages.calls == 1, "a closed-window read paid a screener sweep"

    # The same read against a FRESH sweep of the same frozen price.
    ps._cache.pop(ps._UNIVERSE_KEY)
    fresh = await svc.get_quotes(["AAPL"])
    assert pages.calls == 2
    assert cached == fresh
    # Read-time math against the NEW map: Wednesday's own move, 101 / 100 (its previous
    # close), labelled Wednesday — not the sweep-time figure against Tuesday's close.
    assert cached["AAPL"]["changePercentage"] == pytest.approx(1.0)
    assert cached["AAPL"]["changeSession"] == "2026-09-30"
    assert swept["AAPL"]["previousClose"] == 100.0 and cached["AAPL"]["previousClose"] == 100.0
    assert swept["AAPL"]["price"] == cached["AAPL"]["price"] == 101.0


@pytest.mark.asyncio
async def test_concurrent_closed_window_readers_share_the_cached_sweep(env):
    clock, pages, svc = env
    start = clock.t = WED_NIGHT
    await svc._get_universe()
    clock.t = start + 600
    results = await asyncio.gather(*(svc._get_universe() for _ in range(20)))
    assert pages.calls == 1 and all(r is results[0] for r in results)
