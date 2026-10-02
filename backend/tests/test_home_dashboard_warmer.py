"""The Home warm machinery: `refresh_due_sections`, `warm_all`, `force=` and honest fallbacks.

WHY THIS EXISTS — production, 2026-09/10 (plan: instant first Home paint).

Every guarded Home section kept an in-memory copy but rebuilt it only when a REQUEST found
it expired, so the request waited for the rebuild (p50 1.43 s, p99 6.15 s, 8 s after every
deploy). Market Pulse was never pre-warmed at all, the old scanner warmer only read through
the cache during the regular session, and the themes TTL (600 s) was shorter than its warm
interval (900 s). The warmer now rebuilds each shared section BEFORE its TTL runs out,
around the clock, without ever doubling a build — and when a guard does time out, a cached
copy is served only while it is honest (yesterday's movers never under today's header).

Everything here is hermetic: every section builder is replaced by a counting fake (no FMP,
no Supabase), and every class-level cache is swapped for a fresh dict per test.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import app.services.home_dashboard_service as hds
import app.services.market_movers_service as mm
import app.services.price_service as prices
import app.services.signals_service as sig
import app.services.trillion_club_service as tcs
from app.config import Settings, settings
from app.schemas.home_dashboard import (
    MarketPulseItemResponse,
    ScannerGroupResponse,
    ScannerGroupsResponse,
    SignalGroupResponse,
    SignalRowResponse,
    SignalsGroupResponse,
    ThemesGroupResponse,
    TrendingThemeResponse,
)
from app.schemas.trillion_club import TrillionClubGroupResponse
from app.services.home_dashboard_service import HomeDashboardService

_ET = ZoneInfo("America/New_York")
_LOGGER = "app.services.home_dashboard_service"


def _et(y: int, m: int, d: int, hh: int, mm: int = 0) -> float:
    return datetime(y, m, d, hh, mm, tzinfo=_ET).timestamp()


# Wednesday 2026-09-30, 11:00 ET — the regular session.
WED_11 = _et(2026, 9, 30, 11, 0)
# Saturday 2026-10-03, 12:00 ET — no session at all.
SAT_NOON = _et(2026, 10, 3, 12, 0)
# Captured at import, before any fixture swaps it for a fake.
_REAL_GET_SCANNERS = HomeDashboardService.get_scanners


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """Fresh class-level caches for every section this file touches, restored afterwards."""
    for name in (
        "_cache", "_inflight", "_scanner_cache", "_scanner_inflight",
        "_themes_cache", "_themes_inflight", "_warm_tasks", "_warm_cooldown_until",
    ):
        monkeypatch.setattr(HomeDashboardService, name, {})
    monkeypatch.setattr(prices, "_cache", {})
    monkeypatch.setattr(prices, "_inflight", {})
    monkeypatch.setattr(sig.SignalsService, "_cache", {})
    monkeypatch.setattr(sig.SignalsService, "_inflight", {})
    monkeypatch.setattr(sig.SignalsService, "_degraded_keys", set())
    monkeypatch.setattr(tcs.TrillionClubService, "_group_cache", {})
    monkeypatch.setattr(tcs.TrillionClubService, "_detail_cache", {})
    monkeypatch.setattr(tcs.TrillionClubService, "_inflight", {})
    monkeypatch.setattr(tcs.TrillionClubService, "_invalidated_at", 0.0)
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", True)


def _svc() -> HomeDashboardService:
    return HomeDashboardService.__new__(HomeDashboardService)


async def _drain(timeout: float = 2.0) -> None:
    """Await every warm task still running, so none outlives this test's loop."""
    tasks = [t for t in HomeDashboardService._warm_tasks.values() if not t.done()]
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
    for _ in range(3):
        await asyncio.sleep(0)


def _tile(symbol: str, kind: str = "etf") -> MarketPulseItemResponse:
    return MarketPulseItemResponse(
        symbol=symbol, name=symbol, type=kind, price=100.0, change_percent=1.0, spark=[],
    )


def _full_pulse():
    return [_tile(c["symbol"]) for c in hds._PULSE_SYMBOLS] + [_tile("BTCUSD", "crypto")]


def _partial_pulse():
    return [_tile(c["symbol"]) for c in hds._PULSE_SYMBOLS[:-1]]


def _good_scanners() -> ScannerGroupsResponse:
    return ScannerGroupsResponse(
        movers=ScannerGroupResponse(kind="movers"), volume=ScannerGroupResponse(kind="volume"),
    )


def _degraded_scanners() -> ScannerGroupsResponse:
    # The universe pair is missing (a screener outage); shorts alone survive.
    return ScannerGroupsResponse(shorts=ScannerGroupResponse(kind="shorts"))


def _themes() -> ThemesGroupResponse:
    return ThemesGroupResponse(themes=[
        TrendingThemeResponse(slug="s", title="S", accent_hex="22D3EE", ticker_count=3),
    ])


def _signals() -> SignalsGroupResponse:
    return SignalsGroupResponse(congress=SignalGroupResponse(
        kind="congress", entries=[SignalRowResponse(rank=1, symbol="NVDA", value=3.0)],
    ))


def _prime_all(stamp: float) -> None:
    """Every section present, good, and stamped at ``stamp``."""
    HomeDashboardService._cache[hds._CACHE_KEY] = (stamp, _full_pulse())
    prices._cache[hds._PRICE_UNIVERSE_KEY] = (stamp, {"AAPL": {"symbol": "AAPL"}})
    HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = (stamp, _good_scanners())
    HomeDashboardService._themes_cache[hds._THEMES_CACHE_KEY] = (stamp, _themes())
    sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (stamp, _signals())
    tcs.TrillionClubService._group_cache[tcs._GROUP_KEY] = (stamp, TrillionClubGroupResponse())


