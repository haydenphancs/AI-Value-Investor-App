"""
Refund safety-net idempotency tests for research_reconciliation_service.

The credit-refund RPC is NOT idempotent (it only clamps `used` to >= 0), so
the at-most-once guarantee lives entirely in the atomic row claim on
`research_reports.is_refunded`. These tests pin that guarantee with a
self-contained fake Supabase (mimics the PostgREST filter chain over an
in-memory row store) and a refund recorder — no network, per testing.py
rules.

Covered:
  - a stuck processing row is claimed once and refunded once
  - a second claim is a no-op (is_refunded guard) — never double-refunds
  - a 'completed' row is never claimed/refunded
  - a 'failed' + is_refunded=False row IS claimed (worker pre-set status='failed'
    before re-raising, and the killed-between-mark-and-refund leak)
  - the sweep only touches OLD claimable+unrefunded rows
  - worker-except path and a sweep on the SAME row refund exactly once
  - a refund RPC failure leaves the row claimed (under-refund, never double)
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.services.research_reconciliation_service as recon


# ── Fakes ───────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Mimics the supabase-py / PostgREST builder: filters fold into one
    WHERE; update applies the SET to matching rows and returns them
    (representation), select returns matching rows."""

    def __init__(self, store):
        self._store = store
        self._op = None
        self._set = None
        self._filters = []
        self._limit = None

    def select(self, *_cols):
        self._op = "select"
        return self

    def update(self, values):
        self._op = "update"
        self._set = values
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, vals))
        return self

    def gt(self, col, val):
        self._filters.append(("gt", col, val))
        return self

    def lt(self, col, val):
        self._filters.append(("lt", col, val))
        return self

    def order(self, _col, desc=False):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self, row):
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and rv != val:
                return False
            if kind == "in" and rv not in val:
                return False
            if kind == "gt" and not (rv is not None and rv > val):
                return False
            if kind == "lt" and not (rv is not None and rv < val):
                return False
        return True

    def execute(self):
        matched = [r for r in self._store if self._match(r)]
        if self._op == "update":
            for r in matched:
                r.update(self._set)
            return _Result([dict(r) for r in matched])
        if self._limit is not None:
            matched = matched[: self._limit]
        return _Result([dict(r) for r in matched])


class FakeSupabase:
    def __init__(self, rows):
        self._store = rows

    def table(self, _name):
        return _Query(self._store)


class FakeCreditService:
    DEEP_RESEARCH_COST = 20
    calls = []  # class-level so every instance records to the same log
    ref_ids = []  # recorded separately so the (user, amount) assertions above stay readable
    raise_on_refund = False
    # migration 142: the real method returns {outcome, refunded, spendable}, or None strictly
    # for a transport fault. Override per-test to exercise the no-op outcomes.
    outcome = {"outcome": "refunded", "refunded": 20, "spendable": 999}

    def refund_ledgered(self, user_id, amount, *, reason=None, ref_id=None):
        if FakeCreditService.raise_on_refund:
            raise RuntimeError("refund RPC down")
        FakeCreditService.calls.append((user_id, amount))
        FakeCreditService.ref_ids.append(ref_id)
        return FakeCreditService.outcome


@pytest.fixture(autouse=True)
def _patch_credit_service(monkeypatch):
    FakeCreditService.calls = []
    FakeCreditService.ref_ids = []
    FakeCreditService.raise_on_refund = False
    FakeCreditService.outcome = {
        "outcome": "refunded", "refunded": 20, "spendable": 999}
    monkeypatch.setattr(recon, "CreditService", FakeCreditService)
    yield


def _row(**over):
    base = {
        "id": "r1",
        "user_id": "u1",
        "ticker": "NVDA",
        "status": "processing",
        "is_refunded": False,
        "credits_charged": 5,
        "created_at": datetime(2026, 6, 16, 0, 0, tzinfo=timezone.utc).isoformat(),
    }
    base.update(over)
    return base


