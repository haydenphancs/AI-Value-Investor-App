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
    quota, err = chat._claim_chat_quota(AUTHED, None, ref_id="sess-1")
    assert err is None and quota is not None
    credit.precharge.assert_called_once()
    args, _ = credit.precharge.call_args
    assert args[0] == AUTHED["id"]
    assert args[1] == settings.CHAT_CREDIT_COST   # configurable cost, charged as-is


def test_authed_insufficient_returns_402(monkeypatch):
    credit = _patch_credit(monkeypatch, precharge_return=None)
    quota, err = chat._claim_chat_quota(AUTHED, None, ref_id="sess-1")
    assert quota is None and err is not None
    assert err.status_code == 402                 # Payment Required
    assert json.loads(err.body)["error_code"] == ErrorCode.INSUFFICIENT_CREDITS.value


def test_authed_transient_failure_returns_409_not_402(monkeypatch):
    # A DB blip must be a retryable SYSTEM_BUSY, never INSUFFICIENT_CREDITS.
    _patch_credit(monkeypatch, precharge_raises=True)
    quota, err = chat._claim_chat_quota(AUTHED, None, ref_id="sess-1")
    assert quota is None
    assert err.status_code == 409
    assert json.loads(err.body)["error_code"] == ErrorCode.SYSTEM_BUSY.value


# ── _claim_chat_quota: guest ────────────────────────────────────────

def test_guest_uses_daily_budget_not_credits(monkeypatch):
    credit = _patch_credit(monkeypatch)
    rec = _patch_budget(monkeypatch)
    quota, err = chat._claim_chat_quota(GUEST, "install-1", ref_id="sess-1")
    assert err is None and quota is not None
    credit.precharge.assert_not_called()          # guests are never credit-metered
    assert rec["claimed"] == 1                     # the daily-turn budget instead


def test_guest_daily_cap_reached_short_circuits(monkeypatch):
    credit = _patch_credit(monkeypatch)
    _patch_budget(monkeypatch, claim_returns_error=True)
    quota, err = chat._claim_chat_quota(GUEST, "install-1", ref_id="sess-1")
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
