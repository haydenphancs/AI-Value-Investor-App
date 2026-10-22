"""
Tests for CreditService — the atomic charge + refund layer that
backs the Generate Analysis 5-credit lifecycle.

Atomicity is enforced by the `charge_user_credits` Postgres function
(migration 041), so these tests verify the Python wrapper's contract
with Supabase RPC: the right RPC is called with the right args, and
the wrapper translates the response (None vs int) into the right
return value.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import logging

import pytest

from app.services.credit_service import CreditService, CreditServiceUnavailable


@pytest.fixture
def service():
    # CreditService.__init__ touches Supabase env — bypass it and
    # inject a mock client.
    svc = CreditService.__new__(CreditService)
    svc.supabase = MagicMock()
    return svc


def _stub_rpc(service, return_value):
    """Wire `service.supabase.rpc(...).execute()` to return a mock
    whose `.data` attribute equals `return_value`."""
    rpc_call = MagicMock()
    rpc_call.execute.return_value = MagicMock(data=return_value)
    service.supabase.rpc.return_value = rpc_call


def test_try_charge_succeeds_with_sufficient_balance(service):
    # Postgres function returns the new `remaining` (e.g. 25) when
    # the user had enough credits.
    _stub_rpc(service, 25)

    result = service.try_charge("user-123", amount=5)

    assert result == 25
    service.supabase.rpc.assert_called_once_with(
        "charge_user_credits",
        {"p_user_id": "user-123", "p_amount": 5},
    )


def test_try_charge_fails_atomically_when_below_threshold(service):
    # Postgres function returns NULL when the WHERE predicate
    # `(total - used) >= amount` doesn't match — no row updated.
    _stub_rpc(service, None)

    result = service.try_charge("user-123", amount=5)

    assert result is None
    service.supabase.rpc.assert_called_once()


def test_try_charge_uses_default_report_cost():
    svc = CreditService.__new__(CreditService)
    svc.supabase = MagicMock()
    _stub_rpc(svc, 10)

    svc.try_charge("user-x")

    args, _ = svc.supabase.rpc.call_args
    assert args[0] == "charge_user_credits"
    # Default amount tracks the config-driven report cost (unified scale = 20),
    # asserted against the constant so a future re-price updates in one place.
    assert args[1]["p_amount"] == CreditService.DEEP_RESEARCH_COST


def test_refund_increments_back_5(service):
    # refund_user_credits returns the post-refund `remaining`.
    _stub_rpc(service, 30)

    result = service.refund("user-123", amount=5)

    assert result == 30
    service.supabase.rpc.assert_called_once_with(
        "refund_user_credits",
        {"p_user_id": "user-123", "p_amount": 5},
    )


def test_concurrent_charges_only_one_succeeds_when_balance_is_5():
    # Atomicity is delegated to Postgres. Simulate the race: two
    # concurrent calls, the DB approves the first (returns the new
    # remaining = 0), rejects the second (returns NULL because the
    # predicate no longer holds). The Python wrapper must surface
    # both outcomes faithfully so the endpoint returns
    # INSUFFICIENT_CREDITS to the loser.
    svc1 = CreditService.__new__(CreditService)
    svc1.supabase = MagicMock()
    svc2 = CreditService.__new__(CreditService)
    svc2.supabase = MagicMock()

    _stub_rpc(svc1, 0)      # winner — DB updated, remaining=0
    _stub_rpc(svc2, None)   # loser — DB rejected, no row updated

    a = svc1.try_charge("user-shared", amount=5)
    b = svc2.try_charge("user-shared", amount=5)

    assert a == 0
    assert b is None


def test_try_charge_raises_when_rpc_throws(service):
    # Network blip / RLS deny / table missing is a TRANSIENT/system failure —
    # distinct from a genuine insufficient balance (which the RPC signals by
    # returning NULL, i.e. result.data is None). try_charge raises
    # CreditServiceUnavailable so the endpoint surfaces a RETRYABLE SYSTEM_BUSY,
    # never a dead-end INSUFFICIENT_CREDITS: a DB blip must not tell a paying
    # user they're broke. (Previously this returned None → masqueraded as
    # "out of credits".)
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = RuntimeError("supabase down")
    service.supabase.rpc.return_value = rpc_call

    with pytest.raises(CreditServiceUnavailable):
        service.try_charge("user-123", 5)


# ── precharge / refund_ledgered — the unified gate (migration 101 RPCs) ─────

def test_precharge_calls_spend_credits_and_returns_remaining(service):
    _stub_rpc(service, 40)
    result = service.precharge("user-1", 20, reason="report_charge", ref_id="AAPL")
    assert result == 40
    service.supabase.rpc.assert_called_once_with(
        "spend_credits",
        {"p_user_id": "user-1", "p_amount": 20,
         "p_reason": "report_charge", "p_ref_id": "AAPL"},
    )


def test_precharge_returns_none_on_insufficient(service):
    # spend_credits returns NULL when the balance is short → caller maps to 402.
    _stub_rpc(service, None)
    assert service.precharge("u", 20, reason="report_charge", ref_id="AAPL") is None


def test_precharge_raises_unavailable_on_rpc_error(service):
    # Transient failure must raise (→ retryable SYSTEM_BUSY), never look like 0/None.
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = RuntimeError("supabase down")
    service.supabase.rpc.return_value = rpc_call
    with pytest.raises(CreditServiceUnavailable):
        service.precharge("u", 20, reason="report_charge", ref_id="AAPL")


def test_refund_ledgered_calls_refund_credits(service):
    # migration 142: the RPC returns {outcome, refunded, spendable}, not a bare spendable.
    _stub_rpc(service, {"outcome": "refunded", "refunded": 20, "spendable": 60})
    result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    assert result == {"outcome": "refunded", "refunded": 20, "spendable": 60}
    service.supabase.rpc.assert_called_once_with(
        "refund_credits",
        {"p_user_id": "u", "p_amount": 20,
         "p_reason": "report_refund", "p_ref_id": "AAPL"},
    )


def test_refund_ledgered_never_raises_on_rpc_error(service):
    # Best-effort: a refund RPC failure logs a REFUND LEAK marker but must NOT raise
    # (it must not affect the already-failed action's response).
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = RuntimeError("supabase down")
    service.supabase.rpc.return_value = rpc_call
    assert service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL") is None


# ── migration 142: a refund that moved nothing must be distinguishable ─────────────────
#
# Before 142 every branch of `refund_credits` returned `spendable`, so a refund that moved ZERO
# looked exactly like one that worked and `refund_ledgered` logged "Refunded 20 credits ..."
# regardless. All three report sites burn the one-shot `is_refunded` CAS BEFORE refunding, so
# the user permanently loses the credits — and iOS then shows a "[Refunded]" chip off that same
# flag. The only signal was a Postgres RAISE WARNING that PostgREST never surfaces.


@pytest.mark.parametrize(
    "body",
    [
        "ok",                                  # a string where the envelope should be
        ["refunded"],                          # a list
        {"refunded": 20, "spendable": 60},     # the envelope minus its verdict
        {"outcome": None},                     # the key present, the value absent
        {"outcome": "brand_new_outcome", "refunded": 20},
    ],
)
def test_an_unrecognised_rpc_body_is_a_leak_not_a_success(service, caplog, body):
    """🔴 Until 2026-09-10 every one of these fell through to the final `else` and logged
    "Refunded None credits" at INFO — a success message for a refund nothing proved happened,
    on a path where the one-shot `is_refunded` CAS is already spent. The three report call
    sites then asked `refund_did_not_happen`, whose deny-list said "it happened", so the user
    silently lost the credits and no alert fired. Both halves are closed: this pins the log."""
    _stub_rpc(service, body)
    with caplog.at_level(logging.INFO):
        out = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an unrecognised refund outcome logged no ERROR — it cannot reach Sentry"
    msg = errors[0].getMessage()
    assert "REFUND LEAK" in msg, f"missing the greppable marker: {msg!r}"
    assert "unrecognised" in msg and "AAPL" in msg and "20" in msg, msg
    # And NOT the success line — the two must be mutually exclusive.
    assert not any("Refunded" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records), (
        "the success INFO line still fires for an unproven refund"
    )
    # The return value is what the call sites hand to the predicate; the two must agree.
    from app.services.credit_service import refund_did_not_happen
    assert refund_did_not_happen(out) is True, (
        f"refund_ledgered returned {out!r} and the predicate called it settled"
    )


def test_a_refund_that_worked_still_logs_the_success_line_not_a_leak(service, caplog):
    """Anti-vacuity for the test above: the new arm must not swallow the real success path."""
    _stub_rpc(service, {"outcome": "refunded", "refunded": 20, "spendable": 60})
    with caplog.at_level(logging.INFO):
        service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("Refunded 20 credits" in r.getMessage() for r in caplog.records)


def test_a_refund_that_moved_nothing_is_reported_as_an_error(service, caplog):
    """THE regression guard. `no_matching_debit` means the user is owed credits and the
    one-shot guard is spent — it must reach Sentry, which means logger.ERROR."""
    _stub_rpc(service, {"outcome": "no_matching_debit", "refunded": 0, "spendable": 140})
    with caplog.at_level(logging.ERROR):
        service.refund_ledgered("u", 20, reason="report_refund", ref_id="MISMATCH")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a refund that moved nothing logged no ERROR — it cannot reach Sentry"
    msg = errors[0].getMessage()
    assert "REFUND LEAK" in msg, f"missing the greppable marker: {msg!r}"
    assert "MISMATCH" in msg and "20" in msg, f"missing ids for a manual correction: {msg!r}"


def test_an_already_refunded_charge_does_NOT_page_anyone(service, caplog):
    """The other half, and it matters as much. A replayed settlement is benign; escalating it
    would trade a silent-money bug for alert fatigue, which is how the loud one gets ignored."""
    _stub_rpc(service, {"outcome": "already_refunded", "refunded": 0, "spendable": 140})
    with caplog.at_level(logging.DEBUG):
        service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "an idempotent replay raised an ERROR — Sentry captures at event_level=ERROR, so this "
        "would page on every double-settled report"
    )
    assert any("already" in r.getMessage().lower() for r in caplog.records)


def test_a_partial_refund_does_not_claim_the_full_amount(service, caplog):
    """The LEAST() caps can move less than requested; the old log printed the REQUESTED amount
    and read as a clean refund. Worse, it stayed `outcome: refunded`, which every report site
    reads as SETTLED: a 1-of-20 refund across a monthly reset told the phone "your 20 credits
    have been returned" and paged nobody. A partial refund is a leak: ERROR + `partial`."""
    from app.services.credit_service import refund_did_not_happen

    _stub_rpc(service, {"outcome": "refunded", "refunded": 5, "spendable": 105})
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and "REFUND LEAK" in errors[0] and "PARTIAL REFUND" in errors[0], (
        f"a 5-of-20 refund was not flagged as a leak: {errors!r}"
    )
    assert "15" in errors[0], "the leak line must say how much the user is still owed"
    assert result["outcome"] == "partial" and result["requested"] == 20 and result["refunded"] == 5
    assert refund_did_not_happen(result) is True, (
        "a partial refund must read as NOT settled at every report site"
    )