_BLOB = {"error_code": "REPORT_GENERATION_FAILED", "user_message": "x"}


# ── claim_and_mark_failed ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_refunds_processing_row_once():
    rows = [_row()]
    sb = FakeSupabase(rows)

    won = await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb)

    assert won is True
    assert rows[0]["status"] == "failed"
    assert rows[0]["is_refunded"] is True
    assert FakeCreditService.calls == [("u1", 5)]


@pytest.mark.asyncio
async def test_refund_is_keyed_by_ticker_to_match_the_charge():
    """The reconciled refund MUST use the same `ref_id` the charge used —
    `research.py` charges with `ref_id=request.stock_id.upper()`.

    This stopped being cosmetic at migration 118. `refund_credits` now looks the original
    spend up BY (user, ref_id, amount) to learn which credit pool it drained. This call site
    passed `report_id`, which is never equal to a ticker, so every reconciled failure missed
    the lookup and took the granted-first fallback — refunding a report that was paid for out
    of PURCHASED credits into the GRANTED pool, where `ensure_credit_period` destroys it at
    the month boundary. That is the App Store Guideline 3.1.1 violation the two-pool design
    exists to prevent, on the primary failure path of the 20-credit action.
    """
    rows = [_row(ticker="nvda")]
    sb = FakeSupabase(rows)

    await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb)

    assert FakeCreditService.ref_ids == ["NVDA"], (
        "the refund must be keyed by the UPPER-CASED ticker, exactly as research.py charges — "
        f"got {FakeCreditService.ref_ids}"
    )
    assert "r1" not in FakeCreditService.ref_ids, "report_id is not a valid refund key"


@pytest.mark.asyncio
async def test_refund_without_a_ticker_still_refunds():
    """A row missing its ticker must not lose the user their credits — degrade to the
    fallback (which is safe, just not exact) rather than skipping the refund."""
    rows = [_row(ticker=None)]
    sb = FakeSupabase(rows)

    assert await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb) is True
    assert FakeCreditService.calls == [("u1", 5)]
    assert FakeCreditService.ref_ids == [None]


@pytest.mark.asyncio
async def test_second_claim_is_noop_no_double_refund():
    rows = [_row()]
    sb = FakeSupabase(rows)

    assert await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb) is True
    # Second call: row is now is_refunded=True → claim matches nothing.
    assert await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb) is False
    assert FakeCreditService.calls == [("u1", 5)]  # exactly once


@pytest.mark.asyncio
async def test_completed_row_never_refunded():
    rows = [_row(status="completed")]
    sb = FakeSupabase(rows)

    won = await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb)

    assert won is False
    assert rows[0]["status"] == "completed"
    assert FakeCreditService.calls == []


@pytest.mark.asyncio
async def test_failed_unrefunded_row_is_claimed():
    # generate_report stamps status='failed' before re-raising; the worker
    # except path must still refund. Also covers a row stranded in 'failed'
    # + is_refunded=False by a worker killed between mark and refund.
    rows = [_row(status="failed")]
    sb = FakeSupabase(rows)

    won = await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb)

    assert won is True
    assert FakeCreditService.calls == [("u1", 5)]


@pytest.mark.asyncio
async def test_refund_failure_leaves_row_claimed_no_retry():
    # Under-refund is the safe direction: we won the claim (is_refunded=True)
    # so we never retry, biasing away from double-refund.
    rows = [_row()]
    sb = FakeSupabase(rows)
    FakeCreditService.raise_on_refund = True

    won = await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb)

    assert won is True
    assert rows[0]["is_refunded"] is True
    assert FakeCreditService.calls == []  # refund raised, not recorded, not retried


