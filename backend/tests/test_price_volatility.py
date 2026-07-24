"""The shared volatility math (single source of truth for the report's Recent
Price Movement section, the Updates trigger, and the σ precompute).

Pure functions — everything here is inline data, no network.
"""

import math

from app.services.price_volatility import (
    TIER_EXTREME,
    TIER_NOTABLE,
    TIER_TYPICAL,
    TIER_UNUSUAL,
    _compute_price_volatility,
    _daily_returns,
    _std_dev_pop,
    _tier_for_z,
    _z_score_for_window,
)


def test_std_dev_pop_needs_two_values():
    assert _std_dev_pop([]) is None
    assert _std_dev_pop([0.01]) is None


def test_std_dev_pop_matches_population_formula():
    vals = [0.01, -0.01, 0.02, -0.02]
    mean = sum(vals) / len(vals)
    expected = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    assert _std_dev_pop(vals) == expected


def test_daily_returns_skips_zero_and_missing_prior():
    assert _daily_returns([100.0, 110.0]) == [0.1]
    # A zero prior close is skipped rather than dividing by zero.
    assert _daily_returns([0.0, 110.0]) == []
    assert _daily_returns([100.0]) == []


def test_daily_returns_drops_non_finite_closes():
    # FMP emits NaN/Infinity on thin/just-listed symbols; `curr is not None` does
    # NOT reject those. A non-finite close is skipped (BOTH pairs touching it are
    # dropped), never propagated as a nan return that would poison σ.
    nan, inf = float("nan"), float("inf")
    assert _daily_returns([100.0, nan, 110.0]) == []   # both adjacent pairs invalid
    assert _daily_returns([nan, 110.0]) == []
    assert _daily_returns([100.0, nan]) == []
    # Finite returns on either side of a non-finite gap survive, and none is nan/inf.
    out = _daily_returns([100.0, 110.0, nan, 100.0, 105.0, inf, 110.0])
    assert out == [0.1, 0.05]
    assert all(math.isfinite(r) for r in out)


def test_std_dev_pop_rejects_non_finite_result():
    # Defence in depth: a nan/inf sneaking into the values must yield None, never
    # a nan σ that escapes the `<= 0` guard downstream.
    assert _std_dev_pop([0.01, float("nan")]) is None
    assert _std_dev_pop([0.01, float("inf")]) is None


def test_compute_volatility_with_a_nan_close_never_yields_nan_sigma():
    # A single NaN baseline close must NEVER produce a nan sigma_daily (which would
    # be mislabeled Typical and, in the cache path, written as a poisoned row). The
    # bad pair is dropped and σ is computed from the surviving returns.
    prices = [100.0 + (i % 2) * 0.5 for i in range(199)] + [float("nan")]
    out = _compute_price_volatility(prices)
    sigma = out["sigma_daily"]
    assert sigma is None or math.isfinite(sigma)
    assert not (sigma is not None and math.isnan(sigma))
    # The clean surrounding data still yields a positive σ (not None-by-poison).
    assert sigma is not None and sigma > 0
    # And a fully clean array still computes a finite σ.
    clean = [100.0 * (1.01 if i % 2 else 0.99) for i in range(200)]
    assert _compute_price_volatility(clean)["sigma_daily"] is not None


def test_z_score_uses_sqrt_n_scaling():
    # σ_daily = 1%; a 3% move over 9 trading days → 9-day σ = 1%·√9 = 3% → z = 1.0
    z = _z_score_for_window(3.0, 0.01, 9)
    assert z is not None and math.isclose(z, 1.0, rel_tol=1e-9)
    # Same 3% over 1 day → z = 3.0
    assert math.isclose(_z_score_for_window(3.0, 0.01, 1), 3.0, rel_tol=1e-9)


def test_z_score_degrades_on_bad_inputs():
    assert _z_score_for_window(5.0, None, 5) is None
    assert _z_score_for_window(5.0, 0.0, 5) is None
    assert _z_score_for_window(5.0, -0.01, 5) is None
    assert _z_score_for_window(5.0, 0.01, 0) is None


