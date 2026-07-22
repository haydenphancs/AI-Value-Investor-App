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