# ── sweep_once ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_refunds_only_old_claimable_unrefunded_rows():
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(seconds=recon.RECON_STUCK_THRESHOLD_SECONDS + 60)).isoformat()
    recent = (now - timedelta(seconds=60)).isoformat()
    # STARTED-and-hung: processing_started_at older than the stuck threshold.
    started_old = old

    rows = [
        # started running long ago, never finished → refund
        _row(id="old_processing", created_at=old, processing_started_at=started_old),
        # worker died between mark-failed and refund (also started) → refund
        _row(id="old_failed", status="failed", created_at=old,
             processing_started_at=started_old),
        _row(id="recent_processing", created_at=recent),                 # too new
        _row(id="old_completed", status="completed", created_at=old),    # delivered
        _row(id="old_already_refunded", is_refunded=True, created_at=old),  # done
        _row(id="old_zero_charge", credits_charged=0, created_at=old),   # nothing owed
    ]
    sb = FakeSupabase(rows)

    result = await recon.sweep_once(now=now, supabase=sb)

    assert result == {"stuck": 2, "refunded": 2}
    refunded_users = {c for c in FakeCreditService.calls}
    assert refunded_users == {("u1", 5)}  # two rows, both (u1, 5)
    assert len(FakeCreditService.calls) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["old_processing"]["is_refunded"] is True
    assert by_id["old_failed"]["is_refunded"] is True
    assert by_id["recent_processing"]["is_refunded"] is False
    assert by_id["old_completed"]["status"] == "completed"


@pytest.mark.asyncio
async def test_sweep_does_not_refund_legitimately_queued_report():
    """REGRESSION (money bug): a report that has NOT started (processing_started_at
    NULL) but whose created_at is past the OLD 900s threshold is still
    legitimately queued behind the agent semaphore — it must NOT be refunded
    until the much longer abandon window. (Before processing_started_at, the
    sweep refunded it, which combined with the completion write to double-resolve.)"""
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    # 20 min old, never started — past the 900s stuck threshold but well within
    # the 3600s queue-abandon window.
    queued = (now - timedelta(seconds=1200)).isoformat()
    rows = [_row(id="queued", created_at=queued, processing_started_at=None)]
    sb = FakeSupabase(rows)

    result = await recon.sweep_once(now=now, supabase=sb)

    assert result == {"stuck": 0, "refunded": 0}
    assert rows[0]["is_refunded"] is False          # left alone — still queued
    assert FakeCreditService.calls == []


@pytest.mark.asyncio
async def test_sweep_refunds_never_started_after_abandon_window():
    """A never-started row older than the abandon window IS orphaned (its
    fire-and-forget task died before acquiring a slot, e.g. a redeploy)."""
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    abandoned = (
        now - timedelta(seconds=recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS + 60)
    ).isoformat()
    rows = [_row(id="abandoned", created_at=abandoned, processing_started_at=None)]
    sb = FakeSupabase(rows)

    result = await recon.sweep_once(now=now, supabase=sb)

    assert result == {"stuck": 1, "refunded": 1}
    assert rows[0]["is_refunded"] is True
    assert FakeCreditService.calls == [("u1", 5)]


@pytest.mark.asyncio
async def test_sweep_does_not_refund_recently_started_report():
    """A report that STARTED recently (within the stuck threshold) but whose
    created_at is old (long queue wait then started) is still running — not stuck."""
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    old_created = (now - timedelta(seconds=2000)).isoformat()       # long queue wait
    just_started = (now - timedelta(seconds=120)).isoformat()       # started 2 min ago
    rows = [_row(id="running", created_at=old_created,
                 processing_started_at=just_started)]
    sb = FakeSupabase(rows)

    result = await recon.sweep_once(now=now, supabase=sb)

    assert result == {"stuck": 0, "refunded": 0}
    assert rows[0]["is_refunded"] is False
    assert FakeCreditService.calls == []


