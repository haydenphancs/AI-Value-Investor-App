"""Phase 2: the reconciliation loop retries — once — a claimed refund that did not land.

`sweep_once` never looks at an `is_refunded=True` row, so before this a refund whose RPC
failed after the claim was a permanent leak (REFUND LEAK paged, nothing else). The retry is
safe ONLY because report charges carry unique per-report refs (2026-10-12): a replay of a
refund that already committed answers `already_refunded` and moves nothing. So it must:

  (a) refund on `report_charge_ref(ticker, id)` — the precharge's own builder — and NEVER
      the legacy bare ticker (it does not hold the row's CAS; a bare-ticker replay can pair a
      sibling report's debit and mint 20 credits);
  (b) retry a known failure at most ONCE: a second transport fault on an `rpc_failed` row is
      `retry_rpc_failed`, never re-selected;
  (c) leave every other label alone (business no-ops, confirmed, `legacy_rpc_failed`), and
      re-check an unstamped claimed row of ANY age once it is past the grace window — there
      is no cutover instant: an unstamped row whose per-report ref finds no debit was charged
      under the legacy bare ticker, so it is recorded `legacy` with a WARNING (never a page,
      never the legacy fallback), and an unstamped row whose re-check hits a transport fault
      is left unstamped with a WARNING (its claimer may still be settling it);
  (d) stamp an UNCONFIRMED answer with a CAS on the label it read (an owner's `manual`
      written meanwhile wins) and a CONFIRMED one without it (credits proven back beat a
      concurrent unconfirmed stamp);
  (e) be a no-op on a database without migration 195.
"""

from __future__ import annotations

import ast
import inspect
import logging
from datetime import datetime, timedelta, timezone

import pytest

import app.services.research_reconciliation_service as recon
from app.services.credit_service import ChargeRef, report_charge_ref
from tests.test_research_reconciliation import FakeCreditService, FakeSupabase
from tests.test_supabase_transient_classifier import gateway_error, postgrest_error

_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
_OLD = (_NOW - timedelta(hours=2)).isoformat()            # past the 30-min grace
_FRESH = (_NOW - timedelta(minutes=5)).isoformat()        # inside it
# Long before per-report refs existed: there is no cutover, so age alone never excludes a row.
_LONG_AGO = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()


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
def _no_retry_backoff(monkeypatch):
    from app.utils import supabase_errors as se

    async def _instant(*_a, **_k):
        return None

    monkeypatch.setattr(se.asyncio, "sleep", _instant)


def _claimed(**over):
    row = {"id": "r1", "user_id": "u1", "ticker": "NVDA", "status": "failed",
           "is_refunded": True, "credits_charged": 20, "created_at": _OLD,
           "refund_outcome": "rpc_failed"}
    row.update(over)
    return row


async def _pass(rows, **kw):
    return await recon.retry_unconfirmed_refunds_once(now=_NOW, supabase=FakeSupabase(rows, **kw))


# ── (a) the ref ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_rpc_failed_refund_is_retried_on_the_per_report_ref_and_confirmed():
    rows = [_claimed(ticker="nvda")]
    out = await _pass(rows)
    assert out == {"candidates": 1, "settled": 1}
    assert FakeCreditService.calls == [("u1", 20)]
    ref = FakeCreditService.ref_ids[0]
    assert ref == report_charge_ref("NVDA", "r1") == "NVDA:r1"
    assert isinstance(ref, ChargeRef), "a plain str would not get the transient retry"
    assert ref != "NVDA" and "r1" in ref
    assert FakeCreditService.quiet == [False], "never quiet: a miss on an rpc_failed row must page"
    assert rows[0]["refund_outcome"] == "refunded"


