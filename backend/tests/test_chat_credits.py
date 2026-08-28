"""Credit-metering tests for the Ask Cay AI chat gate.

The send (non-stream) and stream endpoints share the same two primitives, so
they're tested directly here — that's where the risk lives:

  * `_claim_chat_quota` — the guest-vs-authenticated branch, and the 402
    (insufficient) / 409 (transient) mapping. Authed users are charged
    CHAT_CREDIT_COST via the gate; guests use the daily-turn budget (never
    credits — `user_credits` is FK-bound to `public.users`, so a per-install id has no wallet).
  * `_ChatQuota.refund_once` — the at-most-once refund guarantee the finally
    backstop AND all three stream error sites depend on (the underlying
    refund_credits RPC is NOT idempotent).
  * the credit `ref_id` being unique PER TURN. It used to be the bare session id, so
    every turn in one conversation wrote an identical `(ref_id, delta)` ledger row and a
    refund could pair to a sibling turn's debit — adopting its granted/purchased split.

CreditService and the daily-turn budget helpers are mocked — no Supabase/Gemini.
"""

import json
from unittest.mock import MagicMock

import pytest

import app.api.v1.endpoints.chat as chat
from app.api.error_response import ErrorCode
from app.config import settings
from app.dependencies import GUEST_USER_ID
from app.services.credit_service import CreditServiceUnavailable

AUTHED = {"id": "authed-user-1"}
# `is_guest` is what marks a guest now, NOT the id. Migration 111 gives each install its own
# uuid5, so `user["id"] == GUEST_USER_ID` is never true for a real guest and the old shape
# would have sent them into the credit precharge — 402 on a feature that is free for them.
GUEST = {"id": "1cd2b2c4-288b-5c9b-bfe1-154c70266a3f", "is_guest": True}


@pytest.fixture(autouse=True)
def _no_live_free_followup_claim(monkeypatch):
    """`_claim_chat_quota` calls the REAL `claim_free_followup` unless it is stubbed.

    WHY THIS IS AUTOUSE AND NOT OPTIONAL. That method issues
    `supabase.rpc("claim_free_followup", ...)` — a SECURITY DEFINER function with
    `row_security = off` that UPDATEs `chat_sessions` (migration 154). Every test in
    this file was reaching it against PRODUCTION on each run, and passing only because
    `session_id="sess-1"` is not a UUID: Postgres rejected the cast, the RPC errored,
    and `claim_free_followup` fails CLOSED to "charge this turn". So the money-path
    assertions below were exercising the ERROR branch, not the intended one — and a
    single `"sess-1"` → real-UUID edit would have started mutating production rows.

    Default False = "no free allowance", which is the state every existing test here
    assumes. `_grant_free_followup` below opts a test into the True branch explicitly.
    """
    budget = MagicMock()
    budget.claim_free_followup.return_value = False
    monkeypatch.setattr(chat, "get_chat_budget_service", lambda: budget)
    return budget


def _grant_free_followup(monkeypatch):
    """Opt into the earned-free-turn branch — the one the live call could never reach
    deterministically. Returns the stub so callers can assert on it."""
    budget = MagicMock()
    budget.claim_free_followup.return_value = True
    monkeypatch.setattr(chat, "get_chat_budget_service", lambda: budget)
    return budget


def _patch_credit(monkeypatch, *, precharge_return=100, precharge_raises=False):
    inst = MagicMock()
    if precharge_raises:
        inst.precharge.side_effect = CreditServiceUnavailable("transient")
    else:
        inst.precharge.return_value = precharge_return
    inst.refund_ledgered.return_value = 80
    monkeypatch.setattr(chat, "CreditService", MagicMock(return_value=inst))
    return inst


def _patch_budget(monkeypatch, *, claim_returns_error=False):
    """Patch the guest daily-turn helpers; returns a call recorder."""
    rec = {"claimed": 0, "refunded": 0}

    # `req` is the Request the IP-derived anti-rotation ceiling is derived from; it is
    # optional so the non-HTTP call sites in these tests stay one-liners.
    def _claim(user, x_guest_id, req=None):
        rec["claimed"] += 1
        return MagicMock(name="daily_limit_error") if claim_returns_error else None

    def _refund(user, x_guest_id):
        rec["refunded"] += 1

    monkeypatch.setattr(chat, "_claim_chat_turn_or_error", _claim)
    monkeypatch.setattr(chat, "_refund_chat_turn", _refund)
    return rec


# ── _claim_chat_quota: authenticated ────────────────────────────────

def test_authed_precharge_success(monkeypatch):
    credit = _patch_credit(monkeypatch, precharge_return=100)
    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert err is None and quota is not None
    credit.precharge.assert_called_once()
    args, _ = credit.precharge.call_args
    assert args[0] == AUTHED["id"]
    assert args[1] == settings.CHAT_CREDIT_COST   # configurable cost, charged as-is