class _Fakes:
    """Counting stand-ins for each section's FORCED rebuild — the level the warmer calls.

    Each one writes its section's cache exactly as the real getter would on success, so the
    warmer's own "did it leave a new, good entry" judgement is exercised for real.
    """

    def __init__(self, monkeypatch):
        self.calls = {name: 0 for name in hds.WARM_SECTIONS}
        self.forced = {name: [] for name in hds.WARM_SECTIONS}
        self.raise_for: set = set()
        self.degraded_for: set = set()
        self.write_nothing_for: set = set()
        self.gates: dict = {}
        fakes = self

        async def _enter(name, force):
            fakes.calls[name] += 1
            fakes.forced[name].append(force)
            gate = fakes.gates.get(name)
            if gate is not None:
                await gate.wait()
            if name in fakes.raise_for:
                raise RuntimeError(f"{name} upstream down")
            return name not in fakes.write_nothing_for

        async def pulse(self_, *, force=False):
            if await _enter("pulse", force):
                tiles = _partial_pulse() if "pulse" in fakes.degraded_for else _full_pulse()
                HomeDashboardService._cache[hds._CACHE_KEY] = (hds.time.time(), tiles)
                return tiles
            return []

        async def universe(self_):
            await _enter("universe", True)
            universe = {"AAPL": {"symbol": "AAPL"}}
            prices._cache[hds._PRICE_UNIVERSE_KEY] = (hds.time.time(), universe)
            return universe

        async def scanners(self_, *, force=False):
            if await _enter("scanners", force):
                if "scanners" in fakes.degraded_for:
                    # The real writer BACKDATES a degraded build (TTL − degraded TTL).
                    stamp = hds.time.time() - (
                        hds._SCANNER_CACHE_TTL_SECONDS - hds._SCANNER_DEGRADED_TTL_SECONDS
                    )
                    result = _degraded_scanners()
                else:
                    stamp, result = hds.time.time(), _good_scanners()
                HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = (stamp, result)
                return result
            return ScannerGroupsResponse()

        async def themes(self_, *, force=False):
            if await _enter("themes", force):
                HomeDashboardService._themes_cache[hds._THEMES_CACHE_KEY] = (
                    hds.time.time(), _themes(),
                )
            return _themes()

        async def signals(self_, *, force=False):
            if await _enter("signals", force):
                sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (hds.time.time(), _signals())
                if "signals" in fakes.degraded_for:
                    sig.SignalsService._degraded_keys.add(sig._SIGNALS_CACHE_KEY)
                else:
                    sig.SignalsService._degraded_keys.discard(sig._SIGNALS_CACHE_KEY)
            return _signals()

        async def trillion(self_, *, force=False):
            if await _enter("trillion", force):
                tcs.TrillionClubService._group_cache[tcs._GROUP_KEY] = (
                    hds.time.time(), TrillionClubGroupResponse(),
                )
            return TrillionClubGroupResponse()

        monkeypatch.setattr(HomeDashboardService, "_get_pulse_cached", pulse)
        monkeypatch.setattr(prices.PriceService, "refresh_universe", universe)
        monkeypatch.setattr(HomeDashboardService, "get_scanners", scanners)
        monkeypatch.setattr(HomeDashboardService, "get_themes", themes)
        monkeypatch.setattr(sig.SignalsService, "get_signals", signals)
        monkeypatch.setattr(tcs.TrillionClubService, "get_group", trillion)


async def _kick_and_wait(svc, now=None):
    kicked = svc.refresh_due_sections(now=now)
    await _drain()
    return kicked


# ── 1. the ordering every refresh-ahead must keep ────────────────────────────────


def _tick() -> float:
    return float(settings.HOME_WARM_TICK_SECONDS)


def test_every_section_lands_its_rebuild_before_its_ttl():
    """refresh-ahead + one tick + one worst-case build < TTL, section by section.

    Spelled out per section (not only through `warm_ordering_violations`) so a change to
    any one number — themes back to 600, a slower pulse guard — fails by name.
    """
    tick = _tick()
    assert (hds._PULSE_REFRESH_AHEAD_SECONDS + tick + hds._PULSE_BUILD_TIMEOUT_SECONDS
            < hds._CACHE_TTL_SECONDS)
    assert (hds._UNIVERSE_REFRESH_AHEAD_SECONDS + tick + hds._UNIVERSE_BUILD_BUDGET_SECONDS
            < prices._UNIVERSE_TTL)
    # ...and inside a closed window, where the universe lives 900 s and is rebuilt at 840 s.
    universe = _svc()._warm_spec("universe")
    assert universe.closed_ttl_seconds == prices._UNIVERSE_CLOSED_TTL == 900
    assert universe.closed_refresh_ahead_seconds == 840
    assert (universe.closed_refresh_ahead_seconds + tick + hds._UNIVERSE_BUILD_BUDGET_SECONDS
            < prices._UNIVERSE_CLOSED_TTL)
    assert (hds._scanner_refresh_ahead_seconds() + tick + hds._SCANNER_BUILD_TIMEOUT_SECONDS
            < hds._SCANNER_CACHE_TTL_SECONDS)
    assert (hds._THEMES_REFRESH_AHEAD_SECONDS + tick + hds._THEMES_BUILD_TIMEOUT_SECONDS
            < hds._THEMES_CACHE_TTL_SECONDS)
    assert (hds._SIGNALS_REFRESH_AHEAD_SECONDS + tick + sig._SIGNALS_BUILD_TIMEOUT_SECONDS
            < sig._SIGNALS_MEM_TTL_SECONDS)
    assert (hds._TRILLION_REFRESH_AHEAD_SECONDS + tick + tcs._GROUP_TIMEOUT_SECONDS
            < tcs._CACHE_TTL_SECONDS)
    assert _svc().warm_ordering_violations() == []


def test_the_ordering_check_is_not_vacuous():
    """A 30 s tick breaks the two sub-minute sections, and only those; a 60 s tick also
    breaks the universe's closed-window pair (840 + 60 + 3 >= 900)."""
    broken = _svc().warm_ordering_violations(tick_seconds=30)
    assert [line.split(":")[0] for line in broken] == ["pulse", "universe"]
    broken = _svc().warm_ordering_violations(tick_seconds=60)
    assert "universe (closed window)" in [line.split(":")[0] for line in broken], broken
    assert not any(
        line.startswith("universe (closed window)")
        for line in _svc().warm_ordering_violations(tick_seconds=56)
    ), "840 + 56 + 3 = 899 < 900 must still hold"


def test_only_the_universe_carries_a_closed_window_pair():
    """The pulse, scanners and themes rows carry the DAY's move or a live crypto tile; only
    the raw screener sweep (frozen prices, change computed at read time) may live longer."""
    svc = _svc()
    for name in hds.WARM_SECTIONS:
        spec = svc._warm_spec(name)
        has_pair = spec.closed_window is not None
        assert has_pair is (name == "universe"), name
        assert (spec.closed_ttl_seconds is not None) is has_pair, name
        assert (spec.closed_refresh_ahead_seconds is not None) is has_pair, name
    pulse = svc._warm_spec("pulse")
    assert (pulse.refresh_ahead_seconds, pulse.ttl_seconds) == (40, 60), "pulse cadence moved"


def test_the_scanner_refresh_ahead_follows_the_setting_but_is_clamped(monkeypatch):
    monkeypatch.setattr(settings, "SCANNER_PREWARM_INTERVAL_SECONDS", 900)
    assert hds._scanner_refresh_ahead_seconds() == 900
    # Past the TTL would let the entry expire first; 0 would rebuild on every tick.
    monkeypatch.setattr(settings, "SCANNER_PREWARM_INTERVAL_SECONDS", 5000)
    assert hds._scanner_refresh_ahead_seconds() == (
        hds._SCANNER_CACHE_TTL_SECONDS - hds._SCANNER_REFRESH_AHEAD_MARGIN_SECONDS
    )
    monkeypatch.setattr(settings, "SCANNER_PREWARM_INTERVAL_SECONDS", 0)
    assert hds._scanner_refresh_ahead_seconds() == hds._SCANNER_REFRESH_AHEAD_FLOOR_SECONDS


