"""
Math/regression tests for the Hedge Funds quarterly flow pipeline
(`HoldersService` static helpers).

Background — the bug these guard against:
  The hedge_fund_quarters cache froze an early, incomplete-filing computation
  of the most-recent quarter. With few 13F filers reported, the price fallback
  `totalInvested / numberOf13Fshares` produced a wildly inflated price (~$2,985
  vs a real ~$147), so that quarter's buy/sell came out ~15x too large. On the
  chart's shared y-axis that one bar dominated and flattened every other
  quarter to ~0.

Two defenses are tested here:
  1. `_resolve_quarter_price` prefers the nearest known quarter-end close over
     the explosive `totalInvested / shares` ratio.
  2. The volatile-quarter window means the most recent quarters are always
     recomputed (covered indirectly via `_generate_quarter_keys`).

All pure/static — no network, no Supabase, no instance construction.
"""

from __future__ import annotations

from app.services.holders_service import (
    HoldersService,
    _REFRESH_RECENT_QUARTERS,
)


# A realistic ORCL-ish quarter-end price ladder (year, quarter) -> close.
_QTR_PRICES = {
    (2024, 2): 141.20,
    (2024, 3): 170.40,
    (2024, 4): 166.64,
    (2025, 1): 139.81,
    (2025, 2): 218.63,
    (2025, 3): 281.24,
    (2025, 4): 194.91,
    (2026, 1): 147.11,
}


# ── _resolve_quarter_price ────────────────────────────────────────────


def test_resolve_price_uses_quarter_close_when_present():
    price = HoldersService._resolve_quarter_price({}, _QTR_PRICES, 2026, 1)
    assert price == 147.11


def test_resolve_price_uses_nearest_when_quarter_missing():
    """Quarter close absent → nearest known quarter, NOT totalInvested/shares."""
    qp = {k: v for k, v in _QTR_PRICES.items() if k != (2026, 1)}
    # Incomplete-filing row: tiny share count + big dollars => ratio explodes.
    incomplete = {
        "numberOf13Fshares": 16_700_000,
        "totalInvested": 50_000_000_000,  # ratio would be ~$2,994
    }
    price = HoldersService._resolve_quarter_price(incomplete, qp, 2026, 1)
    # Nearest available quarter is 2025Q4 = 194.91.
    assert price == 194.91
    # Guard: must never fall back to the explosive ratio when prices exist.
    assert price < 1000


def test_resolve_price_ratio_only_as_last_resort():
    """With no quarter prices at all, the ratio fallback is allowed."""
    data = {"numberOf13Fshares": 1_000_000_000, "totalInvested": 150_000_000_000}
    price = HoldersService._resolve_quarter_price(data, {}, 2026, 1)
    assert abs(price - 150.0) < 1e-6


# ── _compute_quarter_flow ─────────────────────────────────────────────


def test_compute_quarter_flow_net_sign_and_magnitude():
    """Net = sharesChange × close / 1e6; negative change → net outflow."""
    data = {
        "numberOf13FsharesChange": -40_832_086,
        "newPositions": 204, "increasedPositions": 1707,
        "closedPositions": 525, "reducedPositions": 1412,
        "numberOf13Fshares": 1_207_834_055, "totalInvested": 236_578_042_827,
    }
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(
        data, _QTR_PRICES, 2026, 1
    )
    # -40.83M shares × $147.11 / 1e6 ≈ -6,007M
    assert -6100 < net < -5900
    assert buyers == 1911 and sellers == 1937
    # Gross buy/sell preserve the net constraint buy - sell == net.
    assert abs((buy - sell) - net) < 1.0
    # And stay in a sane band — nowhere near the old ~250,000M outlier.
    assert max(buy, sell) < 50_000


def test_compute_quarter_flow_incomplete_filing_does_not_explode():
    """Regression: an incomplete recent quarter with a missing close must NOT
    produce the ~15x inflated bar that flattened the chart."""
    qp = {k: v for k, v in _QTR_PRICES.items() if k != (2026, 1)}
    incomplete = {
        "numberOf13FsharesChange": -40_000_000,
        "numberOf13Fshares": 16_700_000,         # tiny -> ratio would explode
        "totalInvested": 50_000_000_000,
        "newPositions": 5, "increasedPositions": 3,
        "closedPositions": 4, "reducedPositions": 2,
    }
    buy, sell, net, _, _ = HoldersService._compute_quarter_flow(
        incomplete, qp, 2026, 1
    )
    # With the safe ~$195 price: -40M × 195 / 1e6 ≈ -7,800M (sane).
    # With the old explosive ~$2,994 price it would have been ~-120,000M.
    assert max(abs(buy), abs(sell)) < 50_000


# ── volatile refresh window ───────────────────────────────────────────


def test_recent_quarters_are_marked_volatile():
    """The most recent _REFRESH_RECENT_QUARTERS keys are the ones forced to
    recompute (never served stale from cache)."""
    pairs = HoldersService._generate_quarter_keys(8)
    volatile = pairs[-_REFRESH_RECENT_QUARTERS:]
    assert len(volatile) == _REFRESH_RECENT_QUARTERS
    # They must be the chronologically newest quarters in the window.
    assert volatile == sorted(pairs)[-_REFRESH_RECENT_QUARTERS:]