def test_a_zero_refund_with_no_ref_id_is_still_only_a_warning(service, caplog):
    """The RPC's deliberate `NOT v_searched` shape: nothing to match, so 'owed' is unprovable.
    Escalating it would page on every legacy no-ref_id caller."""
    _stub_rpc(service, {"outcome": "refunded", "refunded": 0, "spendable": 105})
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("PARTIAL REFUND" in r.getMessage() for r in caplog.records)
    assert result["outcome"] == "refunded"


def test_a_full_refund_is_not_flagged_partial(service, caplog):
    """Control: `refunded == amount` stays a clean INFO settlement."""
    _stub_rpc(service, {"outcome": "refunded", "refunded": 20, "spendable": 105})
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert result["outcome"] == "refunded"


def test_a_charge_a_clawback_covered_is_settled_and_never_pages(service, caplog):
    """Migration 200 (IAP audit F15): the charge came out of a subscription period Apple refunded
    and its clawback already wrote the spend off. Nothing is owed — before 200 the floor paid it
    back a second time, or (after a keyed clawback started P2) it paged a false OWED."""
    from app.services.credit_service import refund_did_not_happen

    _stub_rpc(service, {"outcome": "covered_by_clawback", "refunded": 0, "covered": 20, "spendable": 0})
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL:r")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("covered=20" in r.getMessage() for r in caplog.records)
    assert result["outcome"] == "covered_by_clawback"
    assert refund_did_not_happen(result) is False