# ── 2. which sections are due ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_due_sections_kicks_exactly_the_due_set(monkeypatch):
    fakes = _Fakes(monkeypatch)
    svc = _svc()
    now = WED_11
    _prime_all(now - 10)
    # Due by age: pulse (41 ≥ 40) and themes (481 ≥ 480). Just under: everything else.
    HomeDashboardService._cache[hds._CACHE_KEY] = (now - 41, _full_pulse())
    HomeDashboardService._themes_cache[hds._THEMES_CACHE_KEY] = (now - 481, _themes())
    prices._cache[hds._PRICE_UNIVERSE_KEY] = (now - 44, {"AAPL": {}})
    HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = (now - 899, _good_scanners())
    sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (now - 2399, _signals())
    tcs.TrillionClubService._group_cache[tcs._GROUP_KEY] = (now - 479, TrillionClubGroupResponse())
    try:
        kicked = await _kick_and_wait(svc, now=now)
        assert sorted(kicked) == ["pulse", "themes"]
        assert fakes.calls == {
            "pulse": 1, "universe": 0, "scanners": 0, "themes": 1, "signals": 0, "trillion": 0,
        }
        assert fakes.forced["pulse"] == [True] and fakes.forced["themes"] == [True], (
            "a warm rebuild that is not FORCED is a read-through no-op on a fresh entry"
        )
        assert HomeDashboardService._warm_tasks == {}, "a finished warm task kept its reference"
        assert HomeDashboardService._warm_cooldown_until == {}
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_a_missing_section_is_due_at_any_hour(monkeypatch):
    """No `is_open` gate: a Saturday cold process warms every section."""
    fakes = _Fakes(monkeypatch)
    try:
        kicked = await _kick_and_wait(_svc(), now=SAT_NOON)
        assert sorted(kicked) == sorted(hds.WARM_SECTIONS)
        assert all(n == 1 for n in fakes.calls.values()), fakes.calls
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_a_degraded_entry_is_due_but_not_while_in_cooldown(monkeypatch):
    fakes = _Fakes(monkeypatch)
    svc = _svc()
    now = WED_11
    _prime_all(now - 1)
    HomeDashboardService._cache[hds._CACHE_KEY] = (now - 1, _partial_pulse())
    try:
        HomeDashboardService._warm_cooldown_until["pulse"] = now + 30
        assert svc.refresh_due_sections(now=now) == [], "kicked inside the cooldown"
        assert svc.refresh_due_sections(now=now + 31) == ["pulse"]
        await _drain()
        assert fakes.calls["pulse"] == 1
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_a_warm_build_that_comes_back_degraded_sets_the_cooldown(monkeypatch, caplog):
    fakes = _Fakes(monkeypatch)
    fakes.degraded_for = {"pulse", "scanners", "signals"}
    svc = _svc()
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    try:
        kicked = await _kick_and_wait(svc)
        assert set(kicked) == set(hds.WARM_SECTIONS)
        before = hds.time.time()
        cooldown = HomeDashboardService._warm_cooldown_until
        assert set(cooldown) == {"pulse", "scanners", "signals"}
        # The section's own degraded cadence when longer than the default: signals' rebuild
        # is ~8 FMP calls, so it is not retried every 45 s.
        assert cooldown["signals"] - before == pytest.approx(sig._SIGNALS_DEGRADED_TTL_SECONDS, abs=2)
        assert cooldown["pulse"] - before == pytest.approx(hds._WARM_FAILURE_COOLDOWN_SECONDS, abs=2)
        assert "the new entry is degraded" in caplog.text
        # The very next tick: still degraded, but nobody is kicked again.
        assert svc.refresh_due_sections() == []
        assert fakes.calls["pulse"] == fakes.calls["scanners"] == fakes.calls["signals"] == 1
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_a_build_that_writes_nothing_is_a_failure_not_a_success(monkeypatch, caplog):
    """An empty pulse or a failed themes read is NOT cached; the warmer must not call
    that a success (it would then never back off)."""
    fakes = _Fakes(monkeypatch)
    fakes.write_nothing_for = {"pulse", "themes"}
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    try:
        await _kick_and_wait(_svc())
        assert {"pulse", "themes"} <= set(HomeDashboardService._warm_cooldown_until)
        assert "nothing was cached" in caplog.text
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_a_success_clears_the_cooldown_and_logs_the_recovery(monkeypatch, caplog):
    fakes = _Fakes(monkeypatch)
    svc = _svc()
    _prime_all(hds.time.time())
    HomeDashboardService._cache[hds._CACHE_KEY] = (hds.time.time(), _partial_pulse())
    HomeDashboardService._warm_cooldown_until["pulse"] = hds.time.time() - 1   # just expired
    caplog.set_level(logging.INFO, logger=_LOGGER)
    try:
        assert await _kick_and_wait(svc) == ["pulse"]
        assert "pulse" not in HomeDashboardService._warm_cooldown_until
        assert "pulse recovered" in caplog.text
        assert fakes.calls["pulse"] == 1
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_an_invalidated_trillion_group_is_due_and_a_disabled_one_never_is(monkeypatch):
    fakes = _Fakes(monkeypatch)
    svc = _svc()
    now = hds.time.time()
    _prime_all(now - 5)
    tcs.TrillionClubService._invalidated_at = now - 1      # a job wrote after the build
    try:
        assert await _kick_and_wait(svc) == ["trillion"]
        monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", False)
        tcs.TrillionClubService._group_cache.clear()
        assert await _kick_and_wait(svc) == [], "a disabled feature was warmed"
        assert fakes.calls["trillion"] == 1
    finally:
        await _drain()


# ── 2b. the screener universe in a closed window ────────────────────────────────
# Overnight, at weekends and on holidays equity prices cannot move, so the universe — the one
# ~7,000-row FMP response — lives 900 s there (`price_service._UNIVERSE_CLOSED_TTL`) and the
# warmer rebuilds it at 840 s instead of every ~50 s. Any other phase pair keeps 45 s.


@pytest.mark.asyncio
@pytest.mark.parametrize("label, stamp, age, due", [
    ("weeknight, 839 s", _et(2026, 9, 30, 21, 0), 839, False),
    ("weeknight, 840 s", _et(2026, 9, 30, 21, 0), 840, True),
    ("weeknight, 300 s", _et(2026, 9, 30, 21, 0), 300, False),
    ("Saturday, 839 s", SAT_NOON, 839, False),
    ("Saturday, 841 s", SAT_NOON, 841, True),
    ("Thanksgiving, 839 s", _et(2026, 11, 26, 12, 0), 839, False),
    ("Thanksgiving, 840 s", _et(2026, 11, 26, 12, 0), 840, True),
    ("half-day after 13:00, 839 s", _et(2026, 11, 27, 13, 5), 839, False),
    ("regular session, 44 s", WED_11, 44, False),
    ("regular session, 45 s", WED_11, 45, True),
    # After-hours stamp read after 20:00: a phase change, so the short age applies.
    ("after-hours stamp at 20:00:30", _et(2026, 9, 30, 20, 0) - 20, 50, True),
    ("after-hours stamp at 20:00:24", _et(2026, 9, 30, 20, 0) - 20, 44, False),
    # The night's sweep read in pre-market: due at once (it is no longer fresh)...
    ("night stamp read at 04:05", _et(2026, 10, 1, 3, 55), 610, True),
    # ...but one still under 45 s old is left alone (fresh under the 60 s rule).
    ("night stamp read at 04:00:10", _et(2026, 10, 1, 4, 0) - 20, 30, False),
])
async def test_the_universe_due_age_follows_the_closed_window(label, stamp, age, due, monkeypatch):
    fakes = _Fakes(monkeypatch)
    now = stamp + age
    _prime_all(now - 1)
    prices._cache[hds._PRICE_UNIVERSE_KEY] = (stamp, {"AAPL": {"symbol": "AAPL"}})
    try:
        kicked = await _kick_and_wait(_svc(), now=now)
        assert kicked == (["universe"] if due else []), label
        assert fakes.calls["universe"] == (1 if due else 0), label
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_the_pulse_keeps_its_cadence_in_a_closed_window(monkeypatch):
    """Only the universe stretches: the pulse (with its live crypto tile) is still rebuilt at
    40 s overnight, and its build reuses the 10-min-old universe instead of re-sweeping."""
    fakes = _Fakes(monkeypatch)
    now = _et(2026, 9, 30, 21, 0) + 600
    _prime_all(now - 1)
    HomeDashboardService._cache[hds._CACHE_KEY] = (now - 41, _full_pulse())
    prices._cache[hds._PRICE_UNIVERSE_KEY] = (now - 600, {"AAPL": {"symbol": "AAPL"}})
    try:
        assert await _kick_and_wait(_svc(), now=now) == ["pulse"]
        assert fakes.calls["universe"] == 0 and fakes.calls["pulse"] == 1
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_one_closed_hour_of_ticks_sweeps_the_universe_about_four_times(monkeypatch):
    """Drive one simulated closed hour tick by tick (10 s): ~4 universe sweeps, not ~70.
    The same hour in session sweeps every ~50 s."""
    _Fakes(monkeypatch)
    svc = _svc()

    async def _sweeps_in_one_hour(start: float) -> int:
        prices._cache[hds._PRICE_UNIVERSE_KEY] = (start, {"AAPL": {"symbol": "AAPL"}})
        sweeps = 0
        for tick in range(1, 361):
            now = start + tick * 10
            universe_entry = prices._cache[hds._PRICE_UNIVERSE_KEY]
            _prime_all(now)                       # every OTHER section fresh
            prices._cache[hds._PRICE_UNIVERSE_KEY] = universe_entry
            if "universe" in svc.refresh_due_sections(now=now):
                sweeps += 1
                await _drain()
                # The fake stamps with the real clock; re-stamp at the simulated one.
                prices._cache[hds._PRICE_UNIVERSE_KEY] = (now, {"AAPL": {"symbol": "AAPL"}})
        return sweeps

    try:
        closed = await _sweeps_in_one_hour(SAT_NOON)
        in_session = await _sweeps_in_one_hour(WED_11)
        assert 4 <= closed <= 5, closed
        assert 60 <= in_session <= 80, in_session
    finally:
        await _drain()


