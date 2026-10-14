"""Migration 195: `research_reports.refund_outcome` and the list's `refund_confirmed`.

`is_refunded` is the one-shot CAS CLAIM, set BEFORE `refund_credits` runs — so it cannot say
whether the refund LANDED, yet iOS drew "Refunded 20 credits" off it. Every refund site now
stamps what the refund actually did (`record_refund_outcome`), after the refund, in the
refund's own worker thread, never inside the CAS; the Reports list serves it as
`refund_confirmed` (False = "Refund pending").

Pinned here, each with the DEGRADED behaviour it must have:
  (a) the label vocabulary agrees with `refund_did_not_happen` on every outcome shape;
  (b) the worker/sweep claim stamps the real outcome (and `legacy_rpc_failed` when only the
      bare-ticker fallback failed — never retried);
  (c) a database WITHOUT the column: the claim and the refund are untouched, one WARNING
      names migration 195, and the latch stops further stamp attempts;
  (d) both delete paths stamp, and a replay never downgrades a confirmed label — nor turns a
      `partial` into `already_refunded`; a transient stamp failure is retried;
  (e) no `is_refunded` CAS dict ever carries `refund_outcome` (source pin);
  (f) the insert-failure path stamps after EVERY refund, off the loop, not only a claimed one;
  (g) the list: NULL + claimed → False when the column exists, None only while it is missing;
      no raw label on the wire; a missing column never 500s the list.

Uses the reconciliation suite's fakes (FakeSupabase models the PostgREST filter chain; its
`or_`/`is_` support was added for these stamps).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import threading

import pytest

import app.services.research_reconciliation_service as recon
from app.services.credit_service import (
    REFUND_FAILURE_OUTCOMES,
    REFUND_SETTLED_OUTCOMES,
    refund_did_not_happen,
)
from tests.test_research_reconciliation import (
    FakeCreditService,
    FakeSupabase,
    _BLOB,
    _delete,
    _row,
)
from tests.test_supabase_transient_classifier import gateway_error, postgrest_error


@pytest.fixture(autouse=True)
def _credits(monkeypatch):
    FakeCreditService.calls = []
    FakeCreditService.ref_ids = []
    FakeCreditService.quiet = []
    FakeCreditService.quiet_transport = []
    FakeCreditService.raise_on_refund = False
    FakeCreditService.outcome = {"outcome": "refunded", "refunded": 20, "spendable": 99}
    FakeCreditService.outcome_by_ref = {}
    monkeypatch.setattr(recon, "CreditService", FakeCreditService)


@pytest.fixture(autouse=True)
def _no_push(monkeypatch):
    class _Dispatcher:
        async def notify_users(self, user_ids, **kw):
            return len(user_ids)

    monkeypatch.setattr(
        "app.services.push_dispatch_service.get_push_dispatch_service", lambda: _Dispatcher(),
    )


def _missing_column_on_stamp(op, values):
    """PostgREST's answer to a write naming a column the database does not have (195 not
    applied) — for the stamp only, never for the CAS."""
    if op == "update" and values and "refund_outcome" in values:
        return postgrest_error("PGRST204", "Could not find the 'refund_outcome' column")
    return None


# ── (a) the vocabulary ───────────────────────────────────────────────────────────────────

_SHAPES = [
    None, {}, {"outcome": None}, {"outcome": ""}, {"outcome": "   "}, {"outcome": 5},
    {"outcome": True}, 999, 0,
    *({"outcome": o} for o in REFUND_SETTLED_OUTCOMES),
    *({"outcome": o} for o in REFUND_FAILURE_OUTCOMES),
    {"outcome": "partial", "refunded": 1, "requested": 20},
    {"outcome": " refunded"}, {"outcome": "REFUNDED"}, {"outcome": "brand_new_outcome"},
    {"outcome": "x" * 200},
]


@pytest.mark.parametrize("shape", _SHAPES, ids=repr)
@pytest.mark.parametrize("replayable", [True, False])
def test_the_label_agrees_with_the_refund_predicate_on_every_shape(shape, replayable):
    label = recon.refund_outcome_label(shape, replayable=replayable)
    assert isinstance(label, str) and 0 < len(label) <= 40
    assert recon.refund_confirmed_from_label(label) is (not refund_did_not_happen(shape)), label


def test_a_transport_fault_is_retryable_only_on_the_per_report_ref():
    assert recon.refund_outcome_label(None) == recon.LABEL_RPC_FAILED == "rpc_failed"
    assert recon.refund_outcome_label(None, replayable=False) == "legacy_rpc_failed"
    # One grep across the leak lines and the column: the same spelling as the endpoint's.
    from app.api.v1.endpoints.research import _outcome_name
    assert _outcome_name(None) == recon.LABEL_RPC_FAILED


def test_what_the_list_serves_for_each_state():
    f = recon.refund_confirmed_from_label
    assert f("refunded") is True and f("manual") is True and f("legacy") is True
    for owed in ("rpc_failed", "legacy_rpc_failed", "retry_rpc_failed", "partial",
                 "no_matching_debit", "capped_to_zero", "unknown"):
        assert f(owed) is False, owed
    # NULL on a CLAIMED row: "not confirmed" once the column exists — never the old chip.
    assert f(None, is_refunded=True, column_known=True) is False
    # ...None only while the column is latched missing (an old database keeps its chip).
    assert f(None, is_refunded=True, column_known=False) is None
    # Nothing claimed → nothing to confirm.
    assert f(None, is_refunded=False, column_known=True) is None
    # The manual/legacy labels are confirmed; a stray label on an unclaimed row still reads.
    assert recon.REFUND_CONFIRMED_LABELS >= set(REFUND_SETTLED_OUTCOMES) | {"manual", "legacy"}
    assert "partial" not in recon.REFUND_CONFIRMED_LABELS


# ── (b) the claim stamps what the refund did ────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,expected", [
    ({"outcome": "refunded", "refunded": 20}, "refunded"),
    ({"outcome": "already_refunded", "refunded": 0}, "already_refunded"),
    (None, "rpc_failed"),
    ({"outcome": "capped_to_zero", "refunded": 0}, "capped_to_zero"),
    ({"outcome": "partial", "refunded": 5}, "partial"),
    ({}, "unknown"),
])
async def test_the_claim_stamps_the_refund_outcome(outcome, expected):
    FakeCreditService.outcome = outcome
    rows = [_row(credits_charged=20)]
    assert await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase(rows)) is True
    assert rows[0]["is_refunded"] is True and rows[0]["status"] == "failed"
    assert rows[0]["refund_outcome"] == expected


@pytest.mark.asyncio
async def test_a_refund_call_that_raised_is_stamped_retryable():
    FakeCreditService.raise_on_refund = True
    rows = [_row(credits_charged=20)]
    await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase(rows))
    assert rows[0]["refund_outcome"] == "rpc_failed", "nothing was sent: a replay is safe"


@pytest.mark.asyncio
async def test_a_legacy_row_settled_through_the_bare_ticker_is_confirmed():
    FakeCreditService.outcome_by_ref = {
        "NVDA:r1": {"outcome": "no_matching_debit", "refunded": 0},
        "NVDA": {"outcome": "refunded", "refunded": 20},
    }
    rows = [_row(credits_charged=20)]
    await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase(rows))
    assert FakeCreditService.ref_ids == ["NVDA:r1", "NVDA"], "two calls: quiet per-report, legacy"
    assert rows[0]["refund_outcome"] == "refunded"


@pytest.mark.asyncio
async def test_a_failed_bare_ticker_fallback_is_never_marked_retryable():
    """The fallback's transport fault may have COMMITTED with its reply lost; a retry on the
    per-report ref would answer no_matching_debit and page for nothing — and a retry on the
    bare ticker could pair a sibling's debit. `legacy_rpc_failed` is the owner's."""
    FakeCreditService.outcome_by_ref = {
        "NVDA:r1": {"outcome": "no_matching_debit", "refunded": 0},
        "NVDA": None,
    }
    rows = [_row(credits_charged=20)]
    await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase(rows))
    assert rows[0]["refund_outcome"] == "legacy_rpc_failed"


@pytest.mark.asyncio
async def test_a_ticker_less_claimed_row_failing_is_never_marked_retryable():
    FakeCreditService.outcome = None
    rows = [_row(credits_charged=20, ticker=None)]
    await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase(rows))
    assert FakeCreditService.ref_ids == [None]
    assert rows[0]["refund_outcome"] == "legacy_rpc_failed"


@pytest.mark.asyncio
async def test_a_lost_claim_writes_no_stamp():
    rows = [_row(credits_charged=20, is_refunded=True, status="failed",
                 refund_outcome="refunded")]
    assert await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase(rows)) is False
    assert FakeCreditService.calls == [] and rows[0]["refund_outcome"] == "refunded"


@pytest.mark.asyncio
async def test_a_raised_claims_replay_never_downgrades_a_confirmed_stamp():
    """A raised claim's re-read refunds on the per-report ref only. On a LEGACY row a
    concurrent CAS winner already refunded through the bare ticker and stamped `refunded`;
    this replay answers no_matching_debit — it must not turn the card into 'Refund pending'."""
    FakeCreditService.outcome = {"outcome": "no_matching_debit", "refunded": 0}
    rows = [_row(credits_charged=20, is_refunded=True, status="failed",
                 refund_outcome="refunded")]

    def _raise_claim(op, values):
        if op == "update" and values and "is_refunded" in values:
            return RuntimeError("reply lost")
        return None

    await recon.claim_and_mark_failed(
        "r1", _BLOB, supabase=FakeSupabase(rows, raise_on=_raise_claim))
    assert FakeCreditService.ref_ids == ["NVDA:r1"], "the re-read must never use the legacy ref"
    assert rows[0]["refund_outcome"] == "refunded"


@pytest.mark.asyncio
async def test_an_owner_correction_survives_a_later_failed_replay():
    FakeCreditService.outcome = None
    rows = [_row(credits_charged=20, is_refunded=True, status="failed",
                 refund_outcome="manual")]

    def _raise_claim(op, values):
        return RuntimeError("reply lost") if op == "update" and "is_refunded" in (values or {}) else None

    await recon.claim_and_mark_failed(
        "r1", _BLOB, supabase=FakeSupabase(rows, raise_on=_raise_claim))
    assert rows[0]["refund_outcome"] == "manual"


@pytest.mark.asyncio
async def test_an_unconfirmed_stamp_still_replaces_an_unconfirmed_one():
    """The guard protects CONFIRMED labels only — a claimed row's own fresh stamp lands."""
    FakeCreditService.outcome = {"outcome": "refunded", "refunded": 20}
    sb = FakeSupabase([_row(id="r9", credits_charged=20, refund_outcome="rpc_failed")])
    assert recon.record_refund_outcome(sb, "r9", label="retry_rpc_failed") == "retry_rpc_failed"
    assert recon.record_refund_outcome(sb, "r9", label="refunded") == "refunded"
    assert recon.record_refund_outcome(sb, "r9", label="rpc_failed") is None
    assert sb._store[0]["refund_outcome"] == "refunded"


