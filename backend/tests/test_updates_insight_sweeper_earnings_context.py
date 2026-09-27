"""The sweep threads each ticker's earnings STATUS into the gate, the claim and the prompt.

TestFlight ORCL, Thu 2026-09-10: the card froze on its daily cap before the 16:10 ET
release and read "set to report" all evening. The pure pieces (`daily_cap_for(...,
report_day=)`, `decide(..., earnings_pending_today=, earnings_reported_at=)`,
`generate_and_store(..., earnings=)`) can each be green while the sweep feeds one
half and not the other — the 6-vs-16 market incident again (gate admits, RPC
refuses, nothing logged). These are call-site tests: they run the real `run_sweep`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import app.services.updates_insight_sweeper as mod
from app.services.earnings_window_service import (
    EarningsStatus,
    et_date,
    get_earnings_window_service,
)
from app.services.news_cache_service import MARKET_SCOPE
from app.services.updates_insight_sweeper import InsightSweeper
from app.services.updates_materiality import ACTION_GENERATE, Decision
from _price_fakes import PriceFromFMPFake


@pytest.fixture(autouse=True)
def _fresh_earnings_singleton():
    get_earnings_window_service().reset()
    yield
    get_earnings_window_service().reset()


class _Stub(InsightSweeper):
    def __init__(self, universe=(MARKET_SCOPE, "ORCL", "AAPL", "ETHUSD")):
        self.supabase = None
        self.fmp = object()
        self.price = PriceFromFMPFake(None)
        self.vol = self
        self.news = self
        self.insights = self
        self._catalyst_day = self._enrich_day = None
        self._catalyst_count = self._enrich_count = 0
        self._catalyst_scopes = set()
        self._universe_value = list(universe)
        self.claims = {}
        self.generated = {}
        self.finished = {}
        self.claim_ok = True

    async def _universe(self):
        return list(self._universe_value)

    def _company_names(self, scopes):
        return {}

    def _load_state(self, scopes):
        return {}

    def _record_skips(self, skips, now):
        pass

    async def get_sigmas_bulk(self, symbols):
        return {}

    def get_cached_bulk(self, scopes, limit):
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return {s: [{"external_id": f"{s}-1", "headline": f"{s} news",
                     "published_at": fresh}] for s in scopes}

    async def mark_verified_current(self, scopes, market_active):
        pass

    def _claim(self, scope, now, is_market_scope, earnings_window=False, report_day=False):
        self.claims[scope] = (earnings_window, report_day)
        return self.claim_ok

    def _consume_global_budget(self, now):
        return True

    async def _maybe_price_move(self, scope, decision, now, quote):
        return None

    async def generate_and_store(self, **kwargs):
        self.generated[kwargs["scope"]] = kwargs
        return None

    def pop_failure_reason(self, scope):
        return f"conclusion_guard: figure $5,000 ({scope})"

    def _finish_claim(self, scope, now, decision, success, error=None):
        self.finished[scope] = (success, error)


def _today():
    return et_date(datetime.now(timezone.utc))


def _patch(monkeypatch, statuses, *, boost=frozenset(), seen=None):
    async def _window(now, *, fmp):
        return boost

    async def _statuses(now, *, fmp, symbols):
        if seen is not None:
            seen["fmp"] = fmp
            seen["symbols"] = list(symbols)
        return {s: st for s, st in statuses.items() if s in symbols}

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "earnings_statuses_for", _statuses)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)


@pytest.mark.asyncio
async def test_statuses_use_the_sweepers_client_and_only_equities(monkeypatch):
    seen = {}
    _patch(monkeypatch, {}, seen=seen)
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action="skip", reason="no_corpus"))
    stub = _Stub()
    await stub.run_sweep(refresh_news=False)
    assert seen["fmp"] is stub.fmp
    assert sorted(seen["symbols"]) == ["AAPL", "ORCL"], "never the market scope or a coin"


@pytest.mark.asyncio
async def test_gate_claim_and_prompt_get_one_consistent_verdict(monkeypatch):
    today = _today()
    seen_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    statuses = {
        "ORCL": EarningsStatus("reported", today, seen_at),
        "AAPL": EarningsStatus("upcoming", today + timedelta(days=2)),
    }
    _patch(monkeypatch, statuses, boost=frozenset({"ORCL", "AAPL"}))
    gate = {}

    def _decide(**kw):
        gate[kw["scope"]] = (
            kw["earnings_window"], kw["report_day"],
            kw["earnings_pending_today"], kw["earnings_reported_at"],
        )
        return Decision(action=ACTION_GENERATE, reason="new_articles (1)")

    monkeypatch.setattr(mod, "decide", _decide)
    stub = _Stub()
    await stub.run_sweep(refresh_news=False)

    assert gate["ORCL"] == (True, True, False, seen_at)
    assert gate["AAPL"] == (True, False, False, None)
    assert gate[MARKET_SCOPE] == (False, False, False, None)
    assert gate["ETHUSD"] == (False, False, False, None)
    assert stub.claims["ORCL"] == (True, True), "the RPC must see the same report_day"
    assert stub.claims["AAPL"] == (True, False)
    assert stub.generated["ORCL"]["earnings"] == statuses["ORCL"]
    assert stub.generated["AAPL"]["earnings"] == statuses["AAPL"]
    assert stub.generated[MARKET_SCOPE]["earnings"] is None
    now = stub.generated["ORCL"]["now"]
    assert isinstance(now, datetime) and now.tzinfo is not None


@pytest.mark.asyncio
async def test_a_due_today_ticker_is_pending(monkeypatch):
    today = _today()
    _patch(monkeypatch, {"ORCL": EarningsStatus("due_today", today)})
    gate = {}

    def _decide(**kw):
        gate[kw["scope"]] = (kw["report_day"], kw["earnings_pending_today"])
        return Decision(action="skip", reason="no_corpus")

    monkeypatch.setattr(mod, "decide", _decide)
    await _Stub().run_sweep(refresh_news=False)
    assert gate["ORCL"] == (True, True)


@pytest.mark.asyncio
async def test_a_status_lookup_that_raises_is_survived(monkeypatch, caplog):
    async def _window(now, *, fmp):
        return frozenset()

    async def _boom(now, *, fmp, symbols):
        raise RuntimeError("calendar exploded")

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "earnings_statuses_for", _boom)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action="skip", reason="no_corpus"))
    with caplog.at_level("WARNING"):
        result = await _Stub().run_sweep(refresh_news=False)
    assert result["earnings_status"] == 0
    assert "no earnings context" in caplog.text


@pytest.mark.asyncio
async def test_reporting_tickers_are_admitted_ahead_of_bigger_movers(monkeypatch):
    today = _today()
    universe = [f"T{i}" for i in range(12)] + ["ORCL"]
    _patch(monkeypatch, {"ORCL": EarningsStatus("due_today", today)})

    def _decide(**kw):
        score = 1.0 if kw["scope"] == "ORCL" else 50.0
        return Decision(action=ACTION_GENERATE, reason="new_articles (1)", score=score)

    monkeypatch.setattr(mod, "decide", _decide)
    stub = _Stub(universe=universe)
    await stub.run_sweep(refresh_news=False)
    assert "ORCL" in stub.claims, "a report-day ticker must not be deferred behind movers"
    assert len(stub.claims) == mod._PER_CYCLE_REGEN_CAP


@pytest.mark.asyncio
async def test_a_rejected_card_records_the_guard_reason(monkeypatch):
    _patch(monkeypatch, {})
    monkeypatch.setattr(
        mod, "decide", lambda **kw: Decision(action=ACTION_GENERATE, reason="new_articles (1)"),
    )
    stub = _Stub(universe=("ORCL",))
    await stub.run_sweep(refresh_news=False)
    assert stub.finished["ORCL"] == (False, "conclusion_guard: figure $5,000 (ORCL)")


@pytest.mark.asyncio
async def test_the_crypto_pass_never_reads_statuses(monkeypatch):
    calls = []

    async def _statuses(now, *, fmp, symbols):
        calls.append(symbols)
        return {}

    monkeypatch.setattr(mod, "earnings_statuses_for", _statuses)
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    monkeypatch.setattr(mod, "decide", lambda **kw: Decision(action="skip", reason="no_corpus"))
    await _Stub(universe=(MARKET_SCOPE, "ORCL", "ETHUSD")).run_sweep(
        refresh_news=False, crypto_only=True,
    )
    assert calls == []


@pytest.mark.asyncio
async def test_a_catalyst_tier_mover_is_not_starved_by_reporting_tickers(monkeypatch):
    """Review 2026-09-27: on a peak earnings day, hot tickers used to fill every slot and
    an Extreme mover (whose watchers' alert waits on its card) was deferred for hours."""
    from app.services.updates_materiality import TIER_EXTREME

    today = _today()
    reporters = [f"R{i}" for i in range(12)]
    statuses = {s: EarningsStatus("due_today", today) for s in reporters}
    _patch(monkeypatch, statuses)

    def _decide(**kw):
        if kw["scope"] == "CRASH":
            return Decision(action=ACTION_GENERATE, reason="band", score=25.0,
                            price_band=TIER_EXTREME)
        if kw["scope"] == "CALM":
            return Decision(action=ACTION_GENERATE, reason="new_articles (1)", score=30.0)
        return Decision(action=ACTION_GENERATE, reason="new_articles (1)", score=1.0)

    monkeypatch.setattr(mod, "decide", _decide)
    stub = _Stub(universe=[*reporters, "CRASH", "CALM"])
    await stub.run_sweep(refresh_news=False)
    assert "CRASH" in stub.claims, "an Extreme mover must share the front of the queue"
    assert "CALM" not in stub.claims, "an ordinary mover still waits behind urgent scopes"
