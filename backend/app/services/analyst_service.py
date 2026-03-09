"""
Analyst Analysis Service — aggregates FMP analyst data, computes
consensus, momentum, and actions summary for the Analysis tab.

Pattern follows stock_overview_service.py: parallel FMP calls,
in-memory caching, helper functions for derived computations.
"""

import asyncio
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.analyst import (
    AnalystAction,
    AnalystActionsSummary,
    AnalystActionType,
    AnalystAnalysisResponse,
    AnalystConsensus,
    AnalystMomentumMonth,
    AnalystPriceTarget,
    AnalystRatingDistribution,
)

logger = logging.getLogger(__name__)

# ── In-memory cache (same pattern as stock_overview_service) ─────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str, ttl: float = _CACHE_TTL) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any):
    _cache[key] = (time.time(), value)


# ── FMP grade → category mapping ────────────────────────────────────
# Maps the varied FMP grade strings to our 5-bucket system.

_GRADE_TO_CATEGORY: Dict[str, str] = {
    # Strong Buy
    "strong buy": "Strong Buy",
    "long term buy": "Strong Buy",
    # Buy
    "buy": "Buy",
    "outperform": "Buy",
    "overweight": "Buy",
    "market outperform": "Buy",
    "sector outperform": "Buy",
    "positive": "Buy",
    "accumulate": "Buy",
    # Hold
    "neutral": "Hold",
    "hold": "Hold",
    "market perform": "Hold",
    "equal weight": "Hold",
    "perform": "Hold",
    "sector weight": "Hold",
    "sector perform": "Hold",
    "peer perform": "Hold",
    "in line": "Hold",
    # Sell
    "sell": "Sell",
    "underweight": "Sell",
    "underperform": "Sell",
    "negative": "Sell",
    "reduce": "Sell",
    # Strong Sell
    "strong sell": "Strong Sell",
}


def _classify_grade(grade: str) -> str:
    """Map an FMP grade string to one of the 5 distribution buckets."""
    return _GRADE_TO_CATEGORY.get(grade.lower().strip(), "Hold")


# ── FMP action mapping ──────────────────────────────────────────────

_FMP_ACTION_MAP = {
    "upgrade": AnalystActionType.UPGRADE,
    "downgrade": AnalystActionType.DOWNGRADE,
    "maintain": AnalystActionType.MAINTAIN,
    "hold": AnalystActionType.MAINTAIN,
    "init": AnalystActionType.INITIATED,
    "reiterated": AnalystActionType.REITERATED,
}


def _map_action(fmp_action: str) -> AnalystActionType:
    """Map FMP action string to our AnalystActionType enum."""
    return _FMP_ACTION_MAP.get(
        fmp_action.lower().strip(), AnalystActionType.MAINTAIN
    )


# ── Rating distribution from grades ────────────────────────────────

def _compute_distribution(
    grades: List[Dict],
) -> Tuple[Dict[str, int], int]:
    """
    Compute rating distribution from grades by taking each firm's
    most recent grade (grades are sorted newest-first by FMP).

    Returns (counts_dict, total_analysts).
    """
    seen_firms: set = set()
    counts: Counter = Counter()

    for g in grades:
        firm = g.get("gradingCompany", "").strip()
        if not firm or firm in seen_firms:
            continue
        seen_firms.add(firm)

        category = _classify_grade(g.get("newGrade", ""))
        counts[category] += 1

    distribution = {
        "Strong Buy": counts.get("Strong Buy", 0),
        "Buy": counts.get("Buy", 0),
        "Hold": counts.get("Hold", 0),
        "Sell": counts.get("Sell", 0),
        "Strong Sell": counts.get("Strong Sell", 0),
    }
    total = sum(distribution.values())
    return distribution, total


# ── Consensus computation ──────────────────────────────────────────

