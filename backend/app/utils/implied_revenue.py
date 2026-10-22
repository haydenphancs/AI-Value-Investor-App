"""TTM revenue implied by a market cap and FMP's price-to-sales ratio (2026-10-22).

The Moat card's industry concentration is a REVENUE-share HHI (owner decision 2026-10-22,
"Revenue-share HHI"): market-cap shares let one richly valued listing decide the label —
SpaceX's $1.9T cap was 54% of US-listed Aerospace & Defense, flipping it to "oligopoly",
while its ~$31B revenue is under 5% of the industry's.

Revenue = market cap ÷ `priceToSalesRatioTTM`, both from calls the callers already make (the
universe build's company-screener + ratios-ttm pass; the report collector's profile +
ratios-ttm), so it costs no extra FMP call. It is USD TTM revenue with no FX step: the
screener cap is USD and FMP's P/S is currency-consistent (checked 2026-10-22: GE $52.56B
implied vs $52.58B from four quarterly statements; SpaceX $31.2B vs $31.15B; CAE US$3.56B vs
C$4.93B, a ratio of 1.383 = USD/CAD).

Stdlib only (a leaf module, like `earnings_yield`): the universe builder, the industry
dossier and the report collector all import it.
"""

from __future__ import annotations

import math
from typing import Any, Optional

# An implied revenue above 20x the company's market cap (P/S under 0.05) is corrupt data, not
# a business: callers treat a revenue LEADER that implausible as "concentration unmeasured".
MIN_PLAUSIBLE_PS = 0.05


def _real(value: Any) -> Optional[float]:
    """A finite real number as a float; None for a bool, a string, None, NaN or inf (never
    coerced). An int too large for a float is None too, never an OverflowError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (OverflowError, ValueError):
        return None
    return out if math.isfinite(out) else None


def implied_ttm_revenue_usd(
    market_cap_usd: Any,
    ps_ttm: Any,
    revenue_per_share_ttm: Any = None,
) -> Optional[float]:
    """TTM revenue in USD from a USD market cap and FMP's `priceToSalesRatioTTM`.

      * cap missing / non-finite / <= 0 → None (nothing to divide).
      * P/S > 0 → cap / P/S — unless `revenue_per_share_ttm` was given and is not a positive
        number, in which case the two fields disagree → None.
      * P/S == 0 with revenue per share None or 0 → 0.0: FMP's answer for a company with no
        revenue (a pre-revenue SPAC or developer) — an explicit zero, counted as listed.
      * anything else (negative, non-finite, a string, a bool) → None.
    """
    cap = _real(market_cap_usd)
    if cap is None or cap <= 0:
        return None
    ps = _real(ps_ttm)
    if ps is None:
        return None
    revps = None if revenue_per_share_ttm is None else _real(revenue_per_share_ttm)
    if revenue_per_share_ttm is not None and revps is None:
        return None
    if ps > 0:
        if revps is not None and revps <= 0:
            return None
        out = cap / ps
        return out if math.isfinite(out) else None      # a denormal P/S would give inf
    if ps == 0 and (revps is None or revps == 0):
        return 0.0
    return None