@pytest.mark.asyncio
async def test_a_replay_of_a_refund_that_did_land_is_confirmed_not_paid_again():
    FakeCreditService.outcome = {"outcome": "already_refunded", "refunded": 0}
    rows = [_claimed()]
    out = await _pass(rows)
    assert out["settled"] == 1 and rows[0]["refund_outcome"] == "already_refunded"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [
    ("failed", "report_refund_reconciled"), ("deleted", "report_refund_deleted"),
])
async def test_the_retry_reason_follows_the_rows_current_status(status, reason, monkeypatch):
    """The ledger reason is picked from the row's CURRENT status, not recorded from the first
    settlement. That equals the first settlement's reason on most paths; the insert-failure
    orphan claim ('report_refund', retried as 'report_refund_reconciled') is one exception, and
    the other is pinned here as it actually behaves: a FAILED report whose refund leaked (`rpc_failed`, settled as
    `report_refund_reconciled`) that the user then deleted — Retry sends DELETE intent=retry,
    its CAS misses (is_refunded is already true) and the soft-delete flips status to
    'deleted' — is retried as `report_refund_deleted` ("report deleted" in Credit History
    instead of "report didn't finish"). Cosmetic: same amount, same ref."""
    seen = []

    class _Recording(FakeCreditService):
        def refund_ledgered(self, user_id, amount, *, reason=None, ref_id=None, quiet_no_match=False,
                            quiet_transport=False):
            seen.append(reason)
            return super().refund_ledgered(user_id, amount, reason=reason, ref_id=ref_id,
                                           quiet_no_match=quiet_no_match,
                                           quiet_transport=quiet_transport)

    monkeypatch.setattr(recon, "CreditService", _Recording)
    await _pass([_claimed(status=status)])
    assert seen == [reason]


@pytest.mark.asyncio
async def test_a_legacy_charge_amount_is_refunded_as_recorded():
    await _pass([_claimed(credits_charged=5)])
    assert FakeCreditService.calls == [("u1", 5)]


def test_the_retry_never_reaches_the_legacy_fallback():
    """Source pin (comments stripped by `ast`): the retry body builds the ref with the
    precharge's builder and calls refund_ledgered on it — never `refund_report_charge`,
    whose CAS-winner fallback would refund the bare ticker."""
    tree = ast.parse(inspect.getsource(recon._retry_one_refund))
    names = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None))
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "report_charge_ref" in names and "refund_ledgered" in names
    assert "refund_report_charge" not in names
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "refund_ledgered":
            kwargs = {k.arg: k.value for k in n.keywords}
            assert isinstance(kwargs.get("ref_id"), ast.Name) and kwargs["ref_id"].id == "ref"
            # Quiet ONLY for an unstamped re-check: an `rpc_failed` row's miss or fault must page.
            for flag in ("quiet_no_match", "quiet_transport"):
                assert isinstance(kwargs.get(flag), ast.Name) and kwargs[flag].id == "unstamped", flag
    src = inspect.getsource(recon._retry_one_refund)
    assert "unstamped = expected is None" in src
    # The precharge uses the SAME builder (one definition, two sites cannot drift).
    from app.api.v1.endpoints import research as ep
    assert "report_charge_ref(ticker, new_report_id)" in inspect.getsource(ep.generate_research_report)
    assert recon.report_charge_ref is ep.report_charge_ref


# ── (b) at most one automatic retry ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_second_transport_fault_is_final_and_never_retried_again(caplog):
    FakeCreditService.outcome = None
    rows = [_claimed()]
    with caplog.at_level(logging.ERROR):
        out = await _pass(rows)
    assert out == {"candidates": 1, "settled": 0}
    assert rows[0]["refund_outcome"] == "retry_rpc_failed"
    assert any("REFUND LEAK" in r.getMessage() and "r1" in r.getMessage() for r in caplog.records)
    FakeCreditService.calls.clear()
    assert await _pass(rows) == {"candidates": 0, "settled": 0}
    assert FakeCreditService.calls == [], "the retry looped"


@pytest.mark.asyncio
async def test_a_refund_call_that_raises_is_final_too():
    FakeCreditService.raise_on_refund = True
    rows = [_claimed()]
    await _pass(rows)
    assert rows[0]["refund_outcome"] == "retry_rpc_failed"


@pytest.mark.asyncio
async def test_a_business_no_op_is_recorded_and_left_to_the_owner(caplog):
    """On an `rpc_failed` row a `no_matching_debit` is a TRUE page: that label is written only
    after a per-report attempt whose transport fault stopped it before any legacy fallback,
    so the credits really are owed — it is never read as a legacy charge."""
    FakeCreditService.outcome = {"outcome": "no_matching_debit", "refunded": 0}
    rows = [_claimed()]
    with caplog.at_level(logging.WARNING):
        out = await _pass(rows)
    assert out["settled"] == 0 and rows[0]["refund_outcome"] == "no_matching_debit"
    assert any(r.levelno == logging.ERROR and "REFUND LEAK" in r.getMessage()
               for r in caplog.records)
    assert FakeCreditService.ref_ids == ["NVDA:r1"], "never the legacy fallback"
    FakeCreditService.calls.clear()
    await _pass(rows)
    assert FakeCreditService.calls == []


