"""Tests for ChatBudgetService — the durable per-user daily denial-of-wallet ceiling.

Atomicity is delegated to the `claim_chat_turn` Postgres function (migration 096). These
tests pin the Python wrapper's contract with the Supabase RPC (mirrors test_research_credits):
  - a positive count means the turn was granted,
  - -1 means the daily cap is reached (the endpoint → 409 CHAT_DAILY_LIMIT_REACHED),
  - an RPC transport failure RAISES ChatBudgetUnavailable so the endpoint FAILS OPEN
    (a DB blip must never wall a user out of chat),
  - record_tokens is best-effort and never raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.chat_budget_service import ChatBudgetService, ChatBudgetUnavailable


@pytest.fixture
def service():
    # __init__ touches Supabase env — bypass it and inject a mock client.
    svc = ChatBudgetService.__new__(ChatBudgetService)
    svc.supabase = MagicMock()
    return svc


def _stub_rpc(service, return_value):
    rpc_call = MagicMock()
    rpc_call.execute.return_value = MagicMock(data=return_value)
    service.supabase.rpc.return_value = rpc_call


def test_claim_turn_returns_new_count(service):
    _stub_rpc(service, 1)
    assert service.try_claim_turn("bucket-1") == 1
    # Right RPC, right arg keys (day is dynamic ET date → assert keys, not value).
    name, args = service.supabase.rpc.call_args[0]
    assert name == "claim_chat_turn"
    assert set(args.keys()) == {"p_user_id", "p_day", "p_turn_limit"}
    assert args["p_user_id"] == "bucket-1"


def test_claim_turn_increments(service):
    _stub_rpc(service, 37)
    assert service.try_claim_turn("bucket-1") == 37


def test_claim_turn_cap_reached_returns_minus_one(service):
    # The RPC returns -1 when the WHERE (turn_count < limit) fails — cap reached.
    _stub_rpc(service, -1)
    assert service.try_claim_turn("bucket-1") == -1


def test_claim_turn_raises_on_rpc_transport_error(service):
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = RuntimeError("supabase down")
    service.supabase.rpc.return_value = rpc_call
    with pytest.raises(ChatBudgetUnavailable):
        service.try_claim_turn("bucket-1")


def test_claim_turn_raises_on_none_data(service):
    # A well-formed RPC always returns an int; None = unexpected transport shape →
    # treat as unavailable (fail open), NOT as cap-reached.
    _stub_rpc(service, None)
    with pytest.raises(ChatBudgetUnavailable):
        service.try_claim_turn("bucket-1")


def test_record_tokens_swallows_failure(service):
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = RuntimeError("supabase down")
    service.supabase.rpc.return_value = rpc_call
    # Must not raise — accounting is best-effort.
    service.record_tokens("bucket-1", 1234)


def test_record_tokens_skips_zero_and_negative(service):
    service.record_tokens("bucket-1", 0)
    service.record_tokens("bucket-1", -5)
    service.supabase.rpc.assert_not_called()   # nothing to record → no round-trip


def test_record_tokens_calls_add_chat_tokens(service):
    _stub_rpc(service, 500)
    service.record_tokens("bucket-1", 500)
    name, args = service.supabase.rpc.call_args[0]
    assert name == "add_chat_tokens"
    assert args["p_user_id"] == "bucket-1" and args["p_tokens"] == 500


def test_refund_turn_calls_release_rpc(service):
    _stub_rpc(service, 0)
    service.refund_turn("bucket-1")
    name, args = service.supabase.rpc.call_args[0]
    assert name == "release_chat_turn"
    assert args["p_user_id"] == "bucket-1"
    assert set(args.keys()) == {"p_user_id", "p_day"}


def test_refund_turn_swallows_failure(service):
    rpc_call = MagicMock()
    rpc_call.execute.side_effect = RuntimeError("supabase down")
    service.supabase.rpc.return_value = rpc_call
    # Must not raise — refund is best-effort and must not affect the (already-failed) turn.
    service.refund_turn("bucket-1")
