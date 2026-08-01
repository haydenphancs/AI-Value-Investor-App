"""Guest partitioning for watchlist-derived personal data (migration 108).

Before this, every signed-out user wrote `watchlist_items` under the single shared
`GUEST_USER_ID`: one guest adding NVDA put it on every other guest's Tracking tab,
and either could delete the other's tickers. Same defect `guest_user_id_for()` was
introduced to fix for Learn progress (066/067), never extended here.

The failure mode is SILENT — no error, just other people's data — so the wiring is
pinned by source inspection rather than left to review.
"""

import inspect

import pytest

from app.dependencies import GUEST_USER_ID, get_watchlist_identity, guest_user_id_for


# ── the dependency itself ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_guest_installs_get_different_identities(monkeypatch):
    import app.dependencies as deps

    async def _guest(*a, **kw):
        return {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}

    monkeypatch.setattr(deps, "get_current_user_or_guest", _guest)

    a = await get_watchlist_identity(None, "install-A", None)
    b = await get_watchlist_identity(None, "install-B", None)

    assert a["id"] != b["id"], "two guest installs collapsed to one watchlist"
    assert a["id"] != GUEST_USER_ID
    # Hashed, never the raw client-supplied string.
    assert "install-A" not in a["id"]


@pytest.mark.asyncio
async def test_a_signed_in_account_always_wins(monkeypatch):
    """A real account must never be re-bucketed by a client-supplied header —
    otherwise rotating X-Guest-Id would detach a user from their own watchlist."""
    import app.dependencies as deps

    async def _real(*a, **kw):
        return {"id": "real-user-1", "email": "u@example.com", "tier": "pro"}

    monkeypatch.setattr(deps, "get_current_user_or_guest", _real)

    got = await get_watchlist_identity(None, "install-A", None)
    assert got["id"] == "real-user-1"


@pytest.mark.asyncio
async def test_headerless_clients_keep_the_legacy_shared_bucket(monkeypatch):
    """Back-compat: a client that sends no X-Guest-Id still resolves to the shared
    sentinel, so already-shipped builds keep seeing the rows they created."""
    import app.dependencies as deps

    async def _guest(*a, **kw):
        return {"id": GUEST_USER_ID, "email": "guest@local", "tier": "free"}

    monkeypatch.setattr(deps, "get_current_user_or_guest", _guest)

    got = await get_watchlist_identity(None, None, None)
    assert got["id"] == GUEST_USER_ID


def test_identity_is_deterministic_across_restarts():
    """The id must survive app restarts and backend deploys, or a guest loses their
    watchlist every time the process cycles."""
    assert guest_user_id_for("install-A") == guest_user_id_for("install-A")


# ── every route that touches watchlist-derived data uses it ──────────────────

@pytest.mark.parametrize("module_name", ["watchlist", "tracking", "portfolios"])
def test_watchlist_derived_routes_use_the_partitioned_identity(module_name):
    """These modules read or write `watchlist_items` / `portfolios`. If any reverts
    to `get_current_user_or_guest`, guests silently pool into one dataset again."""
    import importlib

    mod = importlib.import_module(f"app.api.v1.endpoints.{module_name}")
    src = inspect.getsource(mod)

    assert "get_watchlist_identity" in src, (
        f"{module_name}.py no longer uses the partitioned identity"
    )
    assert "Depends(get_current_user_or_guest)" not in src, (
        f"{module_name}.py has a route back on the SHARED guest id — guests will "
        f"see and edit each other's data"
    )


def test_updates_tabs_uses_the_partitioned_identity():
    """The Updates pills ARE the user's watchlist. On the shared bucket a guest
    would see someone else's tickers and none of their own."""
    from app.api.v1.endpoints import updates

    src = inspect.getsource(updates.get_updates_tabs)
    assert "get_watchlist_identity" in src


# ── the account-deletion consequence of dropping the cascades ────────────────

def test_dropped_cascades_are_replaced_by_explicit_purges():
    """Migration 108 drops watchlist_items_user_id_fkey and portfolios_user_id_fkey,
    both ON DELETE CASCADE. Without these entries a deleted account keeps its
    watchlist and portfolios — incomplete deletion, and a privacy-policy violation."""
    from app.api.v1.endpoints import users as users_ep

    assert "watchlist_items" in users_ep._UNLINKED_USER_TABLES
    assert "portfolios" in users_ep._UNLINKED_USER_TABLES


def test_migration_108_drops_both_constraints_and_indexes_ticker():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "database/migrations/108_watchlist_guest_partition.sql"
    ).read_text()

    assert "DROP CONSTRAINT IF EXISTS watchlist_items_user_id_fkey" in sql
    assert "DROP CONSTRAINT IF EXISTS portfolios_user_id_fkey" in sql
    # Needed for the reverse lookup ("which users watch AAPL?") — every existing
    # index is user_id-leading, so that query is a seq scan without it.
    assert "idx_watchlist_items_ticker" in sql