# ── (c) what is never retried ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("label", [
    "refunded", "already_refunded", "manual", "legacy", "legacy_rpc_failed", "retry_rpc_failed",
    "no_matching_debit", "capped_to_zero", "partial", "unknown",
])
async def test_other_labels_are_never_retried(label):
    rows = [_claimed(refund_outcome=label)]
    assert await _pass(rows) == {"candidates": 0, "settled": 0}
    assert FakeCreditService.calls == [] and rows[0]["refund_outcome"] == label


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [
    {"is_refunded": False},                     # never claimed: the sweep's, not ours
    {"status": "processing"},                   # still in flight
    {"status": "completed"},
    {"credits_charged": 0},                     # nothing was charged
])
async def test_rows_that_are_not_owed_a_retry_are_never_touched(over):
    rows = [_claimed(**over)]
    assert await _pass(rows) == {"candidates": 0, "settled": 0}
    assert FakeCreditService.calls == []


@pytest.mark.asyncio
async def test_an_unstamped_claimed_row_past_the_grace_is_retried_with_a_null_cas():
    rows = [_claimed(refund_outcome=None)]
    out = await _pass(rows)
    assert out == {"candidates": 1, "settled": 1}
    assert FakeCreditService.ref_ids == ["NVDA:r1"]
    assert rows[0]["refund_outcome"] == "refunded"


@pytest.mark.asyncio
async def test_an_unstamped_row_inside_the_grace_window_is_left_alone():
    rows = [_claimed(refund_outcome=None, created_at=_FRESH)]
    assert await _pass(rows) == {"candidates": 0, "settled": 0}
    assert FakeCreditService.calls == []


@pytest.mark.asyncio
async def test_an_unstamped_row_of_any_age_is_re_checked():
    """No cutover instant: a row older than any deploy is selected like a new one — a refund
    that never landed (a SIGKILL between the claim and the RPC) is found whatever its age."""
    FakeCreditService.outcome = {"outcome": "refunded", "refunded": 20}
    rows = [_claimed(refund_outcome=None, created_at=_LONG_AGO)]
    assert await _pass(rows) == {"candidates": 1, "settled": 1}
    assert FakeCreditService.ref_ids == ["NVDA:r1"]
    assert rows[0]["refund_outcome"] == "refunded"


@pytest.mark.asyncio
@pytest.mark.parametrize("created_at", [_LONG_AGO, _OLD])
async def test_an_unstamped_row_charged_under_the_bare_ticker_reads_legacy_without_a_page(
        created_at, caplog):
    """A research_reports row exists only after its precharge committed, and every precharge
    since per-report refs is named by one: so an unstamped row whose per-report ref finds NO
    debit was charged under the bare ticker, whose refund only its CAS winner may send. It is
    recorded `legacy` (confirmed, like migration 195's backfill) with a WARNING — never a
    REFUND LEAK page, never the legacy fallback (it would pair a sibling's debit) — and it is
    not re-selected."""
    FakeCreditService.outcome = {"outcome": "no_matching_debit", "refunded": 0}
    rows = [_claimed(refund_outcome=None, created_at=created_at)]
    with caplog.at_level(logging.WARNING):
        out = await _pass(rows)
    assert out == {"candidates": 1, "settled": 0}, "an inference is not a proven refund"
    assert rows[0]["refund_outcome"] == recon.LABEL_LEGACY == "legacy"
    assert recon.refund_confirmed_from_label(rows[0]["refund_outcome"]) is True
    assert FakeCreditService.ref_ids == ["NVDA:r1"], "the legacy fallback must never run here"
    assert FakeCreditService.quiet == [True], "a re-check of an unstamped row must not page"
    assert FakeCreditService.quiet_transport == [True]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "a legacy charge is not a refund leak — it must not page"
    )
    assert any(r.levelno == logging.WARNING and "r1" in r.getMessage() and "legacy" in
               r.getMessage() for r in caplog.records)
    FakeCreditService.calls.clear()
    assert await _pass(rows) == {"candidates": 0, "settled": 0}
    assert FakeCreditService.calls == []


