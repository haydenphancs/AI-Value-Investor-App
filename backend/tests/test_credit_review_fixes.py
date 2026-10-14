"""Outlier tests for the credit-enforcement bugs found in the adversarial review.

Backend, cleanly testable slice:
  * #6 research /generate: an insert that RAISES (not just empty .data) still refunds
    the precharge and returns a structured error (guest → no charge, no refund).
  * (#7 ticker /report/chat was deleted 2026-09-11 — no client called it and it bypassed
    every chat guardrail; its tests went with it.)
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


# ── 2026-10-12: the deep door's charge is named by the report it pays for ─────────────

def _generate_supabase(*, insert_raises=False, claim_raises=False, insert_rows=None,
                       threads=None):
    """A recording Supabase mock for /research/generate. Counts and inserts are separate
    chains so the insert payload and the claim can be inspected."""
    import threading

    record = {"insert": None, "claim": None, "stamps": [],
              "threads": threads if threads is not None else []}

    def _exec(result=None, raises=None):
        def _run():
            record["threads"].append(threading.get_ident())
            if raises is not None:
                raise raises
            return result
        return _run

    count_q = MagicMock()
    for m in ("select", "eq", "in_", "gte"):
        getattr(count_q, m).return_value = count_q
    count_q.execute.side_effect = _exec(MagicMock(count=0, data=[]))

    class _Insert:
        def __init__(self, payload):
            record["insert"] = payload
            self.payload = payload

        def execute(self):
            rows = insert_rows if insert_rows is not None else [dict(self.payload)]
            return _exec(MagicMock(data=rows), RuntimeError("insert boom") if insert_raises else None)()

    class _Claim:
        def __init__(self, payload):
            record["claim"] = {"payload": payload, "filters": []}

        def eq(self, *a):
            record["claim"]["filters"].append(("eq",) + a)
            return self

        def in_(self, *a):
            record["claim"]["filters"].append(("in",) + a)
            return self

        def execute(self):
            return _exec(MagicMock(data=[]), RuntimeError("claim boom") if claim_raises else None)()

    class _Stamp:
        """Migration 195's refund_outcome stamp — recorded APART from the orphan claim, so
        `record["claim"]` stays the claim the assertions above are about."""

        def __init__(self, payload):
            self.entry = {"payload": payload, "filters": [], "thread": None}
            record["stamps"].append(self.entry)

        def _f(self, *a):
            self.entry["filters"].append(a)
            return self

        eq = is_ = or_ = in_ = _f

        def execute(self):
            self.entry["thread"] = threading.get_ident()
            return _exec(MagicMock(data=[{"id": "x"}]))()

    class _Table:
        def select(self, *a, **k):
            return count_q.select(*a, **k)

        def insert(self, payload):
            return _Insert(payload)

        def update(self, payload):
            if "refund_outcome" in payload:
                return _Stamp(payload)
            return _Claim(payload)

    sb = MagicMock()
    sb.table.side_effect = lambda name: _Table()
    return sb, record


@pytest.fixture
def _no_worker(monkeypatch):
    """The deep door launches `_run_research_task`; replace it so no pipeline runs."""
    started = []

    async def _fake_task(report_id, ticker, persona, user_id):
        started.append(report_id)

    monkeypatch.setattr(research, "_run_research_task", _fake_task)
    return started


@pytest.mark.asyncio
async def test_the_deep_door_charges_with_the_minted_report_id(monkeypatch, _no_worker):
    from app.services.credit_service import ChargeRef

    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase()
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    ref = credit.precharge.call_args.kwargs["ref_id"]
    assert isinstance(ref, ChargeRef)
    assert ref == f"AAPL:{record['insert']['id']}", "the charge is not named by the row it pays for"
    assert resp.report_id == record["insert"]["id"]
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_an_insert_failure_refunds_on_the_charge_ref(monkeypatch):
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase(insert_raises=True)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    charge = credit.precharge.call_args.kwargs["ref_id"]
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    assert kw["ref_id"] == charge and kw["reason"] == "report_refund"
    assert "quiet_no_match" not in kw, "charged in this request: a no-match must page"
    # The id-scoped claim armed before the refund, on THIS row.
    assert ("eq", "id", record["insert"]["id"]) in record["claim"]["filters"]


@pytest.mark.asyncio
async def test_a_transient_deep_precharge_is_compensated_with_the_same_ref(monkeypatch):
    from app.services.credit_service import CreditServiceUnavailable

    credit = _credit_mock(monkeypatch, research)
    credit.precharge.side_effect = CreditServiceUnavailable("edge 520")
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase()
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    body = json.loads(resp.body)
    assert resp.status_code == 409 and body["error_code"] == ErrorCode.SYSTEM_BUSY.value
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    charge = credit.precharge.call_args.kwargs["ref_id"]
    assert kw["reason"] == "report_precharge_unconfirmed" and kw["quiet_no_match"] is True
    assert kw["ref_id"] == charge
    import re
    assert re.match(r"^AAPL:[0-9a-f-]{36}$", charge), charge
    assert body["details"]["charge_ref"] == charge
    assert record["insert"] is None, "a report row was inserted for an unconfirmed charge"


@pytest.mark.asyncio
async def test_an_insufficient_balance_is_never_compensated(monkeypatch):
    credit = _credit_mock(monkeypatch, research, precharge_return=None)
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase()
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    assert resp.status_code == 402
    credit.refund_ledgered.assert_not_called()
    assert record["insert"] is None


@pytest.mark.asyncio
async def test_a_raised_orphan_claim_still_refunds_now(monkeypatch):
    """The claim raising proves nothing: no row (the likeliest), a row it DID claim before its
    reply was lost, or a committed row it did not. Deferring to the sweep leaks in the first two;
    refunding now is safe because a later sweep refund of the same charge converges."""
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, _record = _generate_supabase(insert_raises=True, claim_raises=True)
    resp = await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    credit.refund_ledgered.assert_called_once()
    assert credit.refund_ledgered.call_args.kwargs["ref_id"] == credit.precharge.call_args.kwargs["ref_id"]


@pytest.mark.asyncio
async def test_a_cancel_after_the_charge_hands_it_back_detached(monkeypatch):
    import asyncio
    from app.services.credit_service import drain_detached_settlements

    credit = _credit_mock(monkeypatch, research)
    fmp = MagicMock()
    fmp.get_company_profile = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr("app.integrations.fmp.get_fmp_client", lambda: fmp)
    sb, record = _generate_supabase()
    with pytest.raises(asyncio.CancelledError):
        await research.generate_research_report(
            request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
        )
    assert await drain_detached_settlements(2.0) == 0
    credit.refund_ledgered.assert_called_once()
    kw = credit.refund_ledgered.call_args.kwargs
    assert kw["reason"] == "report_refund"
    assert kw["ref_id"] == credit.precharge.call_args.kwargs["ref_id"]
    assert record["insert"] is None


@pytest.mark.asyncio
async def test_an_unexpected_error_after_the_charge_refunds_it(monkeypatch):
    """Anything escaping between the charge and the hand-off to the worker must hand the
    charge back (inline, the frame is not being cancelled)."""
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, _record = _generate_supabase(insert_rows=[{"no_id_here": True}])
    with pytest.raises(KeyError):
        await research.generate_research_report(
            request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
        )
    credit.refund_ledgered.assert_called_once()
    assert credit.refund_ledgered.call_args.kwargs["reason"] == "report_refund"


@pytest.mark.asyncio
async def test_a_handed_off_report_is_never_refunded_by_the_door(monkeypatch, _no_worker):
    credit = _credit_mock(monkeypatch, research)
    _stub_fmp(monkeypatch)
    sb, _record = _generate_supabase()
    await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    credit.refund_ledgered.assert_not_called()


@pytest.mark.asyncio
async def test_deep_door_money_calls_leave_the_loop_thread(monkeypatch, _no_worker):
    import threading

    loop_thread = threading.get_ident()
    credit = _credit_mock(monkeypatch, research)
    seen = []
    credit.precharge.side_effect = lambda *a, **k: seen.append(threading.get_ident()) or 80
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase(threads=seen)
    await research.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    # two counts + the precharge + the insert
    assert len(seen) == 4 and loop_thread not in seen, "a deep-door round trip ran on the loop"
