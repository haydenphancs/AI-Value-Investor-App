"""Edge cases for the free-follow-up budget primitives (migration 154).

These exercise `ChatBudgetService.claim_free_followup` / `grant_free_followup` directly
against a faked Supabase client — no DB. The happy path is covered by
`test_chat_credits.py`; what lives here is the set of inputs that would make chat FREE FOR
EVERYONE if the code guessed wrong, which is the only way this feature can cost real money.
"""

from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.services.chat_budget_service import ChatBudgetService


def _svc(monkeypatch, *, rpc_data=None, rpc_raises=False):
    supa = MagicMock()
    if rpc_raises:
        supa.rpc.side_effect = RuntimeError("supabase down")
    else:
        supa.rpc.return_value.execute.return_value = MagicMock(data=rpc_data)
    monkeypatch.setattr(
        "app.services.chat_budget_service.get_supabase", lambda: supa
    )
    return ChatBudgetService(), supa


# ── claim: everything that is not exactly True must CHARGE ──────────

@pytest.mark.parametrize(
    "data",
    [
        False,          # the honest "no live allowance"
        None,           # RPC returned nothing
        "false",        # ⚠️ TRUTHY as a string — `bool()` would have made this a free turn
        "",             # empty string
        0,              # numeric false
        [],             # an empty result set
        [{"claim_free_followup": True}],   # a wrapping shape from a future PostgREST
        {"claimed": True},                 # a dict envelope
        1,              # a row count, not a boolean
    ],
)
def test_claim_charges_for_anything_that_is_not_exactly_true(monkeypatch, data):
    """Fail CLOSED on an unrecognised RPC result.

    `bool(result.data)` reads as equivalent and is not: four of the values above are
    truthy, and each would hand out a free turn. The cost of being wrong in this direction
    is unbounded (chat free for everyone); in the other it is one credit, charged a beat
    early, against an allowance the row still holds.
    """
    svc, _ = _svc(monkeypatch, rpc_data=data)
    assert svc.claim_free_followup("sess-1") is False


def test_claim_succeeds_only_on_a_real_true(monkeypatch):
    svc, supa = _svc(monkeypatch, rpc_data=True)
    assert svc.claim_free_followup("sess-1") is True
    supa.rpc.assert_called_once_with("claim_free_followup", {"p_session_id": "sess-1"})


def test_claim_fails_closed_when_the_rpc_raises(monkeypatch):
    """A DB blip charges. Every OTHER budget path here fails open so an outage cannot wall
    a user out of chat; this one is the mirror image, because failing open makes chat free
    for everyone. Self-healing: the allowance row was never cleared."""
    svc, _ = _svc(monkeypatch, rpc_raises=True)
    assert svc.claim_free_followup("sess-1") is False


@pytest.mark.parametrize("session_id", [None, ""])
def test_claim_without_a_session_never_hits_the_rpc(monkeypatch, session_id):
    svc, supa = _svc(monkeypatch, rpc_data=True)
    assert svc.claim_free_followup(session_id) is False
    supa.rpc.assert_not_called()


# ── grant: best-effort, and a true kill switch ──────────────────────

def test_grant_passes_the_configured_window(monkeypatch):
    svc, supa = _svc(monkeypatch, rpc_data=None)
    svc.grant_free_followup("sess-1")
    supa.rpc.assert_called_once_with(
        "grant_free_followup",
        {"p_session_id": "sess-1", "p_seconds": settings.CHAT_FREE_FOLLOWUP_SECONDS},
    )


def test_grant_of_zero_is_a_kill_switch_not_a_zero_length_window(monkeypatch):
    """0 must reach the RPC as 0 so it CLEARS any live window, rather than being dropped.

    A grant that simply did nothing would leave already-granted windows to drain on their
    own, so flipping the setting off would not take effect for everyone at once.
    """
    monkeypatch.setattr(settings, "CHAT_FREE_FOLLOWUP_SECONDS", 0)
    svc, supa = _svc(monkeypatch, rpc_data=None)
    svc.grant_free_followup("sess-1")
    assert supa.rpc.call_args.args[1]["p_seconds"] == 0


@pytest.mark.parametrize("window", [None, 0, -5])
def test_grant_coerces_a_degenerate_window_without_raising(monkeypatch, window):
    """`int(None)` raises — and this runs on the answer path, after the turn is persisted."""
    svc, supa = _svc(monkeypatch, rpc_data=None)
    svc.grant_free_followup("sess-1", seconds=window)
    assert isinstance(supa.rpc.call_args.args[1]["p_seconds"], int)


def test_grant_never_raises_when_the_rpc_fails(monkeypatch):
    """A failure means the user simply gets no free follow-up — strictly no worse than the
    pricing they were promised. It must never surface on an already-delivered turn."""
    svc, _ = _svc(monkeypatch, rpc_raises=True)
    svc.grant_free_followup("sess-1")      # must not raise


@pytest.mark.parametrize("session_id", [None, ""])
def test_grant_without_a_session_never_hits_the_rpc(monkeypatch, session_id):
    svc, supa = _svc(monkeypatch, rpc_data=None)
    svc.grant_free_followup(session_id)
    supa.rpc.assert_not_called()
