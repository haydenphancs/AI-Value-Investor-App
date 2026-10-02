"""The Home first-paint warm at boot, the deploy gate it holds, and the all-hours warmer.

WHY THIS EXISTS — production, 2026-09/10 (plan: instant first Home paint).

Every Railway deploy emptied the in-process caches, and the first `GET /home/dashboard`
after one waited 8.1-8.5 s (17 of 17): the scanners' close-map sweep outlived their 8 s
guard. Now `_run_home_boot_warm` builds the close map and every shared Home section at t=0,
and until it finishes — or `HOME_BOOT_WARM_MAX_WAIT_SECONDS` pass — `/health/pdf` (Railway's
healthcheckPath) answers 503 "warming", so Railway keeps serving the old deployment.

The properties pinned here are the ones a "simplification" would break:

* the route enforces the deadline ITSELF, so a warm that hangs or dies holds a deploy for
  the deadline at most; the gate is checked before the memo and the render, with no data;
* the gate opens ALWAYS (success, failure, timeout), and a timeout does NOT cancel the
  shared builds requests may have joined (`shield`);
* cancelling the boot warm (shutdown inside the warm window) DOES cancel its work, so no
  build outlives the teardown that closes the HTTP clients;
* the old read-through, market-hours-only scanner warmer is gone: the new loop waits for
  the gate, then calls the sync `refresh_due_sections()` every tick, around the clock,
  survives a failing tick, and idles (never returns) when switched off.

Hermetic: every section builder and the close-map select are fakes; WeasyPrint is replaced
in `sys.modules` (no pango locally), which also makes the render count observable.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import re
import sys
import textwrap
import time
import types

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import app.main as main_mod
import app.services.home_dashboard_service as hds
import app.services.market_movers_service as mm
import app.services.price_service as prices
import app.services.signals_service as sig
import app.services.trillion_club_service as tcs
from app.config import settings
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
from app.services.market_movers_service import MarketMoversService

_MAIN = "app.main"


# ── fixtures ────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def _isolated_state(monkeypatch):
    """Fresh caches for every section the boot warm touches; no gate; no stragglers."""
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
    monkeypatch.setattr(tcs.TrillionClubService, "_inflight", {})
    monkeypatch.setattr(tcs.TrillionClubService, "_invalidated_at", 0.0)
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", True)
    monkeypatch.setattr(mm, "_cache", {})
    monkeypatch.setattr(mm, "_inflight", {})
    monkeypatch.setattr(mm, "_closes_refresh_task", None)
    # The dashboard singleton is built WITHOUT __init__ (no FMP client) — every builder the
    # boot warm reaches is a fake below, or the real `get_scanners` over a fake inner build.
    monkeypatch.setattr(hds, "_service", HomeDashboardService.__new__(HomeDashboardService))
    monkeypatch.setattr(mm, "_service", MarketMoversService())
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", None)
    yield
    # Nothing a test started may outlive its loop (a straggler would reach the network once
    # monkeypatch restores the real builders).
    stragglers = [t for t in HomeDashboardService._warm_tasks.values() if not t.done()]
    stragglers += [t for t in list(mm._background_tasks) if not t.done()]
    for task in stragglers:
        task.cancel()
    for task in stragglers:
        try:
            await task
        except BaseException:  # noqa: BLE001 — cancelled or failed; both fine here
            pass


class _FakeWeasy:
    """A `weasyprint` stand-in that counts renders (same shape as test_health_pdf_memoised)."""

    def __init__(self):
        self.renders = 0
        self.__version__ = "66.0"

    def HTML(self, string=""):
        outer = self

        class _Doc:
            def write_pdf(self, buf):
                outer.renders += 1
                buf.write(b"%PDF-1.7 fake")
        return _Doc()


@pytest.fixture
def weasy(monkeypatch):
    fake = _FakeWeasy()
    monkeypatch.setitem(sys.modules, "weasyprint", fake)
    monkeypatch.setitem(sys.modules, "pydyf", types.SimpleNamespace(__version__="0.11.0"))
    monkeypatch.setattr(main_mod, "_PDF_HEALTH_OK", None)
    return fake


def _client() -> TestClient:
    return TestClient(main_mod.app)   # no context manager: the lifespan must not run


def _gate(*, seconds: float = 60.0, ready: bool = False) -> "main_mod._WarmGate":
    gate = main_mod._WarmGate(ready=asyncio.Event(), deadline=time.monotonic() + seconds)
    if ready:
        gate.ready.set()
    return gate


# ── section fakes ───────────────────────────────────────────────────────────────


def _tile(symbol: str, kind: str = "etf") -> MarketPulseItemResponse:
    return MarketPulseItemResponse(
        symbol=symbol, name=symbol, type=kind, price=100.0, change_percent=1.0, spark=[],
    )


def _good_scanners() -> ScannerGroupsResponse:
    return ScannerGroupsResponse(
        movers=ScannerGroupResponse(kind="movers"), volume=ScannerGroupResponse(kind="volume"),
    )


class _Sections:
    """Stand-ins for each section's FORCED rebuild, writing its cache like the real one.

    Scanners stay REAL (`get_scanners`, with its `_inflight` lead/join and cancellation
    arm); only `_build_scanner_groups` is faked, and it reads the close map through
    `_all_closes()` the way the real build does — so a test can hold it on an Event, or
    count close-map sweeps. ``raise_all`` makes every builder (and the sweep) raise.
    """

    def __init__(self, monkeypatch, *, raise_all: bool = False):
        self.raise_all = raise_all
        self.scanner_release: asyncio.Event | None = None
        self.scanner_started = asyncio.Event()
        self.scanner_cancelled = False
        self.sweeps = 0
        fakes = self

        def _maybe_raise(name):
            if fakes.raise_all:
                raise RuntimeError(f"{name} upstream down")

        async def pulse(self_, *, force=False):
            _maybe_raise("pulse")
            tiles = [_tile(c["symbol"]) for c in hds._PULSE_SYMBOLS] + [_tile("BTCUSD", "crypto")]
            HomeDashboardService._cache[hds._CACHE_KEY] = (time.time(), tiles)
            return tiles

        async def universe(self_):
            _maybe_raise("universe")
            prices._cache[hds._PRICE_UNIVERSE_KEY] = (time.time(), {"AAPL": {"symbol": "AAPL"}})
            return prices._cache[hds._PRICE_UNIVERSE_KEY][1]

        async def build_scanners(self_):
            fakes.scanner_started.set()
            try:
                await mm.get_market_movers_service()._all_closes()
                if fakes.scanner_release is not None:
                    await fakes.scanner_release.wait()
            except asyncio.CancelledError:
                fakes.scanner_cancelled = True
                raise
            _maybe_raise("scanners")
            return _good_scanners()

        async def themes(self_, *, force=False):
            _maybe_raise("themes")
            result = ThemesGroupResponse(themes=[
                TrendingThemeResponse(slug="s", title="S", accent_hex="22D3EE", ticker_count=3),
            ])
            HomeDashboardService._themes_cache[hds._THEMES_CACHE_KEY] = (time.time(), result)
            return result

        async def signals(self_, *, force=False):
            _maybe_raise("signals")
            result = SignalsGroupResponse(congress=SignalGroupResponse(
                kind="congress", entries=[SignalRowResponse(rank=1, symbol="NVDA", value=3.0)],
            ))
            sig.SignalsService._cache[sig._SIGNALS_CACHE_KEY] = (time.time(), result)
            return result

        async def trillion(self_, *, force=False):
            _maybe_raise("trillion")
            tcs.TrillionClubService._group_cache[tcs._GROUP_KEY] = (
                time.time(), TrillionClubGroupResponse(),
            )
            return TrillionClubGroupResponse()

        def select_all_closes():
            fakes.sweeps += 1
            if fakes.raise_all:
                raise RuntimeError("market_close_snapshot read failed")
            return {"AAPL": {"symbol": "AAPL", "close": 1.0, "previous_close": 1.0,
                             "trade_date": "2026-09-30"}}

        monkeypatch.setattr(HomeDashboardService, "_get_pulse_cached", pulse)
        monkeypatch.setattr(prices.PriceService, "refresh_universe", universe)
        monkeypatch.setattr(HomeDashboardService, "_build_scanner_groups", build_scanners)
        monkeypatch.setattr(HomeDashboardService, "get_themes", themes)
        monkeypatch.setattr(sig.SignalsService, "get_signals", signals)
        monkeypatch.setattr(tcs.TrillionClubService, "get_group", trillion)
        monkeypatch.setattr(MarketMoversService, "_select_all_closes", staticmethod(select_all_closes))


def _messages(caplog, level=logging.DEBUG) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _MAIN and r.levelno >= level]


# ═══ 1. the gate in /health/pdf ══════════════════════════════════════════════════


def test_a_closed_gate_answers_warming_with_no_data_and_renders_nothing(weasy, monkeypatch):
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", _gate(seconds=60))
    c = _client()
    for _ in range(3):
        r = c.get("/health/pdf")
        assert r.status_code == 503
        assert r.json() == {"status": "warming"}, "the warming body must carry no data"
    assert weasy.renders == 0, "the gate let a render through while the warm ran"
    assert main_mod._PDF_HEALTH_OK is None


def test_an_open_gate_renders_once_as_before(weasy, monkeypatch):
    gate = _gate(seconds=60)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    c = _client()
    assert c.get("/health/pdf").status_code == 503
    gate.ready.set()
    bodies = [c.get("/health/pdf") for _ in range(3)]
    assert all(r.status_code == 200 for r in bodies)
    assert bodies[0].json()["status"] == "healthy"
    assert weasy.renders == 1, "the memo stopped working behind the gate"


def test_the_deadline_opens_the_gate_even_when_the_warm_never_finishes(weasy, monkeypatch):
    """A warm task that hangs or dies never sets `ready`; the ROUTE's own deadline check is
    what bounds the deploy hold."""
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", _gate(seconds=-0.001))
    r = _client().get("/health/pdf")
    assert r.status_code == 200 and r.json()["status"] == "healthy"
    assert weasy.renders == 1


def test_the_deadline_is_judged_on_every_request(weasy, monkeypatch):
    gate = _gate(seconds=60)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    c = _client()
    assert c.get("/health/pdf").status_code == 503
    gate.deadline = time.monotonic() - 0.001      # the deadline passes; `ready` never set
    assert c.get("/health/pdf").status_code == 200
    assert not gate.ready.is_set()


def test_no_gate_answers_exactly_as_before(weasy):
    assert main_mod._HOME_WARM_GATE is None
    r = _client().get("/health/pdf")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_the_gate_is_checked_before_the_memo(weasy, monkeypatch):
    """Even a memoised success must not answer 200 while the warm holds the gate (and the
    503 must not leak the memo's renderer versions)."""
    monkeypatch.setattr(main_mod, "_PDF_HEALTH_OK", {"status": "healthy", "weasyprint": "66.0"})
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", _gate(seconds=60))
    r = _client().get("/health/pdf")
    assert r.status_code == 503 and r.json() == {"status": "warming"}


def _code(obj) -> str:
    """Source with comments and docstrings removed (so prose can't satisfy a scan)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ) and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_gate_is_the_first_thing_health_pdf_does():
    code = _code(main_mod.health_pdf)
    gate = code.index("_HOME_WARM_GATE")
    memo = code.index("if _PDF_HEALTH_OK is not None")
    render = code.index("write_pdf(")
    assert gate < memo < render
    cond = code[gate:memo]
    assert "ready.is_set()" in cond and "deadline" in cond and "time.monotonic()" in cond, (
        "the route must enforce the deadline itself, or a hung warm holds the deploy"
    )


# ═══ 2. the boot warm ════════════════════════════════════════════════════════════


def test_the_boot_warm_is_a_registered_one_shot():
    assert "run_home_boot_warm" in main_mod._ONE_SHOT_TASKS


@pytest.mark.asyncio
async def test_a_fast_warm_opens_the_gate_and_logs_ready(monkeypatch, caplog):
    fakes = _Sections(monkeypatch)
    gate = _gate(seconds=5)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    with caplog.at_level(logging.INFO, logger=_MAIN):
        started = time.monotonic()
        await asyncio.wait_for(main_mod._run_home_boot_warm(), 2.0)
    assert gate.ready.is_set()
    assert time.monotonic() - started < 2.0
    ready = [m for m in _messages(caplog, logging.INFO) if m.startswith("Home boot warm: ready in")]
    assert ready, _messages(caplog)
    assert "close_map=ok" in ready[0] and "NOT warm" not in ready[0], ready[0]
    for name in hds.WARM_SECTIONS:
        assert f"{name}=ok" in ready[0], ready[0]
    # The scanners joined the boot warm's close-map build: ONE sweep, not two.
    assert fakes.sweeps == 1
    assert HomeDashboardService._scanner_cache and not HomeDashboardService._scanner_inflight
    assert not mm._inflight


@pytest.mark.asyncio
async def test_every_piece_raising_still_opens_the_gate_and_never_escapes(monkeypatch, caplog):
    _Sections(monkeypatch, raise_all=True)
    gate = _gate(seconds=5)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(main_mod._run_home_boot_warm(), 2.0)   # must not raise
    assert gate.ready.is_set(), "a failed warm must still open the deploy gate"
    warnings = _messages(caplog, logging.WARNING)
    assert any(m.startswith("Home boot warm: close map not rebuilt (RuntimeError") for m in warnings), warnings
    ready = [m for m in warnings if m.startswith("Home boot warm: ready in")]
    assert ready and "close_map=NOT warm" in ready[0] and "scanners=NOT warm" in ready[0], ready
    assert not mm._inflight and not HomeDashboardService._scanner_inflight


@pytest.mark.asyncio
async def test_a_warm_that_raises_outright_still_opens_the_gate(monkeypatch, caplog):
    async def broken():
        raise ImportError("cannot import name 'get_home_dashboard_service'")

    monkeypatch.setattr(main_mod, "_home_boot_warm_work", broken)
    gate = _gate(seconds=5)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    with caplog.at_level(logging.ERROR, logger=_MAIN):
        await asyncio.wait_for(main_mod._run_home_boot_warm(), 2.0)
    assert gate.ready.is_set()
    failed = [r for r in caplog.records if r.name == _MAIN and r.levelno >= logging.ERROR]
    assert failed and "ImportError" in failed[0].getMessage() and failed[0].exc_info, (
        "an unexpected warm failure must log its stack"
    )


@pytest.mark.asyncio
async def test_the_deadline_opens_the_gate_and_the_shared_build_is_not_cancelled(monkeypatch, caplog):
    fakes = _Sections(monkeypatch)
    fakes.scanner_release = asyncio.Event()
    gate = _gate(seconds=0.05)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    with caplog.at_level(logging.INFO, logger=_MAIN):
        task = asyncio.create_task(main_mod._run_home_boot_warm())
        await asyncio.wait_for(fakes.scanner_started.wait(), 0.5)
        await asyncio.wait_for(gate.ready.wait(), 0.5)
        # The gate is open, but the held scanner build — which a request could have
        # joined — is still running: the timeout did not cancel it.
        await asyncio.sleep(0.05)
        assert not task.done(), "the boot warm stopped owning its remainder"
        assert hds._SCANNER_CACHE_KEY in HomeDashboardService._scanner_inflight
        assert not fakes.scanner_cancelled
        joined = asyncio.ensure_future(hds.get_home_dashboard_service().get_scanners())
        await asyncio.sleep(0)

        fakes.scanner_release.set()
        await asyncio.wait_for(task, 1.0)
        result = await asyncio.wait_for(joined, 1.0)
    assert result.movers is not None, "the request that joined the held build got nothing"
    assert hds._SCANNER_CACHE_KEY in HomeDashboardService._scanner_cache
    assert HomeDashboardService._scanner_inflight == {}
    assert not fakes.scanner_cancelled
    messages = _messages(caplog, logging.INFO)
    assert any("not finished after" in m and "the warm continues" in m for m in messages), messages
    assert any("after the deploy gate opened" in m for m in messages), messages


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before the deadline", "after the gate opened"])
async def test_cancelling_the_boot_warm_cancels_its_work(monkeypatch, phase):
    """Shutdown inside the warm window: the shielded work must die with the task, or it
    outlives `close_fmp_client()` and fails every request joined to its builds."""
    fakes = _Sections(monkeypatch)
    fakes.scanner_release = asyncio.Event()
    gate = _gate(seconds=10 if phase == "before the deadline" else 0.02)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    seen: dict = {}
    real_work = main_mod._home_boot_warm_work

    async def recording_work():
        seen["work"] = asyncio.current_task()
        return await real_work()

    monkeypatch.setattr(main_mod, "_home_boot_warm_work", recording_work)

    task = asyncio.create_task(main_mod._run_home_boot_warm())
    await asyncio.wait_for(fakes.scanner_started.wait(), 1.0)
    if phase == "after the gate opened":
        await asyncio.wait_for(gate.ready.wait(), 0.5)
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    work = seen["work"]
    await asyncio.wait([work], timeout=1.0)
    assert work.cancelled(), "the warm's work outlived its cancelled owner"
    assert fakes.scanner_cancelled, "the scanner build the warm led kept running"
    assert HomeDashboardService._scanner_inflight == {}
    assert mm._inflight == {}
    assert gate.ready.is_set()


@pytest.mark.asyncio
async def test_without_a_gate_the_warm_still_runs_to_the_end(monkeypatch, caplog):
    """HOME_BOOT_WARM_MAX_WAIT_SECONDS = 0: nothing gates the deploy, the warm still runs."""
    _Sections(monkeypatch)
    assert main_mod._HOME_WARM_GATE is None
    with caplog.at_level(logging.INFO, logger=_MAIN):
        await asyncio.wait_for(main_mod._run_home_boot_warm(), 2.0)
    assert any(m.startswith("Home boot warm: ready in") for m in _messages(caplog, logging.INFO))
    assert HomeDashboardService._cache and HomeDashboardService._scanner_cache


def test_the_summary_line_never_raises():
    assert main_mod._format_warm_summary({"close_map": True, "pulse": False}) == (
        "close_map=ok, pulse=NOT warm"
    )
    assert main_mod._format_warm_summary({}) == "{}"
    assert main_mod._format_warm_summary(None) == "None"


# ═══ 3. the all-hours warmer ═════════════════════════════════════════════════════


class _FakeHome:
    """`get_home_dashboard_service()` stand-in for the warmer loop."""

    def __init__(self, *, fail_first: bool = False):
        self.ticks = 0
        self.fail_first = fail_first
        self.ticked = asyncio.Event()
        self._real = HomeDashboardService.__new__(HomeDashboardService)

    def refresh_due_sections(self, now=None):
        self.ticks += 1
        self.ticked.set()
        if self.fail_first and self.ticks == 1:
            raise RuntimeError("stamp table corrupt")
        return []

    def warm_ordering_violations(self, tick_seconds=None):
        return self._real.warm_ordering_violations(tick_seconds)


def _install_home(monkeypatch, fake: _FakeHome) -> _FakeHome:
    monkeypatch.setattr(hds, "get_home_dashboard_service", lambda: fake)
    return fake


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_the_warmer_waits_for_the_gate_then_ticks(monkeypatch):
    monkeypatch.setattr(settings, "SCANNER_PREWARM_ENABLED", True)
    home = _install_home(monkeypatch, _FakeHome())
    gate = _gate(seconds=10)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", gate)
    task = asyncio.create_task(main_mod._run_home_dashboard_warmer())
    await asyncio.sleep(0.05)
    assert home.ticks == 0, "the warmer raced the boot warm for the same cold builds"
    gate.ready.set()
    await asyncio.wait_for(home.ticked.wait(), 1.0)
    assert home.ticks == 1
    await _stop(task)


@pytest.mark.asyncio
async def test_the_warmer_starts_at_the_gate_deadline_even_if_the_warm_hangs(monkeypatch):
    monkeypatch.setattr(settings, "SCANNER_PREWARM_ENABLED", True)
    home = _install_home(monkeypatch, _FakeHome())
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", _gate(seconds=0.05))
    task = asyncio.create_task(main_mod._run_home_dashboard_warmer())
    await asyncio.wait_for(home.ticked.wait(), 1.0)
    await _stop(task)


@pytest.mark.asyncio
async def test_a_failing_tick_is_logged_and_the_loop_survives(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SCANNER_PREWARM_ENABLED", True)
    home = _install_home(monkeypatch, _FakeHome(fail_first=True))
    with caplog.at_level(logging.ERROR, logger=_MAIN):
        task = asyncio.create_task(main_mod._run_home_dashboard_warmer())
        await asyncio.wait_for(home.ticked.wait(), 1.0)
        await asyncio.sleep(0.05)
    assert not task.done(), "one failing tick killed the warmer"
    errors = [r for r in caplog.records if r.name == _MAIN and r.levelno >= logging.ERROR]
    assert errors and "Home warmer tick failed (RuntimeError" in errors[0].getMessage()
    assert errors[0].exc_info, "the tick failure must carry its stack"
    await _stop(task)


@pytest.mark.asyncio
async def test_a_zero_tick_cannot_spin_the_loop(monkeypatch):
    monkeypatch.setattr(settings, "SCANNER_PREWARM_ENABLED", True)
    monkeypatch.setattr(settings, "HOME_WARM_TICK_SECONDS", 0)
    home = _install_home(monkeypatch, _FakeHome())
    task = asyncio.create_task(main_mod._run_home_dashboard_warmer())
    await asyncio.wait_for(home.ticked.wait(), 1.0)
    await asyncio.sleep(0.3)
    assert home.ticks == 1, f"HOME_WARM_TICK_SECONDS=0 ticked {home.ticks}x in 0.3 s"
    await _stop(task)


@pytest.mark.asyncio
async def test_a_broken_ordering_is_logged_at_start(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SCANNER_PREWARM_ENABLED", True)
    monkeypatch.setattr(settings, "HOME_WARM_TICK_SECONDS", 30)
    home = _install_home(monkeypatch, _FakeHome())
    with caplog.at_level(logging.WARNING, logger=_MAIN):
        task = asyncio.create_task(main_mod._run_home_dashboard_warmer())
        await asyncio.wait_for(home.ticked.wait(), 1.0)
    await _stop(task)
    warned = [m for m in _messages(caplog, logging.WARNING) if "can expire before their rebuild" in m]
    assert warned and "pulse" in warned[0] and "universe" in warned[0], warned


@pytest.mark.asyncio
async def test_the_default_ordering_logs_nothing(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SCANNER_PREWARM_ENABLED", True)
    home = _install_home(monkeypatch, _FakeHome())
    with caplog.at_level(logging.INFO, logger=_MAIN):
        task = asyncio.create_task(main_mod._run_home_dashboard_warmer())
        await asyncio.wait_for(home.ticked.wait(), 1.0)
    await _stop(task)
    assert not [m for m in _messages(caplog, logging.WARNING)], _messages(caplog)
    assert any(m.startswith("Home warmer: started (tick 10s") for m in _messages(caplog, logging.INFO))


@pytest.mark.asyncio
async def test_switched_off_the_warmer_idles_instead_of_returning(monkeypatch, caplog):
    """A `_spawn`ed loop that returns is reported as a dead loop at WARNING."""
    monkeypatch.setattr(settings, "SCANNER_PREWARM_ENABLED", False)
    home = _install_home(monkeypatch, _FakeHome())
    with caplog.at_level(logging.INFO, logger=_MAIN):
        task = asyncio.create_task(main_mod._run_home_dashboard_warmer())
        await asyncio.sleep(0.05)
    assert not task.done(), "the switched-off warmer returned (read as a dead loop)"
    assert home.ticks == 0
    assert any("SCANNER_PREWARM_ENABLED is off" in m for m in _messages(caplog, logging.INFO))
    await _stop(task)


def test_the_warmer_has_no_market_hours_gate_and_never_awaits_a_build():
    code = _code(main_mod._run_home_dashboard_warmer)
    assert "is_open" not in code and "_market_status" not in code, (
        "the warmer is gated to market hours again — a lone open after hours pays cold builds"
    )
    assert "refresh_due_sections()" in code
    assert "await get_home_dashboard_service" not in code and "await svc" not in code
    assert code.index("_wait_for_home_warm_gate()") < code.index("refresh_due_sections()")
    for banned in ("get_scanners(", "get_themes(", "get_signals(", "warm_all("):
        assert banned not in code, f"the warmer awaits a build directly again: {banned}"


def test_the_old_read_through_scanner_warmer_is_gone():
    src = inspect.getsource(main_mod)
    code = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
    assert "_run_scanner_pre_warmer" not in code
    assert not hasattr(main_mod, "_run_scanner_pre_warmer")


# ═══ 4. lifespan wiring ══════════════════════════════════════════════════════════


def test_the_lifespan_spawns_the_warm_first_and_tears_it_down_before_the_clients():
    code = _code(main_mod.lifespan)
    local = code.index("if is_local_dev:")
    yield_at = code.index("yield")
    warm = code.index("_spawn(_run_home_boot_warm(), 'run_home_boot_warm')")
    first_loop = code.index("_spawn(_run_close_snapshot_loop()")
    warmer = code.index("_spawn(_run_home_dashboard_warmer(), 'run_home_dashboard_warmer')")
    assert local < warm < first_loop < yield_at, "the boot warm is not the first Railway spawn"
    assert warm < warmer < yield_at
    gate_set = code.index("_HOME_WARM_GATE = _WarmGate(")
    assert local < gate_set < warm, "the gate must exist before the warm (and /health/pdf) runs"
    assert "HOME_BOOT_WARM_MAX_WAIT_SECONDS" in code[local:warm]

    cancel = code.index("pending = [t for t in background_tasks if not t.done()]")
    shutdown = code.index("shutdown_warm_tasks(")
    reset = code.index("_HOME_WARM_GATE = None")
    close_fmp = code.index("await close_fmp_client()")
    assert yield_at < cancel < shutdown < close_fmp, (
        "warm builds must stop after the warmer loop and BEFORE the HTTP clients close"
    )
    assert yield_at < reset


@pytest.mark.asyncio
async def test_teardown_resets_the_gate(monkeypatch):
    """Drive the REAL lifespan (local-dev branch: no loops) and check the gate is gone."""
    import app.services.universe_data as ud

    async def _no_db():
        return False

    monkeypatch.setattr(main_mod, "check_supabase_health", _no_db)
    monkeypatch.setattr(ud, "verify_universe_files_present", lambda: {})
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "RUN_NOTIFICATION_JOBS_LOCALLY", False)
    monkeypatch.setattr(main_mod, "_HOME_WARM_GATE", _gate(seconds=60))
    async with main_mod.lifespan(main_mod.app):
        pass
    assert main_mod._HOME_WARM_GATE is None