# ── 3. the trading session the numbers describe ─────────────────────────────────


@pytest.mark.parametrize("moment, expected", [
    (_et(2026, 9, 30, 3, 0), date(2026, 9, 29)),     # Wed overnight → Tue's numbers
    (_et(2026, 9, 30, 5, 0), date(2026, 9, 29)),     # Wed pre-market → STILL Tue's move
    (_et(2026, 9, 30, 9, 29), date(2026, 9, 29)),
    (_et(2026, 9, 30, 9, 30), date(2026, 9, 30)),    # the open: the only change of the day
    (_et(2026, 9, 30, 17, 0), date(2026, 9, 30)),    # after hours
    (_et(2026, 9, 30, 21, 0), date(2026, 9, 30)),    # overnight, before midnight
    (_et(2026, 10, 3, 12, 0), date(2026, 10, 2)),    # Saturday → Friday
    (_et(2026, 10, 5, 6, 0), date(2026, 10, 2)),     # Monday pre-market → Friday
    (_et(2026, 9, 7, 10, 0), date(2026, 9, 4)),      # Labor Day → the Friday before
    (_et(2026, 9, 8, 6, 0), date(2026, 9, 4)),       # the Tuesday after, pre-market
    (_et(2026, 11, 27, 14, 0), date(2026, 11, 27)),  # half-day, after its 13:00 close
])
def test_numbers_session(moment, expected):
    assert hds._numbers_session(moment) == expected


@pytest.mark.asyncio
async def test_a_new_trading_session_makes_scanners_and_themes_due(monkeypatch):
    """Built pre-market (Tuesday's moves), read after the open: due although young by age."""
    fakes = _Fakes(monkeypatch)
    svc = _svc()
    # Just past the open grace, and 360 s after the build: under both refresh-ahead ages.
    now = _et(2026, 9, 30, 9, 30) + hds._SESSION_GRACE_SECONDS + 30
    built = now - 360
    assert built < _et(2026, 9, 30, 9, 30)
    _prime_all(now - 1)
    HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = (built, _good_scanners())
    HomeDashboardService._themes_cache[hds._THEMES_CACHE_KEY] = (built, _themes())
    # Not session-bound: an old-session stamp alone does not make these due.
    sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (built, _signals())
    tcs.TrillionClubService._group_cache[tcs._GROUP_KEY] = (built, TrillionClubGroupResponse())
    try:
        assert sorted(await _kick_and_wait(svc, now=now)) == ["scanners", "themes"]
        assert fakes.calls["scanners"] == fakes.calls["themes"] == 1
    finally:
        await _drain()


def test_the_open_grace_keeps_a_pre_open_copy_for_one_short_window():
    """Inside the grace after 09:30 the universe may still be pre-open, so a pre-open copy
    is not yet a different session; once the grace has passed it is."""
    open_ = _et(2026, 9, 30, 9, 30)
    grace = hds._SESSION_GRACE_SECONDS
    built = _et(2026, 9, 30, 9, 25)
    assert hds._same_numbers_session(built, open_ + grace - 1)
    assert not hds._same_numbers_session(built, open_ + grace + 1)
    # A build stamped inside the grace is not passed off as today's, either.
    assert not hds._same_numbers_session(open_ + 30, _et(2026, 9, 30, 10, 0))
    assert not hds._same_numbers_session(open_ + grace - 1, _et(2026, 9, 30, 10, 0))
    assert hds._same_numbers_session(open_ + grace + 1, _et(2026, 9, 30, 10, 0))


def test_the_open_grace_covers_both_universe_caches_a_tick_and_the_upstream_builds():
    """The scanners read the screener universe (60 s) THROUGH the movers universe (60 s),
    so a build that STARTS after 09:30 can still read pre-open numbers for both TTLs, plus
    the run of the two upstream builds (each stamps its entry when it ENDS). The scanner
    build's own run is not part of it: its entry is stamped with its start.
    Derived, not hand-written: a change to either TTL moves the grace with it."""
    assert hds._SESSION_GRACE_SECONDS == (
        prices._UNIVERSE_TTL
        + mm._UNIVERSE_TTL
        + Settings.model_fields["HOME_WARM_TICK_SECONDS"].default
        + hds._SESSION_GRACE_UPSTREAM_BUILD_SECONDS
    )
    assert hds._SESSION_GRACE_SECONDS >= 150
    # The margin covers a screener sweep AND a movers derivation at the sweep's budget.
    assert hds._SESSION_GRACE_UPSTREAM_BUILD_SECONDS >= 2 * hds._UNIVERSE_BUILD_BUDGET_SECONDS