def test_a_partly_covered_charge_whose_rest_was_capped_is_a_partial_leak(service, caplog):
    """200: 15 covered, the other 5 capped to zero. The RPC wrote the pairing row, so a replay
    answers already_refunded — the label must be `partial`, which that replay never overwrites."""
    from app.services.credit_service import refund_did_not_happen

    _stub_rpc(service, {"outcome": "capped_to_zero", "refunded": 0, "covered": 15, "spendable": 50})
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL:r")
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "REFUND LEAK: PARTIAL REFUND" in errors[0], errors
    assert "covered 15 of 20" in errors[0] and "OWED 5 credits" in errors[0], errors[0]
    assert result["outcome"] == "partial" and result["requested"] == 20
    assert refund_did_not_happen(result) is True


@pytest.mark.parametrize("refunded,covered,owed", [(5, 15, None), (3, 15, 2), (20, 0, None)])
def test_a_refund_plus_what_a_clawback_covered_decides_partial(service, caplog, refunded, covered, owed):
    """200: `refunded + covered == amount` is whole; short of it, the rest is owed."""
    from app.services.credit_service import refund_did_not_happen

    payload = {"outcome": "refunded", "refunded": refunded, "spendable": 5}
    if covered:
        payload["covered"] = covered
    _stub_rpc(service, payload)
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL:r")
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    if owed is None:
        assert not errors and result["outcome"] == "refunded"
        assert refund_did_not_happen(result) is False
    else:
        assert errors and f"OWED {owed} credits" in errors[0], errors
        assert result["outcome"] == "partial" and refund_did_not_happen(result) is True


