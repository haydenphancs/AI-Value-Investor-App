"""Benchmark PRODUCER, review round 3 P3-2 (2026-10-07): a 429 back-off that covers FMP's
per-MINUTE quota window.

The burst retries (2 s + 4 s + 8 s, ~14-17.5 s with jitter) were shorter than the window FMP
refuses for once a minute's quota is spent. Every call still inside the window ran out of
retries together, the breaker opened after 20 of them, and the rest of the window failed at
network speed — companies silently dropped from their medians (or a sector left skewed).
Now a 429 that outlives its burst retries opens ONE shared window: every caller waits for
one shared instant (60 s, or FMP's Retry-After up to that) and retries. The breaker stays for
a sustained lockout (3 windows in a row with no success AND 20 calls past their retries), and
a run's window waits are bounded (`RATE_LIMIT_RUN_WAIT_BUDGET_SECONDS`) and logged.

Time is VIRTUAL: `_VirtualTime` replaces `asyncio.sleep` and the module's window clock, and
advances to the next sleeper's wake-up only once every task is blocked — so concurrent
sleepers overlap like real ones, and a 50 s window is 50 s for everybody.

Hermetic: an FMP fake keyed on the virtual clock, the round-2 in-memory `sector_benchmarks`.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
from typing import Any, Dict, List, Optional

import pytest

import app.services.industry_benchmark_service as ibs
import app.services.sector_benchmark_service as sbs
from app.integrations.fmp import FMPRateLimitException, FMPUnavailableException
from tests.test_benchmark_producer_round2_2026_10_07 import _svc, _universe

_REAL_SLEEP = asyncio.sleep


class _VirtualTime:
    """A virtual clock. `sleep` parks the caller until the clock reaches its wake-up; `run`
    drives a coroutine, advancing the clock to the earliest wake-up whenever nothing else can
    run (the heap of sleepers stopped changing for a few loop turns)."""

    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: List[tuple] = []
        self._seq = 0
        self.slept: List[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float = 0, *_a: Any, **_k: Any) -> None:
        self.slept.append(delay)
        delay = max(0.0, float(delay))
        if delay == 0:
            await _REAL_SLEEP(0)
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self.now + delay, self._seq, fut))
        self._seq += 1
        await fut

    async def run(self, coro: Any, *, max_virtual_seconds: float = 3600.0) -> Any:
        task = asyncio.ensure_future(coro)
        quiet = 0
        last = None
        while not task.done():
            await _REAL_SLEEP(0)
            state = (len(self._sleepers), self._seq)
            quiet = quiet + 1 if state == last else 0
            last = state
            if quiet < 5 or not self._sleepers:
                continue
            wake_at = self._sleepers[0][0]
            assert wake_at <= max_virtual_seconds, "virtual time ran away — an unbounded wait"
            self.now = max(self.now, wake_at)
            while self._sleepers and self._sleepers[0][0] <= self.now:
                _, _, fut = heapq.heappop(self._sleepers)
                if not fut.done():
                    fut.set_result(None)
            quiet = 0
        return task.result()


@pytest.fixture
def vt(monkeypatch) -> _VirtualTime:
    """Virtual time, and the process-wide 429 state reset (restored after the test)."""
    clock = _VirtualTime()
    monkeypatch.setattr(sbs.asyncio, "sleep", clock.sleep)      # asyncio.sleep, everywhere
    monkeypatch.setattr(sbs, "_rate_limit_clock", clock.clock)
    monkeypatch.setattr(sbs, "_exhausted_in_a_row", 0)
    monkeypatch.setattr(sbs, "_window", sbs._SharedWindow())
    monkeypatch.setattr(sbs.random, "uniform", lambda a, b: 0.0)   # no jitter
    return clock


class _QuotaFMP:
    """FMP whose quota is spent while `refusing(now)` holds: every call 429s, with
    `retry_after` as FMP's header. Otherwise it answers like the round-2 fake (one complete
    2024 annual ratio row; one TTM row). Records (virtual time, ticker) per call."""

    def __init__(self, vt: _VirtualTime, refusing, retry_after: Optional[str] = None,
                 down: frozenset = frozenset()) -> None:
        self.vt = vt
        self.refusing = refusing
        self.retry_after = retry_after
        self.down = set(down)
        self.calls: List[tuple] = []

    def __getattr__(self, name: str):
        if not name.startswith("get_"):
            raise AttributeError(name)

        async def call(ticker, *a, **k):
            self.calls.append((self.vt.now, ticker))
            if ticker in self.down:
                raise FMPUnavailableException(f"{name}({ticker}): 503")
            if self.refusing(self.vt.now):
                raise FMPRateLimitException(f"429 {name}", retry_after=self.retry_after)
            n = int(ticker[1:]) if ticker[1:].isdigit() else 0
            if name == "get_financial_ratios" and k.get("period") == "annual":
                return [{"date": "2024-12-31", "grossProfitMargin": 0.40 + n / 100}]
            if name == "get_ratios_ttm":
                return [{"grossProfitMarginTTM": 0.40 + n / 100}]
            return []

        return call


_GROUPS = {
    "Technology": {"Semiconductors": [f"S{i}" for i in range(6)],
                   "Software - Application": [f"A{i}" for i in range(6)]},
    "Energy": {"Oil & Gas E&P": [f"E{i}" for i in range(5)]},
}


# ═══ The acceptance test: a 50 s quota window loses no company ════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_a_50_second_429_window_loses_no_company(monkeypatch, caplog, vt, mode):
    """FMP refuses everything for the first 50 s of the run. Pre-fix every call exhausted
    its ~14 s of burst retries inside the window and was dropped (Technology then settled
    lossy, or skewed). Now one shared window carries the whole fetch past it."""
    fmp = _QuotaFMP(vt, refusing=lambda t: t < 50.0)
    svc, db = _svc(monkeypatch, fmp, _universe(_GROUPS))
    sweep = svc.recompute_all if mode == "fiscal" else svc.recompute_all_ttm

    with caplog.at_level(logging.INFO, logger=sbs.logger.name):
        summary = await vt.run(sweep(skip_if_fresh_hours=24))

    assert summary["fetch_failures"] == 0 and summary["fetch_failures_by_kind"]["rate_limited"] == 0
    assert summary["sectors_lossy"] == 0 and summary["tickers_fetched"] == 17
    assert summary["rate_limit_windows"] == 1                     # ONE window, not one per caller
    assert summary["rate_limit_wait_seconds"] == sbs.RATE_LIMIT_WINDOW_SECONDS
    assert summary["rate_limit_lockouts"] == 0
    label, marker = ("2024", "annual") if mode == "fiscal" else ("TTM", "ttm")
    for sector, industry, n in (("Technology", "Semiconductors", 6),
                                ("Technology", "Software - Application", 6),
                                ("Technology", "", 12), ("Energy", "", 5)):
        assert db.group(sector, industry, marker)[("gross_margin", label)]["sample_size"] == n
    # Nobody spent a request inside the open window: calls stop at the burst's end (t=14)
    # and resume at its close (t=74).
    assert not [t for t, _ in fmp.calls if 14.0 < t < 74.0]
    assert 50.0 <= vt.now <= 14.0 + sbs.RATE_LIMIT_WINDOW_SECONDS + 1.0
    opened = [r for r in caplog.records if "one shared window of 60 s" in r.getMessage()]
    assert len(opened) == 1 and opened[0].levelno == logging.WARNING
    assert f"[{mode} run]" in opened[0].getMessage()
    assert any("answers again after 1 shared window" in r.getMessage() for r in caplog.records)
    assert sbs.current_rate_limit_run() is None                   # the run's scope is closed


@pytest.mark.asyncio
async def test_callers_share_one_window_and_new_ones_wait_before_calling(vt):
    """30 concurrent calls in a 50 s window: one window, every caller waits to the same
    instant, and a caller that arrives while it is open spends no request until it closes."""
    fmp = _QuotaFMP(vt, refusing=lambda t: t < 50.0)

    async def late_caller():
        await vt.sleep(20.0)                           # arrives inside the open window
        return await sbs.call_with_rate_limit_retry(fmp.get_quote, "LATE")

    async def scenario():
        with sbs.rate_limit_run("unit") as run:
            results = await asyncio.gather(
                *[sbs.call_with_rate_limit_retry(fmp.get_quote, f"T{i}") for i in range(30)],
                late_caller(),
            )
            return results, run

    results, run = await vt.run(scenario())
    assert results == [[]] * 31
    assert run.windows == 1 and run.lockouts == 0
    late = [t for t, ticker in fmp.calls if ticker == "LATE"]
    assert late == [74.0]                                # one call, at the window's close
    # Each early caller: 4 burst attempts (t = 0, 2, 6, 14) then one at 74.
    assert sorted({t for t, ticker in fmp.calls if ticker == "T0"}) == [0.0, 2.0, 6.0, 14.0, 74.0]


# ═══ The window's length ═════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("header, expected", [
    (None, 60.0), ("", 60.0), ("soon", 60.0), ("nan", 60.0),   # absent / unreadable → 60 s
    ("45", 45.0), ("60", 60.0), ("999", 60.0),                  # honoured up to 60 s
    ("0", 2.0), ("-5", 2.0),                                    # never below one base delay
])
def test_the_window_honours_retry_after_up_to_sixty_seconds(header, expected):
    assert sbs._window_length(FMPRateLimitException("429", retry_after=header)) == expected


@pytest.mark.asyncio
async def test_a_retry_after_shorter_than_the_minute_sets_the_window(vt):
    fmp = _QuotaFMP(vt, refusing=lambda t: t < 30.0, retry_after="20")
    # Burst retries honour Retry-After too (20 s each): 0, 20, 40 → the third retry succeeds.
    assert await vt.run(sbs.call_with_rate_limit_retry(fmp.get_quote, "X")) == []
    assert [t for t, _ in fmp.calls] == [0.0, 20.0, 40.0]

    fmp = _QuotaFMP(vt, refusing=lambda t: t < 200.0, retry_after="20")
    start = vt.now
    with pytest.raises(FMPRateLimitException):
        await vt.run(sbs.call_with_rate_limit_retry(fmp.get_quote, "Y"))
    # 3 burst retries of 20 s, then 3 windows of 20 s (not 60): 6 waits of 20 s.
    assert [round(t - start) for t, _ in fmp.calls] == [0, 20, 40, 60, 80, 100, 120]


# ═══ The breaker: a sustained lockout fails fast; one stuck ticker cannot open it ═══════════


@pytest.mark.asyncio
async def test_a_sustained_lockout_opens_the_breaker_after_three_windows(vt, caplog):
    fmp = _QuotaFMP(vt, refusing=lambda t: True)

    async def scenario():
        with sbs.rate_limit_run("unit") as run:
            outcomes = await asyncio.gather(
                *[sbs.call_with_rate_limit_retry(fmp.get_quote, f"T{i}") for i in range(30)],
                return_exceptions=True,
            )
            return outcomes, run

    caplog.set_level(logging.INFO, logger=sbs.logger.name)
    outcomes, run = await vt.run(scenario())
    assert all(isinstance(o, FMPRateLimitException) for o in outcomes)
    assert run.windows == sbs.RATE_LIMIT_LOCKOUT_WINDOWS == 3 and run.lockouts == 1
    assert sbs._window.lockout is True     # flagged as the last callers gave up
    # Bounded: the burst (14 s) and three 60 s windows, never hours.
    assert vt.now <= 14.0 + 3 * sbs.RATE_LIMIT_WINDOW_SECONDS + 1.0
    assert len(fmp.calls) <= 30 * (sbs.RATE_LIMIT_MAX_RETRIES + 1 + sbs.RATE_LIMIT_LOCKOUT_WINDOWS)

    # A call that comes next, the breaker open: ONE attempt, no wait, counted.
    before, slept_before = len(fmp.calls), len(vt.slept)
    with pytest.raises(FMPRateLimitException):
        await vt.run(sbs.call_with_rate_limit_retry(fmp.get_quote, "NEXT"))
    assert len(fmp.calls) == before + 1 and len(vt.slept) == slept_before

    # A 5xx neither opens nor closes it; a success closes it, and 429s are retried again.
    down = _QuotaFMP(vt, refusing=lambda t: False, down=frozenset({"D"}))
    with pytest.raises(FMPUnavailableException):
        await vt.run(sbs.call_with_rate_limit_retry(down.get_quote, "D"))
    assert sbs._window.lockout is True
    ok = _QuotaFMP(vt, refusing=lambda t: False)
    assert await vt.run(sbs.call_with_rate_limit_retry(ok.get_quote, "OK")) == []
    assert sbs._window.lockout is False and sbs._exhausted_in_a_row == 0
    lockouts = [r for r in caplog.records if "a quota lockout" in r.getMessage()]
    assert len(lockouts) == 1 and lockouts[0].levelno == logging.WARNING
    assert any("answers again" in r.getMessage() for r in caplog.records)
    once = _QuotaFMP(vt, refusing=lambda t, t0=vt.now: t < t0 + 1.0)
    assert await vt.run(sbs.call_with_rate_limit_retry(once.get_quote, "AGAIN")) == []
    assert len(once.calls) == 2                       # one burst retry, as before the lockout


@pytest.mark.asyncio
async def test_one_stuck_ticker_cannot_open_the_breaker_for_everyone(vt):
    """A single call refused for ever waits out its own three windows and gives up — but one
    ticker is not an account-wide lockout: the next ticker's 429 is still retried."""
    stuck = _QuotaFMP(vt, refusing=lambda t: True)
    with pytest.raises(FMPRateLimitException):
        await vt.run(sbs.call_with_rate_limit_retry(stuck.get_quote, "STUCK"))
    assert len(stuck.calls) == 1 + sbs.RATE_LIMIT_MAX_RETRIES + sbs.RATE_LIMIT_LOCKOUT_WINDOWS
    assert sbs._window.lockout is False
    t0 = vt.now
    brief = _QuotaFMP(vt, refusing=lambda t: t < t0 + 3.0)
    assert await vt.run(sbs.call_with_rate_limit_retry(brief.get_quote, "OTHER")) == []
    assert len(brief.calls) == 3                      # 2 s + 4 s of burst retries, then data


