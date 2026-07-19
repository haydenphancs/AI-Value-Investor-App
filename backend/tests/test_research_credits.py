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


def test_try_charge_uses_default_cost_of_5():
    svc = CreditService.__new__(CreditService)
    svc.supabase = MagicMock()
    _stub_rpc(svc, 10)

    svc.try_charge("user-x")

    args, _ = svc.supabase.rpc.call_args
    assert args[0] == "charge_user_credits"
    assert args[1]["p_amount"] == CreditService.DEEP_RESEARCH_COST == 5


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
