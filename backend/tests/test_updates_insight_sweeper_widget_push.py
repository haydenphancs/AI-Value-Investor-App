"""The sweeper's widget-push hook (spec §3 "Push", D11): `run_sweep` → `_wake_widgets`.

Drives the REAL `run_sweep` through a stub with no network clients (the jump tests' pattern) and
pins:

* only the scopes whose card was STORED this pass are woken — never a refused, failed or raised
  generation — and an empty pass still calls the hook (it flushes owed wakes);
* the hook runs LAST: after the 6b jump alerts and step-7 enrichment, before the summary;
* it is bounded by WIDGET_PUSH_PASS_BUDGET_SECONDS: a wake that never returns, AND a wake that
  swallows its CancelledError, cannot hold the summary past the budget (`asyncio.wait_for` would
  wait for the swallowed cancellation to finish — Python 3.11);
* a raising wake changes nothing in the pass's answer;
* with the switch off the real `_wake_widgets` returns before importing anything (no Supabase).
"""

from __future__ import annotations

import asyncio
import ast
import inspect
import logging
import re
import textwrap
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

import app.services.updates_insight_sweeper as mod
from app.services.earnings_window_service import get_earnings_window_service
from app.services.news_cache_service import MARKET_SCOPE, BulkCorpus
from app.services.updates_insight_sweeper import InsightSweeper
from app.services.updates_materiality import ACTION_GENERATE, TIER_EXTREME, Decision
from app.utils.market_hours import session_trading_date
from _price_fakes import PriceFromFMPFake


class _Settings:
    def __init__(self, base: Any, **overrides: Any) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_over", dict(overrides))

    def __getattr__(self, name: str) -> Any:
        over = object.__getattribute__(self, "_over")
        if name in over:
            return over[name]
        return getattr(object.__getattribute__(self, "_base"), name)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    get_earnings_window_service().reset()

    async def _window(now, *, fmp):
        return frozenset()

    async def _statuses(now, *, fmp, symbols):
        return {}

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "earnings_statuses_for", _statuses)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    monkeypatch.setattr(mod, "settings", _Settings(
        mod.settings, WIDGET_PUSH_ENABLED=True, WIDGET_PUSH_PASS_BUDGET_SECONDS=0.3))
    yield
    get_earnings_window_service().reset()


def _row(symbol, pct=0.4):
    return {"symbol": symbol, "price": 100.0, "changePercentage": pct, "marketCap": 4e11,
            "changeSession": session_trading_date(datetime.now(timezone.utc)).isoformat()}


class _QuoteFMP:
    def __init__(self, rows):
        self.rows = rows

    async def get_batch_quotes_bulk(self, symbols):
        return [r for r in self.rows if r["symbol"] in symbols]


class _Stub(InsightSweeper):
    """Generates a card for `cards`, refuses (price guard) for the rest."""

    def __init__(self, universe, cards, *, raises=()):
        self.supabase = None
        self.fmp = object()
        self.price = PriceFromFMPFake(_QuoteFMP([_row(s) for s in universe if s != MARKET_SCOPE]))
        self.vol = self
        self.news = self
        self.insights = self
        self._enrich_day = None
        self._enrich_count = 0
        self._universe_value = list(universe)
        self.cards = set(cards)
        self.raises = set(raises)
        self.events: List[str] = []
        self.woken: List[tuple] = []

    async def _universe(self):
        return list(self._universe_value)

    def _company_names(self, scopes):
        return mod._MarkedRead(complete=True)

    def _load_state(self, scopes):
        return mod._MarkedRead(complete=True)

    def _record_skips(self, skips, now):
        pass

    async def get_sigmas_bulk(self, symbols):
        return {}

    def get_cached_bulk(self, scopes, limit):
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return BulkCorpus({s: [{"external_id": f"{s}-1", "headline": f"{s} news",
                                "published_at": fresh}] for s in scopes}, complete=True)

    async def mark_verified_current(self, scopes, market_active):
        pass

    async def touch(self, scope, market_active):
        pass

    def _mark_cycle_touched(self, scopes, now):
        pass

    def _claim(self, scope, now, is_market_scope, earnings_window=False, report_day=False,
               jumping=False):
        return True

    def _consume_global_budget(self, now):
        return True

    async def generate_and_store(self, **kwargs):
        scope = kwargs["scope"]
        if scope in self.raises:
            raise RuntimeError("generation bug")
        return {"headline": "h"} if scope in self.cards else None

    def pop_failure_reason(self, scope):
        return "price_guard: figure"

    def _finish_claim(self, scope, now, decision, success, error=None):
        pass

    async def _notify_watchers(self, scope, decision, card, now, quote=None):
        self.events.append("notify")

    async def _refresh_news(self, scopes):
        pass

    async def _enrich_windows(self, corpora, scopes, now):
        self.events.append("enrich")
        return 0, 0


def _generate_all(monkeypatch):
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(
        action=ACTION_GENERATE, reason="new_articles (1)", score=1.0, price_band=TIER_EXTREME))


