"""
Volatility-relative price-move math — the SINGLE SOURCE OF TRUTH.

Extracted verbatim from ``agents/ticker_report_data_collector.py`` so the SAME
z-score method that powers the report's "Recent Price Movement" section also
drives the Updates-screen insight trigger (``updates_materiality.classify_move``)
and the daily σ precompute (``volatility_cache_service``). A stock therefore gets
the SAME tier on the report and on the Updates card.

PURE — no network, no Supabase, no service imports (only ``bisect``/``datetime``/
typing) — so the pure, exhaustively-testable materiality gate can import it
without picking up the collector's heavy dependency tree.

Method: σ_daily = population std of daily returns over a ``_BASELINE_DAYS``-day
baseline; a move over N trading days scores as ``z = |move%| / (σ_daily·√N·100)``;
the tier is Typical / Notable (z≥1) / Unusual (z≥2) / Extreme (z≥3). The baseline
length is independent of the move horizon (√N scaling handles that) and is a
tunable constant.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

_EVAL_WINDOWS: Tuple[int, ...] = (7, 15, 30, 45, 60)  # incl. 60d (2mo) so a slow build is detectable
_BASELINE_DAYS: int = 180
_DEFAULT_WINDOW: int = 30

# |z| threshold that defines a "BIG move" worth explaining. The price-action
# section decides significance FIRST and only hunts for a catalyst/reason when
# the move clears this bar — never the other way round. 1.0σ = Notable+ (the
# same Typical→Notable line _compute_price_volatility uses). Raise to 2.0
# (Unusual) or 3.0 (Extreme) to only explain larger moves.
_BIG_MOVE_Z: float = 1.0

# User-facing tier vocabulary, shared by the report AND the Updates gate.
TIER_TYPICAL = "Typical"
TIER_NOTABLE = "Notable"
TIER_UNUSUAL = "Unusual"
TIER_EXTREME = "Extreme"


def _daily_returns(prices: List[float]) -> List[float]:
    """Daily simple returns from a price array (oldest→newest).

    Skips pairs where the prior close is zero/missing, and — critically — where
    EITHER close is non-finite. FMP emits ``NaN``/``Infinity`` JSON tokens on
    thin / just-listed symbols (Python's ``json`` parses them into float
    nan/inf); ``curr is not None`` does NOT reject those, and a single nan close
    propagates through the mean/variance and poisons the whole σ. Finite-guard
    both sides so one bad row cannot NaN the baseline (CLAUDE.md hardening rule).
    """
    out: List[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        curr = prices[i]
        if (
            prev is not None and curr is not None
            and math.isfinite(prev) and math.isfinite(curr)
            and prev > 0
        ):
            out.append((curr - prev) / prev)
    return out


def _std_dev_pop(values: List[float]) -> Optional[float]:
    """Population standard deviation. None if <2 values or the result is
    non-finite (defence in depth against a nan/inf sneaking into ``values``)."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    sigma = var ** 0.5
    return sigma if math.isfinite(sigma) else None


def _z_score_for_window(
    move_pct: float, sigma_daily: Optional[float], days: int,
) -> Optional[float]:
    """Absolute z-score for an N-day move given the daily-return σ.
    Uses the random-walk √N scaling rule: σ over N days = σ_daily × √N.
    """
    if sigma_daily is None or sigma_daily <= 0 or days <= 0:
        return None
    n_day_sigma_pct = sigma_daily * (days ** 0.5) * 100
    if n_day_sigma_pct <= 0:
        return None
    return abs(move_pct) / n_day_sigma_pct


def _tier_for_z(z: Optional[float]) -> str:
    """Map |z| → user-facing tier label."""
    if z is None:
        return TIER_TYPICAL
    if z >= 3:
        return TIER_EXTREME
    if z >= 2:
        return TIER_UNUSUAL
    if z >= 1:
        return TIER_NOTABLE
    return TIER_TYPICAL