def test_the_reviewed_worst_case_build_is_judged_pre_open():
    """The timeline from the review: a pre-open sweep at 09:29:59 (stamped when it ends) is
    served until just before 09:30:59, a movers universe derived from it just before then
    (also stamped at its end) lives until just before 09:31:59, and a scanner build STARTS
    then and reads it — its entry is stamped with that start, however long it runs. That
    build must NOT count as today's, so the warmer rebuilds it after the grace."""
    sweep = _et(2026, 9, 30, 9, 30) - 1
    sweep_stamp = sweep + hds._UNIVERSE_BUILD_BUDGET_SECONDS
    movers_derived = sweep_stamp + prices._UNIVERSE_TTL - 0.5 + hds._UNIVERSE_BUILD_BUDGET_SECONDS
    build_starts = movers_derived + mm._UNIVERSE_TTL - 0.5   # the build's stamp
    assert build_starts - sweep <= hds._SESSION_GRACE_SECONDS - 10   # a tick of margin left
    ten = _et(2026, 9, 30, 10, 0)
    assert not hds._same_numbers_session(build_starts, ten), "a pre-open build passed as today's"
    problem = hds._stale_serve_problem(
        build_starts, now=ten,
        in_session_ceiling=hds._SCANNER_STALE_CEILING_IN_SESSION_SECONDS,
        off_session_ceiling=hds._SCANNER_STALE_CEILING_OFF_SESSION_SECONDS,
    )
    assert problem is not None and "session" in problem
    # The warmer's session-change rebuild fires only once every input is post-open: by
    # then any live screener sweep is from after 09:30 and any movers universe from after it.
    first_due = _et(2026, 9, 30, 9, 30) + hds._SESSION_GRACE_SECONDS
    assert first_due - mm._UNIVERSE_TTL - prices._UNIVERSE_TTL >= _et(2026, 9, 30, 9, 30)


class _Clock:
    """A settable wall clock standing in for `hds.time` (monotonic stays real)."""

    def __init__(self, now: float):
        self.now = float(now)

    def time(self) -> float:
        return self.now


def _fake_clock(monkeypatch, now: float) -> _Clock:
    clock = _Clock(now)
    monkeypatch.setattr(hds, "time", SimpleNamespace(time=clock.time, monotonic=time.monotonic))
    return clock


@pytest.mark.asyncio
async def test_a_slow_scanner_build_is_judged_by_when_it_started(monkeypatch):
    """Round-3 review: the shorts leg has no overall timeout, so a build that read the
    universe at 09:30:10 (pre-open numbers) could END after 09:32:30 — and an END stamp,
    shifted back by the grace, then counted as today's session. The entry carries the
    build's START, so every session check (warmer, stale serve) sees it as pre-open."""
    clock = _fake_clock(monkeypatch, _et(2026, 9, 30, 9, 30) + 10)
    started = clock.now

    async def slow_build(self_):
        clock.now += 300   # the shorts leg ran on for five minutes
        return _good_scanners()

    monkeypatch.setattr(HomeDashboardService, "_build_scanner_groups", slow_build)
    svc = _svc()
    await svc.get_scanners(force=True)
    stamp, result = HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY]
    landed = clock.now
    assert stamp == started and not hds._scanner_is_degraded(result)
    # An end stamp would have passed for today's session — the defect this pins.
    assert hds._same_numbers_session(landed, landed + 60)
    assert not hds._same_numbers_session(stamp, landed)
    reason = HomeDashboardService._warm_due_reason(svc._warm_spec("scanners"), landed)
    assert reason == "a new trading session"
    problem = hds._stale_serve_problem(
        stamp, now=landed,
        in_session_ceiling=hds._SCANNER_STALE_CEILING_IN_SESSION_SECONDS,
        off_session_ceiling=hds._SCANNER_STALE_CEILING_OFF_SESSION_SECONDS,
    )
    assert problem is not None and "session" in problem


@pytest.mark.asyncio
async def test_a_scanner_build_ages_from_its_start(monkeypatch):
    """The TTL counts the age of the numbers inside: a build that ran 100 s lands 100 s old."""
    clock = _fake_clock(monkeypatch, WED_11)
    started = clock.now

    async def slow_build(self_):
        clock.now += 100
        return _good_scanners()

    monkeypatch.setattr(HomeDashboardService, "_build_scanner_groups", slow_build)
    await _svc().get_scanners()
    stamp, _ = HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY]
    assert stamp == started
    assert clock.now - stamp == 100


@pytest.mark.asyncio
async def test_a_slow_degraded_scanner_build_is_still_held_from_when_it_lands(monkeypatch):
    """A degraded entry is a herd guard: held `_SCANNER_DEGRADED_TTL_SECONDS` after it lands
    (an expired-on-arrival entry would make every request rebuild), and never stamped later
    than its start."""
    clock = _fake_clock(monkeypatch, WED_11)
    started = clock.now

    async def slow_degraded(self_):
        clock.now += 100
        return _degraded_scanners()

    monkeypatch.setattr(HomeDashboardService, "_build_scanner_groups", slow_degraded)
    await _svc().get_scanners()
    stamp, _ = HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY]
    held_for = hds._SCANNER_CACHE_TTL_SECONDS - (clock.now - stamp)
    assert held_for == hds._SCANNER_DEGRADED_TTL_SECONDS
    assert stamp <= started


@pytest.mark.asyncio
async def test_a_slow_themes_build_is_judged_by_when_it_started(monkeypatch):
    """Themes read the universe after a Supabase read, and finish on the review and insight
    reads; their entry carries the START too, so a slow pre-open build is not today's."""
    clock = _fake_clock(monkeypatch, _et(2026, 9, 30, 9, 30) + 10)
    started = clock.now

    async def slow_build(self_):
        clock.now += 300
        return _themes()

    monkeypatch.setattr(HomeDashboardService, "_build_themes", slow_build)
    svc = _svc()
    await svc.get_themes(force=True)
    stamp, _ = HomeDashboardService._themes_cache[hds._THEMES_CACHE_KEY]
    assert stamp == started
    reason = HomeDashboardService._warm_due_reason(svc._warm_spec("themes"), clock.now)
    assert reason == "a new trading session"


# ── 4. force: skip the freshness check, never the dedup ──────────────────────────


def _arm(section: str, monkeypatch, svc: HomeDashboardService):
    """Real getter, stubbed INNER build held on a gate. Returns (call, builds, gate, prime)."""
    builds = {"n": 0}
    gate = asyncio.Event()

    async def held(value):
        builds["n"] += 1
        await gate.wait()
        return value

    if section == "pulse":
        async def build(self_):
            return await held(_full_pulse())
        monkeypatch.setattr(HomeDashboardService, "_build_pulse", build)

        def prime(stamp):
            HomeDashboardService._cache[hds._CACHE_KEY] = (stamp, _full_pulse())
        return (lambda force: svc._get_pulse_cached(force=force)), builds, gate, prime
    if section == "scanners":
        async def build(self_):
            return await held(_good_scanners())
        monkeypatch.setattr(HomeDashboardService, "_build_scanner_groups", build)

        def prime(stamp):
            HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = (stamp, _good_scanners())
        return (lambda force: svc.get_scanners(force=force)), builds, gate, prime
    if section == "themes":
        async def build(self_):
            return await held(_themes())
        monkeypatch.setattr(HomeDashboardService, "_build_themes", build)

        def prime(stamp):
            HomeDashboardService._themes_cache[hds._THEMES_CACHE_KEY] = (stamp, _themes())
        return (lambda force: svc.get_themes(force=force)), builds, gate, prime
    if section == "signals":
        ssvc = sig.SignalsService.__new__(sig.SignalsService)
        monkeypatch.setattr(sig.SignalsService, "_read_supabase_cache", lambda self_: None)
        monkeypatch.setattr(sig.SignalsService, "_write_supabase_cache", lambda self_, r: None)

        async def build(self_):
            return await held((_signals(), frozenset()))
        monkeypatch.setattr(sig.SignalsService, "_build", build)

        def prime(stamp):
            sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (stamp, _signals())
        return (lambda force: ssvc.get_signals(force=force)), builds, gate, prime
    if section == "trillion":
        tsvc = tcs.TrillionClubService()

        async def build(self_):
            return await held(TrillionClubGroupResponse())
        monkeypatch.setattr(tcs.TrillionClubService, "_build_group", build)

        def prime(stamp):
            tcs.TrillionClubService._group_cache[tcs._GROUP_KEY] = (stamp, TrillionClubGroupResponse())
        return (lambda force: tsvc.get_group(force=force)), builds, gate, prime
    raise AssertionError(section)


