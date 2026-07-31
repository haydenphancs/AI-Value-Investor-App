"""
Per-install guest identity.

Until real login ships, no request carries a Bearer token, so the backend used to
attribute EVERY install to one shared `GUEST_USER_ID`. Because the iOS Learn
stores union-merge the server's completed set into their local one, that meant
one person's finished lessons and saved books were merged into every other
person's app — permanently, and in both directions.

`guest_user_id_for` partitions guests by an opaque per-install id sent in
`X-Guest-Id`. These tests pin the properties that make that safe.
"""

import uuid

import pytest

from fastapi import HTTPException

from app.dependencies import GUEST_USER_ID, guest_user_id_for


def test_distinct_installs_get_distinct_identities():
    """The whole point: two installs must not share a progress partition."""
    assert guest_user_id_for("install-A") != guest_user_id_for("install-B")


def test_the_same_install_is_stable_across_calls():
    # Must survive app restarts and backend deploys, or progress "disappears".
    first = guest_user_id_for("install-A")
    for _ in range(5):
        assert guest_user_id_for("install-A") == first


@pytest.mark.parametrize("absent", [None, "", "   ", "\n\t"])
def test_a_missing_id_falls_back_to_the_shared_guest(absent):
    """Back-compat: already-shipped app versions send no header.

    Their behaviour must be byte-identical to before this change.
    """
    assert guest_user_id_for(absent) == GUEST_USER_ID


def test_the_result_is_always_a_valid_uuid():
    # user_learn_progress.user_id is a uuid column — a non-uuid would 500 on write.
    for raw in ["install-A", "x" * 500, "🎧 emoji id", "'; DROP TABLE users;--", "0"]:
        uuid.UUID(guest_user_id_for(raw))


def test_a_client_cannot_impersonate_an_account_by_sending_its_uuid():
    """The header value is HASHED, never used as the identity directly.

    Otherwise anyone could read and write another user's Learn progress just by
    sending their uuid — the header is unauthenticated by construction.
    """
    victim = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    assert guest_user_id_for(victim) != victim
    # And it cannot be steered onto the shared guest row either.
    assert guest_user_id_for(GUEST_USER_ID) != GUEST_USER_ID


def test_whitespace_is_normalised_so_one_install_is_one_identity():
    assert guest_user_id_for("  install-A  ") == guest_user_id_for("install-A")


def test_overlong_ids_are_bounded_but_still_distinct():
    # Truncation must not collapse two different installs onto one partition.
    a, b = "A" * 199 + "1", "A" * 199 + "2"
    assert guest_user_id_for(a) != guest_user_id_for(b)


def test_learn_routes_use_the_per_install_identity():
    """Regression guard: the Learn routes must not drift back to the shared guest."""
    import inspect
    from app.api.v1.endpoints import learn as learn_module

    src = inspect.getsource(learn_module)
    assert "get_learn_identity" in src
    assert "Depends(get_current_user_or_guest)" not in src, (
        "a Learn route reverted to the shared guest — progress will pool across users"
    )


def test_other_features_keep_the_seeded_shared_guest():
    """Scoped on purpose.

    research / credits / portfolios hang off a seeded GUEST_USER_ID row that owns
    real credits; pointing those at a synthetic uuid would break them. Learn is
    safe to partition because `user_learn_progress.user_id` has no foreign key.
    """
    import inspect
    from app.api.v1.endpoints import research

    assert "get_learn_identity" not in inspect.getsource(research)


# ── rate-limit bucketing ─────────────────────────────────────────────────────
#
# `RateLimitChecker` used to bucket EVERY unauthenticated caller under the single
# key `guest:{GUEST_USER_ID}`. That is the worst of both worlds: no per-attacker
# protection (one key, one shared budget) AND real guests 429ing each other on a
# busy day. These pin the per-install split without breaking already-shipped
# clients that send no header.

@pytest.mark.asyncio
async def test_rate_limit_buckets_guests_per_install():
    from app.core.security import rate_limiter
    from app.dependencies import RateLimitChecker

    rate_limiter._requests.clear()
    checker = RateLimitChecker(max_requests=2, window_seconds=60)

    # Install A burns its whole window.
    await checker(user_id=None, x_guest_id="install-A")
    await checker(user_id=None, x_guest_id="install-A")
    with pytest.raises(HTTPException) as exc:
        await checker(user_id=None, x_guest_id="install-A")
    assert exc.value.status_code == 429

    # Install B is unaffected — the bug this replaces would have 429'd it too.
    await checker(user_id=None, x_guest_id="install-B")
    await checker(user_id=None, x_guest_id="install-B")


@pytest.mark.asyncio
async def test_rate_limit_headerless_clients_share_the_legacy_bucket():
    """Back-compat: shipped app versions that send no X-Guest-Id must keep
    working, landing on the shared GUEST_USER_ID key exactly as before."""
    from app.core.security import rate_limiter
    from app.dependencies import RateLimitChecker, GUEST_USER_ID

    rate_limiter._requests.clear()
    checker = RateLimitChecker(max_requests=1, window_seconds=60)

    await checker(user_id=None, x_guest_id=None)
    with pytest.raises(HTTPException):
        await checker(user_id=None, x_guest_id=None)

    assert f"guest:{GUEST_USER_ID}" in rate_limiter._requests


@pytest.mark.asyncio
async def test_rate_limit_authenticated_user_ignores_the_guest_header():
    """A signed-in caller must never be bucketed by a client-supplied header —
    otherwise rotating X-Guest-Id would grant unlimited windows."""
    from app.core.security import rate_limiter
    from app.dependencies import RateLimitChecker

    rate_limiter._requests.clear()
    checker = RateLimitChecker(max_requests=1, window_seconds=60)

    await checker(user_id="real-user", x_guest_id="install-A")
    with pytest.raises(HTTPException):
        await checker(user_id="real-user", x_guest_id="install-B")