@pytest.mark.asyncio
async def test_worker_except_then_sweep_refunds_exactly_once():
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(seconds=recon.RECON_STUCK_THRESHOLD_SECONDS + 60)).isoformat()
    rows = [_row(id="r1", created_at=old)]
    sb = FakeSupabase(rows)

    # Worker's except path wins the claim first…
    assert await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb) is True
    # …then the sweep runs over the same (now already-refunded) row.
    result = await recon.sweep_once(now=now, supabase=sb)

    assert result == {"stuck": 0, "refunded": 0}  # nothing left to claim
    assert FakeCreditService.calls == [("u1", 5)]  # exactly one refund total


# ── Transient Supabase failure on the sweep's lookup ─────────────────────────
#
# Supabase sits behind Cloudflare; a 520/525 edge page makes postgrest raise
# APIError('JSON could not be generated'). This sweep is the ONLY mechanism that
# refunds credits for reports killed mid-flight, so losing a pass to a blip leaves
# paid-for orphans un-refunded — and it logged at ERROR, opening a Sentry issue for
# an upstream hiccup ("research reconciliation sweep: lookup failed: APIError:
# Error 520:").


def _gateway_error(status: int = 520):
    from postgrest.exceptions import APIError, generate_default_error_message

    class _R:
        status_code = status
        content = b"<!DOCTYPE html><title>520: Web server is returning an unknown error</title>"

    return APIError(generate_default_error_message(_R()))


class _FlakyLookupSupabase(FakeSupabase):
    """Raises a scripted error on the first N `research_reports` SELECTs."""

    def __init__(self, rows, script):
        super().__init__(rows)
        self.script = list(script)
        self.select_calls = 0

    def table(self, name):
        q = super().table(name)
        outer = self

        class _Flaky(type(q)):  # noqa: N801
            def execute(self):
                if self._op == "select":
                    outer.select_calls += 1
                    if outer.script:
                        exc = outer.script.pop(0)
                        if exc is not None:
                            raise exc
                return super().execute()

        q.__class__ = _Flaky
        return q


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    from app.utils import supabase_errors as se

    async def _instant(*_a, **_k):
        return None

    monkeypatch.setattr(se.asyncio, "sleep", _instant)


@pytest.mark.asyncio
async def test_sweep_retries_a_transient_lookup_then_proceeds():
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(seconds=recon.RECON_STUCK_THRESHOLD_SECONDS + 60)).isoformat()
    # STARTED-and-hung, so it is genuinely claimable (a row with no
    # processing_started_at is still legitimately queued behind the semaphore).
    rows = [_row(id="r1", created_at=old, processing_started_at=old)]
    sb = _FlakyLookupSupabase(rows, [_gateway_error(520), None])

    result = await recon.sweep_once(now=now, supabase=sb)

    assert sb.select_calls >= 2, "the 520 was not retried"
    assert result == {"stuck": 1, "refunded": 1}, "the orphan was not recovered"
    assert FakeCreditService.calls == [("u1", 5)]


@pytest.mark.asyncio
async def test_sweep_warns_rather_than_errors_on_a_persistent_transient(caplog):
    import logging

    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    sb = _FlakyLookupSupabase([_row()], [_gateway_error(520)] * 10)

    with caplog.at_level(logging.DEBUG, logger=recon.logger.name):
        result = await recon.sweep_once(now=now, supabase=sb)

    assert result == {"stuck": 0, "refunded": 0}  # degrades, never raises
    hits = [r for r in caplog.records if "lookup failed" in r.getMessage()]
    assert hits and all(r.levelno == logging.WARNING for r in hits)


@pytest.mark.asyncio
async def test_sweep_still_errors_with_a_stack_for_a_real_bug(caplog):
    """Negative control: the demotion must stay scoped to transients."""
    import logging

    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    sb = _FlakyLookupSupabase([_row()], [KeyError("status")])

    with caplog.at_level(logging.DEBUG, logger=recon.logger.name):
        result = await recon.sweep_once(now=now, supabase=sb)

    assert result == {"stuck": 0, "refunded": 0}
    hits = [r for r in caplog.records if "lookup failed" in r.getMessage()]
    assert hits and hits[0].levelno == logging.ERROR
    assert hits[0].exc_info is not None