@pytest.mark.asyncio
async def test_the_legacy_inference_never_overwrites_a_label_written_meanwhile(monkeypatch):
    """The inference is not a proof, so it lands only on a row that is still unstamped: a
    claimer settling the same row right now (the grace runs from created_at) whose fallback
    failed has the truer word — `legacy_rpc_failed` must stand."""
    rows = [_claimed(refund_outcome=None)]

    class _ClaimerStampsFirst(FakeCreditService):
        def refund_ledgered(self, *a, **k):
            rows[0]["refund_outcome"] = "legacy_rpc_failed"
            return {"outcome": "no_matching_debit", "refunded": 0}

    monkeypatch.setattr(recon, "CreditService", _ClaimerStampsFirst)
    await _pass(rows)
    assert rows[0]["refund_outcome"] == "legacy_rpc_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_an_unstamped_rows_transport_fault_is_left_for_the_next_pass_without_a_page(
        raises, caplog):
    """Its claimer may still be settling it (the grace is measured from created_at, not from
    the claim), so a fault here is neither a REFUND LEAK page nor `retry_rpc_failed`: the row
    stays unstamped and the next pass re-checks it on the same ref."""
    FakeCreditService.outcome = None
    FakeCreditService.raise_on_refund = raises
    rows = [_claimed(refund_outcome=None)]
    with caplog.at_level(logging.WARNING):
        out = await _pass(rows)
    assert out == {"candidates": 1, "settled": 0}
    assert rows[0]["refund_outcome"] is None, "a transport fault decided the row's label"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(r.levelno == logging.WARNING and "r1" in r.getMessage() for r in caplog.records)
    # The next pass decides.
    FakeCreditService.raise_on_refund = False
    FakeCreditService.outcome = {"outcome": "already_refunded", "refunded": 0}
    assert await _pass(rows) == {"candidates": 1, "settled": 1}
    assert rows[0]["refund_outcome"] == "already_refunded"


