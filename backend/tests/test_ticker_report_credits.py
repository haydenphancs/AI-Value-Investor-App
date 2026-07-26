"""Control-flow tests for TickerReportView charge-on-fresh-generation + refund.

Guards the invariant: a cache HIT is free; a cache MISS charges exactly once; ANY
non-delivery (generation error, ticker-not-found, schema drift) refunds exactly
once; insufficient credits never generates; a transient charge failure fails
retryably without generating. Pure control flow — CreditService, the cache
lookups, generation, and schema validation are mocked, so no Supabase / FMP /
Gemini. Mirrors tests/test_research_credits.py's mock style.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.v1.endpoints.ticker_report as tr
from app.dependencies import GUEST_USER_ID
from app.services.credit_service import CreditServiceUnavailable

# A non-guest (authenticated) user id — the billable path. Guest handling is
# covered separately in test_guest_is_not_charged.
USER = {"id": "authed-user-1"}


def _install_fakes(
    monkeypatch,
    *,
    cached=None,
    legacy=None,
    charge_return=100,
    charge_raises=False,
    generate_return=None,
    generate_raises=None,
    validate_ok=True,
):
    """Wire the endpoint's collaborators to controllable fakes.

    Returns (fake_credit_class, fake_service) so tests can assert on
    precharge / refund / generate_fresh_report.
    """

    async def _fake_legacy(ticker, persona):
        return legacy

    async def _fake_cached(ticker, persona):
        return cached

    monkeypatch.setattr(tr, "_check_legacy_report_cache", _fake_legacy)
    monkeypatch.setattr(tr, "get_cached_report", _fake_cached)

    gen = AsyncMock()
    if generate_raises is not None:
        gen.side_effect = generate_raises
    else:
        gen.return_value = generate_return if generate_return is not None else {"ok": True}
    fake_service = MagicMock()
    fake_service.generate_fresh_report = gen
    monkeypatch.setattr(tr, "TickerReportService", lambda: fake_service)

    # Bypass the real (heavy) schema: success → (report, None), drift → (None, err).
    def _fake_validate(report, ticker, persona):
        if validate_ok:
            return report, None
        return None, MagicMock(name="error_response")

    monkeypatch.setattr(tr, "_validate_report", _fake_validate)

    precharge = MagicMock()
    if charge_raises:
        precharge.side_effect = CreditServiceUnavailable("transient")
    else:
        precharge.return_value = charge_return
    refund_ledgered = MagicMock(return_value=80)

    class FakeCreditService:
        DEEP_RESEARCH_COST = 20

        def __init__(self):
            pass

    # Class-attr MagicMocks (not descriptors) → instance access returns them
    # unbound, so calls record exactly the endpoint's args (no self).
    FakeCreditService.precharge = precharge
    FakeCreditService.refund_ledgered = refund_ledgered
    monkeypatch.setattr(tr, "CreditService", FakeCreditService)

    return FakeCreditService, fake_service


@pytest.mark.asyncio
async def test_cache_hit_is_free(monkeypatch):
    credit, service = _install_fakes(monkeypatch, cached={"ok": True})
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()
    service.generate_fresh_report.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_cache_hit_is_free(monkeypatch):
    credit, service = _install_fakes(monkeypatch, legacy={"ticker_report_data": 1})
    # patch_wall_street_consensus_live / patch_legacy_price_action run on this
    # path; stub them so we don't hit FMP.
    async def _noop_ws(payload, ticker):
        return payload
    monkeypatch.setattr(tr, "patch_wall_street_consensus_live", _noop_ws)
    monkeypatch.setattr(tr, "patch_legacy_price_action", lambda p: p)
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_not_called()
    service.generate_fresh_report.assert_not_called()


@pytest.mark.asyncio
async def test_miss_success_charges_once_no_refund(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_return={"ok": True}, charge_return=100
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_awaited_once()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_miss_generation_failure_refunds_once(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_raises=RuntimeError("gemini down"),
        charge_return=100,
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


@pytest.mark.asyncio
async def test_ticker_not_found_refunds(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_raises=ValueError("no profile"),
        charge_return=100,
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


@pytest.mark.asyncio
async def test_schema_drift_refunds(monkeypatch):
    credit, service = _install_fakes(
        monkeypatch, cached=None, generate_return={"bad": True},
        validate_ok=False, charge_return=100,
    )
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


@pytest.mark.asyncio
async def test_insufficient_credits_does_not_generate(monkeypatch):
    credit, service = _install_fakes(monkeypatch, cached=None, charge_return=None)
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_not_called()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_charge_unavailable_is_retryable_no_generate(monkeypatch):
    credit, service = _install_fakes(monkeypatch, cached=None, charge_raises=True)
    await tr.get_ticker_report("AAPL", "warren_buffett", user=USER)
    credit.precharge.assert_called_once()
    service.generate_fresh_report.assert_not_called()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_guest_is_not_charged(monkeypatch):
    # The shared guest sentinel is a credit no-op: no precharge, no refund — but the
    # report is still generated (guests are governed by rate limits, not credits).
    credit, service = _install_fakes(monkeypatch, cached=None, generate_return={"ok": True})
    await tr.get_ticker_report("AAPL", "warren_buffett", user={"id": GUEST_USER_ID})
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()
    service.generate_fresh_report.assert_awaited_once()