def _compute_consensus(
    strong_buy: int, buy: int, hold: int, sell: int, strong_sell: int,
) -> AnalystConsensus:
    """Weighted average score → consensus label."""
    total = strong_buy + buy + hold + sell + strong_sell
    if total == 0:
        return AnalystConsensus.HOLD

    score = (
        strong_buy * 5 + buy * 4 + hold * 3 + sell * 2 + strong_sell * 1
    ) / total

    if score >= 4.5:
        return AnalystConsensus.STRONG_BUY
    if score >= 3.5:
        return AnalystConsensus.BUY
    if score >= 2.5:
        return AnalystConsensus.HOLD
    if score >= 1.5:
        return AnalystConsensus.SELL
    return AnalystConsensus.STRONG_SELL


# ── Momentum computation ───────────────────────────────────────────

def _compute_momentum(
    grades: List[Dict], months: int = 12,
) -> Tuple[List[AnalystMomentumMonth], int, int]:
    """
    Group grades by month, count positive (upgrades + initiated) vs
    negative (downgrades) actions for each month.

    Returns (momentum_months, total_positive, total_negative).
    Always returns exactly ``months`` entries (filling zeros for
    months with no activity). The frontend filters to 6M or 1Y
    based on the user's toggle.
    """
    now = datetime.utcnow()

    # Pre-populate all month slots so gaps show as zero.
    # Use proper month arithmetic to avoid timedelta rounding issues.
    month_keys: List[str] = []
    cur_year = now.year
    cur_month = now.month
    for i in range(months - 1, -1, -1):
        # Go back i months
        m = cur_month - i
        y = cur_year
        while m <= 0:
            m += 12
            y -= 1
        month_keys.append(f"{y:04d}-{m:02d}")

    monthly: Dict[str, Dict[str, int]] = {
        k: {"positive": 0, "negative": 0} for k in month_keys
    }

    for g in grades:
        date_str = g.get("date", "")
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        sort_key = dt.strftime("%Y-%m")
        if sort_key not in monthly:
            continue

        action = g.get("action", "").lower().strip()
        if action in ("upgrade", "init"):
            monthly[sort_key]["positive"] += 1
        elif action == "downgrade":
            monthly[sort_key]["negative"] += 1

    # Build result in chronological order
    result: List[AnalystMomentumMonth] = []
    total_pos = 0
    total_neg = 0

    for sort_key in month_keys:
        counts = monthly[sort_key]
        try:
            dt = datetime.strptime(sort_key, "%Y-%m")
            month_label = dt.strftime("%b")
        except ValueError:
            month_label = sort_key

        result.append(AnalystMomentumMonth(
            month=month_label,
            positive_count=counts["positive"],
            negative_count=counts["negative"],
        ))
        total_pos += counts["positive"]
        total_neg += counts["negative"]

    return result, total_pos, total_neg


# ── Actions summary ────────────────────────────────────────────────

def _compute_actions_summary(grades: List[Dict]) -> AnalystActionsSummary:
    """Count upgrades, maintains, downgrades from the last 12 months only."""
    cutoff = datetime.utcnow() - timedelta(days=365)
    upgrades = 0
    maintains = 0
    downgrades = 0

    for g in grades:
        date_str = g.get("date", "")
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            continue

        action = g.get("action", "").lower().strip()
        if action == "upgrade":
            upgrades += 1
        elif action == "downgrade":
            downgrades += 1
        else:
            maintains += 1

    return AnalystActionsSummary(
        upgrades=upgrades,
        maintains=maintains,
        downgrades=downgrades,
    )


# ── Build individual actions ───────────────────────────────────────

def _build_actions(grades: List[Dict], limit: int = 50) -> List[AnalystAction]:
    """Convert recent FMP grade records (last 12 months) to AnalystAction models."""
    cutoff = datetime.utcnow() - timedelta(days=365)
    actions: List[AnalystAction] = []
    for g in grades:
        if len(actions) >= limit:
            break
        date_str = g.get("date", "")
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            break  # Grades are sorted newest-first, so stop once past cutoff
        action_type = _map_action(g.get("action", ""))
        actions.append(AnalystAction(
            firm_name=g.get("gradingCompany", "Unknown"),
            action_type=action_type,
            date=date_str[:10],
            previous_rating=g.get("previousGrade") or None,
            new_rating=g.get("newGrade", "N/A"),
            previous_price_target=None,
            new_price_target=None,
        ))
    return actions


