"""`implied_ttm_revenue_usd` — TTM revenue = market cap ÷ priceToSalesRatioTTM (2026-10-22).

The revenue-share concentration (owner decision 2026-10-22) reads it from the universe
build and the report collector; a wrong or coerced value would move an industry's label.
"""

from __future__ import annotations

import math

import pytest

from app.utils.implied_revenue import MIN_PLAUSIBLE_PS, implied_ttm_revenue_usd


def test_cap_over_ps_is_the_revenue():
    # GE 2026-10-22: $301.68B cap / P/S 5.7402 = $52.56B (four quarters sum to $52.58B).
    assert implied_ttm_revenue_usd(301.68e9, 5.7402) == pytest.approx(52.556e9, rel=1e-3)
    assert implied_ttm_revenue_usd(301.68e9, 5.7402, 53.1) == pytest.approx(52.556e9, rel=1e-3)


def test_an_explicit_zero_revenue_company_is_zero():
    assert implied_ttm_revenue_usd(1.2e9, 0.0, 0.0) == 0.0
    assert implied_ttm_revenue_usd(1.2e9, 0, None) == 0.0


@pytest.mark.parametrize("cap,ps,revps", [
    (1e9, 0.0, 2.5),                 # P/S zero but revenue per share positive: inconsistent
    (1e9, 3.0, 0.0),                 # P/S positive but no revenue per share: inconsistent
    (1e9, 3.0, -1.0),
    (1e9, -2.0, None),               # negative P/S
    (1e9, float("nan"), None),
    (1e9, float("inf"), None),
    (1e9, "1.2", None),              # never coerced
    (1e9, True, None),
    (1e9, None, None),
    (0.0, 3.0, None),                # no cap
    (-5e9, 3.0, None),
    (float("nan"), 3.0, None),
    ("1e9", 3.0, None),
    (1e9, 3.0, "10"),                # a revenue-per-share string is a disagreement, not None
    (1e9, 3.0, float("nan")),
    (10 ** 400, 3.0, None),          # too big for a float: None, never OverflowError
])
def test_unusable_inputs_give_none(cap, ps, revps):
    assert implied_ttm_revenue_usd(cap, ps, revps) is None


def test_the_plausibility_floor_means_twenty_times_cap():
    assert MIN_PLAUSIBLE_PS == 0.05
    assert math.isclose(implied_ttm_revenue_usd(1e9, MIN_PLAUSIBLE_PS), 20e9)


def test_a_denormal_ps_gives_none_not_inf():
    """cap / 1e-320 overflows to inf; the contract is a finite revenue or None."""
    assert implied_ttm_revenue_usd(1e9, 1e-320) is None