@pytest.mark.asyncio
async def test_an_rpc_failed_row_of_any_age_is_retried_and_its_fault_still_pages(caplog):
    """`rpc_failed` keeps its single-retry bound whatever its age: a second transport fault
    is `retry_rpc_failed` and a REFUND LEAK page (the unstamped rule above does not apply)."""
    FakeCreditService.outcome = None
    rows = [_claimed(created_at=_LONG_AGO)]
    with caplog.at_level(logging.ERROR):
        assert (await _pass(rows))["candidates"] == 1
    assert rows[0]["refund_outcome"] == "retry_rpc_failed"
    assert any("REFUND LEAK" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_row_without_a_ticker_is_never_replayed_unpaired(caplog):
    rows = [_claimed(ticker="  ")]
    with caplog.at_level(logging.ERROR):
        out = await _pass(rows)
    assert out == {"candidates": 1, "settled": 0}
    assert FakeCreditService.calls == [], "an unpaired refund is not replay-safe"
    assert rows[0]["refund_outcome"] == "legacy_rpc_failed"
    assert any("REFUND LEAK" in r.getMessage() for r in caplog.records)
    FakeCreditService.calls.clear()
    assert (await _pass(rows))["candidates"] == 0, "it must not be re-selected every 5 minutes"


@pytest.mark.asyncio
async def test_a_row_without_a_user_is_never_refunded():
    rows = [_claimed(user_id=None)]
    out = await _pass(rows)
    assert out["settled"] == 0 and FakeCreditService.calls == []
    assert rows[0]["refund_outcome"] == "legacy_rpc_failed"


@pytest.mark.asyncio
async def test_one_bad_row_does_not_stop_the_batch():
    FakeCreditService.outcome_by_ref = {"NVDA:r1": None}
    rows = [_claimed(id="r1"), _claimed(id="r2"), _claimed(id="r3", ticker="")]
    out = await _pass(rows)
    assert out == {"candidates": 3, "settled": 1}
    assert [r["refund_outcome"] for r in rows] == ["retry_rpc_failed", "refunded",
                                                   "legacy_rpc_failed"]


# ── (d) the stamp is a CAS on the label read ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_label_changed_meanwhile_is_not_overwritten(monkeypatch):
    rows = [_claimed()]

    class _OwnerFixesItMeanwhile(FakeCreditService):
        def refund_ledgered(self, *a, **k):
            rows[0]["refund_outcome"] = "manual"     # the owner's hand correction lands now
            return {"outcome": "no_matching_debit", "refunded": 0}

    monkeypatch.setattr(recon, "CreditService", _OwnerFixesItMeanwhile)
    await _pass(rows)
    assert rows[0]["refund_outcome"] == "manual"


@pytest.mark.asyncio
async def test_a_concurrent_settlement_of_an_unstamped_row_wins(monkeypatch):
    """Another container's settlement stamps the row between this pass's read and its
    stamp: this pass's `already_refunded` says nothing new and never replaces a confirmed
    label, so that stamp stands."""
    rows = [_claimed(refund_outcome=None)]

    class _Concurrent(FakeCreditService):
        def refund_ledgered(self, *a, **k):
            rows[0]["refund_outcome"] = "refunded"
            return {"outcome": "already_refunded", "refunded": 0}

    monkeypatch.setattr(recon, "CreditService", _Concurrent)
    await _pass(rows)
    assert rows[0]["refund_outcome"] == "refunded"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,label", [
    ({"outcome": "refunded", "refunded": 20}, "refunded"),
    ({"outcome": "already_refunded", "refunded": 0}, "already_refunded"),
])
async def test_a_confirmed_retry_beats_a_concurrent_unconfirmed_stamp(answer, label, monkeypatch):
    """Two containers during a deploy overlap read the same `rpc_failed` row; the other's
    call faults and stamps `retry_rpc_failed` first. This pass's refund LANDED, so its
    confirmed label must still land: a CAS on the `rpc_failed` it read would miss and leave a
    false 'Refund pending' plus a REFUND LEAK page with the credits back."""
    rows = [_claimed()]

    class _OtherContainerFaultsFirst(FakeCreditService):
        def refund_ledgered(self, *a, **k):
            rows[0]["refund_outcome"] = "retry_rpc_failed"
            return answer

    monkeypatch.setattr(recon, "CreditService", _OtherContainerFaultsFirst)
    out = await _pass(rows)
    assert out == {"candidates": 1, "settled": 1}
    assert rows[0]["refund_outcome"] == label


@pytest.mark.asyncio
async def test_a_confirmed_retry_still_never_overwrites_an_owner_correction(monkeypatch):
    rows = [_claimed()]

    class _OwnerFixesItMeanwhile(FakeCreditService):
        def refund_ledgered(self, *a, **k):
            rows[0]["refund_outcome"] = "manual"
            return {"outcome": "already_refunded", "refunded": 0}

    monkeypatch.setattr(recon, "CreditService", _OwnerFixesItMeanwhile)
    await _pass(rows)
    assert rows[0]["refund_outcome"] == "manual"


# ── (e) a database without migration 195, and lookup failures ──────────────────────────

@pytest.mark.asyncio
async def test_while_the_column_is_latched_missing_the_pass_does_nothing():
    recon.note_refund_outcome_column_missing("test")
    queried = []

    class _Spy(FakeSupabase):
        def table(self, name):
            queried.append(name)
            return super().table(name)

    out = await recon.retry_unconfirmed_refunds_once(now=_NOW, supabase=_Spy([_claimed()]))
    assert out == {"candidates": 0, "settled": 0}
    assert queried == [] and FakeCreditService.calls == []


@pytest.mark.asyncio
async def test_a_missing_column_on_the_lookup_latches_and_refunds_nothing(caplog):
    def _raise(op, _values):
        return postgrest_error("42703", "column research_reports.refund_outcome does not exist") \
            if op == "select" else None

    with caplog.at_level(logging.WARNING):
        out = await _pass([_claimed()], raise_on=_raise)
    assert out == {"candidates": 0, "settled": 0} and FakeCreditService.calls == []
    assert not recon.refund_outcome_column_available()
    assert any("migration 195" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_transient_lookup_failure_is_a_warning_and_never_latches(caplog):
    def _raise(op, _values):
        return gateway_error(520) if op == "select" else None

    with caplog.at_level(logging.WARNING):
        out = await _pass([_claimed()], raise_on=_raise)
    assert out == {"candidates": 0, "settled": 0}
    assert recon.refund_outcome_column_available()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "a gateway blip must not page — the next pass retries"
    )


