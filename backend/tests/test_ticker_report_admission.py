"""Admission-gate + slot-accounting tests for GET /stocks/{ticker}/report.

The direct report path had NO per-caller limit and no concurrency gate: a cache
MISS costs ~17 Gemini + ~20 FMP calls, and the cache is keyed (ticker, persona),
so an unauthenticated loop over tickers could burn the whole upstream budget.

Guards, in order of what breaks worst if wrong:
  * a cache HIT is served even at cap  — shedding those turns a capacity blip
    into a total outage on already-generated reports;
  * rejection at cap never precharges  — the same invariant
    test_research_concurrency_cap pins for /research/generate;
  * the in-flight slot is released on EVERY exit — generation failure, client
    disconnect (CancelledError), and both credit early-returns. A leaked slot is
    permanent: after N of them the endpoint is bricked until redeploy.

Mirrors tests/test_ticker_report_credits.py's mock style — no Supabase, FMP or
Gemini.
"""

import asyncio

import pytest

import app.api.v1.endpoints.ticker_report as tr
from app.api.error_response import ErrorCode
from app.dependencies import GUEST_USER_ID
from app.services.credit_service import CreditServiceUnavailable

USER = {"id": "authed-user-1"}
GUEST = {"id": GUEST_USER_ID}


def _install(monkeypatch, *, cached=None, generate_raises=None,
             charge_return=100, charge_raises=False):
    """Wire the endpoint's collaborators. Returns (FakeCreditService, calls).

    The guest-budget stub that used to live here is gone with the guest path itself:
    `GET /stocks/{ticker}/report` is account-only now, because its per-install
    allowance keyed on the client-supplied `X-Guest-Id` and rotating that header
    bought unlimited generations.
    """

    async def _fake_legacy(ticker, persona):
        return None

    async def _fake_cached(ticker, persona):
        return cached

    monkeypatch.setattr(tr, "_check_legacy_report_cache", _fake_legacy)
    monkeypatch.setattr(tr, "get_cached_report", _fake_cached)

    calls = {"generate": 0, "precharge": 0, "refund": 0}

    class _FakeService:
        async def generate_fresh_report(self, ticker, persona):
            calls["generate"] += 1
            if generate_raises is not None:
                raise generate_raises
            return {"ok": True}

    monkeypatch.setattr(tr, "TickerReportService", lambda: _FakeService())
    monkeypatch.setattr(tr, "_validate_report", lambda r, t, p: (r, None))

    class FakeCreditService:
        DEEP_RESEARCH_COST = 20

        def precharge(self, *a, **kw):
            calls["precharge"] += 1
            if charge_raises:
                raise CreditServiceUnavailable("transient")
            return charge_return

        def refund_ledgered(self, *a, **kw):
            calls["refund"] += 1
            return 80

    monkeypatch.setattr(tr, "CreditService", FakeCreditService)

    return FakeCreditService, calls


@pytest.fixture(autouse=True)
def _reset_counter():
    """The counter is module-global; a test that left it dirty would silently
    poison every later test in the file."""
    tr._INFLIGHT_REPORTS = 0
    yield
    tr._INFLIGHT_REPORTS = 0


def _err_code(resp):
    """Extract error_code from a JSONResponse body."""
    import json
    return json.loads(resp.body).get("error_code")


# ── the gate itself ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_at_cap_returns_409_system_busy(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)
    tr._INFLIGHT_REPORTS = 2

    resp = await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert resp.status_code == 409
    assert _err_code(resp) == ErrorCode.SYSTEM_BUSY.value


@pytest.mark.asyncio
async def test_at_cap_never_precharges_or_generates(monkeypatch):
    """Rejection must cost the user nothing and do no AI work."""
    _fake, calls = _install(monkeypatch)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 1)
    tr._INFLIGHT_REPORTS = 1

    await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert calls["precharge"] == 0
    assert calls["generate"] == 0
    assert calls["refund"] == 0