def _record_wake(stub, answer=None):
    async def _wake(scopes, phase):
        stub.events.append("wake")
        stub.woken.append((list(scopes), phase))
        return answer if answer is not None else {"accepted": 1, "due": 1}

    stub._wake_widgets = _wake


# ── what is woken ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_stored_scopes_are_woken(monkeypatch):
    _generate_all(monkeypatch)
    monkeypatch.setattr(mod, "session_phase", lambda now=None: "afterhours")
    stub = _Stub(["AAPL", "MSFT", "NVDA"], cards={"AAPL", "NVDA"}, raises={"MSFT"})
    _record_wake(stub)
    result = await stub.run_sweep(refresh_news=False)
    assert result["generated"] == 2
    ((scopes, phase),) = stub.woken
    assert sorted(scopes) == ["AAPL", "NVDA"]   # MSFT raised: never woken
    assert phase == "afterhours"                 # the pass's own phase, read once at its start


@pytest.mark.asyncio
async def test_a_price_guard_refusal_is_not_woken(monkeypatch):
    _generate_all(monkeypatch)
    stub = _Stub(["AAPL", "MSFT"], cards={"AAPL"})   # MSFT: card None + price_guard
    _record_wake(stub)
    await stub.run_sweep(refresh_news=False)
    assert stub.woken[0][0] == ["AAPL"]


@pytest.mark.asyncio
async def test_a_pass_with_nothing_admitted_still_flushes_owed_wakes(monkeypatch):
    from app.services.updates_materiality import ACTION_SKIP

    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action=ACTION_SKIP,
                                                             reason="fingerprint_unchanged"))
    stub = _Stub(["AAPL"], cards={"AAPL"})
    _record_wake(stub)
    await stub.run_sweep(refresh_news=False)
    assert stub.woken == [([], stub.woken[0][1])]