def test_authed_insufficient_returns_402(monkeypatch):
    credit = _patch_credit(monkeypatch, precharge_return=None)
    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert quota is None and err is not None
    assert err.status_code == 402                 # Payment Required
    assert json.loads(err.body)["error_code"] == ErrorCode.INSUFFICIENT_CREDITS.value


def test_authed_transient_failure_returns_409_not_402(monkeypatch):
    # A DB blip must be a retryable SYSTEM_BUSY, never INSUFFICIENT_CREDITS.
    _patch_credit(monkeypatch, precharge_raises=True)
    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert quota is None
    assert err.status_code == 409
    assert json.loads(err.body)["error_code"] == ErrorCode.SYSTEM_BUSY.value


# ── _claim_chat_quota: guest ────────────────────────────────────────

def test_guest_uses_daily_budget_not_credits(monkeypatch):
    credit = _patch_credit(monkeypatch)
    rec = _patch_budget(monkeypatch)
    quota, err = chat._claim_chat_quota(GUEST, "install-1", session_id="sess-1")
    assert err is None and quota is not None
    credit.precharge.assert_not_called()          # guests are never credit-metered
    assert rec["claimed"] == 1                     # the daily-turn budget instead


def test_guest_daily_cap_reached_short_circuits(monkeypatch):
    credit = _patch_credit(monkeypatch)
    _patch_budget(monkeypatch, claim_returns_error=True)
    quota, err = chat._claim_chat_quota(GUEST, "install-1", session_id="sess-1")
    assert quota is None and err is not None       # the daily-limit JSONResponse
    credit.precharge.assert_not_called()


# ── _ChatQuota.refund_once: at-most-once ────────────────────────────

def test_authed_refund_once_fires_exactly_once(monkeypatch):
    # The finally backstop + 3 stream error sites may all call refund_once; the
    # non-idempotent refund_credits RPC must fire at most once.
    credit = _patch_credit(monkeypatch)
    quota = chat._ChatQuota(AUTHED, None, is_guest=False, ref_id="sess-1")
    quota.refund_once("first")
    quota.refund_once("second")
    quota.refund_once("third")
    credit.refund_ledgered.assert_called_once()


def test_guest_refund_once_releases_daily_turn_once(monkeypatch):
    credit = _patch_credit(monkeypatch)
    rec = _patch_budget(monkeypatch)
    quota = chat._ChatQuota(GUEST, "install-1", is_guest=True, ref_id="sess-1")
    quota.refund_once("x")
    quota.refund_once("y")
    assert rec["refunded"] == 1                    # daily turn released once
    credit.refund_ledgered.assert_not_called()     # guests never touch credits


# ── the credit ref_id is per-TURN, not per-session ──────────────────

def test_credit_ref_id_is_unique_per_turn(monkeypatch):
    """Two turns in ONE session must write two DISTINCT ledger refs.

    With the old `ref_id=session_id` both debits were `(session, -1)` and
    `refund_credits` — which pairs to the newest un-reversed match — could reverse
    turn B's debit when turn A failed, adopting B's recorded pool split. Migration 124
    names chat.py as the reason its `reverses_id` anti-join exists; a unique ref per
    debit is what makes the pairing exact rather than merely bounded.
    """
    credit = _patch_credit(monkeypatch)
    chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    chat._claim_chat_quota(AUTHED, None, session_id="sess-1")

    refs = [kw["ref_id"] for _, kw in credit.precharge.call_args_list]
    assert len(refs) == 2
    assert refs[0] != refs[1], "two turns in one session shared a credit ref_id"
    # Still greppable back to the conversation it belongs to.
    assert all(r.startswith("sess-1:") for r in refs)


def test_refund_uses_the_ref_id_its_charge_used(monkeypatch):
    """The standing invariant: a refund must present the ref its debit was written with.

    A mismatch is not a loud failure — `refund_credits` answers `no_matching_debit`
    and the user is silently never repaid.
    """
    credit = _patch_credit(monkeypatch)
    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-9")
    assert err is None
    charged_ref = credit.precharge.call_args.kwargs["ref_id"]

    quota.refund_once("chat_undelivered")
    assert credit.refund_ledgered.call_args.kwargs["ref_id"] == charged_ref


def test_guest_ref_id_is_never_used_for_credits(monkeypatch):
    """A guest turn mints a ref but must never reach the credit ledger with it."""
    credit = _patch_credit(monkeypatch)
    rec = _patch_budget(monkeypatch)
    quota, err = chat._claim_chat_quota(GUEST, "install-1", session_id="sess-1")
    assert err is None
    quota.refund_once("chat_undelivered")
    credit.precharge.assert_not_called()
    credit.refund_ledgered.assert_not_called()
    assert rec["refunded"] == 1