_FORCE_SECTIONS = ["pulse", "scanners", "themes", "signals", "trillion"]
_STALE_AGE = {"pulse": 61, "scanners": 1201, "themes": 601, "signals": 2701, "trillion": 601}


@pytest.mark.asyncio
@pytest.mark.parametrize("section", _FORCE_SECTIONS)
async def test_force_on_a_fresh_entry_builds_exactly_once(section, monkeypatch):
    svc = _svc()
    call, builds, gate, prime = _arm(section, monkeypatch, svc)
    prime(hds.time.time())
    gate.set()
    assert builds["n"] == 0
    await call(False)
    assert builds["n"] == 0, "a fresh entry was rebuilt without force"
    await call(True)
    assert builds["n"] == 1, "force did not rebuild a fresh entry"
    await call(False)
    assert builds["n"] == 1, "the forced build was not cached"


@pytest.mark.asyncio
@pytest.mark.parametrize("section", _FORCE_SECTIONS)
@pytest.mark.parametrize("forced_first", [True, False])
async def test_force_and_a_concurrent_read_share_one_build(section, forced_first, monkeypatch):
    svc = _svc()
    call, builds, gate, prime = _arm(section, monkeypatch, svc)
    prime(hds.time.time() - _STALE_AGE[section])
    order = [True, False] if forced_first else [False, True]
    first = asyncio.create_task(call(order[0]))
    for _ in range(5):
        await asyncio.sleep(0)
    second = asyncio.create_task(call(order[1]))
    third = asyncio.create_task(call(True))            # and a second forced caller
    for _ in range(5):
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(first, second, third), 2.0)
    assert builds["n"] == 1, f"{section}: force started a second concurrent build"
    assert results[0] is results[1] is results[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("build", ["partial", "empty"])
async def test_a_forced_pulse_refresh_never_downgrades_a_still_valid_strip(build, monkeypatch):
    """Refresh-ahead runs while the complete strip is still valid; a degraded (or empty)
    early build must not replace it — the warmer would otherwise be worse than no warmer."""
    svc = _svc()
    new = {"partial": _partial_pulse(), "empty": []}[build]

    async def build_pulse(self_):
        return list(new)

    monkeypatch.setattr(HomeDashboardService, "_build_pulse", build_pulse)
    good = _full_pulse()
    entry = (hds.time.time() - 41, good)
    HomeDashboardService._cache[hds._CACHE_KEY] = entry
    assert await svc._get_pulse_cached(force=True) is good
    assert HomeDashboardService._cache[hds._CACHE_KEY] is entry, "the good strip was replaced"

    # Once the complete strip has EXPIRED, the partial build is the best there is.
    if build == "partial":
        HomeDashboardService._cache[hds._CACHE_KEY] = (hds.time.time() - 61, good)
        result = await svc._get_pulse_cached(force=True)
        assert len(result) == len(new) and HomeDashboardService._cache[hds._CACHE_KEY][1] == result
        # And a NON-forced build writes its partial exactly as before.
        HomeDashboardService._cache[hds._CACHE_KEY] = (hds.time.time() - 61, good)
        assert len(await svc._get_pulse_cached()) == len(new)


@pytest.mark.asyncio
@pytest.mark.parametrize("build", ["degraded", "raises"])
async def test_a_forced_scanner_refresh_never_downgrades_still_valid_cards(build, monkeypatch):
    svc = _svc()

    async def build_scanners(self_):
        if build == "raises":
            raise RuntimeError("screener down")
        return _degraded_scanners()

    monkeypatch.setattr(HomeDashboardService, "_build_scanner_groups", build_scanners)
    good = _good_scanners()
    entry = (hds.time.time() - 901, good)
    HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = entry
    assert await svc.get_scanners(force=True) is good
    assert HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] is entry
    # Past the TTL the old rules apply: a degraded build is written (backdated).
    HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = (hds.time.time() - 1201, good)
    result = await svc.get_scanners(force=True)
    if build == "degraded":
        assert hds._scanner_is_degraded(result)
        assert HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY][1] is result
    else:
        assert result == ScannerGroupsResponse()


@pytest.mark.asyncio
async def test_a_forced_signals_refresh_never_downgrades_still_valid_cards(monkeypatch):
    ssvc = sig.SignalsService.__new__(sig.SignalsService)
    writes = []
    monkeypatch.setattr(sig.SignalsService, "_read_supabase_cache", lambda self_: None)
    monkeypatch.setattr(sig.SignalsService, "_write_supabase_cache", lambda self_, r: writes.append(r))

    async def build(self_):
        return SignalsGroupResponse(earnings=_signals().congress), frozenset({"ceo"})

    monkeypatch.setattr(sig.SignalsService, "_build", build)
    good = _signals()
    entry = (hds.time.time() - 2401, good)
    sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = entry
    assert await ssvc.get_signals(force=True) is good
    assert sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] is entry
    assert sig._SIGNALS_CACHE_KEY not in sig.SignalsService._degraded_keys
    assert writes == []
    # Not forced (the entry expired): the degraded build is cached in memory as before.
    sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (hds.time.time() - 2701, good)
    result = await ssvc.get_signals()
    assert result is not good and sig._SIGNALS_CACHE_KEY in sig.SignalsService._degraded_keys


@pytest.mark.asyncio
async def test_a_kept_entry_is_judged_a_failed_warm_build(monkeypatch, caplog):
    """The warmer must back off when its forced build was refused, not call it a success."""
    svc = _svc()

    async def build_pulse(self_):
        return _partial_pulse()

    monkeypatch.setattr(HomeDashboardService, "_build_pulse", build_pulse)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    HomeDashboardService._cache[hds._CACHE_KEY] = (hds.time.time() - 41, _full_pulse())
    assert await svc._warm_one(svc._warm_spec("pulse"), "41s old") is False
    assert "pulse" in HomeDashboardService._warm_cooldown_until
    assert "the cached entry was not replaced" in caplog.text


@pytest.mark.asyncio
async def test_forced_signals_still_read_the_supabase_tier_first(monkeypatch):
    """Only the MEMORY fast path is skipped: a fresh Tier-2 row is reloaded, not rebuilt."""
    ssvc = sig.SignalsService.__new__(sig.SignalsService)
    tier2 = _signals()
    builds = {"n": 0}

    async def build(self_):
        builds["n"] += 1
        return SignalsGroupResponse(), frozenset()

    monkeypatch.setattr(sig.SignalsService, "_read_supabase_cache", lambda self_: tier2)
    monkeypatch.setattr(sig.SignalsService, "_build", build)
    sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (hds.time.time(), SignalsGroupResponse())
    assert await ssvc.get_signals(force=True) is tier2
    assert builds["n"] == 0
    assert sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY][1] is tier2


# ── 5. never two builds; one section never stops the others ─────────────────────