@pytest.mark.parametrize("current,written", [
    (None, True), ("rpc_failed", True), ("retry_rpc_failed", True),
    ("legacy_rpc_failed", True), ("unknown", True),
    ("partial", False),                       # still owed: a replay must not hide it
    ("refunded", False), ("manual", False), ("legacy", False), ("already_refunded", False),
])
def test_already_refunded_never_replaces_partial(current, written):
    """A refund cut short at a month boundary (`partial`) still INSERTs its reversal row, so
    any later replay of that row — a raised claim's or a raised delete CAS's re-read — answers
    `already_refunded` while the rest of the charge is owed. That answer may only replace an
    unstamped or an unconfirmed label other than `partial`."""
    sb = FakeSupabase([_row(id="r9", credits_charged=20, refund_outcome=current)])
    got = recon.record_refund_outcome(sb, "r9", label="already_refunded")
    assert (got == "already_refunded") is written
    assert sb._store[0]["refund_outcome"] == ("already_refunded" if written else current)


@pytest.mark.asyncio
async def test_a_raised_claims_replay_keeps_a_partial_refund_owed():
    """End to end: the row was settled `partial` (REFUND LEAK paged); a second claimer's CAS
    raises, its re-read replays on the per-report ref and the RPC answers already_refunded."""
    FakeCreditService.outcome = {"outcome": "already_refunded", "refunded": 0}
    rows = [_row(credits_charged=20, is_refunded=True, status="failed", refund_outcome="partial")]

    def _raise_claim(op, values):
        return RuntimeError("reply lost") if op == "update" and "is_refunded" in (values or {}) else None

    await recon.claim_and_mark_failed(
        "r1", _BLOB, supabase=FakeSupabase(rows, raise_on=_raise_claim))
    assert FakeCreditService.ref_ids == ["NVDA:r1"]
    assert rows[0]["refund_outcome"] == "partial"
    assert recon.refund_confirmed_from_label(rows[0]["refund_outcome"]) is False