def test_tier_boundaries_return_the_shared_constants():
    assert _tier_for_z(None) == TIER_TYPICAL
    assert _tier_for_z(0.99) == TIER_TYPICAL
    assert _tier_for_z(1.0) == TIER_NOTABLE
    assert _tier_for_z(1.99) == TIER_NOTABLE
    assert _tier_for_z(2.0) == TIER_UNUSUAL
    assert _tier_for_z(2.99) == TIER_UNUSUAL
    assert _tier_for_z(3.0) == TIER_EXTREME
    # The literal values must stay these exact strings (report + iOS depend on them).
    assert (TIER_TYPICAL, TIER_NOTABLE, TIER_UNUSUAL, TIER_EXTREME) == (
        "Typical", "Notable", "Unusual", "Extreme",
    )


def test_compute_volatility_needs_at_least_30_closes():
    out = _compute_price_volatility([100.0] * 25)
    assert out["sigma_daily"] is None
    assert out["tier"] == TIER_TYPICAL


def test_compute_volatility_sigma_matches_the_helper_over_the_baseline():
    # A deterministic zig-zag so σ is well-defined and reproducible.
    prices = [100.0]
    for i in range(1, 200):
        prices.append(prices[-1] * (1.01 if i % 2 else 0.99))
    out = _compute_price_volatility(prices)  # trading-day mode (no dates)
    baseline_slice = prices[-(180 + 1):]
    expected_sigma = _std_dev_pop(_daily_returns(baseline_slice))
    assert out["sigma_daily"] == expected_sigma
    # A window was chosen and a tier derived from its z.
    assert out["chosen_window"] in (7, 15, 30, 45, 60)
    assert out["tier"] in (TIER_TYPICAL, TIER_NOTABLE, TIER_UNUSUAL, TIER_EXTREME)


def test_a_big_recent_move_lands_in_an_elevated_tier():
    # Flat history (σ tiny) then a large jump on the last day → high z → elevated tier.
    prices = [100.0 + (i % 2) * 0.05 for i in range(199)]  # ~flat, tiny σ
    prices.append(prices[-1] * 1.10)                        # +10% shock
    out = _compute_price_volatility(prices)
    assert out["sigma_daily"] is not None
    assert out["tier"] in (TIER_NOTABLE, TIER_UNUSUAL, TIER_EXTREME)


def test_nan_latest_close_degrades_to_typical_not_a_nan_tier():
    # A non-finite LATEST close makes every window's move_pct/z NaN. Unguarded,
    # `max(key=z)` picks the first window and `nan < 1.0` (False) mislabels a real
    # move; "{:+.1f}".format(nan) also leaks "nan%" into the catalyst prompt. The
    # guard bails with sigma only → Typical, no bogus windows, nothing NaN.
    prices = [100.0 * (1.01 if i % 2 else 0.99) for i in range(199)] + [float("nan")]
    out = _compute_price_volatility(prices)
    assert out["tier"] == TIER_TYPICAL
    assert not out.get("windows")                       # no windows emitted
    # No NaN escaped into the chosen-move fields (the "nothing to show" defaults).
    assert out.get("chosen_move_pct") is None
    assert out.get("chosen_ref_idx") is None
    # Inf behaves the same.
    inf_prices = prices[:-1] + [float("inf")]
    assert _compute_price_volatility(inf_prices)["tier"] == TIER_TYPICAL


def test_nan_intermediate_close_skips_only_that_window():
    # Trading-day mode. A NaN at a window's `oldest` (ref_idx) slips past the old
    # `not oldest or oldest <= 0` guard (`not nan` is False, `nan <= 0` is False)
    # and yields a NaN-z window that can win the max. It must be SKIPPED instead,
    # while the surviving windows compute a finite tier.
    prices = [100.0 + (i % 2) * 0.05 for i in range(199)]   # ~flat, tiny σ
    prices.append(prices[-1] * 1.10)                         # +10% shock on the last day
    prices[len(prices) - 8] = float("nan")                   # the 7-day window's `oldest`
    out = _compute_price_volatility(prices)
    assert out["tier"] in (TIER_TYPICAL, TIER_NOTABLE, TIER_UNUSUAL, TIER_EXTREME)
    for w in out.get("windows", []):
        assert math.isfinite(w["z"]) and math.isfinite(w["move_pct"])
    assert all(w["days"] != 7 for w in out.get("windows", []))   # poisoned window absent
