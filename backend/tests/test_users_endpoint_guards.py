"""Guards for the user endpoints + the guest fallback dependency.

Covers the adversarial-review findings:
- /me/credits must NOT fabricate a 50/0/50 balance on a transient DB error (C4/C10).
- get_current_user_or_guest must NOT demote a VALID authenticated user to the shared
  guest on a transient users-table read (C5).

Hermetic: no app startup, no network — the endpoint/dependency callables are invoked
directly with a stub Supabase client.
"""

import json

import pytest
from fastapi import HTTPException
from starlette.responses import JSONResponse

from app.api.v1.endpoints.users import credits_response_from_rows, get_user_credits
from app.dependencies import GUEST_USER_ID
import app.dependencies as deps


# ── stub Supabase clients ────────────────────────────────────────────────────

class _RaisingSupabase:
    """Any query chain that reaches .execute() raises (simulates a transient blip)."""
    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self, *a, **k):
        raise RuntimeError("transient db blip")


class _EmptySupabase:
    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self, *a, **k):
        class _R:
            data = []
        return _R()


# ── credits_response_from_rows (pure) ────────────────────────────────────────

def test_credits_no_row_gives_optimistic_free():
    r = credits_response_from_rows([])
    assert (r.total, r.used, r.remaining) == (50, 0, 50)


def test_credits_uses_real_row():
    r = credits_response_from_rows(
        [{"total": 1200, "used": 1190, "remaining": 10,
          "resets_at": "2026-08-01T00:00:00+00:00"}]
    )
    assert (r.total, r.used, r.remaining) == (1200, 1190, 10)
    assert r.resets_at == "2026-08-01T00:00:00+00:00"


# ── /me/credits transient error must not fabricate ───────────────────────────

@pytest.mark.asyncio
async def test_credits_transient_error_returns_system_busy_not_fabricated():
    guest = {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}  # skips ensure_period
    resp = await get_user_credits(user=guest, supabase=_RaisingSupabase())
    # A transient read must surface a retryable error, NOT a fake 50/0/50 balance.
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 409  # SYSTEM_BUSY
    body = json.loads(resp.body)
    assert body["error_code"] == "SYSTEM_BUSY"


# ── get_current_user_or_guest transient-read handling ────────────────────────

@pytest.mark.asyncio
async def test_guest_dep_no_token_returns_guest():
    r = await deps.get_current_user_or_guest(authorization=None, supabase=_RaisingSupabase())
    assert r["id"] == GUEST_USER_ID


@pytest.mark.asyncio
async def test_guest_dep_valid_token_transient_read_raises_not_guest(monkeypatch):
    # Valid token resolves to a real user_id, but the users read blips.
    monkeypatch.setattr(deps, "decode_token", lambda _t: {"sub": "real-user-123"})
    with pytest.raises(HTTPException) as ei:
        await deps.get_current_user_or_guest(
            authorization="Bearer valid", supabase=_RaisingSupabase()
        )
    assert ei.value.status_code == 503  # retryable, NOT a silent guest demotion


@pytest.mark.asyncio
async def test_guest_dep_valid_token_no_row_falls_back_to_guest(monkeypatch):
    # Valid token but no public.users row (rare first-touch) → guest, not a 500.
    monkeypatch.setattr(deps, "decode_token", lambda _t: {"sub": "real-user-123"})
    r = await deps.get_current_user_or_guest(
        authorization="Bearer valid", supabase=_EmptySupabase()
    )
    assert r["id"] == GUEST_USER_ID