# ── Free follow-up (migration 154) ──────────────────────────────────
#
# The rule: a turn that was actually CHARGED earns the session ONE free follow-up. A free
# turn earns nothing. That single asymmetry is the only thing bounding the feature to
# 2 turns per credit — if a free turn could grant another, one credit would buy an endless
# conversation as long as the user kept replying inside the window.


def _patch_followup(monkeypatch, *, claim_returns=False, claim_raises=False):
    """Patch the budget singleton chat.py reaches for the free-follow-up RPCs."""
    svc = MagicMock()
    if claim_raises:
        # The service itself swallows RPC errors and returns False (fail closed); this
        # models the layer above having already degraded.
        svc.claim_free_followup.return_value = False
    else:
        svc.claim_free_followup.return_value = claim_returns
    monkeypatch.setattr(chat, "get_chat_budget_service", MagicMock(return_value=svc))
    return svc


def test_free_followup_skips_the_precharge_entirely(monkeypatch):
    """A claimed follow-up must not touch the wallet AT ALL — not charge-then-refund.

    Charging and reversing would write a debit/refund pair into the ledger for a turn that
    was never meant to cost anything, and every extra pair is another chance for
    `refund_credits` to mispair.
    """
    credit = _patch_credit(monkeypatch)
    _patch_followup(monkeypatch, claim_returns=True)
    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert err is None and quota is not None
    credit.precharge.assert_not_called()
    assert quota.outcome == "free_followup"
    assert quota.charged == 0


def test_free_followup_is_claimed_before_the_insufficient_credits_gate(monkeypatch):
    """A user at 0 credits still gets the follow-up their PREVIOUS turn paid for."""
    credit = _patch_credit(monkeypatch, precharge_return=None)   # wallet is empty
    _patch_followup(monkeypatch, claim_returns=True)
    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert err is None, "an earned follow-up must not be blocked by a 402"
    assert quota.outcome == "free_followup"
    credit.precharge.assert_not_called()


def test_free_followup_claim_failure_charges_normally(monkeypatch):
    """FAILS CLOSED. Every other budget path fails open so a DB blip can't wall a user out
    of chat; this one is the mirror image — failing open would make chat free for everyone
    during an outage. Charging is self-healing: the allowance row was never cleared."""
    credit = _patch_credit(monkeypatch)
    _patch_followup(monkeypatch, claim_raises=True)
    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert err is None
    credit.precharge.assert_called_once()
    assert quota.outcome == "charged"


def test_a_charged_delivered_turn_grants_one_free_followup(monkeypatch):
    _patch_credit(monkeypatch)
    svc = _patch_followup(monkeypatch, claim_returns=False)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    quota.on_delivered()
    svc.grant_free_followup.assert_called_once_with("sess-1")


def test_a_free_turn_never_grants_another(monkeypatch):
    """Non-chainable. This is the bound on the whole feature."""
    _patch_credit(monkeypatch)
    svc = _patch_followup(monkeypatch, claim_returns=True)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert quota.outcome == "free_followup"
    quota.on_delivered()
    svc.grant_free_followup.assert_not_called()


def test_a_refunded_turn_never_grants_a_free_followup(monkeypatch):
    """The perk is earned by PAYING. A turn we handed the credit back for was not paid for.

    Ordering matters: both endpoints run the cache-hit / degraded refund BEFORE
    `on_delivered`, so the settled flag is already set by the time it is asked.
    """
    _patch_credit(monkeypatch)
    svc = _patch_followup(monkeypatch, claim_returns=False)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    quota.refund_once("chat_cache_hit")
    quota.on_delivered()
    svc.grant_free_followup.assert_not_called()


def test_a_failed_free_turn_never_calls_refund_ledgered(monkeypatch):
    """⚠️ THE credit-minting guard. Do not delete.

    A free turn wrote NO debit. Refunding against its ref_id finds no matching row, so
    `refund_credits` takes the granted-first fallback and pays out `LEAST(amount, used)` —
    handing the user a credit they never spent, on EVERY failed free turn. `refund_once`
    must return in the `_free` branch before reaching the ledger.
    """
    credit = _patch_credit(monkeypatch)
    _patch_followup(monkeypatch, claim_returns=True)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    quota.refund_once("chat_stream_empty")
    credit.refund_ledgered.assert_not_called()


def test_a_failed_free_turn_restores_the_entitlement(monkeypatch):
    """The allowance was consumed for a turn that never arrived — hand it back."""
    _patch_credit(monkeypatch)
    svc = _patch_followup(monkeypatch, claim_returns=True)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    quota.refund_once("chat_stream_empty")
    svc.grant_free_followup.assert_called_once_with("sess-1")
    # And it is still reported as a free turn, not as a refund that never happened.
    assert quota.outcome == "free_followup"


