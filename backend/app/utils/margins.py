"""Revenue-denominated figures FMP reports for a company with NO revenue (2026-10-11).

FMP divides by revenue and answers 0 when there is none: grossProfitMargin =
operatingProfitMargin = netProfitMargin = 0 with revenuePerShare 0 in /ratios (every fiscal
year and quarter) and /ratios-ttm, and priceToSalesRatio / assetTurnover 0 too (VKTX, a
pre-revenue biotech, live 2026-10-11). Read as figures, those zeros said a heavy loss-maker
"breaks even", scored Brand Power and Cost Advantage 0.0/10 for every pre-revenue company,
and pinned Biotechnology's peer net-margin median at 0.0% (72 of 213 companies; the
revenue-backed median is -76.9%).

Pure and dependency-free so the median producers (`sector_benchmark_service`,
`industry_benchmark_service`), moat scoring and the report collector share ONE rule.
`profitability_snapshot_service._ttm_margin` (the Profitability card) applies the same rule
and, in addition, requires the TTM revenue per share to be present.
"""

import math
from typing import Optional


def revenue_backed_margin(
    margin: Optional[float], revenue_per_share: Optional[float],
) -> Optional[float]:
    """A margin that can be a real margin, else None.

    Dropped: an exact 0 (the no-revenue answer; a true break-even to the last digit is
    vanishingly rare, and the Profitability card drops it too) and any margin whose revenue
    per share is present and not positive (no revenue, or a negative one that sign-flips a
    loss into a "+113%" margin). A MISSING revenue per share keeps a non-zero margin: FMP
    sent the field on all 1,325 sampled fiscal ratio rows, and requiring it would let a
    vendor schema change silently empty every margin median. A non-finite margin is no
    reading; a non-finite revenue per share counts as missing.
    """
    if margin is None or not math.isfinite(margin) or margin == 0:
        return None
    if (
        revenue_per_share is not None
        and math.isfinite(revenue_per_share)
        and revenue_per_share <= 0
    ):
        return None
    return margin


def revenue_scaled_ratio(value: Optional[float]) -> Optional[float]:
    """Price-to-sales or asset turnover, else None: both are positive whenever the company
    has revenue (price / revenue per share; revenue / assets), so FMP's 0 (no revenue) or a
    negative (negative revenue) is no reading, nor is a non-finite value."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return value
