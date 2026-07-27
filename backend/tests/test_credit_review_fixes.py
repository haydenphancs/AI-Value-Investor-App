"""Outlier tests for the credit-enforcement bugs found in the adversarial review.

Backend, cleanly testable slice:
  * #6 research /generate: an insert that RAISES (not just empty .data) still refunds
    the precharge and returns a structured error (guest → no charge, no refund).
  * #7 ticker /report/chat: a generation failure refunds the charge (success does not).
  * #1 GET /me/credits: rolls the monthly allocation (ensure_period) for an authed user
    and SKIPS the guest sentinel.

All collaborators mocked — no Supabase / FMP / Gemini. Mirrors the existing
test_research_concurrency_cap / test_ticker_report_credits mock style.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.v1.endpoints.research as research
import app.api.v1.endpoints.ticker_report as ticker_report
import app.api.v1.endpoints.users as users
from app.api.error_response import ErrorCode
from app.dependencies import GUEST_USER_ID
from app.schemas.research import GenerateResearchRequest


def _credit_mock(monkeypatch, module, *, precharge_return=100):
    inst = MagicMock()
    inst.precharge.return_value = precharge_return
    inst.refund_ledgered.return_value = 80
    cls = MagicMock(return_value=inst)
    cls.DEEP_RESEARCH_COST = 20
    monkeypatch.setattr(module, "CreditService", cls)
    return inst


def _generate_supabase_insert_raises():
    """Supabase mock: the two in-flight COUNT queries succeed, the research_reports
    INSERT (3rd execute) raises — the exact 'insert raised, not empty' failure."""
    q = MagicMock()
    for m in ("select", "eq", "in_", "gte", "insert"):
        getattr(q, m).return_value = q
    q.execute.side_effect = [
        MagicMock(count=0, data=[]),   # per-user concurrency cap
        MagicMock(count=0, data=[]),   # global admission cap
        RuntimeError("insert boom"),   # research_reports insert RAISES
    ]
    sb = MagicMock()
    sb.table.return_value = q
    return sb


def _stub_fmp(monkeypatch):
    fmp = MagicMock()
    fmp.get_company_profile = AsyncMock(return_value={})
    monkeypatch.setattr("app.integrations.fmp.get_fmp_client", lambda: fmp)


def _req():
    return GenerateResearchRequest(stock_id="AAPL", investor_persona="warren_buffett")


# ── #6 research /generate: insert RAISE still refunds ────────────────

@pytest.mark.asyncio
async def test_research_insert_raise_refunds_precharge(monkeypatch):
    credit = _credit_mock(monkeypatch, research, precharge_return=99)
    _stub_fmp(monkeypatch)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "authed-1"},
        supabase=_generate_supabase_insert_raises(), _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()   # charge handed back despite the raise


@pytest.mark.asyncio
async def test_research_guest_insert_raise_no_refund(monkeypatch):
    # Guests are never charged → nothing to refund even when the insert raises.
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    resp = await research.generate_research_report(
        request=_req(), user={"id": GUEST_USER_ID},
        supabase=_generate_supabase_insert_raises(), _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()


# ── #7 ticker /report/chat: refund on generation failure ─────────────

def _report_chat_body():
    return ticker_report.TickerReportChatRequest(
        ticker="AAPL", message="is it cheap?", persona="warren_buffett"
    )


@pytest.mark.asyncio
async def test_report_chat_refunds_on_failure(monkeypatch):
    credit = _credit_mock(monkeypatch, ticker_report, precharge_return=100)
    svc = MagicMock()
    svc.chat_about_ticker = AsyncMock(side_effect=RuntimeError("gemini down"))
    monkeypatch.setattr(ticker_report, "TickerReportService", lambda: svc)
    await ticker_report.chat_with_ticker_report(
        "AAPL", _report_chat_body(), user={"id": "authed-1"}, x_guest_id=None, _rate=None,
    )
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


@pytest.mark.asyncio
async def test_report_chat_success_no_refund(monkeypatch):
    credit = _credit_mock(monkeypatch, ticker_report, precharge_return=100)
    svc = MagicMock()
    svc.chat_about_ticker = AsyncMock(return_value="Cheap on normalized FCF.")
    monkeypatch.setattr(ticker_report, "TickerReportService", lambda: svc)
    await ticker_report.chat_with_ticker_report(
        "AAPL", _report_chat_body(), user={"id": "authed-1"}, x_guest_id=None, _rate=None,
    )
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_report_chat_guest_not_charged(monkeypatch):
    credit = _credit_mock(monkeypatch, ticker_report)
    svc = MagicMock()
    svc.chat_about_ticker = AsyncMock(side_effect=RuntimeError("gemini down"))
    monkeypatch.setattr(ticker_report, "TickerReportService", lambda: svc)
    await ticker_report.chat_with_ticker_report(
        "AAPL", _report_chat_body(), user={"id": GUEST_USER_ID}, x_guest_id="i1", _rate=None,
    )
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()


# ── #1 GET /me/credits: monthly reset on read ────────────────────────

def _credits_supabase(row):
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.single.return_value = q
    q.execute.return_value = MagicMock(data=row)
    sb = MagicMock()
    sb.table.return_value = q
    return sb


@pytest.mark.asyncio
async def test_get_credits_rolls_month_for_authed(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr(users, "CreditService", MagicMock(return_value=inst))
    sb = _credits_supabase({"total": 50, "used": 10, "remaining": 40, "resets_at": None})
    await users.get_user_credits(user={"id": "authed-1"}, supabase=sb)
    inst.ensure_period.assert_called_once_with("authed-1")   # fresh balance on read


@pytest.mark.asyncio
async def test_get_credits_skips_reset_for_guest(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr(users, "CreditService", MagicMock(return_value=inst))
    sb = _credits_supabase({"total": 100000, "used": 0, "remaining": 100000, "resets_at": None})
    await users.get_user_credits(user={"id": GUEST_USER_ID}, supabase=sb)
    inst.ensure_period.assert_not_called()   # guest is never reset
