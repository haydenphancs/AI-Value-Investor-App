"""Guest chat history is partitioned per install (migration 111).

THE LEAK. `/chat/*` resolved identity through `get_current_user_or_guest`, which returns the
shared `GUEST_USER_ID` for every signed-out caller, and both read paths filter on that column:

    GET /chat/sessions       .eq("user_id", user["id"])
    GET /chat/sessions/{id}  .eq("user_id", user["id"])

So guest A asked Cay AI about their holdings and guest B — a different person on a different
device — opened the chat history tab and saw A's conversations. B could open them, rename them,
and DELETE them. Chat is where people paste portfolios and ask personal financial questions, so
this was the worst remaining instance of a defect migrations 108 (watchlist) and 110 (research)
had already fixed elsewhere. It was a KNOWN gap: SYSTEM_DESIGN_GUIDELINES §9.3 recorded that
guest chat-history isolation "resolves when real login ships".

The failure mode is SILENT — no error, just other people's conversations — so the wiring is
pinned by source inspection as well as behaviour.
"""

import inspect
import re
from pathlib import Path

import pytest

import app.api.v1.endpoints.chat as chat
from app.dependencies import GUEST_USER_ID, get_chat_identity, guest_user_id_for

_REPO = Path(__file__).resolve().parents[2]


# ── the dependency ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_guest_installs_get_different_chat_identities(monkeypatch):
    import app.dependencies as deps

    async def _guest(*a, **kw):
        return {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}

    monkeypatch.setattr(deps, "get_current_user_or_guest", _guest)

    a = await get_chat_identity(None, "install-A", None)
    b = await get_chat_identity(None, "install-B", None)

    assert a["id"] != b["id"], "two guest installs collapsed to one chat history"
    assert a["id"] != GUEST_USER_ID
    assert a["is_guest"] is True
    # Hashed, never the raw client-supplied string — otherwise a client could send a real
    # account's uuid and read its chats.
    assert "install-A" not in a["id"]


@pytest.mark.asyncio
async def test_a_signed_in_account_always_wins(monkeypatch):
    import app.dependencies as deps

    async def _real(*a, **kw):
        return {"id": "real-user-1", "email": "u@example.com", "tier": "pro"}

    monkeypatch.setattr(deps, "get_current_user_or_guest", _real)

    user = await get_chat_identity(None, "install-A", None)
    assert user["id"] == "real-user-1"
    assert user["is_guest"] is False


@pytest.mark.asyncio
async def test_identity_is_stable_across_requests(monkeypatch):
    """A guest must find their OWN chats again on the next launch, so the derivation has to be
    deterministic rather than random-per-request."""
    import app.dependencies as deps

    async def _guest(*a, **kw):
        return {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}

    monkeypatch.setattr(deps, "get_current_user_or_guest", _guest)

    first = await get_chat_identity(None, "install-A", None)
    second = await get_chat_identity(None, "install-A", None)
    assert first["id"] == second["id"] == guest_user_id_for("install-A")


@pytest.mark.asyncio
async def test_a_client_sending_no_install_id_still_gets_the_shared_sentinel(monkeypatch):
    """Older builds send no header. They keep the legacy behaviour rather than erroring."""
    import app.dependencies as deps

    async def _guest(*a, **kw):
        return {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}

    monkeypatch.setattr(deps, "get_current_user_or_guest", _guest)

    user = await get_chat_identity(None, None, None)
    assert user["id"] == GUEST_USER_ID
    assert user["is_guest"] is True


# ── the wiring (source-level, because the failure is silent) ─────────────────

def test_every_chat_route_uses_the_partitioned_identity():
    src = (_REPO / "backend/app/api/v1/endpoints/chat.py").read_text()
    assert "Depends(get_current_user_or_guest)" not in src, (
        "a /chat route still resolves the SHARED guest sentinel — that route lets one guest "
        "read, rename and delete every other guest's conversations"
    )
    assert src.count("Depends(get_chat_identity)") >= 7