@pytest.mark.asyncio
async def test_sweep_does_not_retry_a_non_transient_lookup_failure():
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    sb = _FlakyLookupSupabase([_row()], [KeyError("status"), None])

    await recon.sweep_once(now=now, supabase=sb)

    assert sb.select_calls == 1


# ── Deleting an in-flight report must refund it ───────────────────────────────────────
#
# 'deleted' is TERMINAL and unreachable by the sweep: `_CLAIMABLE_STATUSES` is
# (pending, processing, failed), so once delete_report sets it, nothing can ever return the
# credits. The iOS list lets a user select and delete a still-generating card (selection is
# gated only on `backendId != nil`, unlike tap and retry which gate on status), and the worker
# then finishes into a conditional write whose status filter no longer matches — it logs and
# returns WITHOUT raising, so no except fires and no refund happens. 20 credits, silently; on a
# free account (50/month) that is 40% of the allocation.


def _delete(rows, report_id="rep-1", user_id="user-1", refunds=None):
    import asyncio
    from app.api.v1.endpoints import research as research_ep

    calls = refunds if refunds is not None else []

    class _Credits:
        def refund_ledgered(self, uid, amount, *, reason, ref_id):
            calls.append({"user_id": uid, "amount": amount, "reason": reason, "ref_id": ref_id})
            return 999

    original = research_ep.CreditService
    research_ep.CreditService = _Credits
    try:
        return asyncio.run(research_ep.delete_report(
            report_id, {"id": user_id}, FakeSupabase(rows)
        )), calls
    finally:
        research_ep.CreditService = original


def test_deleting_an_in_flight_report_refunds_it():
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "processing",
             "is_refunded": False, "credits_charged": 20, "ticker": "AAPL"}]
    _, calls = _delete(rows)

    assert rows[0]["status"] == "deleted"
    assert rows[0]["is_refunded"] is True, "the refund must be CLAIMED, or the sweep could double it"
    assert len(calls) == 1 and calls[0]["amount"] == 20


def test_the_delete_refund_uses_the_ticker_the_charge_used():
    """`refund_credits` matches the debit on (user_id, ref_id, delta). research.py charges with
    `request.stock_id.upper()`, so a lowercase or None ref_id finds no split and falls back to
    granted-first — converting PERMANENT purchased credits into expiring ones (3.1.1)."""
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "pending",
             "is_refunded": False, "credits_charged": 20, "ticker": "aapl"}]
    _, calls = _delete(rows)
    assert calls[0]["ref_id"] == "AAPL"


def test_deleting_a_finished_report_refunds_nothing():
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "ready",
             "is_refunded": False, "credits_charged": 20, "ticker": "AAPL"}]
    _, calls = _delete(rows)
    assert rows[0]["status"] == "deleted"
    assert calls == [], "a delivered report is not owed a refund"


def test_deleting_an_already_refunded_report_does_not_refund_twice():
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "failed",
             "is_refunded": True, "credits_charged": 20, "ticker": "AAPL"}]
    _, calls = _delete(rows)
    assert rows[0]["status"] == "deleted"
    assert calls == []


def test_deleting_a_zero_charge_report_refunds_nothing():
    """Cache hits and guest-era rows carry credits_charged=0. Refunding 0 would write a
    meaningless ledger row and, worse, could pair with an unrelated debit."""
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "pending",
             "is_refunded": False, "credits_charged": 0, "ticker": "AAPL"}]
    _, calls = _delete(rows)
    assert rows[0]["status"] == "deleted"
    assert calls == []