@pytest.fixture
def _no_stamp_backoff(monkeypatch):
    from app.utils import supabase_errors as se

    slept = []
    monkeypatch.setattr(se.time, "sleep", lambda s: slept.append(s))
    return slept


def _flaky_stamp(failures, exc_factory=lambda: gateway_error(520)):
    """Fail the first `failures` stamp UPDATEs (never the CAS) with `exc_factory()`."""
    seen = []

    def _raise_on(op, values):
        if op == "update" and values and "refund_outcome" in values:
            seen.append(values)
            if len(seen) <= failures:
                return exc_factory()
        return None

    return _raise_on, seen


def test_a_transient_stamp_failure_is_retried(_no_stamp_backoff, caplog):
    """One gateway blip must not decide a permanent chip: the stamp is idempotent, so it goes
    through retry_idempotent_sync."""
    raise_on, seen = _flaky_stamp(1)
    sb = FakeSupabase([_row(id="r9", refund_outcome=None)], raise_on=raise_on)
    with caplog.at_level(logging.WARNING):
        assert recon.record_refund_outcome(sb, "r9", label="refunded") == "refunded"
    assert len(seen) == 2 and sb._store[0]["refund_outcome"] == "refunded"
    assert _no_stamp_backoff, "the retry must back off between attempts"


