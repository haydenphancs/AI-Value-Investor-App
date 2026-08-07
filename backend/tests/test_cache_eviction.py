"""
Tests for the size-bounded in-memory cache eviction added to the detail
services (stock/etf/crypto/index). Without a cap these module-level dicts grew
unbounded in the long-lived Railway process — expired rows are only swept
lazily on read of the same key.

Eviction is least-recently-written: a write moves the key to the tail and, once
the cap is exceeded, entries are dropped from the head.
"""

import importlib

import pytest


@pytest.mark.parametrize(
    "modname",
    [
        "app.services.stock_overview_service",
        "app.services.etf_service",
        "app.services.crypto_service",
        "app.services.index_service",
        # Added after an audit found these 16 unbounded: `_cache_get` only prunes a key when
        # that SAME key is read again after expiry, so a ticker fetched once and never
        # revisited stayed resident forever. Seventeen such dicts on a long-lived Railway
        # container is a slow leak whose only resolution is an OOM restart — which drops
        # every in-flight report with it.
        "app.services.analyst_service",
        "app.services.earnings_service",
        "app.services.growth_service",
        "app.services.growth_snapshot_service",
        "app.services.health_check_service",
        "app.services.health_snapshot_service",
        "app.services.home_service",
        "app.services.ownership_snapshot_service",
        "app.services.profit_power_service",
        "app.services.profitability_snapshot_service",
        "app.services.revenue_breakdown_service",
        "app.services.sector_benchmark_lookup",
        "app.services.sentiment_service",
        "app.services.signal_of_confidence_service",
        "app.services.technical_analysis_service",
        "app.services.valuation_snapshot_service",
    ],
)
def test_cache_is_bounded_and_keeps_recent(modname):
    mod = importlib.import_module(modname)
    cap = mod._CACHE_MAX_ENTRIES
    assert cap > 0

    mod._cache.clear()
    try:
        # Write well past the cap.
        total = cap + 250
        for i in range(total):
            mod._cache_set(f"k{i}", i)

        # Never exceeds the cap.
        assert len(mod._cache) <= cap

        # The most-recently-written keys survive; the oldest were evicted.
        assert f"k{total - 1}" in mod._cache
        assert mod._cache_get(f"k{total - 1}") == total - 1
        assert f"k0" not in mod._cache
    finally:
        mod._cache.clear()


def test_rewrite_moves_key_to_tail_and_spares_it_from_eviction():
    """A hot key that keeps getting re-written should not be evicted just because
    it was inserted early (least-recently-WRITTEN, not first-inserted)."""
    mod = importlib.import_module("app.services.stock_overview_service")
    cap = mod._CACHE_MAX_ENTRIES

    mod._cache.clear()
    try:
        mod._cache_set("hot", "v0")
        # Fill to just under the cap with unique keys.
        for i in range(cap - 1):
            mod._cache_set(f"cold{i}", i)
        # Re-write the hot key so it moves to the tail (freshest).
        mod._cache_set("hot", "v1")
        # Now push more entries so the head (oldest cold keys) is evicted.
        for i in range(cap - 1, cap + 100):
            mod._cache_set(f"cold{i}", i)

        assert len(mod._cache) <= cap
        # 'hot' survived because its last write kept it near the tail.
        assert mod._cache.get("hot") is not None
        assert mod._cache_get("hot") == "v1"
        # The earliest cold key was evicted.
        assert "cold0" not in mod._cache
    finally:
        mod._cache.clear()


def test_whale_service_caches_are_bounded_too():
    """`whale_service` keeps SEVERAL caches and passes the target dict into `_cache_set`, so
    it cannot join the parametrised list above — but it had the identical unbounded leak and
    must be covered."""
    import importlib

    mod = importlib.import_module("app.services.whale_service")
    cap = mod._CACHE_MAX_ENTRIES
    cache: dict = {}
    for i in range(cap + 50):
        mod._cache_set(cache, f"k{i}", i)

    assert len(cache) <= cap, f"cache grew to {len(cache)}, cap is {cap}"
    # The most recent writes must survive — evicting those would defeat the cache.
    assert f"k{cap + 49}" in cache
    assert "k0" not in cache, "eviction should drop the least-recently-written first"