def test_a_short_replay_is_a_partial_leak_not_a_benign_replay(service, caplog):
    """Migration 200: a lost reply's retry answers already_refunded; when the earlier refund settled
    less than the charge (`short`), the rest is still owed — relabelled `partial`, paged."""
    from app.services.credit_service import refund_did_not_happen

    _stub_rpc(service, {"outcome": "already_refunded", "refunded": 0, "short": 5, "spendable": 50})
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL:r")
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "REFUND LEAK: PARTIAL REFUND" in errors[0] and "OWED 5 credits" in errors[0]
    assert result["outcome"] == "partial" and refund_did_not_happen(result) is True


@pytest.mark.parametrize("short", [None, 0, -1, True, "5"])
def test_a_replay_with_no_usable_short_stays_benign(service, caplog, short):
    payload = {"outcome": "already_refunded", "refunded": 0, "spendable": 50}
    if short is not None:
        payload["short"] = short
    _stub_rpc(service, payload)
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL:r")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert result["outcome"] == "already_refunded"


@pytest.mark.parametrize("bad", [True, "15", -3, 15.0, None])
def test_a_malformed_covered_key_never_makes_a_short_refund_look_whole(service, caplog, bad):
    _stub_rpc(service, {"outcome": "refunded", "refunded": 5, "covered": bad, "spendable": 5})
    with caplog.at_level(logging.DEBUG):
        result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL:r")
    assert result["outcome"] == "partial"
    assert any("OWED 15 credits" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)


def test_the_pre_142_integer_contract_still_works(service):
    """Deploy order is code-then-migration, so this method MUST tolerate the old bare-integer
    return. Without it, `int(dict)`'s mirror — a dict-expecting parse of an int — would break
    every refund in the gap."""
    _stub_rpc(service, 60)
    result = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    assert result["spendable"] == 60 and result["outcome"] == "refunded"
    assert result["refunded"] is None, "an unknown moved-amount must not be guessed at"


