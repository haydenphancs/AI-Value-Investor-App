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
from typing import Any, Optional

#: The `sector_benchmarks` metrics that are a margin of revenue. A STORED median of exactly
#: 0 for one of them is the no-revenue answer above counted as a figure: the fiscal rows the
#: 2026-10-04 run wrote (before this rule reached the producers) held 1,113 of them, drawn as
#: a 0.0% peer line on Profit Power, the report drill-down and Cay AI's grounding (Healthcare
#: sector net margin 0.0% for every year 2016-2025, n ~440). The lookup never serves one
#: (`sector_benchmark_lookup.servable_benchmark_rows`, 2026-10-21).
MARGIN_METRICS = frozenset({"gross_margin", "operating_margin", "net_margin", "fcf_margin"})


def is_zero_median(value: Any) -> bool:
    """True when a stored median reads as exactly 0 (``0``, ``0.0``, ``-0.0``,
    ``"0.000000"``). Anything that is not a number is False: unreadable values are another
    gate's job, and guessing would hide real rows."""
    if isinstance(value, bool):
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


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


def free_cash_flow_reading(value: Optional[float]) -> Optional[float]:
    """Free cash flow (a total or per share) that can be a real figure, else None.

    FMP fills a cash-flow statement it does not have with zeros: HSBC, a half-year filer,
    reads freeCashFlow = operatingCashFlow = capitalExpenditure = 0 for every quarter, and
    JPM's newest, not-yet-filed quarter read the same (live 2026-10-21). Counted as a figure,
    that 0 drew a flat 0.0% FCF-margin line for the company and set 77 calendar-quarter
    FCF-margin MEDIANS to exactly 0 on the 2026-10-21 recompute (Banks - Diversified alone
    23). A true free cash flow of exactly 0 to the last unit is vanishingly rare, so an exact
    0 is no reading — the same rule as `revenue_backed_margin`. A non-finite value is no
    reading either; a negative one is real (kept)."""
    if value is None or not math.isfinite(value) or value == 0:
        return None
    return value


def revenue_scaled_ratio(value: Optional[float]) -> Optional[float]:
    """Price-to-sales or asset turnover, else None: both are positive whenever the company
    has revenue (price / revenue per share; revenue / assets), so FMP's 0 (no revenue) or a
    negative (negative revenue) is no reading, nor is a non-finite value."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return value
