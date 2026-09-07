"""Guest partitioning for watchlist-derived personal data (migration 108).

Before this, every signed-out user wrote `watchlist_items` under the single shared
`GUEST_USER_ID`: one guest adding NVDA put it on every other guest's Tracking tab,
and either could delete the other's tickers. Same defect `guest_user_id_for()` was
introduced to fix for Learn progress (066/067), never extended here.

The failure mode is SILENT — no error, just other people's data — so the wiring is
pinned by source inspection rather than left to review.

⚠️ ACCOUNT-ONLY SINCE 2026-09-07. The guest half of that story is over: FMP's signed Order
Form grants End-User Display Rights only, so market data — which is what `watchlist_items`
drives on the Tracking tab — may be shown solely to a signed-in caller. The partitioning
tests below are inverted accordingly. What is NOT over is the bug class: an identity that
more than one caller can hold is still a cross-user data leak, so the assertions now pin the
absence of any such identity rather than the correctness of the per-install one.
"""

import inspect

import pytest

from app.dependencies import GUEST_USER_ID, get_watchlist_identity, guest_user_id_for


# ── the dependency itself ────────────────────────────────────────────────────

def _strip_py_comments(src: str) -> str:
    """Source with its docstring and every `#` comment removed (testing.md §3 rule 1).

    Load-bearing here: this file's prose narrates the guest history it now asserts is gone,
    so an un-stripped scan for `guest_user_id_for` would fail on a docstring, and one for its
    absence would pass on a docstring after a real revert.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(src))
    fn = tree.body[0]
    body = getattr(fn, "body", None)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        fn.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


@pytest.mark.asyncio
async def test_a_signed_in_account_passes_through_flagged_not_guest(monkeypatch):
    """A real account's row survives the wrapper intact, with `is_guest` present-and-FALSE.

    Was `test_a_signed_in_account_always_wins`, which proved a client-supplied X-Guest-Id
    could not re-bucket a signed-in user. There is no bucket to be re-assigned to now, but the
    second half of that test is MORE load-bearing than before: `whales.py:130` reads
    `bool(user.get("is_guest", True))` — absent means DENY — so a wrapper that returned a bare
    account row would classify every signed-in user as a guest and silently disable whale
    force-refresh for the whole user base.
    """
    got = await get_watchlist_identity(
        user={"id": "real-user-1", "email": "u@example.com", "tier": "pro"}
    )
    assert got["id"] == "real-user-1"
    assert got["tier"] == "pro", "the wrapper must not shadow the account's tier"
    assert "is_guest" in got, "callers default is_guest to True (deny) when it is absent"
    assert got.get("is_guest") is False


def test_the_watchlist_identity_can_no_longer_produce_a_guest():
    """No guest branch survives in the wrapper — neither per-install nor the shared sentinel.

    Replaces `test_two_guest_installs_get_different_identities` and
    `test_headerless_clients_keep_the_legacy_shared_bucket`. Both asserted the guest product
    that FMP's End-User Display licence removed: market data may be shown only "through the
    Licensee's authenticated platform", and `watchlist_items` rows drive the Tracking tab's
    prices.

    Source-scanned rather than called, because the absence of a branch is not observable from
    the outside — a wrapper can compute a synthetic id and simply not return it on the paths a
    behavioural test exercises. Comment-stripped: this file's own prose names both symbols, so
    an un-stripped scan would fail on the docstring above it.
    """
    code = _strip_py_comments(inspect.getsource(get_watchlist_identity))
    assert "guest_user_id_for" not in code
    assert "GUEST_USER_ID" not in code


def test_identity_is_deterministic_across_restarts():
    """`guest_user_id_for` outlives the guest product — it is the RATE-LIMIT bucket key.

    Kept deliberately, and this test with it. `RateLimitChecker` and `identity_key` use it to
    bucket unauthenticated callers, and after the sign-in wall `/auth/login` and
    `/auth/register` are the only unauthenticated surface in the app — precisely the one that
    most needs a per-caller key. Delete the function and every anonymous caller shares one
    bucket, so one attacker exhausts everyone's login allowance.
    """
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