@pytest.mark.asyncio
async def test_the_wake_runs_last_after_enrichment(monkeypatch, caplog):
    _generate_all(monkeypatch)
    stub = _Stub(["AAPL"], cards={"AAPL"})
    _record_wake(stub, {"accepted": 3, "due": 4})
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        await stub.run_sweep(refresh_news=True)
    assert stub.events[-2:] == ["enrich", "wake"]
    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Insight sweep (")]
    assert len(summary) == 1 and "widget_push=3/4" in summary[0]


# ── the budget ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_wake_that_never_returns_cannot_hold_the_summary(monkeypatch, caplog):
    _generate_all(monkeypatch)
    stub = _Stub(["AAPL"], cards={"AAPL"})

    async def _forever(scopes, phase):
        await asyncio.Event().wait()

    stub._wake_widgets = _forever
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        result = await stub.run_sweep(refresh_news=False)
    assert time.monotonic() - started < 2.0
    assert result["generated"] == 1
    assert any("widget_push=timeout" in r.getMessage() for r in caplog.records)
    await asyncio.sleep(0.05)   # let the cancelled wake finish cancelling
    assert not InsightSweeper._widget_wakes


@pytest.mark.asyncio
async def test_a_wake_that_swallows_its_cancellation_cannot_hold_the_summary(monkeypatch, caplog):
    _generate_all(monkeypatch)
    stub = _Stub(["AAPL"], cards={"AAPL"})
    stop = asyncio.Event()
    cancels = {"n": 0}

    async def _stubborn(scopes, phase):
        while not stop.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                cancels["n"] += 1   # swallowed: `asyncio.wait_for` would wait for this forever
        return {"accepted": 0, "due": 0}

    stub._wake_widgets = _stubborn
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        sweep = asyncio.ensure_future(stub.run_sweep(refresh_news=False))
        done, _ = await asyncio.wait({sweep}, timeout=2.0)
        if not done:
            # A regression (e.g. back to `wait_for`) would hold the pass forever: release the
            # wake so the test FAILS instead of hanging the suite.
            stop.set()
            await sweep
            pytest.fail("the sweep summary waited on a wake that swallowed its cancellation")
        result = sweep.result()
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"the summary waited {elapsed:.2f}s on a wake past its budget"
    assert result["generated"] == 1
    assert any("widget_push=timeout" in r.getMessage() for r in caplog.records)
    await asyncio.sleep(0.1)
    assert cancels["n"] >= 1
    # Parked, referenced, still running — and ends on its own without an unhandled error.
    parked = list(InsightSweeper._widget_wakes)
    assert len(parked) == 1
    stop.set()
    await asyncio.wait(parked, timeout=1.0)
    await asyncio.sleep(0.05)
    assert not InsightSweeper._widget_wakes


@pytest.mark.asyncio
async def test_wait_for_would_have_broken_the_budget():
    """Why the hook does not use `asyncio.wait_for`: in Python 3.11 it waits for a timed-out task
    to FINISH cancelling. Measured here so a future "simplification" back to it is caught."""
    stop = asyncio.Event()

    async def _stubborn():
        while not stop.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass

    async def _release():
        await asyncio.sleep(0.6)
        stop.set()

    releaser = asyncio.ensure_future(_release())
    started = time.monotonic()
    try:
        # The swallowed cancellation even turns the timeout into a normal return.
        await asyncio.wait_for(_stubborn(), 0.1)
    except asyncio.TimeoutError:
        pass
    assert time.monotonic() - started >= 0.5   # held until released, far past its 0.1 s
    await releaser


@pytest.mark.asyncio
async def test_a_raising_wake_changes_nothing_in_the_answer(monkeypatch, caplog):
    _generate_all(monkeypatch)
    stub = _Stub(["AAPL", "MSFT"], cards={"AAPL", "MSFT"})

    async def _boom(scopes, phase):
        raise RuntimeError("apns down")

    stub._wake_widgets = _boom
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        result = await stub.run_sweep(refresh_news=False)
    assert result["generated"] == 2
    msgs = [r.getMessage() for r in caplog.records]
    assert any("widget push wake failed (RuntimeError: apns down)" in m for m in msgs)
    assert any("widget_push=error" in m for m in msgs)


@pytest.mark.asyncio
async def test_cancelling_the_pass_cancels_the_wake(monkeypatch):
    _generate_all(monkeypatch)
    monkeypatch.setattr(mod, "settings", _Settings(
        mod.settings, WIDGET_PUSH_ENABLED=True, WIDGET_PUSH_PASS_BUDGET_SECONDS=30))
    stub = _Stub(["AAPL"], cards={"AAPL"})
    entered = asyncio.Event()
    seen = {"cancelled": False}

    async def _slow(scopes, phase):
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise

    stub._wake_widgets = _slow
    task = asyncio.ensure_future(stub.run_sweep(refresh_news=False))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert seen["cancelled"] is True


# ── the real `_wake_widgets` ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_off_returns_before_any_import_or_supabase(monkeypatch):
    import app.services.widget_push_service as wps

    monkeypatch.setattr(mod, "settings", _Settings(mod.settings, WIDGET_PUSH_ENABLED=False,
                                                    WIDGET_PUSH_PASS_BUDGET_SECONDS=1))

    def _no(*a, **k):
        raise AssertionError("touched the push service / Supabase with the switch off")

    monkeypatch.setattr(wps, "get_widget_push_service", _no)
    monkeypatch.setattr(mod, "get_supabase", _no)
    stub = _Stub(["AAPL"], cards={"AAPL"})
    assert await InsightSweeper._wake_widgets(stub, ["AAPL"], "afterhours") == {}


@pytest.mark.asyncio
async def test_switch_on_delegates_the_stored_scopes_and_phase(monkeypatch):
    import app.services.widget_push_service as wps

    calls: List[tuple] = []

    class _Svc:
        async def wake(self, scopes, phase):
            calls.append((scopes, phase))
            return {"accepted": 1, "due": 1}

    monkeypatch.setattr(wps, "get_widget_push_service", lambda: _Svc())
    stub = _Stub(["AAPL"], cards={"AAPL"})
    assert await InsightSweeper._wake_widgets(stub, ["AAPL"], "premarket") == {
        "accepted": 1, "due": 1}
    assert calls == [(["AAPL"], "premarket")]


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        out.append(re.sub(r"#.*$", "", line))
    return "\n".join(out)


def test_the_switch_is_read_before_the_import():
    """Source pin (comment-stripped, bound to the method's own body): the `WIDGET_PUSH_ENABLED`
    early return precedes the lazy import, so a switched-off sweep never loads the push service.
    Mutation-checked by hand: moving the import above the `if` turns this red."""
    src = textwrap.dedent(inspect.getsource(InsightSweeper._wake_widgets))
    body = ast.parse(src).body[0]
    stmts = [s for s in body.body
             if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
    first = stmts[0]
    assert isinstance(first, ast.If) and "WIDGET_PUSH_ENABLED" in ast.unparse(first.test)
    assert isinstance(first.body[0], ast.Return)
    imports = [i for i, s in enumerate(stmts) if isinstance(s, (ast.Import, ast.ImportFrom))]
    assert imports and min(imports) > 0


def test_the_hook_is_bounded_with_wait_not_wait_for():
    """Source pin on `run_sweep` (comment-stripped): the wake is awaited through `asyncio.wait`
    with the budget, never `asyncio.wait_for`, and the stored list feeds it."""
    src = _strip_comments(inspect.getsource(InsightSweeper.run_sweep))
    hook = src[src.index("self._wake_widgets("):]
    hook = hook[: hook.index("logger.info(")]
    assert "asyncio.wait(" in hook and "WIDGET_PUSH_PASS_BUDGET_SECONDS" in src
    assert "wait_for" not in hook
    assert "self._wake_widgets(stored, phase)" in src


@pytest.mark.parametrize("answer,label", [
    ({}, "-"), (None, "-"), ({"skipped": "regular_session"}, "skipped:regular_session"),
    # Push on, grants off: the service's own gate, legible in the sweep summary (not "-").
    ({"skipped": "grants_off", "pending_cleared": 3}, "skipped:grants_off"),
    ({"error": "schema_missing"}, "error:schema_missing"), ({"accepted": 2, "due": 5}, "2/5"),
])
def test_the_summary_label(answer, label):
    assert InsightSweeper._widget_push_label(answer) == label