def test_the_guest_test_does_not_rely_on_the_sentinel_comparison():
    """THE trap. A per-install id never equals GUEST_USER_ID, so `user["id"] == GUEST_USER_ID`
    silently classifies every guest as a paying account and sends them into a credit precharge
    against a `user_credits` row that does not exist — 402 "insufficient credits" on a feature
    that is supposed to be free for them."""
    src = inspect.getsource(chat._claim_chat_quota)
    assert 'user.get("is_guest")' in src
    # Comments legitimately NAME the retired comparison to explain why it is wrong; only live
    # code can actually reinstate it.
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'user["id"] == GUEST_USER_ID' not in code


# ── account deletion + claim ─────────────────────────────────────────────────

def test_chat_sessions_is_purged_explicitly_on_account_deletion():
    """The dropped FK was ON DELETE CASCADE — the ONLY thing removing a deleted user's chats."""
    from app.api.v1.endpoints.users import _UNLINKED_USER_TABLES

    assert "chat_sessions" in _UNLINKED_USER_TABLES, (
        "migration 111 dropped chat_sessions_user_id_fkey, so deleting an account no longer "
        "cascades to their conversations — the most sensitive rows the app stores"
    )


def test_chat_messages_needs_no_entry_because_it_cascades_from_sessions():
    """Deliberately absent: `chat_messages_session_id_fkey` references chat_sessions(id) and is
    untouched by 111, so removing a session still removes its messages. Adding it would be
    harmless but misleading about where the guarantee comes from."""
    from app.api.v1.endpoints.users import _UNLINKED_USER_TABLES

    assert "chat_messages" not in _UNLINKED_USER_TABLES
    snapshot = (_REPO / "backend/database/schema_snapshot.sql").read_text()
    assert re.search(
        r"chat_messages_session_id_fkey FOREIGN KEY \(session_id\) "
        r"REFERENCES public\.chat_sessions\(id\) ON DELETE CASCADE",
        snapshot,
    )


def test_signing_up_claims_this_installs_chats():
    src = (_REPO / "backend/app/api/v1/endpoints/users.py").read_text()
    claim = src[src.index("async def claim_guest_data"):src.index("_UNLINKED_USER_TABLES")]
    assert 'supabase.table("chat_sessions")' in claim, (
        "a guest who asked Cay AI about a ticker and THEN signed up would find their history "
        "empty"
    )
    assert 'claimed["chat_sessions"]' in claim


# ── the migration itself ─────────────────────────────────────────────────────

def test_the_migration_that_drops_the_cascade_exists():
    sql = (_REPO / "backend/database/migrations/111_chat_sessions_guest_partition.sql").read_text()
    assert "DROP CONSTRAINT IF EXISTS chat_sessions_user_id_fkey" in sql
    # Idempotent per .claude/rules/database.md — safe to re-run after a partial apply.
    assert "IF EXISTS" in sql
    # The consequence has to be written down where whoever applies it will read it.
    assert "_UNLINKED_USER_TABLES" in sql


# ── the deploy-order hazard ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_rejected_session_insert_is_legible_not_a_bare_500():
    """Deploying this code before applying migration 111 breaks guest chat creation.

    A guest `user_id` is a synthetic uuid5 with no `public.users` row, so the insert violates
    `chat_sessions_user_id_fkey` until 111 drops it. supabase-py RAISES on a non-2xx insert
    rather than returning empty `.data`, so that used to fall through to the global handler as
    a bare 500 — sending whoever debugs it looking at Gemini instead of at a pending migration.
    """
    import json

    class _RaisingSupabase:
        def __getattr__(self, _name):
            return lambda *a, **k: self

        def execute(self, *a, **k):
            raise RuntimeError('insert or update on table "chat_sessions" violates foreign key')

    class _Req:
        context_type = None
        stock_id = None
        reference_id = None

    resp = await chat.create_chat_session(
        request=_Req(),
        user={"id": guest_user_id_for("install-A"), "is_guest": True},
        supabase=_RaisingSupabase(),
    )
    assert resp.status_code == 409
    body = json.loads(bytes(resp.body))
    # Contract-shaped, per CLAUDE.md invariant #3 — not `{"detail": ...}`.
    assert body["error_code"] == "SYSTEM_BUSY"
    assert body["user_message"]
