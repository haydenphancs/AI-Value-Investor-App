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
    DEEP_RESEARCH_COST = 5
    calls = []  # class-level so every instance records to the same log
    raise_on_refund = False

    def refund(self, user_id, amount):
        if FakeCreditService.raise_on_refund:
            raise RuntimeError("refund RPC down")
        FakeCreditService.calls.append((user_id, amount))
        return 999


@pytest.fixture(autouse=True)
def _patch_credit_service(monkeypatch):
    FakeCreditService.calls = []
    FakeCreditService.raise_on_refund = False
    monkeypatch.setattr(recon, "CreditService", FakeCreditService)
    yield


def _row(**over):
    base = {
        "id": "r1",
        "user_id": "u1",
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

    rows = [
        _row(id="old_processing", created_at=old),                       # refund
        _row(id="old_failed", status="failed", created_at=old),          # refund
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
