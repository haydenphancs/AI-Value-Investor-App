"""Tests for RateLimiter memory-bounding (adversarial-review finding).

The chat rate-limit bucket keys off the per-install X-Guest-Id header, which is
ATTACKER-CONTROLLED — a flood of distinct header values would otherwise grow the
in-memory `_requests` map without bound (memory-exhaustion DoS), because a key's
timestamp list is trimmed to the window but the KEY is never evicted. These pin that
the map stays bounded and that normal per-identifier limiting still works.
"""

from __future__ import annotations

from app.core.security import RateLimiter


def test_distinct_identifier_flood_stays_bounded():
    rl = RateLimiter()
    rl._MAX_TRACKED = 500          # shrink the cap so the test is fast
    for i in range(5_000):         # 10x the cap worth of distinct attacker ids
        assert rl.is_allowed(f"guest-{i}", max_requests=15, window_seconds=60) is True
    # The map must be bounded near the cap, not ~5000.
    assert len(rl._requests) <= rl._MAX_TRACKED + 1, len(rl._requests)


def test_normal_limiting_unaffected_by_bound():
    rl = RateLimiter()
    # A single identifier still gets exactly `max_requests` allowed, then blocked.
    allowed = sum(1 for _ in range(20) if rl.is_allowed("stable-user", max_requests=15, window_seconds=60))
    assert allowed == 15
    assert rl.is_allowed("stable-user", max_requests=15, window_seconds=60) is False


def test_active_key_survives_eviction_pressure():
    rl = RateLimiter()
    rl._MAX_TRACKED = 100
    # Warm an active key, then flood with distinct ids; the active key may be evicted but
    # re-inserting it must still enforce the limit from a clean window (never crash / never
    # grant unlimited within a window).
    for _ in range(5):
        rl.is_allowed("vip", max_requests=15, window_seconds=60)
    for i in range(1_000):
        rl.is_allowed(f"flood-{i}", max_requests=15, window_seconds=60)
    # vip still limited to 15 in its (possibly reset) window — never unbounded.
    allowed = sum(1 for _ in range(30) if rl.is_allowed("vip", max_requests=15, window_seconds=60))
    assert allowed <= 15
    assert len(rl._requests) <= rl._MAX_TRACKED + 1


# ── The credential pool (`protected=True`) ────────────────────────────────────
#
# These pin the fix for the eviction-resets-the-brute-force-cap defect. Eviction used to be
# FIFO by INSERTION order in a single shared table, and re-assigning an existing key preserves
# its dict position — so a long-lived `login:email:<victim>` bucket sat at the front and was the
# FIRST thing evicted. Flooding the table with rotated X-Guest-Id values (cheap, and every one
# is ALLOWED because each distinct key gets its own budget) deleted the victim's bucket and
# handed the attacker a fresh allowance of password guesses.


def test_credential_bucket_survives_a_general_pool_flood():
    """The attack, end to end: rotate X-Guest-Id to reset someone else's login cap."""
    rl = RateLimiter()
    rl._MAX_TRACKED = 100  # the attacker-reachable pool

    # Victim's address has already burned 9 of its 10 attempts per 15 minutes.
    for _ in range(9):
        assert rl.is_allowed(
            "login:email:victim@example.com", max_requests=10, window_seconds=900, protected=True
        ) is True

    # Attacker floods the general pool with 20x the cap in distinct per-install ids.
    for i in range(2_000):
        rl.is_allowed(f"chat:guest-{i}", max_requests=15, window_seconds=60)

    # The 10th attempt is the last one allowed; the 11th is still refused. Before the pool
    # split this second assertion returned True — the window had been silently reset.
    assert rl.is_allowed(
        "login:email:victim@example.com", max_requests=10, window_seconds=900, protected=True
    ) is True
    assert rl.is_allowed(
        "login:email:victim@example.com", max_requests=10, window_seconds=900, protected=True
    ) is False


def test_denied_bucket_is_not_the_first_evicted():
    """LRU must count a DENIED touch, or the bucket under attack is the one that ages out."""
    rl = RateLimiter()
    rl._MAX_TRACKED = 50
    for _ in range(5):
        rl.is_allowed("hot", max_requests=5, window_seconds=60)
    assert rl.is_allowed("hot", max_requests=5, window_seconds=60) is False

    # Interleave denied touches with sustained eviction pressure in the same pool.
    for i in range(500):
        rl.is_allowed(f"filler-{i}", max_requests=15, window_seconds=60)
        assert rl.is_allowed("hot", max_requests=5, window_seconds=60) is False


def test_both_pools_are_independently_bounded():
    rl = RateLimiter()
    rl._MAX_TRACKED = 50
    rl._MAX_TRACKED_PROTECTED = 50
    for i in range(500):
        rl.is_allowed(f"login:email:{i}@x.com", max_requests=10, window_seconds=900, protected=True)
        rl.is_allowed(f"guest-{i}", max_requests=15, window_seconds=60)
    assert len(rl._protected) <= rl._MAX_TRACKED_PROTECTED
    assert len(rl._requests) <= rl._MAX_TRACKED


def test_a_key_is_not_shared_between_pools():
    """Same identifier, different pool — separate windows, no cross-talk."""
    rl = RateLimiter()
    for _ in range(15):
        rl.is_allowed("collide", max_requests=15, window_seconds=60)
    assert rl.is_allowed("collide", max_requests=15, window_seconds=60) is False
    # The protected pool has never seen this key.
    assert rl.is_allowed("collide", max_requests=15, window_seconds=60, protected=True) is True


# ── Outliers ──────────────────────────────────────────────────────────────────


def test_zero_max_requests_does_not_grow_the_pool():
    """Every call is denied. The old code returned False BEFORE the eviction check, so a
    caller configured with max_requests=0 grew the table without bound."""
    rl = RateLimiter()
    rl._MAX_TRACKED = 20
    for i in range(500):
        assert rl.is_allowed(f"k-{i}", max_requests=0, window_seconds=60) is False
    assert len(rl._requests) <= rl._MAX_TRACKED


def test_stale_keys_are_reclaimed_within_a_bounded_scan():
    rl = RateLimiter()
    rl._MAX_TRACKED = 10
    rl._STALE_SECONDS = 0  # everything reads as idle the instant it is written
    for i in range(200):
        rl.is_allowed(f"k-{i}", max_requests=5, window_seconds=60)
    assert len(rl._requests) <= rl._MAX_TRACKED


def test_empty_and_unicode_identifiers_do_not_crash():
    rl = RateLimiter()
    for ident in ["", " ", "\n", "🙂" * 100, "a" * 10_000, "login:email:"]:
        assert rl.is_allowed(ident, max_requests=2, window_seconds=60) is True
        assert rl.is_allowed(ident, max_requests=2, window_seconds=60) is True
        assert rl.is_allowed(ident, max_requests=2, window_seconds=60) is False