# ═══ The per-run wait budget ═════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_run_never_waits_past_its_budget(monkeypatch, vt, caplog):
    """A quota that keeps relapsing (each window ends in a brief recovery, then 429s again)
    never trips the in-a-row breaker; the run's budget bounds it instead, and says so."""
    monkeypatch.setattr(sbs, "RATE_LIMIT_RUN_WAIT_BUDGET_SECONDS", 150.0)
    # Refuses except in the first second of every 100 s.
    fmp = _QuotaFMP(vt, refusing=lambda t: (t % 100.0) >= 1.0 or t < 1.0)

    async def scenario():
        with sbs.rate_limit_run("unit") as run:
            outcomes = []
            for i in range(6):
                try:
                    outcomes.append(await sbs.call_with_rate_limit_retry(fmp.get_quote, f"T{i}"))
                except FMPRateLimitException as exc:
                    outcomes.append(exc)
            return outcomes, run

    with caplog.at_level(logging.INFO, logger=sbs.logger.name):
        outcomes, run = await vt.run(scenario(), max_virtual_seconds=10_000)
    assert run.windows == 2 and run.wait_seconds == 120.0 <= 150.0   # a third would be 180
    assert run.lockouts >= 1
    assert any(isinstance(o, FMPRateLimitException) for o in outcomes)
    assert any("window-wait budget" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    end = [r for r in caplog.records if "[unit run]: FMP's per-minute quota ran out" in r.getMessage()]
    assert len(end) == 1 and "2 shared 429 window(s), 120 s of waiting (budget 150 s)" in end[0].getMessage()


@pytest.mark.asyncio
async def test_overlapping_runs_keep_their_own_budgets(vt):
    """The quarterly and weekly sweeps can overlap; each `rate_limit_run` is a ContextVar
    scope, so neither reads (or exhausts) the other's budget, and both close cleanly."""
    seen: Dict[str, Any] = {}

    async def one(label: str):
        with sbs.rate_limit_run(label) as run:
            await vt.sleep(1.0)
            seen[label] = sbs.current_rate_limit_run()
            return run

    a, b = await vt.run(asyncio.gather(one("fiscal"), one("ttm")))
    assert seen["fiscal"] is a and seen["ttm"] is b and a is not b
    assert sbs.current_rate_limit_run() is None


# ═══ Compatibility: a zeroed counter is a clean slate ════════════════════════════════════


@pytest.mark.asyncio
async def test_a_zeroed_counter_voids_a_stale_window_and_lockout(monkeypatch, vt):
    """A window or lockout counts only while some call is exhausted since the last success:
    the old suite's fixtures reset `_exhausted_in_a_row` alone, and a success does the same
    in production. A leftover far-future window and an open breaker change nothing."""
    stale = sbs._SharedWindow()
    stale.seq, stale.retry_at, stale.in_a_row, stale.lockout = 7, 1e9, 5, True
    monkeypatch.setattr(sbs, "_window", stale)
    fmp = _QuotaFMP(vt, refusing=lambda t: t < 5.0)
    assert await vt.run(sbs.call_with_rate_limit_retry(fmp.get_quote, "X")) == []
    assert vt.slept == [2.0, 4.0]                     # burst retries only: no window, no raise
    assert stale.lockout is False and stale.in_a_row == 0


class _MidSectorOutage(_QuotaFMP):
    """The quota runs out when the first Software ticker is asked — part-way through
    Technology, after Semiconductors — and stays out for 45 s."""

    def __init__(self, vt: _VirtualTime) -> None:
        self.outage_from: Optional[float] = None
        super().__init__(vt, refusing=self._refusing)

    def _refusing(self, t: float) -> bool:
        return self.outage_from is not None and t < self.outage_from + 45.0

    def __getattr__(self, name: str):
        inner = super().__getattr__(name)

        async def call(ticker, *a, **k):
            if ticker.startswith("A") and self.outage_from is None:
                self.outage_from = self.vt.now
            return await inner(ticker, *a, **k)

        return call


@pytest.mark.asyncio
async def test_a_storm_inside_a_sector_settles_once_the_window_passes(monkeypatch, vt):
    """P3-1 and P3-2 together: the quota runs out part-way through Technology. Pre-fix the
    Software calls were dropped and the sector left lossy (or, before P3-1, skewed and
    marked fresh); with the shared window it settles complete."""
    fmp = _MidSectorOutage(vt)
    svc, db = _svc(monkeypatch, fmp, _universe(_GROUPS))
    summary = await vt.run(svc.recompute_all(skip_if_fresh_hours=24))
    # It began mid-sector: every Semiconductors call came (and was answered) before it.
    first_software = next(i for i, (_, ticker) in enumerate(fmp.calls) if ticker.startswith("A"))
    assert all(not ticker.startswith("S") for _, ticker in fmp.calls[first_software:])
    assert fmp.outage_from is not None
    assert summary["fetch_failures"] == 0 and summary["sectors_lossy"] == 0
    assert summary["rate_limit_windows"] == 1
    assert db.group("Technology", "", "annual")[("gross_margin", "2024")]["sample_size"] == 12
    assert db.group("Technology", "Software - Application", "annual")[
        ("gross_margin", "2024")]["sample_size"] == 6