def test_a_transient_stamp_failure_that_persists_is_a_warning_not_a_latch(_no_stamp_backoff, caplog):
    raise_on, seen = _flaky_stamp(99)
    sb = FakeSupabase([_row(id="r9", refund_outcome=None)], raise_on=raise_on)
    with caplog.at_level(logging.WARNING):
        assert recon.record_refund_outcome(sb, "r9", label="refunded") is None
    assert len(seen) == 3, "bounded by retry_idempotent_sync's attempts"
    assert sb._store[0]["refund_outcome"] is None
    assert recon.refund_outcome_column_available()
    assert any("refund_outcome stamp failed for report r9" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_permanent_stamp_failure_is_not_retried(_no_stamp_backoff):
    raise_on, seen = _flaky_stamp(99, lambda: postgrest_error("42501", "permission denied"))
    sb = FakeSupabase([_row(id="r9", refund_outcome=None)], raise_on=raise_on)
    assert recon.record_refund_outcome(sb, "r9", label="refunded") is None
    assert len(seen) == 1 and _no_stamp_backoff == []


def test_a_missing_column_is_still_latched_not_retried(_no_stamp_backoff):
    raise_on, seen = _flaky_stamp(99, lambda: postgrest_error("PGRST204", "no refund_outcome"))
    sb = FakeSupabase([_row(id="r9", refund_outcome=None)], raise_on=raise_on)
    assert recon.record_refund_outcome(sb, "r9", label="refunded") is None
    assert len(seen) == 1 and not recon.refund_outcome_column_available()


def test_the_stamp_is_scoped_to_the_owner():
    sb = FakeSupabase([_row(id="r1", user_id="victim", refund_outcome=None)])
    assert recon.record_refund_outcome(sb, "r1", label="refunded", user_id="attacker") is None
    assert sb._store[0]["refund_outcome"] is None


def test_the_stamp_never_raises_and_says_why(caplog):
    class _Boom:
        def table(self, _name):
            raise RuntimeError("edge 520")

    with caplog.at_level(logging.WARNING):
        assert recon.record_refund_outcome(_Boom(), "r1", label="refunded") is None
        assert recon.record_refund_outcome(_Boom(), "", label="refunded") is None
        assert recon.record_refund_outcome(_Boom(), None, label="refunded") is None
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "refund_outcome stamp failed for report r1" in text and "edge 520" in text
    assert "no report id" in text
    assert recon.refund_outcome_column_available(), "a transport error must not latch the column"


@pytest.mark.asyncio
async def test_the_stamp_runs_in_the_claims_worker_thread(monkeypatch):
    loop_thread = threading.get_ident()
    seen = []
    real = recon.record_refund_outcome

    def _spy(*a, **k):
        seen.append(threading.get_ident())
        return real(*a, **k)

    monkeypatch.setattr(recon, "record_refund_outcome", _spy)
    await recon.claim_and_mark_failed("r1", _BLOB, supabase=FakeSupabase([_row(credits_charged=20)]))
    assert seen and all(t != loop_thread for t in seen)


# ── (c) a database without the column ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_without_the_column_the_claim_and_refund_are_untouched_and_it_latches(caplog):
    rows = [_row(credits_charged=20), _row(id="r2", credits_charged=20)]
    stamp_attempts = []

    def _raise_on(op, values):
        if op == "update" and values and "refund_outcome" in values:
            stamp_attempts.append(values)
        return _missing_column_on_stamp(op, values)

    sb = FakeSupabase(rows, raise_on=_raise_on)
    with caplog.at_level(logging.INFO):
        assert await recon.claim_and_mark_failed("r1", _BLOB, supabase=sb) is True
        assert await recon.claim_and_mark_failed("r2", _BLOB, supabase=sb) is True
    assert FakeCreditService.calls == [("u1", 20), ("u1", 20)], "the refund must not depend on 195"
    assert all(r["is_refunded"] is True and r["status"] == "failed" for r in rows)
    assert all("refund_outcome" not in r for r in rows)
    assert len(stamp_attempts) == 1, "the latch must stop a second stamp attempt"
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "migration 195" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert not recon.refund_outcome_column_available()


def test_the_latch_rechecks_after_its_window(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(recon.time, "monotonic", lambda: clock[0])
    recon.note_refund_outcome_column_missing("test")
    assert not recon.refund_outcome_column_available()
    clock[0] += recon._REFUND_OUTCOME_RECHECK_SECONDS - 1
    assert not recon.refund_outcome_column_available()
    clock[0] += 2
    assert recon.refund_outcome_column_available(), "195 applied later must not need a restart"
    sb = FakeSupabase([_row(id="r1", refund_outcome=None)])
    assert recon.record_refund_outcome(sb, "r1", label="refunded") == "refunded"
    assert recon._refund_outcome_missing_at is None, "a successful stamp clears the latch"


def test_a_relatch_does_not_warn_twice(caplog):
    with caplog.at_level(logging.INFO):
        recon.note_refund_outcome_column_missing("a")
        recon.note_refund_outcome_column_missing("b")
    levels = [r.levelno for r in caplog.records if "migration 195" in r.getMessage()]
    assert levels == [logging.WARNING, logging.INFO]


# ── (d) the delete paths ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("outcome,expected", [
    (999, "refunded"),                                      # the pre-142 integer
    ({"outcome": "refunded", "refunded": 20}, "refunded"),
    (None, "rpc_failed"),
    ({"outcome": "capped_to_zero", "refunded": 0}, "capped_to_zero"),
])
def test_a_cas_winning_delete_stamps_its_refund(outcome, expected):
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "processing",
             "is_refunded": False, "credits_charged": 20, "ticker": "AAPL"}]
    _delete(rows, refund_outcome=outcome)
    assert rows[0]["status"] == "deleted" and rows[0]["is_refunded"] is True
    assert rows[0]["refund_outcome"] == expected