def test_try_charge_returns_none_only_for_genuine_insufficiency(service):
    # The OTHER None path must stay None: when the RPC succeeds but returns
    # NULL (balance below threshold, no row mutated), try_charge returns None
    # and the endpoint maps it to INSUFFICIENT_CREDITS. This is the case that
    # must NOT be conflated with the transient-failure raise above.
    _stub_rpc(service, None)

    assert service.try_charge("user-123", 5) is None


def test_refund_returns_none_when_rpc_throws(service):
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = RuntimeError("supabase down")
    service.supabase.rpc.return_value = rpc_call

    assert service.refund("user-123", 5) is None


# ── stock_id is validated BEFORE the 20-credit precharge ──────────────────────────────


def test_an_unusable_stock_id_is_rejected_before_any_charge():
    """An empty/blank ticker used to be charged and then refunded with ref_id='' — and
    `refund_credits` gates its entire split lookup on `p_ref_id <> ''`, so that refund could
    never find the debit's recorded granted/purchased split and always took the granted-first
    fallback, converting PERMANENT purchased credits into expiring ones (Guideline 3.1.1).
    It also burned a MAX_CONCURRENT_REPORTS_PER_USER slot for the whole close cycle.
    """
    import pytest
    from pydantic import ValidationError

    from app.schemas.research import GenerateResearchRequest

    for bad in ["", "   ", "\t", "A" * 13, "AA PL", "AA;PL", "../../etc", "AA\nPL"]:
        with pytest.raises(ValidationError):
            GenerateResearchRequest(stock_id=bad, investor_persona="warren_buffett")


def test_real_tickers_still_validate():
    """Must not reject what the shipped client sends: dots and hyphens are real (BRK.B, RDS-A)."""
    from app.schemas.research import GenerateResearchRequest

    for good in ["AAPL", "aapl", "BRK.B", "RDS-A", "F", "GOOGL"]:
        req = GenerateResearchRequest(stock_id=good, investor_persona="warren_buffett")
        assert req.stock_id == good


# ── The insert-failure orphan claim must name the row it created ─────────────
#
# When the `research_reports` insert reports failure AFTER the 20-credit precharge, we
# cannot know whether Postgres committed (a Cloudflare 520 or a read timeout lands here
# with the row PRESENT). The endpoint therefore claims the row before refunding, so the
# reconciliation sweep can never refund it a second time.
#
# That claim used to select on (user_id, ticker, investor_persona, is_refunded=False,
# status ∈ pending/processing, created_at >= cycle_start) — which cannot distinguish
# the row THIS call tried to create from any OTHER live report the same user has for
# the same pair. Nothing forbids two: the client caps only the NUMBER of concurrent
# generations, and Retry regenerates the same (ticker, persona). So a failed second
# insert claimed the FIRST, HEALTHY row, marked it failed + refunded — its worker's
# conditional completion write then no-op'd and the report was dropped — while the
# refund paid exactly ONE cost regardless of how many rows matched. Net on a two-row
# match: 40 charged, 20 returned, zero reports.


def test_the_orphan_claim_is_scoped_to_a_single_row_id():
    import inspect
    import re

    from app.api.v1.endpoints import research

    src = inspect.getsource(research.generate_research_report)
    # The claim is the update that sets both status='failed' and is_refunded=True.
    start = src.index('.update({"status": "failed", "is_refunded": True})')
    claim = src[start:start + 600]

    assert '.eq("id", new_report_id)' in claim, (
        "the insert-failure claim must name the row this call minted, or it can seize "
        "a different in-flight report for the same (user, ticker, persona)"
    )
    for broad in ('.eq("ticker"', '.eq("investor_persona"', '.gte("created_at"'):
        assert broad not in claim, (
            f"{broad} is a heuristic filter, not an identity — it is what let the claim "
            "match somebody else's live row"
        )
    # And the id must actually be pre-minted rather than left to the column default.
    assert re.search(r"new_report_id = str\(uuid\.uuid4\(\)\)", src)
    assert '"id": new_report_id' in src