def test_guest_never_touches_the_free_followup_rpcs(monkeypatch):
    _patch_credit(monkeypatch)
    _patch_budget(monkeypatch)
    svc = _patch_followup(monkeypatch, claim_returns=False)
    quota, err = chat._claim_chat_quota(GUEST, "install-1", session_id="sess-1")
    assert err is None
    quota.on_delivered()
    svc.claim_free_followup.assert_not_called()
    svc.grant_free_followup.assert_not_called()


# ── What the user is told it cost ───────────────────────────────────

def test_a_normal_charge_shows_the_user_nothing(monkeypatch):
    """No chip on a plain charge. Stamping a price on every answer turns chat into a
    meter, and the asking is the product — the ask was to surface the GOOD news."""
    _patch_credit(monkeypatch, precharge_return=42)
    _patch_followup(monkeypatch, claim_returns=False)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    assert quota.cost_payload() is None
    # The live frame still goes out — the client needs the balance even with no chip.
    frame = quota.cost_frame()
    assert frame["outcome"] == "charged" and frame["label"] is None
    assert frame["balance"] == 42


def test_a_free_turn_is_labelled_for_the_user(monkeypatch):
    _patch_credit(monkeypatch)
    _patch_followup(monkeypatch, claim_returns=True)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    payload = quota.cost_payload()
    assert payload["outcome"] == "free_followup"
    assert payload["credits"] == 0
    assert payload["label"] == "Free follow-up"


def test_a_refund_says_why(monkeypatch):
    """The reason is machine-readable; the label is server-authored so the wording can
    change without an App Store release."""
    _patch_credit(monkeypatch)
    _patch_followup(monkeypatch, claim_returns=False)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    quota.refund_once("chat_degraded_unmerged")
    payload = quota.cost_payload()
    assert payload["outcome"] == "refunded"
    assert payload["reason"] == "chat_degraded_unmerged"
    assert "incomplete" in payload["label"]
    assert payload["credits"] == 0


def test_refund_balance_ignores_a_transport_fault(monkeypatch):
    """`refund_ledgered` returns None STRICTLY for a transport fault, never a business
    outcome. Reading a balance out of it would show the user an invented number."""
    credit = _patch_credit(monkeypatch, precharge_return=7)
    credit.refund_ledgered.return_value = None
    _patch_followup(monkeypatch, claim_returns=False)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    quota.refund_once("chat_undelivered")
    assert quota.cost_frame()["balance"] == 7        # the pre-charge value, not None/0


def test_refund_balance_uses_the_rpc_spendable(monkeypatch):
    credit = _patch_credit(monkeypatch, precharge_return=7)
    credit.refund_ledgered.return_value = {"outcome": "refunded", "refunded": 1, "spendable": 8}
    _patch_followup(monkeypatch, claim_returns=False)
    quota, _ = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")
    quota.refund_once("chat_undelivered")
    assert quota.cost_frame()["balance"] == 8


# ── The earned-free-turn branch, finally tested ──────────────────────────────
#
# Until the autouse stub above landed, `claim_free_followup` was a LIVE call to
# production, so this branch could only be reached by chance and was never asserted.
# These two pin it deterministically.

def test_an_earned_free_followup_is_not_charged(monkeypatch):
    """The whole point of the free follow-up: no credit is spent."""
    budget = _grant_free_followup(monkeypatch)
    credit = _patch_credit(monkeypatch)

    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")

    assert err is None and quota is not None
    assert quota.outcome == "free_followup"
    credit.precharge.assert_not_called()          # the assertion that matters
    budget.claim_free_followup.assert_called_once_with("sess-1")


def test_a_free_followup_bypasses_the_insufficient_credits_gate(monkeypatch):
    """Documented intent: the turn was paid for by the PREVIOUS one, so a user who has
    since hit 0 still gets the answer they are already owed — no 402."""
    _grant_free_followup(monkeypatch)
    credit = _patch_credit(monkeypatch, precharge_return=None)   # wallet empty

    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")

    assert err is None
    assert quota.outcome == "free_followup"
    credit.precharge.assert_not_called()


def test_a_claim_failure_charges_normally(monkeypatch):
    """Fails CLOSED. A DB blip must charge, never hand out a free turn we cannot
    record as spent — a sustained outage would otherwise make chat free for everyone."""
    budget = MagicMock()
    budget.claim_free_followup.return_value = False       # what the real one returns on error
    monkeypatch.setattr(chat, "get_chat_budget_service", lambda: budget)
    credit = _patch_credit(monkeypatch, precharge_return=100)

    quota, err = chat._claim_chat_quota(AUTHED, None, session_id="sess-1")

    assert err is None
    assert quota.outcome == "charged"
    credit.precharge.assert_called_once()