def test_a_delete_whose_legacy_fallback_failed_is_the_owners():
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "processing",
             "is_refunded": False, "credits_charged": 20, "ticker": "AAPL"}]
    _, calls = _delete(rows, refund_outcome={"by_ref": {
        "AAPL:rep-1": {"outcome": "no_matching_debit", "refunded": 0}, "AAPL": None}})
    assert [c["ref_id"] for c in calls] == ["AAPL:rep-1", "AAPL"]
    assert rows[0]["refund_outcome"] == "legacy_rpc_failed"


def test_a_raised_delete_claim_that_committed_stamps_too():
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "processing",
             "is_refunded": False, "credits_charged": 20, "ticker": "AAPL"}]

    def _cas(op, values):
        return RuntimeError("reply lost") if op == "update" and "is_refunded" in (values or {}) else None

    _delete(rows, raise_on=_cas, commit_before_raise=True, refund_outcome=None)
    assert rows[0]["refund_outcome"] == "rpc_failed"


def test_a_replayed_delete_never_downgrades_a_settled_refund():
    """Re-tapping delete after a lost reply: the re-read replays on the per-report ref; a
    transport fault there must not turn the earlier, settled 'refunded' into pending."""
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "deleted", "is_refunded": True,
             "credits_charged": 20, "ticker": "AAPL", "refund_outcome": "refunded"}]

    def _cas(op, values):
        return RuntimeError("reply lost") if op == "update" and "is_refunded" in (values or {}) else None

    _, calls = _delete(rows, raise_on=_cas, refund_outcome=None)
    assert len(calls) == 1 and calls[0]["ref_id"] == "AAPL:rep-1"
    assert rows[0]["refund_outcome"] == "refunded"


