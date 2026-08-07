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
from pathlib import Path
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
async def test_research_insert_raise_refunds_the_charge(monkeypatch):
    """Insert failure AFTER a successful precharge must hand the credits back.

    Was `test_research_guest_insert_raise_no_refund`, asserting the guest branch charged
    nothing so had nothing to refund. Generation is account-only now, so every caller is
    charged and this path always owes a refund — the stronger property, and the one where a
    bug actually costs a user money.
    """
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"},
        supabase=_generate_supabase_insert_raises(), _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


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
        "AAPL", _report_chat_body(), user={"id": "authed-1"}, _rate=None,
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
        "AAPL", _report_chat_body(), user={"id": "authed-1"}, _rate=None,
    )
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_report_chat_refunds_on_generation_failure(monkeypatch):
    """A charged turn that never produced an answer is refunded.

    Was `test_report_chat_guest_not_charged`. Report chat is account-only now (it is a full
    Gemini answer, and the guest no-op made it a free denial-of-wallet bypass), so the
    interesting invariant moved from "guests aren't charged" to "a failed turn is refunded".
    """
    credit = _credit_mock(monkeypatch, ticker_report)
    svc = MagicMock()
    svc.chat_about_ticker = AsyncMock(side_effect=RuntimeError("gemini down"))
    monkeypatch.setattr(ticker_report, "TickerReportService", lambda: svc)
    await ticker_report.chat_with_ticker_report(
        "AAPL", _report_chat_body(), user={"id": "user-1"}, _rate=None,
    )
    credit.precharge.assert_called_once()
    credit.refund_ledgered.assert_called_once()


# ── #1 GET /me/credits: monthly reset on read ────────────────────────

def _credits_supabase(row):
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.limit.return_value = q          # endpoint now reads via .limit(1), not .single()
    q.single.return_value = q         # harmless back-compat
    q.execute.return_value = MagicMock(data=[row])  # list result (limit(1))
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


# ── A refund that lands AFTER a monthly reset cannot mint credits ─────────────
#
# Flagged by the 2026-08-07 audit and left unresolved (its adversarial verifiers died on a
# session limit). Resolved by reading the SQL: it is a FALSE ALARM, and this pins why so it is
# not re-chased.
#
# The worry: a report fails at 23:59 on the last day of the month, `ensure_credit_period` rolls
# the period, and the refund then lands in the NEW period — crediting back 20 credits that were
# spent out of the OLD one.
#
# It cannot happen. `ensure_credit_period` (migration 100:224-230) sets `used = 0` on the roll,
# and `refund_credits` (migration 101:139) floors at zero:
#
#     SET used = GREATEST(0, u.used - p_amount)
#
# so the late refund computes `GREATEST(0, 0 - 20) = 0` — no change. The ledger then records
# `v_delta = prev.old_used - u.used = 0`, i.e. it reports the ACTUAL change rather than the
# requested amount, so `sum(delta)` cannot desync from the balance either.
#
# The floor was written for "a stray double-refund"; the cross-period case falls out of the
# same guard.

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database/migrations"


def _sql(pattern: str) -> str:
    matches = sorted(_MIGRATIONS.glob(pattern))
    assert matches, f"no migration matching {pattern}"
    return matches[-1].read_text()


def test_the_monthly_reset_zeroes_used():
    """Half of the reason a late refund is harmless."""
    sql = _sql("100_*.sql")
    reset = sql[sql.index("IF v_row.resets_at IS NULL OR now() >= v_row.resets_at"):]
    reset = reset[: reset.index("END IF;")]
    assert "used       = 0" in reset or "used = 0" in reset


def test_refund_floors_used_at_zero():
    """The other half. Without this floor, a refund crossing the boundary WOULD mint."""
    sql = _sql("101_*.sql")
    fn = sql[sql.index("CREATE OR REPLACE FUNCTION public.refund_credits"):]
    assert "GREATEST(0, u.used - p_amount)" in fn, (
        "the zero floor is what makes a post-reset refund a no-op instead of a credit grant"
    )


def test_the_refund_ledger_records_the_actual_change_not_the_request():
    """So a no-op refund writes delta 0 rather than -20, and sum(delta) stays consistent with
    the balance."""
    sql = _sql("101_*.sql")
    fn = sql[sql.index("CREATE OR REPLACE FUNCTION public.refund_credits"):]
    assert "(prev.old_used - u.used)" in fn


def test_refund_does_not_touch_total_or_resets_at():
    """A refund must not roll the period itself — that would be a second way to mint."""
    sql = _sql("101_*.sql")
    fn = sql[sql.index("CREATE OR REPLACE FUNCTION public.refund_credits"):]
    body = fn[: fn.index("$$;")]
    update = body[body.index("UPDATE public.user_credits"):]
    update = update[: update.index("RETURNING")]
    assert "total" not in update, "refund_credits must never change the allocation"
    assert "resets_at" not in update, "refund_credits must never roll the period"