@pytest.mark.asyncio
async def test_a_section_a_request_is_building_is_not_kicked(monkeypatch):
    fakes = _Fakes(monkeypatch)
    svc = _svc()
    _prime_all(hds.time.time())
    HomeDashboardService._scanner_cache.clear()          # due: missing
    loop = asyncio.get_running_loop()
    request_build = loop.create_future()
    HomeDashboardService._scanner_inflight[hds._SCANNER_CACHE_KEY] = request_build
    try:
        assert svc.refresh_due_sections() == []
        assert fakes.calls["scanners"] == 0
    finally:
        request_build.cancel()
        await _drain()


@pytest.mark.asyncio
async def test_a_still_running_warm_task_is_not_kicked_twice(monkeypatch):
    fakes = _Fakes(monkeypatch)
    svc = _svc()
    _prime_all(hds.time.time())
    HomeDashboardService._themes_cache.clear()
    fakes.gates["themes"] = asyncio.Event()
    try:
        assert svc.refresh_due_sections() == ["themes"]
        for _ in range(3):
            await asyncio.sleep(0)
        assert svc.refresh_due_sections() == [], "a second build of a running section"
        fakes.gates["themes"].set()
        await _drain()
        assert fakes.calls["themes"] == 1
    finally:
        fakes.gates["themes"].set()
        await _drain()


@pytest.mark.asyncio
async def test_warm_all_joins_a_build_the_warmer_already_started(monkeypatch):
    """The boot warm and a warmer tick racing each other still build each section once."""
    svc = _svc()
    _call, builds, gate, _prime = _arm("scanners", monkeypatch, svc)
    fakes_for_rest = _Fakes(monkeypatch)
    # Put the REAL scanner getter back (the fakes replaced it) so its dedup is exercised.
    monkeypatch.setattr(HomeDashboardService, "get_scanners", _REAL_GET_SCANNERS)
    try:
        assert "scanners" in svc.refresh_due_sections()
        for _ in range(5):
            await asyncio.sleep(0)
        warm = asyncio.create_task(svc.warm_all())
        for _ in range(5):
            await asyncio.sleep(0)
        gate.set()
        outcome = await asyncio.wait_for(warm, 2.0)
        await _drain()
        assert builds["n"] == 1, "warm_all started a second scanner build"
        # The joined build counts as warm for both callers, and the other sections (their
        # warmer tasks had already finished, so warm_all rebuilt them) warmed too.
        assert outcome == {name: True for name in hds.WARM_SECTIONS}
        assert fakes_for_rest.calls["scanners"] == 0, "the fake, not the real getter, ran"
    finally:
        gate.set()
        await _drain()


@pytest.mark.asyncio
async def test_one_raising_section_does_not_stop_the_others(monkeypatch, caplog):
    fakes = _Fakes(monkeypatch)
    fakes.raise_for = {"scanners"}
    svc = _svc()
    real_spec = HomeDashboardService._warm_spec

    def spec_or_boom(self_, name):
        if name == "themes":
            raise ImportError("themes module is broken")
        return real_spec(self_, name)

    monkeypatch.setattr(HomeDashboardService, "_warm_spec", spec_or_boom)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    try:
        kicked = await _kick_and_wait(svc)
        assert set(kicked) == set(hds.WARM_SECTIONS) - {"themes"}
        for name in ("pulse", "universe", "signals", "trillion"):
            assert fakes.calls[name] == 1, name
        assert "scanners rebuild failed: RuntimeError: scanners upstream down" in caplog.text
        assert "could not check or kick the themes section: ImportError" in caplog.text
        assert set(HomeDashboardService._warm_cooldown_until) == {"scanners", "themes"}
        # The healthy sections were cached by their builds.
        assert HomeDashboardService._cache.get(hds._CACHE_KEY) is not None
    finally:
        await _drain()


@pytest.mark.asyncio
async def test_warm_all_reports_every_section_and_omits_a_disabled_one(monkeypatch):
    fakes = _Fakes(monkeypatch)
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", False)
    outcome = await _svc().warm_all()
    assert outcome == {name: True for name in hds.WARM_SECTIONS if name != "trillion"}
    assert all(forced == [True] for name, forced in fakes.forced.items() if name != "trillion")
    assert fakes.calls["trillion"] == 0


@pytest.mark.asyncio
async def test_warm_all_never_raises_when_every_section_raises(monkeypatch, caplog):
    fakes = _Fakes(monkeypatch)
    fakes.raise_for = set(hds.WARM_SECTIONS)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    outcome = await _svc().warm_all()
    assert outcome == {name: False for name in hds.WARM_SECTIONS}
    for name in hds.WARM_SECTIONS:
        assert f"{name} rebuild failed: RuntimeError: {name} upstream down" in caplog.text
    assert "0/6 sections warm" in caplog.text


@pytest.mark.asyncio
async def test_warm_all_survives_a_section_whose_descriptor_cannot_be_built(monkeypatch):
    _Fakes(monkeypatch)
    real_spec = HomeDashboardService._warm_spec

    def spec_or_boom(self_, name):
        if name == "trillion":
            raise ImportError("trillion module is broken")
        return real_spec(self_, name)

    monkeypatch.setattr(HomeDashboardService, "_warm_spec", spec_or_boom)
    outcome = await _svc().warm_all()
    assert outcome["trillion"] is False
    assert all(outcome[name] for name in hds.WARM_SECTIONS if name != "trillion")


# ── 6. task ownership ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_cancels_a_running_warm_build_without_stranding_its_joiners(monkeypatch):
    svc = _svc()
    _call, builds, gate, _prime = _arm("scanners", monkeypatch, svc)
    _prime_all(hds.time.time())
    HomeDashboardService._scanner_cache.clear()
    try:
        assert svc.refresh_due_sections() == ["scanners"]
        for _ in range(5):
            await asyncio.sleep(0)
        assert hds._SCANNER_CACHE_KEY in HomeDashboardService._scanner_inflight
        joiner = asyncio.create_task(svc.get_scanners())   # a request parked on the build
        for _ in range(3):
            await asyncio.sleep(0)
        await HomeDashboardService.shutdown_warm_tasks(timeout=1.0)
        assert HomeDashboardService._warm_tasks == {}
        assert HomeDashboardService._scanner_inflight == {}, "the cancelled leader left its key"
        # The joiner is SETTLED (with the leader's cancellation), not parked forever.
        done, _ = await asyncio.wait({joiner}, timeout=1.0)
        assert joiner in done
    finally:
        gate.set()
        await _drain()


@pytest.mark.asyncio
async def test_a_task_left_on_a_dead_loop_does_not_block_its_section(monkeypatch):
    fakes = _Fakes(monkeypatch)

    class _Orphan:
        def done(self):
            return False

        def get_loop(self):
            return object()          # not this loop

    HomeDashboardService._warm_tasks["pulse"] = _Orphan()
    _prime_all(hds.time.time())
    HomeDashboardService._cache.clear()
    try:
        assert await _kick_and_wait(_svc()) == ["pulse"]
        assert fakes.calls["pulse"] == 1
    finally:
        await _drain()


