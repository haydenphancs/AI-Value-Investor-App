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

The REPORT adds an absolute-size floor on top (`effective_tier`, owner decision
2026-10-31): a move of 20%+ over a window, or a single session of 10%+ inside it, is
never "Typical", however volatile the stock. A stock that swings ~5% a day otherwise
scored a −20.6% month after a weak quarter as Typical (Intel, z 0.87), and the insight
called it routine trading. The Updates gate (`updates_materiality`) scores one session
and deliberately keeps the plain z tiers ("Report only").
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
# section decides significance FIRST and hunts for a catalyst/reason when the move
# clears this bar — or when the absolute-size floor lifts it, or an earnings report
# falls in or after its window (`_build_price_action`, 2026-10-31). 1.0σ = Notable+
# (the same Typical→Notable line _compute_price_volatility uses). Raise to 2.0
# (Unusual) or 3.0 (Extreme) to only explain larger moves.
_BIG_MOVE_Z: float = 1.0

# User-facing tier vocabulary, shared by the report AND the Updates gate.
TIER_TYPICAL = "Typical"
TIER_NOTABLE = "Notable"
TIER_UNUSUAL = "Unusual"
TIER_EXTREME = "Extreme"

# Rank of each tier, so the absolute-size floor below can raise a tier without ever
# lowering one, and window selection can compare floored and unfloored windows.
TIER_RANK: Dict[str, int] = {
    TIER_TYPICAL: 0, TIER_NOTABLE: 1, TIER_UNUSUAL: 2, TIER_EXTREME: 3,
}

# Absolute-size floor (report only — see the module docstring). |move| over a window at
# or above FLOOR_WINDOW_MOVE_PCT, or one session inside it at or above
# FLOOR_SESSION_MOVE_PCT, lifts a Typical window to Notable.
FLOOR_WINDOW_MOVE_PCT: float = 20.0
FLOOR_SESSION_MOVE_PCT: float = 10.0
# A big session counts toward the floor only when it happened in the last this-many
# calendar days, so a spike 50 days ago cannot switch the card to "Last 60 Days".
FLOOR_SESSION_RECENCY_DAYS: int = 30

FLOOR_WINDOW_MOVE = "window_move"
FLOOR_SINGLE_SESSION = "single_session"


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


_TRADING_DAYS_PER_YEAR: int = 252
_REALIZED_VOL_WINDOW: int = 21  # ~1 trading month, the standard short-horizon window


def realized_volatility_pct(
    prices: List[float], window: int = _REALIZED_VOL_WINDOW
) -> Optional[float]:
    """Annualized realized volatility, in percent, over the trailing `window` sessions.

    `prices` is oldest→newest. Returns None — never 0.0 — when there is not enough
    history: a flat 0% volatility reading would classify as the calmest possible market
    rather than as "unknown", and the caller's severity bands would then emit a
    confident all-clear built on nothing.

    ⚠️ This is REALIZED volatility, and it is NOT interchangeable with the VIX, which is
    IMPLIED. Measured against FRED `VIXCLS` over the trailing year: correlation on levels
    is only **+0.35**, and the ranges barely overlap (realized 5.4–19.7 vs VIX 13.5–31.1)
    because implied carries a variance risk premium. Any severity band calibrated for the
    VIX has to be re-derived for this, not reused.

    It is used because the VIX itself cannot be: FMP 402s `^VIX`, no ETF tracks it
    honestly (VIXY scores TE 77.3% against the index, a −37.9pp 1-year gap — roll decay,
    not tracking), and FRED's `VIXCLS` carries a Cboe copyright whose reprint permission
    runs to FRED rather than to us. SPY's own daily history is entitled and free of all
    three problems.
    """
    if window < 2 or len(prices) < window + 1:
        return None
    returns = _daily_returns(prices[-(window + 1):])
    sigma_daily = _std_dev_pop(returns)
    if sigma_daily is None or sigma_daily <= 0:
        return None
    annualized = sigma_daily * (_TRADING_DAYS_PER_YEAR ** 0.5) * 100.0
    return round(annualized, 4) if math.isfinite(annualized) else None


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