def test_the_worker_task_handle_is_retained():
    """`asyncio.create_task` keeps only a WEAK reference, so an untracked task can be
    collected mid-execution. This one owns a report the user has already paid 20
    credits for, and if it dies before `processing_started_at` is stamped the sweep
    will not reconsider the row for ~3.2 hours."""
    import inspect

    from app.api.v1.endpoints import research

    src = inspect.getsource(research.generate_research_report)
    assert "_RESEARCH_TASKS.add(task)" in src
    assert "add_done_callback(_RESEARCH_TASKS.discard)" in src
    assert isinstance(research._RESEARCH_TASKS, set)


# ── 2026-10-12: ChargeRef — one debit per ref, and only then a retried refund ────────────


def _flaky_rpc(service, side_effect):
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = side_effect
    service.supabase.rpc.return_value = rpc_call
    return rpc_call


@pytest.fixture
def _no_backoff(monkeypatch):
    from app.utils import supabase_errors as se
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)


def test_a_charge_ref_refund_is_retried_on_a_transient_error_and_converges(service, caplog, _no_backoff):
    """The first call committed but its reply was lost (an edge 520); the replay answers
    `already_refunded` — the refund landed, nothing pages."""
    from app.services.credit_service import ChargeRef, refund_did_not_happen
    from tests.test_supabase_transient_classifier import gateway_error

    rpc = _flaky_rpc(service, [
        gateway_error(520),
        MagicMock(data={"outcome": "already_refunded", "refunded": 0, "spendable": 60}),
    ])
    with caplog.at_level(logging.DEBUG):
        out = service.refund_ledgered("u", 20, reason="report_refund", ref_id=ChargeRef("AAPL:r1"))
    assert rpc.execute.call_count == 2, "a ChargeRef refund was not retried"
    assert out["outcome"] == "already_refunded" and not refund_did_not_happen(out)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "a converged retry paged"


def test_a_shared_ref_refund_is_never_retried(service, caplog, _no_backoff):
    """A plain str (a legacy bare ticker) may name SEVERAL debits; replayed, it would reverse
    a sibling's. Single-shot, and the leak is loud."""
    from tests.test_supabase_transient_classifier import gateway_error

    rpc = _flaky_rpc(service, [
        gateway_error(520),
        MagicMock(data={"outcome": "refunded", "refunded": 20, "spendable": 60}),
    ])
    with caplog.at_level(logging.ERROR):
        out = service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    assert rpc.execute.call_count == 1
    assert out is None
    assert any("REFUND LEAK" in r.getMessage() and "AAPL" in r.getMessage() for r in caplog.records)


def test_a_rebuilt_ref_string_is_never_retried(service, _no_backoff):
    """Fail-safe by construction: any str operation drops the ChargeRef type."""
    from app.services.credit_service import ChargeRef
    from tests.test_supabase_transient_classifier import gateway_error

    rebuilt = ChargeRef("aapl:r1").upper()
    assert type(rebuilt) is str
    rpc = _flaky_rpc(service, [gateway_error(520), MagicMock(data={"outcome": "refunded"})])
    assert service.refund_ledgered("u", 20, reason="report_refund", ref_id=rebuilt) is None
    assert rpc.execute.call_count == 1


def test_a_non_transient_error_is_not_retried_even_for_a_charge_ref(service, _no_backoff):
    from app.services.credit_service import ChargeRef

    rpc = _flaky_rpc(service, [RuntimeError("boom"), MagicMock(data={"outcome": "refunded"})])
    assert service.refund_ledgered("u", 20, reason="report_refund", ref_id=ChargeRef("AAPL:r1")) is None
    assert rpc.execute.call_count == 1