# ── Main service class ─────────────────────────────────────────────

class AnalystService:
    """Aggregates FMP analyst data for the Analysis tab."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()

    async def get_analysis(self, ticker: str) -> AnalystAnalysisResponse:
        ticker = ticker.upper()

        # Check cache
        cache_key = f"analyst_analysis:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # Parallel FMP fetches (3 calls)
        results = await asyncio.gather(
            self.fmp.get_grades(ticker, limit=100),
            self.fmp.get_price_target_consensus(ticker),
            self.fmp.get_stock_price_quote(ticker),
            return_exceptions=True,
        )

        # Extract with safe fallbacks
        grades = results[0] if not isinstance(results[0], Exception) else []
        pt_consensus = results[1] if not isinstance(results[1], Exception) else {}
        quote = results[2] if not isinstance(results[2], Exception) else {}

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"FMP analyst call {i} failed for {ticker}: {r}")

        if not isinstance(grades, list):
            grades = []

        # Rating distribution (most recent grade per firm)
        dist_counts, total_analysts = _compute_distribution(grades)
        strong_buy = dist_counts["Strong Buy"]
        buy = dist_counts["Buy"]
        hold = dist_counts["Hold"]
        sell = dist_counts["Sell"]
        strong_sell = dist_counts["Strong Sell"]

        # Consensus
        consensus = _compute_consensus(strong_buy, buy, hold, sell, strong_sell)

        # Distributions
        distributions = [
            AnalystRatingDistribution(label="Strong Buy", count=strong_buy),
            AnalystRatingDistribution(label="Buy", count=buy),
            AnalystRatingDistribution(label="Hold", count=hold),
            AnalystRatingDistribution(label="Sell", count=sell),
            AnalystRatingDistribution(label="Strong Sell", count=strong_sell),
        ]

        # Price targets
        current_price = float(quote.get("price", 0))
        target_consensus_price = float(pt_consensus.get("targetConsensus", 0))
        target_high = float(pt_consensus.get("targetHigh", 0))
        target_low = float(pt_consensus.get("targetLow", 0))

        target_upside = 0.0
        if current_price > 0 and target_consensus_price > 0:
            target_upside = round(
                ((target_consensus_price - current_price) / current_price) * 100,
                2,
            )

        price_target = AnalystPriceTarget(
            low_price=target_low,
            average_price=target_consensus_price,
            high_price=target_high,
            current_price=current_price,
        )

        # Momentum (12 months — frontend filters to 6M or 1Y)
        momentum_data, net_positive, net_negative = _compute_momentum(
            grades, months=12
        )

        # Actions summary
        actions_summary = _compute_actions_summary(grades)

        # Individual actions (all within 12 months — frontend filters by period)
        actions = _build_actions(grades, limit=500)

        # Updated date (most recent grade)
        updated_date = ""
        if grades:
            updated_date = grades[0].get("date", "")[:10]

        # Assemble response
        response = AnalystAnalysisResponse(
            symbol=ticker,
            total_analysts=total_analysts,
            updated_date=updated_date,
            consensus=consensus,
            target_price=target_consensus_price,
            target_upside=target_upside,
            distributions=distributions,
            price_target=price_target,
            momentum_data=momentum_data,
            net_positive=net_positive,
            net_negative=net_negative,
            actions_summary=actions_summary,
            actions=actions,
        )

        _cache_set(cache_key, response)
        return response


# ── Singleton ───────────────────────────────────────────────────────

_service: Optional[AnalystService] = None


def get_analyst_service() -> AnalystService:
    global _service
    if _service is None:
        _service = AnalystService()
    return _service