def _finite_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def max_session_move(
    prices: List[float], start_idx: int, recent_from_idx: int = 0,
) -> Tuple[Optional[float], Optional[int]]:
    """The largest single-session move (signed %, rounded to 2 dp) among the sessions
    that END inside a window, and the index of that session's close.

    A session ``i`` (close ``prices[i-1]`` → ``prices[i]``) counts when ``start_idx < i``
    (it lies inside the window that opens at ``prices[start_idx]``) and
    ``i >= recent_from_idx`` (it is recent enough to floor — see
    ``FLOOR_SESSION_RECENCY_DAYS``). Non-finite or non-positive closes are skipped.
    ``(None, None)`` when no session qualifies.
    """
    best_pct: Optional[float] = None
    best_idx: Optional[int] = None
    lo = max(1, start_idx + 1, recent_from_idx)
    for i in range(lo, len(prices)):
        prev = _finite_or_none(prices[i - 1])
        curr = _finite_or_none(prices[i])
        if prev is None or curr is None or prev <= 0:
            continue
        pct = (curr - prev) / prev * 100.0
        if best_pct is None or abs(pct) > abs(best_pct):
            best_pct, best_idx = pct, i
    if best_pct is None:
        return None, None
    return round(best_pct, 2), best_idx


def effective_tier(
    z: Optional[float],
    move_pct: Optional[float],
    session_pct: Optional[float] = None,
) -> Tuple[str, Optional[str]]:
    """The z tier, raised to Notable by the absolute-size floor. Returns
    ``(tier, floor_reason)`` where ``floor_reason`` is ``None`` unless the floor is
    what lifted the tier (``FLOOR_WINDOW_MOVE`` or ``FLOOR_SINGLE_SESSION``).

    The floor never lowers a tier and applies when σ is unknown (``z`` None). The
    window move is judged at the one decimal the card prints, so a move shown as
    "−20.0%" is floored and "−19.9%" is not. Non-finite inputs never trigger it.
    """
    z_f = _finite_or_none(z)
    tier = _tier_for_z(z_f)
    if TIER_RANK[tier] >= TIER_RANK[TIER_NOTABLE]:
        return tier, None
    move = _finite_or_none(move_pct)
    if move is not None and abs(round(move, 1)) >= FLOOR_WINDOW_MOVE_PCT:
        return TIER_NOTABLE, FLOOR_WINDOW_MOVE
    session = _finite_or_none(session_pct)
    if session is not None and abs(session) >= FLOOR_SESSION_MOVE_PCT:
        return TIER_NOTABLE, FLOOR_SINGLE_SESSION
    return tier, None


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
        # Absolute-size floor on the chosen window (`effective_tier`): the reason it
        # lifted the tier (None when it did not), and the window's largest recent
        # single session (signed %, and the index of its close).
        "chosen_floor": None,
        "chosen_session_pct": None,
        "chosen_session_idx": None,
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
    if newest is None or not math.isfinite(newest):
        # A non-finite latest close makes every window's move_pct/z NaN. The tier
        # ladder (`nan >= k` is always False) would then mislabel a genuinely
        # large move as Typical and lose its catalyst, and `"{:+.1f}".format(nan)`
        # leaks "nan%" into the prompt. Bail with sigma only — no bogus windows.
        return out
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
            if not oldest or not math.isfinite(oldest) or oldest <= 0:
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
            if not oldest or not math.isfinite(oldest) or oldest <= 0:
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

    # Absolute-size floor per window (`effective_tier`). A big session counts only when
    # it closed in the last FLOOR_SESSION_RECENCY_DAYS (calendar days in production,
    # sessions in the trading-day test path).
    if price_dates:
        recent_from_idx = bisect.bisect_right(
            price_dates, price_dates[-1] - timedelta(days=FLOOR_SESSION_RECENCY_DAYS),
        )
    else:
        recent_from_idx = max(1, len(prices) - FLOOR_SESSION_RECENCY_DAYS)
    for w in metrics:
        session_pct, session_idx = max_session_move(prices, w["ref_idx"], recent_from_idx)
        eff, floor = effective_tier(w["z"], w["move_pct"], session_pct)
        w["max_session_pct"] = session_pct
        w["max_session_idx"] = session_idx
        w["eff_tier"] = eff
        w["floor"] = floor

    # Pick the most unusual window: the highest effective tier, then the highest z (ties
    # keep the shortest window, `max`'s first match). Without a floor this is the old
    # argmax-z rule, since rank rises with z. If every window ranks Typical (genuinely
    # quiet stock-week), default to 30 days so the section still has something to show.
    most_unusual = max(metrics, key=lambda w: (TIER_RANK[w["eff_tier"]], w["z"]))
    if TIER_RANK[most_unusual["eff_tier"]] == 0:
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
    out["chosen_floor"] = chosen["floor"]
    out["chosen_session_pct"] = chosen["max_session_pct"]
    out["chosen_session_idx"] = chosen["max_session_idx"]
    out["tier"] = chosen["eff_tier"]
    return out