def _compute_price_volatility(
    prices: List[float],
    price_dates: Optional[List[date]] = None,
    baseline_days: int = _BASELINE_DAYS,
    windows: Tuple[int, ...] = _EVAL_WINDOWS,
) -> Dict[str, Any]:
    """Compute the daily-return σ over the baseline plus per-window z-scores.

    Returns a dict with sigma_daily, per-window metrics, the chosen window
    (argmax |z|, or _DEFAULT_WINDOW when every window is within ±1σ), the
    tier label, the chosen-window's move/z/band, and the index in `prices`
    of the reference close used for the chosen window (so the caller can
    compute change_pct against the same anchor instead of recomputing).

    `windows` is interpreted in calendar days when `price_dates` is given
    (production path): "30 days" means today vs the close on or before
    today−30 calendar days. When `price_dates` is omitted (tests with
    synthetic price arrays), the function falls back to trading-day
    indexing so historical fixtures keep working unchanged.

    When fewer than 30 daily returns are available the result still has the
    same shape but sigma_daily is None and the chosen window stays at the
    default — callers should treat tier as "Typical" without the σ math.
    """
    out: Dict[str, Any] = {
        "sigma_daily": None,
        "windows": [],
        "chosen_window": _DEFAULT_WINDOW,
        "chosen_ref_idx": None,
        "tier": TIER_TYPICAL,
        "chosen_z": None,
        "chosen_move_pct": None,
        "chosen_band_pct": None,
    }
    if len(prices) < 30:
        return out
    # If a date list was provided but doesn't line up, drop it and fall
    # back to trading-day mode rather than emitting subtly wrong windows.
    if price_dates is not None and len(price_dates) != len(prices):
        price_dates = None

    # Use the last `baseline_days + 1` closes so the returns array has
    # at most `baseline_days` entries. The +1 covers the inter-day diff.
    baseline_slice = prices[-(baseline_days + 1):]
    sigma_daily = _std_dev_pop(_daily_returns(baseline_slice))
    if sigma_daily is None or not math.isfinite(sigma_daily) or sigma_daily <= 0:
        return out
    out["sigma_daily"] = sigma_daily

    newest = prices[-1]
    metrics: List[Dict[str, Any]] = []

    if price_dates:
        # Calendar-day mode (production).
        today = price_dates[-1]
        for n in windows:
            target = today - timedelta(days=n)
            # Rightmost index whose date is <= target (handles
            # weekends/holidays by stepping back to the prior trading day).
            idx = bisect.bisect_right(price_dates, target) - 1
            if idx < 0 or idx >= len(prices) - 1:
                continue
            oldest = prices[idx]
            if not oldest or oldest <= 0:
                continue
            move_pct = (newest - oldest) / oldest * 100
            # Trading-day count actually elapsed in this calendar window;
            # feeds the √n scaling so the σ band shrinks accordingly
            # (e.g., 30 calendar days ≈ 21 trading days → smaller band
            # than the old code's √30).
            trading_days = len(prices) - 1 - idx
            n_day_sigma_pct = sigma_daily * (trading_days ** 0.5) * 100
            z = abs(move_pct) / n_day_sigma_pct if n_day_sigma_pct > 0 else 0.0
            metrics.append({
                "days": n,
                "ref_idx": idx,
                "move_pct": round(move_pct, 2),
                "z": round(z, 2),
                "band_2sigma": round(n_day_sigma_pct * 2, 2),
            })
    else:
        # Trading-day mode (test fixtures with synthetic price arrays).
        for n in windows:
            if len(prices) <= n:
                continue
            ref_idx = len(prices) - (n + 1)
            oldest = prices[ref_idx]
            if not oldest or oldest <= 0:
                continue
            move_pct = (newest - oldest) / oldest * 100
            n_day_sigma_pct = sigma_daily * (n ** 0.5) * 100
            z = abs(move_pct) / n_day_sigma_pct if n_day_sigma_pct > 0 else 0.0
            metrics.append({
                "days": n,
                "ref_idx": ref_idx,
                "move_pct": round(move_pct, 2),
                "z": round(z, 2),
                "band_2sigma": round(n_day_sigma_pct * 2, 2),
            })

    out["windows"] = metrics
    if not metrics:
        return out

    # Pick the most unusual window. If every window is within ±1σ
    # (genuinely quiet stock-week), default to 30 days so the section
    # still has something to show — `tier` will be "Typical".
    most_unusual = max(metrics, key=lambda w: w["z"])
    if most_unusual["z"] < 1.0:
        default = next(
            (w for w in metrics if w["days"] == _DEFAULT_WINDOW), most_unusual,
        )
        chosen = default
    else:
        chosen = most_unusual

    out["chosen_window"] = chosen["days"]
    out["chosen_ref_idx"] = chosen["ref_idx"]
    out["chosen_z"] = chosen["z"]
    out["chosen_move_pct"] = chosen["move_pct"]
    out["chosen_band_pct"] = chosen["band_2sigma"]
    out["tier"] = _tier_for_z(chosen["z"])
    return out