def test_a_delete_without_the_column_still_refunds_and_answers_normally():
    rows = [{"id": "rep-1", "user_id": "user-1", "status": "processing",
             "is_refunded": False, "credits_charged": 20, "ticker": "AAPL"}]
    resp, calls = _delete(rows, raise_on=_missing_column_on_stamp)
    assert resp == {"message": "Report deleted successfully", "outcome": "refunded"}
    assert len(calls) == 1 and rows[0]["is_refunded"] is True
    assert "refund_outcome" not in rows[0]


# ── (e) the stamp is never part of an is_refunded CAS (source pin) ──────────────────────

def _cas_dicts(module):
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update" and node.args
                and isinstance(node.args[0], ast.Dict)):
            keys = node.args[0].keys
            literal = {k.value for k in keys if isinstance(k, ast.Constant)}
            names = {k.id for k in keys if isinstance(k, ast.Name)}
            yield node.lineno, literal, names


@pytest.mark.parametrize("module_name", [
    "app.api.v1.endpoints.research", "app.services.research_reconciliation_service",
])
def test_no_is_refunded_cas_carries_the_outcome(module_name):
    import importlib

    module = importlib.import_module(module_name)
    cas = [(line, lit, names) for line, lit, names in _cas_dicts(module) if "is_refunded" in lit]
    # research.py: the insert-failure orphan claim + the delete CAS; the service: the claim.
    assert len(cas) >= (2 if module_name.endswith("research") else 1), (
        "the CAS detector found too few is_refunded updates — it has rotted"
    )
    for line, literal, names in cas:
        assert "refund_outcome" not in literal and "REFUND_OUTCOME_COLUMN" not in names, (
            f"{module_name}:{line}: the is_refunded CAS writes refund_outcome — a missing "
            f"column (195 not applied) would then break the money guard itself"
        )