@pytest.mark.asyncio
async def test_rejection_does_not_consume_a_slot(monkeypatch):
    """A 409 must leave the counter untouched — otherwise being at cap once
    would ratchet the endpoint permanently closed."""
    _install(monkeypatch)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 1)
    tr._INFLIGHT_REPORTS = 1

    await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert tr._INFLIGHT_REPORTS == 1


@pytest.mark.asyncio
async def test_cache_hit_is_served_even_at_cap(monkeypatch):
    """A cache hit costs nothing upstream, so the gate must not shed it."""
    _fake, calls = _install(monkeypatch, cached={"cached": True})
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 1)
    tr._INFLIGHT_REPORTS = 99  # far past the cap

    result = await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert result == {"cached": True}
    assert calls["precharge"] == 0
    assert calls["generate"] == 0


@pytest.mark.asyncio
async def test_zero_cap_disables_the_gate(monkeypatch):
    _fake, calls = _install(monkeypatch)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 0)
    tr._INFLIGHT_REPORTS = 5000

    await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert calls["generate"] == 1


# ── slot release on every exit path ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_slot_released_on_success(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 4)

    await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert tr._INFLIGHT_REPORTS == 0


@pytest.mark.asyncio
async def test_slot_released_on_generation_failure(monkeypatch):
    """N consecutive failures must not exhaust the pool."""
    _install(monkeypatch, generate_raises=RuntimeError("gemini down"))
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)

    for _ in range(5):
        await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert tr._INFLIGHT_REPORTS == 0


@pytest.mark.asyncio
async def test_slot_released_on_client_disconnect(monkeypatch):
    """CancelledError is a BaseException — `except Exception` misses it, only
    `finally` runs. This is the disconnect path that would leak in production."""
    _install(monkeypatch, generate_raises=asyncio.CancelledError())
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)

    with pytest.raises(asyncio.CancelledError):
        await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert tr._INFLIGHT_REPORTS == 0


@pytest.mark.asyncio
async def test_slot_released_on_insufficient_credits(monkeypatch):
    """This early-return sits between the increment and the generation call —
    the exact shape that leaks if it isn't inside the try."""
    _fake, calls = _install(monkeypatch, charge_return=None)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)

    resp = await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert _err_code(resp) == ErrorCode.INSUFFICIENT_CREDITS.value
    assert tr._INFLIGHT_REPORTS == 0
    assert calls["generate"] == 0


@pytest.mark.asyncio
async def test_slot_released_on_transient_charge_failure(monkeypatch):
    _fake, calls = _install(monkeypatch, charge_raises=True)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)

    resp = await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert resp.status_code == 409
    assert tr._INFLIGHT_REPORTS == 0
    assert calls["generate"] == 0


# ── refund must key off an actual debit, not merely "authenticated" ──────────

@pytest.mark.asyncio
async def test_failed_precharge_is_never_refunded(monkeypatch):
    """A refund without a matching debit CREDITS a user who never paid. The
    finally now runs on this path (it didn't before the gate was added), so the
    condition has to be 'was charged', not 'is not a guest'."""
    _fake, calls = _install(monkeypatch, charge_raises=True)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)

    await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert calls["refund"] == 0


@pytest.mark.asyncio
async def test_insufficient_credits_is_never_refunded(monkeypatch):
    _fake, calls = _install(monkeypatch, charge_return=None)
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)

    await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert calls["refund"] == 0


@pytest.mark.asyncio
async def test_generation_failure_still_refunds(monkeypatch):
    """The pre-existing refund-on-non-delivery behaviour must survive."""
    _fake, calls = _install(monkeypatch, generate_raises=RuntimeError("boom"))
    monkeypatch.setattr(tr.settings, "REPORT_GET_MAX_INFLIGHT", 2)

    await tr.get_ticker_report("AAPL", "warren_buffett", USER)

    assert calls["precharge"] == 1
    assert calls["refund"] == 1