@pytest.mark.asyncio
async def test_an_empty_pass_is_quiet():
    assert await _pass([]) == {"candidates": 0, "settled": 0}


def test_the_loop_runs_the_retry_after_the_sweep_in_its_own_try():
    import app.main as main

    src = inspect.getsource(main._run_research_reconciliation_job)
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    sweep, retry = code.index("await sweep_once()"), code.index("await retry_unconfirmed_refunds_once()")
    assert sweep < retry
    # Separate try blocks: a failing retry must never cost the sweep its tick (or vice versa).
    between = code[sweep:retry]
    assert "except Exception" in between and "try:" in between


# ── the REAL refund_ledgered: an unstamped re-check must not page (2026-10-12 review) ───────
#
# Every retry test above fakes `refund_ledgered`, so an ERROR logged INSIDE credit_service was
# invisible to them. These drive the real method with only `supabase.rpc` faked.

class _RealCredits:
    """`CreditService` with the real `refund_ledgered` and a fake RPC."""

    answer = None          # dict → the RPC's data; an Exception instance → raised by execute()

    def __new__(cls):
        from app.services.credit_service import CreditService as _Real

        svc = _Real.__new__(_Real)

        class _Exec:
            def execute(self_inner):
                if isinstance(cls.answer, Exception):
                    raise cls.answer
                return type("R", (), {"data": cls.answer})()

        class _Sb:
            def rpc(self_inner, *_a, **_k):
                return _Exec()

        svc.supabase = _Sb()
        return svc


@pytest.fixture
def _no_sync_backoff(monkeypatch):
    from app.utils import supabase_errors as se
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)


def _credit_service_errors(caplog):
    """ERROR records raised INSIDE credit_service (not the retry's own log lines)."""
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.ERROR and r.name == "app.services.credit_service"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,label", [
    (lambda: {"outcome": "no_matching_debit", "refunded": 0, "spendable": 10}, "legacy"),
    (lambda: gateway_error(520), None),          # a TRANSIENT fault: the next pass re-checks
])
async def test_an_unstamped_re_check_never_pages_from_inside_refund_ledgered(
        answer, label, monkeypatch, caplog, _no_sync_backoff):
    _RealCredits.answer = answer()
    monkeypatch.setattr(recon, "CreditService", _RealCredits)
    rows = [_claimed(refund_outcome=None)]
    with caplog.at_level(logging.INFO):
        await _pass(rows)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"an unstamped re-check paged: {errors}"
    assert rows[0]["refund_outcome"] == label


@pytest.mark.asyncio
async def test_an_unstamped_re_checks_non_transient_failure_still_pages(
        monkeypatch, caplog, _no_sync_backoff):
    """Quiet covers only a TRANSIENT fault. A failure that will repeat on every pass (here a
    plain RuntimeError, which the classifier does not call transient) would otherwise hide a
    refund that is really owed — e.g. a claimer killed between its claim and its refund —
    behind a WARNING every 5 minutes forever."""
    _RealCredits.answer = RuntimeError("permission denied for function refund_credits")
    monkeypatch.setattr(recon, "CreditService", _RealCredits)
    rows = [_claimed(refund_outcome=None)]
    with caplog.at_level(logging.INFO):
        await _pass(rows)
    assert any("REFUND LEAK" in m for m in _credit_service_errors(caplog))
    assert rows[0]["refund_outcome"] is None, "left for the next pass, never a guessed label"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    lambda: {"outcome": "no_matching_debit", "refunded": 0, "spendable": 10},
    lambda: gateway_error(520),
])
async def test_a_known_failed_refund_still_pages_from_inside_refund_ledgered(
        answer, monkeypatch, caplog, _no_sync_backoff):
    """The quiet flags are for a RE-CHECK only: an `rpc_failed` row's miss or fault is owed
    credits and must still reach Sentry FROM credit_service (the retry's own "REFUND LEAK:
    refund retry" line does not count — it would hide a quieted inner page)."""
    _RealCredits.answer = answer()
    monkeypatch.setattr(recon, "CreditService", _RealCredits)
    with caplog.at_level(logging.INFO):
        await _pass([_claimed()])
    assert any("REFUND LEAK" in m for m in _credit_service_errors(caplog))