def test_a_user_cannot_delete_or_refund_another_users_report():
    rows = [{"id": "rep-1", "user_id": "victim", "status": "processing",
             "is_refunded": False, "credits_charged": 20, "ticker": "AAPL"}]
    _, calls = _delete(rows, user_id="attacker")
    assert rows[0]["status"] == "processing", "another user's report was mutated"
    assert calls == [], "an attacker triggered a refund against someone else's row"


# ── The commit-then-error double refund ───────────────────────────────────────────────


def test_the_insert_failure_path_claims_a_committed_row_before_refunding():
    """`insert` raising does NOT prove Postgres rolled back.

    A Cloudflare edge 520 or a read timeout after the commit reaches the failure branch with
    the row PRESENT and pristine (status='pending', is_refunded=False, credits_charged=20) —
    matching every filter the reconciliation sweep uses. The endpoint refunds; hours later the
    sweep wins its own claim, knows nothing about that refund, and refunds AGAIN. By then
    `refund_credits` finds no un-reversed debit, takes its granted-first fallback bounded only
    by the user's CURRENT `used`, and pays out a second time from unrelated spend.

    Source-scanned because the surrounding endpoint needs the full FastAPI dependency graph,
    while the property is structural: the refund on this path must be preceded by the same
    `is_refunded=False` compare-and-set the sweep uses. `test_claim_is_idempotent...` above
    already proves that flag blocks a second claim.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/research.py"
    code = src.read_text(encoding="utf-8")
    code = "\n".join("" if l.strip().startswith("#") else l for l in code.splitlines())

    start = code.index("if not insert_ok:")
    end = code.index("return make_error_response", start)
    branch = code[start:end]

    assert 'refund_ledgered' in branch, "the insert-failure branch no longer refunds"
    claim = branch.index('.eq("is_refunded", False)')
    refund = branch.index("refund_ledgered")
    assert claim < refund, (
        "the insert-failure path refunds WITHOUT first claiming a possibly-committed row — "
        "the reconciliation sweep can then refund the same charge a second time"
    )
    assert '"is_refunded": True' in branch[:refund], "the claim does not set the flag"
    assert '.in_("status", ["pending", "processing"])' in branch[:refund]


@pytest.mark.asyncio
async def test_a_claimed_orphan_is_invisible_to_the_sweep():
    """The other half of the pair: once the endpoint sets is_refunded=True, the sweep's own
    claim must find nothing, so the same charge cannot be refunded twice. Real code, not a
    scan."""
    rows = [_row(is_refunded=True)]
    FakeCreditService.calls.clear()

    won = await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase(rows))

    assert won is False, "the sweep re-claimed a row the endpoint had already refunded"
    assert FakeCreditService.calls == [], "a second refund was issued for one charge"


# ── migration 142: the reconciliation site must SEE a refund that moved nothing ────────


@pytest.mark.asyncio
async def test_a_no_op_refund_is_logged_with_the_report_id(caplog):
    """The site's `except Exception` was dead code — `refund_ledgered` never raises, it catches
    and returns None — so this, the ONLY leak log carrying `report_id` for a manual correction,
    could never fire. The claim (is_refunded=True) is already spent by the time it matters."""
    import logging

    FakeCreditService.outcome = {
        "outcome": "no_matching_debit", "refunded": 0, "spendable": 140}
    sb = FakeSupabase([_row()])
    with caplog.at_level(logging.ERROR):
        await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a refund that moved nothing produced no ERROR at the reconciliation site"
    msg = errors[0].getMessage()
    assert "REFUND LEAK" in msg and "r1" in msg, (
        f"the leak line must carry the report_id a human needs to correct it: {msg!r}"
    )


@pytest.mark.asyncio
async def test_a_successful_reconciled_refund_does_not_log_a_leak(caplog):
    """The success log used to fire unconditionally — including when the refund did nothing."""
    import logging

    FakeCreditService.outcome = {"outcome": "refunded", "refunded": 5, "spendable": 140}
    sb = FakeSupabase([_row()])
    with caplog.at_level(logging.DEBUG):
        await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