# ── (f) the insert-failure path stamps after EVERY refund ───────────────────────────────

def _insert_failure_def():
    from app.api.v1.endpoints import research as ep

    tree = ast.parse(inspect.getsource(ep.generate_research_report))
    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_refund_and_stamp"]
    assert len(defs) == 1, "the insert-failure refund's thread body is gone"
    return defs[0], tree


def _call_names(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.append((n.lineno, f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)))
    return sorted(out)


def test_the_insert_failure_stamp_follows_its_refund_in_the_same_thread_body():
    fn, tree = _insert_failure_def()
    names = [name for _line, name in _call_names(fn)]
    assert "refund_ledgered" in names and "record_refund_outcome" in names
    assert names.index("refund_ledgered") < names.index("record_refund_outcome")
    # Not gated on `claimed_orphan`: a claim that committed with its reply lost reads False.
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "claimed_orphan" in ast.unparse(node.test):
            assert "record_refund_outcome" not in ast.unparse(node), (
                "the stamp is gated on claimed_orphan — a lost-reply claim reads pending forever"
            )
    # And it runs through settle_off_loop (a worker thread), never inline on the loop.
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "settle_off_loop"]
    assert any(isinstance(c.args[0], ast.Name) and c.args[0].id == "_refund_and_stamp"
               for c in calls if c.args)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_raises", [False, True])
