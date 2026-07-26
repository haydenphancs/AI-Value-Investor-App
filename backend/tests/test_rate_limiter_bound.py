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