def test_refresh_due_sections_without_a_running_loop_kicks_nothing(caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    assert _svc().refresh_due_sections() == []
    assert "without a running event loop" in caplog.text


@pytest.mark.asyncio
async def test_the_universe_stamp_the_warmer_reads_is_the_one_refresh_universe_writes(monkeypatch):
    """Pins `_PRICE_UNIVERSE_KEY` to price_service's real cache key — a rename there would
    otherwise leave the warmer believing the universe is always missing."""
    async def pages(self_):
        return [{"symbol": "AAPL", "price": 1.0}, {"symbol": "MSFT", "price": 2.0}]

    monkeypatch.setattr(prices.PriceService, "_fetch_universe_pages", pages)
    spec = _svc()._warm_spec("universe")
    assert spec.stamp() is None and not spec.busy()
    universe = await prices.PriceService().refresh_universe()
    assert set(universe) == {"AAPL", "MSFT"}
    assert spec.stamp() is not None
    assert spec.stamp() == prices._cache[hds._PRICE_UNIVERSE_KEY][0]


# ── 7. honest fallbacks when a guard times out ──────────────────────────────────


@pytest.mark.parametrize("stamp, now, servable", [
    # In the regular session: the 1500 s scanner ceiling.
    (_et(2026, 9, 30, 11, 0) - 1499, _et(2026, 9, 30, 11, 0), True),
    (_et(2026, 9, 30, 11, 0) - 1500, _et(2026, 9, 30, 11, 0), False),
    # Pre-open copy, read after the open: young enough, but Tuesday's moves.
    (_et(2026, 9, 30, 9, 25), _et(2026, 9, 30, 9, 45), False),
    # Tuesday evening copy, read Wednesday pre-market: still Tuesday's numbers → honest.
    (_et(2026, 9, 29, 23, 0), _et(2026, 9, 30, 4, 30), True),
    # Off-session ceiling (6 h): Saturday copies.
    (_et(2026, 10, 3, 9, 0), _et(2026, 10, 3, 10, 0), True),
    (_et(2026, 10, 3, 3, 0), _et(2026, 10, 3, 10, 0), False),
    # Friday copy read on Monday after the open: a different session.
    (_et(2026, 10, 2, 15, 50), _et(2026, 10, 5, 9, 45), False),
    # A stamp in the future is skew, never fresh.
    (_et(2026, 9, 30, 11, 0) + 60, _et(2026, 9, 30, 11, 0), False),
])
def test_stale_serve_problem(stamp, now, servable):
    problem = hds._stale_serve_problem(
        stamp, now=now,
        in_session_ceiling=hds._SCANNER_STALE_CEILING_IN_SESSION_SECONDS,
        off_session_ceiling=hds._SCANNER_STALE_CEILING_OFF_SESSION_SECONDS,
    )
    assert (problem is None) is servable, problem


def test_an_unreadable_stamp_is_not_servable():
    assert hds._stale_serve_problem(
        1e20, now=1e20, in_session_ceiling=10, off_session_ceiling=10,
    ).startswith("unreadable stamp")
    for bad in ("not-a-number", None, [1.0]):      # ValueError, TypeError, TypeError
        assert hds._stale_serve_problem(
            bad, in_session_ceiling=10, off_session_ceiling=10,
        ).startswith("unreadable stamp"), bad


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_stamp", ["not-a-number", None])
async def test_a_corrupt_scanner_entry_costs_the_section_never_the_dashboard(bad_stamp):
    """The fallback runs inside the guard's `except`, under a gather with no
    `return_exceptions`: a corrupt stamp must ship an empty section, not raise."""
    HomeDashboardService._scanner_cache[hds._SCANNER_CACHE_KEY] = (bad_stamp, _good_scanners())
    result = await _svc()._get_scanners_guarded()
    assert result == ScannerGroupsResponse()


def _slow(monkeypatch, attr: str, value, cls=HomeDashboardService):
    gate = asyncio.Event()

    async def build(self_):
        await gate.wait()
        return value

    monkeypatch.setattr(cls, attr, build)
    return gate


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["scanners", "themes"])
@pytest.mark.parametrize("case", ["over_ceiling", "previous_session", "honest"])
async def test_a_guard_timeout_serves_only_an_honest_copy(section, case, monkeypatch, caplog):
    """The guard wiring: a timed-out build falls back to the cached copy ONLY when it is
    under the ceiling and from the current session; otherwise the section ships empty.

    The clock semantics are pinned (always the regular session; a stamp older than a
    marker belongs to the previous session) so the test means the same at any hour; the
    calendar logic itself is `test_stale_serve_problem` above.
    """
    svc = _svc()
    now = hds.time.time()
    if section == "scanners":
        monkeypatch.setattr(hds, "_SCANNER_BUILD_TIMEOUT_SECONDS", 0.05)
        gate = _slow(monkeypatch, "_build_scanner_groups", _good_scanners())
        cache, key, cached, empty = (
            HomeDashboardService._scanner_cache, hds._SCANNER_CACHE_KEY,
            _good_scanners(), ScannerGroupsResponse(),
        )
        ttl, ceiling = hds._SCANNER_CACHE_TTL_SECONDS, hds._SCANNER_STALE_CEILING_IN_SESSION_SECONDS
        guarded = svc._get_scanners_guarded
    else:
        monkeypatch.setattr(hds, "_THEMES_BUILD_TIMEOUT_SECONDS", 0.05)
        gate = _slow(monkeypatch, "_build_themes", _themes())
        cache, key, cached, empty = (
            HomeDashboardService._themes_cache, hds._THEMES_CACHE_KEY,
            _themes(), ThemesGroupResponse(),
        )
        ttl, ceiling = hds._THEMES_CACHE_TTL_SECONDS, hds._THEMES_STALE_CEILING_IN_SESSION_SECONDS
        guarded = svc._get_themes_guarded

    monkeypatch.setattr(hds, "session_phase", lambda *_a, **_k: hds.SESSION_REGULAR)
    if case == "previous_session":
        marker = now - 300
        monkeypatch.setattr(
            hds, "_numbers_session",
            lambda ts: date(2026, 9, 29) if ts < marker else date(2026, 9, 30),
        )
    else:
        monkeypatch.setattr(hds, "_numbers_session", lambda ts: date(2026, 9, 30))
    # Every case is past the TTL, so the getter really rebuilds and the guard times out.
    stamp = now - ceiling - 5 if case == "over_ceiling" else now - ttl - 1
    assert stamp < now - ttl
    cache[key] = (stamp, cached)
    caplog.set_level(logging.INFO, logger=_LOGGER)
    try:
        result = await guarded()
        if case == "honest":
            assert result is cached, "an honest copy was not served"
        else:
            assert result is not cached and result == empty, (
                f"{section}: a {case} copy was served under the live header"
            )
            assert "not servable" in caplog.text
    finally:
        gate.set()
        for _ in range(5):
            await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("age, served", [(3600, True), (24 * 3600 + 5, False)])
async def test_the_signals_guard_serves_a_copy_only_under_a_day_old(age, served, monkeypatch):
    ssvc = sig.SignalsService.__new__(sig.SignalsService)
    monkeypatch.setattr(sig, "_SIGNALS_BUILD_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(sig.SignalsService, "_read_supabase_cache", lambda self_: None)
    monkeypatch.setattr(sig.SignalsService, "_write_supabase_cache", lambda self_, r: None)
    gate = asyncio.Event()

    async def build(self_):
        await gate.wait()
        return _signals(), frozenset()

    monkeypatch.setattr(sig.SignalsService, "_build", build)
    cached = _signals()
    sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (hds.time.time() - age, cached)
    try:
        result = await ssvc.get_signals_guarded()
        assert (result is cached) is served
        if not served:
            assert result == SignalsGroupResponse()
    finally:
        gate.set()
        for _ in range(5):
            await asyncio.sleep(0)