def test_a_persistent_transient_error_is_bounded_and_pages(service, caplog, _no_backoff):
    from app.services.credit_service import ChargeRef
    from tests.test_supabase_transient_classifier import gateway_error

    rpc = _flaky_rpc(service, [gateway_error(520)] * 10)
    with caplog.at_level(logging.ERROR):
        out = service.refund_ledgered("u", 20, reason="report_refund", ref_id=ChargeRef("AAPL:r1"))
    assert out is None and rpc.execute.call_count == 3, "the retry must stay bounded"
    msg = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR][0]
    assert "REFUND LEAK" in msg and "AAPL:r1" in msg and "after transient retries" in msg


def test_the_precharge_is_never_retried_even_for_a_charge_ref(service, _no_backoff):
    """`spend_credits` is NOT idempotent: a replay of a committed charge debits twice."""
    from app.services.credit_service import ChargeRef
    from tests.test_supabase_transient_classifier import gateway_error

    rpc = _flaky_rpc(service, [gateway_error(520), MagicMock(data=40)])
    with pytest.raises(CreditServiceUnavailable):
        service.precharge("u", 20, reason="report_charge", ref_id=ChargeRef("AAPL:r1"))
    assert rpc.execute.call_count == 1


@pytest.mark.asyncio
async def test_a_refund_on_the_event_loop_trips_the_warning(service, caplog):
    _stub_rpc(service, {"outcome": "refunded", "refunded": 20, "spendable": 60})
    with caplog.at_level(logging.WARNING):
        service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
        _stub_rpc(service, 40)
        service.precharge("u", 20, reason="report_charge", ref_id="AAPL")
    hits = [r for r in caplog.records if "EVENT LOOP" in r.getMessage()]
    assert len(hits) == 2 and hits[0].levelno == logging.WARNING


def test_off_the_loop_the_tripwire_is_silent(service, caplog):
    _stub_rpc(service, {"outcome": "refunded", "refunded": 20, "spendable": 60})
    with caplog.at_level(logging.WARNING):
        service.refund_ledgered("u", 20, reason="report_refund", ref_id="AAPL")
    assert not [r for r in caplog.records if "EVENT LOOP" in r.getMessage()]


def test_charge_refs_are_unique_and_parse_back_to_the_ticker():
    from app.services.credit_history_service import _ticker_from_report_ref
    from app.services.credit_service import (
        ChargeRef, chat_turn_ref, direct_report_charge_ref, report_charge_ref,
    )

    deep = report_charge_ref(" nvda ", "ABC-uuid")
    assert deep == "NVDA:abc-uuid" and isinstance(deep, ChargeRef)
    one, two = (direct_report_charge_ref("aapl", "warren_buffett") for _ in range(2))
    assert one != two
    import re
    for ref in (one, two):
        assert isinstance(ref, ChargeRef)
        assert re.match(r"^AAPL:warren_buffett:[0-9a-f]{32}$", ref), ref
    chat = chat_turn_ref("s")
    assert chat.startswith("s:") and isinstance(chat, ChargeRef)
    assert chat_turn_ref("s") != chat
    for ref, ticker in ((deep, "NVDA"), (one, "AAPL"), (report_charge_ref("BRK.B", "x"), "BRK.B")):
        assert _ticker_from_report_ref(ref) == ticker
    # Fail-safe: string operations and formatting yield plain str (never retried).
    assert type(deep.upper()) is str and type(f"{deep}") is str and type(str(deep)) is str
    assert type(deep[:4]) is str and type(deep + "x") is str


@pytest.mark.parametrize("ticker,report_id", [("", "r1"), ("  ", "r1"), (None, "r1"),
                                              ("AAPL", ""), ("AAPL", None), ("AAPL", "  ")])
def test_a_report_charge_ref_refuses_a_missing_half(ticker, report_id):
    """`":r1"` or `"AAPL:"` would be a ref nothing else rebuilds — fail loudly instead."""
    from app.services.credit_service import report_charge_ref

    with pytest.raises(ValueError):
        report_charge_ref(ticker, report_id)


@pytest.mark.parametrize("ticker", ["", "   ", None])
def test_a_direct_charge_ref_refuses_an_empty_ticker(ticker):
    from app.services.credit_service import direct_report_charge_ref

    with pytest.raises(ValueError):
        direct_report_charge_ref(ticker, "warren_buffett")