async def test_the_insert_failure_path_stamps_off_the_loop_whatever_the_claim_did(
        monkeypatch, claim_raises):
    from app.api.v1.endpoints import research as ep
    from tests.test_credit_review_fixes import _credit_mock, _generate_supabase, _req, _stub_fmp

    loop_thread = threading.get_ident()
    credit = _credit_mock(monkeypatch, ep)
    credit.refund_ledgered.return_value = {"outcome": "refunded", "refunded": 20}
    _stub_fmp(monkeypatch)
    sb, record = _generate_supabase(insert_raises=True, claim_raises=claim_raises)
    resp = await ep.generate_research_report(
        request=_req(), user={"id": "u1"}, supabase=sb, _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == "REPORT_GENERATION_FAILED"
    credit.refund_ledgered.assert_called_once()
    assert len(record["stamps"]) == 1, record["stamps"]
    stamp = record["stamps"][0]
    assert stamp["payload"] == {"refund_outcome": "refunded"}
    assert ("id", record["insert"]["id"]) in stamp["filters"]
    assert ("user_id", "u1") in stamp["filters"]
    assert stamp["thread"] not in (None, loop_thread), "the stamp ran on the event loop"


# ── (g) the Reports list ─────────────────────────────────────────────────────────────────

class _ListSupabase:
    """Records each list select; `missing_column` makes a select naming refund_outcome fail
    the way PostgREST does on a database without migration 195."""

    def __init__(self, rows, *, missing_column=False, raise_exc=None):
        self.rows, self.missing_column, self.raise_exc = rows, missing_column, raise_exc
        self.selects: list[str] = []
        self.threads: list[int] = []

    def table(self, _name):
        outer = self

        class _Q:
            def select(self, cols):
                self.cols = cols
                outer.selects.append(cols)
                return self

            def eq(self, *a):
                return self

            neq = eq

            def order(self, *a, **k):
                return self

            def limit(self, *a):
                return self

            def execute(self):
                outer.threads.append(threading.get_ident())
                if outer.raise_exc is not None:
                    raise outer.raise_exc
                if outer.missing_column and "refund_outcome" in self.cols:
                    raise postgrest_error("42703", "column research_reports.refund_outcome does not exist")
                rows = [dict(r) for r in outer.rows]
                if "refund_outcome" not in self.cols:
                    for r in rows:
                        r.pop("refund_outcome", None)
                return type("R", (), {"data": rows})()

        return _Q()


def _list_row(i, **over):
    base = {"id": f"r{i}", "ticker": "AAPL", "company_name": "Apple", "industry": None,
            "investor_persona": "warren_buffett", "status": "failed", "title": None,
            "executive_summary": None, "overall_score": None, "fair_value_estimate": None,
            "progress": 0, "current_step": None, "created_at": "2026-10-12T00:00:00Z",
            "processing_started_at": None, "completed_at": None, "user_rating": None,
            "is_refunded": True, "credits_charged": 20, "pdf_status": None}
    base.update(over)
    return base


async def _list(sb):
    from app.api.v1.endpoints import research as ep
    from app.schemas.research import ResearchReportListItem

    items = await ep.get_my_reports(limit=20, user={"id": "u1"}, supabase=sb)
    # The handler's rows go through the route's response_model on the wire.
    return [ResearchReportListItem.model_validate(i) for i in items], items


@pytest.mark.asyncio
async def test_the_list_serves_the_confirmation_and_never_the_raw_label():
    rows = [
        _list_row(1, refund_outcome="refunded"),
        _list_row(2, refund_outcome="rpc_failed"),
        _list_row(3, refund_outcome=None),                           # claimed, stamp lost
        _list_row(4, refund_outcome="legacy"),
        _list_row(5, refund_outcome=None, is_refunded=False, status="completed"),
        _list_row(6, refund_outcome="manual"),
    ]
    sb = _ListSupabase(rows)
    models, raw = await _list(sb)
    assert [m.refund_confirmed for m in models] == [True, False, False, True, None, True]
    assert all("refund_outcome" not in r for r in raw), "the raw label reached the wire"
    assert sb.selects == [_expected_columns(with_outcome=True)]


@pytest.mark.asyncio
async def test_a_database_without_195_still_lists_and_serves_null(caplog):
    rows = [_list_row(1), _list_row(2, is_refunded=False, status="processing")]
    sb = _ListSupabase(rows, missing_column=True)
    with caplog.at_level(logging.WARNING):
        models, raw = await _list(sb)
    assert [m.refund_confirmed for m in models] == [None, None], "an old database keeps the old chip"
    assert sb.selects == [_expected_columns(True), _expected_columns(False)]
    assert any("migration 195" in r.getMessage() for r in caplog.records)
    # The next poll does not pay for the failing select again.
    sb.selects.clear()
    await _list(sb)
    assert sb.selects == [_expected_columns(False)]


@pytest.mark.asyncio
async def test_a_transient_list_failure_is_not_mistaken_for_a_missing_column():
    from tests.test_supabase_transient_classifier import gateway_error

    sb = _ListSupabase([_list_row(1)], raise_exc=gateway_error(520))
    with pytest.raises(Exception):
        await _list(sb)
    assert recon.refund_outcome_column_available(), "a 520 latched the column as missing"
    assert len(sb.selects) == 1, "a transient failure must not trigger the fallback select"


@pytest.mark.asyncio
async def test_the_list_query_runs_off_the_loop():
    sb = _ListSupabase([_list_row(1, refund_outcome="refunded")])
    loop_thread = threading.get_ident()
    await _list(sb)
    assert sb.threads and all(t != loop_thread for t in sb.threads)


@pytest.mark.asyncio
async def test_an_empty_list_is_still_a_list():
    models, raw = await _list(_ListSupabase([]))
    assert models == [] and raw == []


def _expected_columns(with_outcome: bool) -> str:
    from app.api.v1.endpoints.research import _REPORT_LIST_COLUMNS

    return _REPORT_LIST_COLUMNS + (", refund_outcome" if with_outcome else "")
