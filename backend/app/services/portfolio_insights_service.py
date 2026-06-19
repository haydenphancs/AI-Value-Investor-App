"""
Portfolio Insights Service — server-side diversification scoring.

Single source of truth for the Portfolio Insights / Diversification Health
score (iOS consumes ``GET /tracking/portfolio-insights``; the Swift
``DiversificationCalculator`` is only an offline fallback).

The score is a 0..100 composite of **normalized-HHI** sub-scores. HHI = Σ wᵢ²;
normalized so an equal-weighted split scores 100 and full concentration scores
0 — which is what makes the score respond to weight changes (the old additive
"penalty" rubric saturated to ~0 for any small, single-sector book and never
moved). Dimensions:

  position    — per-ticker weight balance
  sector      — spread across GICS sectors
  single_top5 — largest single position + top-5 weight
  marketcap   — mega / large / mid / small mix
  region      — US vs international

``effective_holdings`` (1 / HHI) is reported as the intuitive headline
("your N stocks behave like ~K independent bets").

The scoring itself is a pure function (``score_holdings``) so it's unit-tested
without Supabase / FMP. The service wraps it with data fetching: for holdings
with ``shares`` set, ``market_value`` is recomputed from the current FMP price,
and rows missing ``sector`` / ``market_cap`` are lazy-enriched from the FMP
company profile on read (this is the fix for the "Other 100%" donut — watchlist
rows never had a sector before).
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.tracking import (
    AllocationResponse,
    DiversificationSubScoreResponse,
    PortfolioHoldingResponse,
    PortfolioInsightsResponse,
)
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)


# ── Tunable thresholds ──────────────────────────────────────────────

MIN_HOLDINGS = 2

# Additive point budgets per dimension — they SUM TO 100, so the bars add up to
# the overall score (the "old way" the bars contribute points to the whole).
# Market-cap is the minor one; when its data is unavailable its budget is
# redistributed across the other three (see _BUDGETS_NO_CAP) so the total can
# still reach 100. (Geography is intentionally excluded — US-only universe.)
_BUDGETS = [
    ("position", "Position Balance", 30),
    ("sector", "Sector Spread", 30),
    ("single_top5", "Concentration", 25),
    ("marketcap", "Market-Cap Mix", 15),
]
_BUDGETS_NO_CAP = [
    ("position", "Position Balance", 35),
    ("sector", "Sector Spread", 35),
    ("single_top5", "Concentration", 30),
]

# Market-cap bucket cutoffs (USD).
CAP_MEGA = 200_000_000_000.0
CAP_LARGE = 10_000_000_000.0
CAP_MID = 2_000_000_000.0


# ── Pure scoring helpers (no I/O — unit-tested directly) ────────────


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def hhi(weights: List[float]) -> float:
    """Herfindahl-Hirschman Index = Σ wᵢ². Expects weights summing to ~1."""
    return sum(w * w for w in weights)


def effective_holdings(weights: List[float]) -> float:
    """1 / HHI — the effective number of independent positions."""
    h = hhi(weights)
    return (1.0 / h) if h > 0 else 0.0


def normalized_hhi_score(weights: List[float], n: int) -> float:
    """Map a weight distribution to 0..100 (100 = perfectly equal across the
    ``n`` buckets, 0 = fully concentrated in one). ``n`` is the number of
    buckets actually present, so 2 sectors at 50/50 reads as 100."""
    if n <= 1:
        return 0.0
    observed = hhi(weights)
    min_hhi = 1.0 / n
    if 1.0 - min_hhi <= 0:
        return 100.0
    norm = (observed - min_hhi) / (1.0 - min_hhi)  # 0 (equal) .. 1 (concentrated)
    return _clamp((1.0 - norm) * 100.0, 0.0, 100.0)


def _top5_score(weights: List[float], n: int) -> float:
    """Penalize when the 5 largest positions dominate. For n<=5 the top-5 IS
    the whole book, so there's nothing to penalize (returns 100)."""
    if n <= 5:
        return 100.0
    top5 = sum(sorted(weights, reverse=True)[:5])
    ideal = 5.0 / n
    excess = max(0.0, (top5 - ideal) / (1.0 - ideal))
    return _clamp((1.0 - excess) * 100.0, 0.0, 100.0)


def _cap_bucket(market_cap: Optional[float]) -> Optional[str]:
    if not market_cap or market_cap <= 0:
        return None
    if market_cap >= CAP_MEGA:
        return "Mega Cap"
    if market_cap >= CAP_LARGE:
        return "Large Cap"
    if market_cap >= CAP_MID:
        return "Mid Cap"
    return "Small Cap"


def _zone(ratio_0_100: int) -> str:
    """Color band for a 0..100 quality ratio (points / max_points)."""
    if ratio_0_100 >= 70:
        return "green"
    if ratio_0_100 >= 40:
        return "yellow"
    return "red"


def _weighted_group(pairs: List[Tuple[str, float]]) -> Dict[str, float]:
    """Sum weights by key."""
    out: Dict[str, float] = {}
    for key, w in pairs:
        out[key] = out.get(key, 0.0) + w
    return out


def _allocations(group: Dict[str, float]) -> List[AllocationResponse]:
    return [
        AllocationResponse(name=name, percentage=round(weight * 100.0, 1))
        for name, weight in sorted(group.items(), key=lambda kv: kv[1], reverse=True)
    ]


def _message(score: int) -> str:
    """Short, neutral descriptor of the overall score (no advice)."""
    if score >= 85:
        return "Excellent diversification"
    if score >= 70:
        return "Well diversified"
    if score >= 55:
        return "Moderately diversified"
    if score >= 40:
        return "Somewhat concentrated"
    return "Highly concentrated"


def score_holdings(
    holdings: List[PortfolioHoldingResponse],
) -> Optional[PortfolioInsightsResponse]:
    """Pure diversification scoring. Returns ``None`` when there are fewer than
    ``MIN_HOLDINGS`` positions or the total value is non-positive. No I/O — the
    caller supplies fully-resolved holdings (price-refreshed + enriched).

    Each dimension earns ``points = quality x max_points``; the dimensions'
    ``max_points`` sum to 100, so the bars add up to the overall ``score``.
    Geography is excluded (US-only universe)."""
    if len(holdings) < MIN_HOLDINGS:
        return None

    total_value = sum(h.market_value for h in holdings)
    if total_value <= 0:
        return None

    weights = [h.market_value / total_value for h in holdings]
    n = len(holdings)

    # ── Per-dimension quality (0..100, normalized HHI = weight-responsive) ──
    position_q = normalized_hhi_score(weights, n)

    sector_group = _weighted_group(
        [(_normalize_sector(h.sector) if h.sector else "Other", w)
         for h, w in zip(holdings, weights)]
    )
    sector_q = normalized_hhi_score(list(sector_group.values()), len(sector_group))

    max_w = max(weights)
    single = _clamp(100.0 - max_w * 100.0, 0.0, 100.0)
    top5 = _top5_score(weights, n)
    concentration_q = 0.5 * single + 0.5 * top5

    # Market-cap mix (scored only over holdings with a known cap).
    cap_group: Dict[str, float] = {}
    known_cap_weight = 0.0
    for h, w in zip(holdings, weights):
        bucket = _cap_bucket(h.market_cap)
        if bucket:
            cap_group[bucket] = cap_group.get(bucket, 0.0) + w
            known_cap_weight += w
    marketcap_available = known_cap_weight > 0
    marketcap_q = (
        normalized_hhi_score(
            [w / known_cap_weight for w in cap_group.values()], len(cap_group)
        )
        if marketcap_available
        else 0.0
    )

    qualities = {
        "position": position_q,
        "sector": sector_q,
        "single_top5": concentration_q,
        "marketcap": marketcap_q,
    }

    # ── Additive points (max_points sum to 100; bars add up to the score) ──
    budgets = _BUDGETS if marketcap_available else _BUDGETS_NO_CAP
    sub_scores: List[DiversificationSubScoreResponse] = []
    total = 0
    for key, label, max_points in budgets:
        quality = qualities[key]
        points = int(max(0, min(max_points, round(quality / 100.0 * max_points))))
        total += points
        ratio = int(round(points / max_points * 100)) if max_points else 0
        sub_scores.append(
            DiversificationSubScoreResponse(
                key=key, label=label, points=points,
                max_points=max_points, zone=_zone(ratio),
            )
        )
    total = int(max(0, min(100, total)))

    # ── Allocations (donuts: sector + size) ─────────────────────────────
    sector_allocations = _allocations(sector_group)
    marketcap_allocations = _allocations(
        _weighted_group(
            [(_cap_bucket(h.market_cap) or "Unknown", w)
             for h, w in zip(holdings, weights)]
        )
    )

    return PortfolioInsightsResponse(
        score=total,
        zone=_zone(total),
        effective_holdings=round(effective_holdings(weights), 1),
        message=_message(total),
        sector_count=len(sector_group),
        sub_scores=sub_scores,
        sector_allocations=sector_allocations,
        marketcap_allocations=marketcap_allocations,
        holdings_count=n,
        total_value=total_value,
    )


# ── Service ─────────────────────────────────────────────────────────


class PortfolioInsightsService:
    """Compute the Portfolio Insights payload for a user."""

    async def get_holdings(
        self, user_id: str
    ) -> List[PortfolioHoldingResponse]:
        """Fetch portfolio holdings — watchlist rows the user opted into the
        portfolio by setting ``shares`` or ``market_value``.

        Rows missing ``sector`` / ``market_cap`` are lazy-enriched from the FMP
        company profile (and written back). For rows with ``shares`` set,
        ``market_value`` is recomputed from the current FMP price.
        """
        sb = get_supabase()
        result = (
            sb.table("watchlist_items")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return []

        # Keep only rows the user opted into the portfolio.
        rows = [
            r for r in rows
            if r.get("shares") is not None or float(r.get("market_value") or 0) > 0
        ]
        if not rows:
            return []

        # Fill in missing sector / market_cap / industry / beta from FMP.
        await self._enrich_missing(user_id, rows)

        # Refresh prices for share-based rows in one parallel batch.
        share_tickers = [r["ticker"] for r in rows if r.get("shares")]
        price_map = await self._fetch_prices(share_tickers)

        out: List[PortfolioHoldingResponse] = []
        for row in rows:
            stored_value = float(row.get("market_value") or 0)
            shares = row.get("shares")
            if shares and row["ticker"] in price_map:
                market_value = float(shares) * price_map[row["ticker"]]
            else:
                market_value = stored_value

            mc = row.get("market_cap")
            beta = row.get("beta")
            out.append(
                PortfolioHoldingResponse(
                    id=str(row["id"]),
                    ticker=row["ticker"],
                    company_name=row.get("company_name") or row["ticker"],
                    market_value=market_value,
                    shares=float(shares) if shares is not None else None,
                    sector=row.get("sector"),
                    asset_type=row.get("asset_type") or "Stock",
                    country=row.get("country") or "US",
                    market_cap=float(mc) if mc is not None else None,
                    industry=row.get("industry"),
                    beta=float(beta) if beta is not None else None,
                )
            )
        out.sort(key=lambda h: h.market_value, reverse=True)
        return out

    async def compute_insights(
        self, user_id: str
    ) -> Optional[PortfolioInsightsResponse]:
        """Fetch the user's flat watchlist holdings and score them. ``None``
        when there are fewer than ``MIN_HOLDINGS`` positions or non-positive
        total value. (Legacy watchlist-scoped path.)"""
        holdings = await self.get_holdings(user_id)
        return score_holdings(holdings)

    async def get_portfolio_holdings(
        self, user_id: str, portfolio_id: str
    ) -> List[PortfolioHoldingResponse]:
        """Holdings for ONE named portfolio: per-portfolio shares / market_value
        from ``portfolio_items`` joined with the metadata (sector / market_cap /
        country / beta) on the user's ``watchlist_items`` rows, which are
        lazy-enriched from the FMP profile when missing."""
        sb = get_supabase()
        item_rows = (
            sb.table("portfolio_items")
            .select("ticker,shares,market_value")
            .eq("portfolio_id", portfolio_id)
            .execute()
            .data
            or []
        )
        # Only rows the user actually entered a holding value for.
        holdings_rows = [
            r for r in item_rows
            if r.get("shares") is not None or float(r.get("market_value") or 0) > 0
        ]
        if not holdings_rows:
            return []

        tickers = [r["ticker"].upper() for r in holdings_rows]
        meta_rows = (
            sb.table("watchlist_items")
            .select("*")
            .eq("user_id", user_id)
            .in_("ticker", tickers)
            .execute()
            .data
            or []
        )
        # Enrich missing sector / market_cap / etc. (mutates rows + writes back).
        await self._enrich_missing(user_id, meta_rows)
        meta_by_ticker = {m["ticker"].upper(): m for m in meta_rows}

        share_tickers = [r["ticker"] for r in holdings_rows if r.get("shares")]
        price_map = await self._fetch_prices(share_tickers)

        out: List[PortfolioHoldingResponse] = []
        for r in holdings_rows:
            ticker = r["ticker"]
            meta = meta_by_ticker.get(ticker.upper(), {})
            shares = r.get("shares")
            stored_value = float(r.get("market_value") or 0)
            if shares and ticker in price_map:
                market_value = float(shares) * price_map[ticker]
            else:
                market_value = stored_value

            mc = meta.get("market_cap")
            beta = meta.get("beta")
            out.append(
                PortfolioHoldingResponse(
                    id=f"{portfolio_id}:{ticker}",
                    ticker=ticker,
                    company_name=meta.get("company_name") or ticker,
                    market_value=market_value,
                    shares=float(shares) if shares is not None else None,
                    sector=meta.get("sector"),
                    asset_type=meta.get("asset_type") or "Stock",
                    country=meta.get("country") or "US",
                    market_cap=float(mc) if mc is not None else None,
                    industry=meta.get("industry"),
                    beta=float(beta) if beta is not None else None,
                )
            )
        out.sort(key=lambda h: h.market_value, reverse=True)
        return out

    async def compute_insights_for_portfolio(
        self, user_id: str, portfolio_id: str
    ) -> Optional[PortfolioInsightsResponse]:
        """Score one named portfolio's holdings. ``None`` when it has fewer than
        ``MIN_HOLDINGS`` priced positions. This is the path the iOS app uses —
        it matches exactly what the user enters in the config sheet."""
        holdings = await self.get_portfolio_holdings(user_id, portfolio_id)
        return score_holdings(holdings)

    # ── Internals ──────────────────────────────────────────────────

    async def _enrich_missing(self, user_id: str, rows: List[dict]) -> None:
        """Fill in sector / market_cap / industry / beta / country for rows
        that are missing them, from the FMP company profile, and write the
        values back so future reads (and the assets feed) are fast.

        Best-effort: any FMP or write failure is logged and skipped — the score
        still computes from whatever data is present.
        """
        missing = [
            r["ticker"] for r in rows
            if not r.get("sector") or r.get("market_cap") is None
        ]
        if not missing:
            return

        try:
            fmp = get_fmp_client()
            profiles = await fmp.get_company_profiles_batch(missing)
        except Exception as e:
            logger.warning(
                "[portfolio_insights] Profile enrichment fetch failed for %s: %s: %s",
                missing, type(e).__name__, e,
            )
            return

        by_ticker = {
            str(p.get("symbol", "")).upper(): p
            for p in profiles if p.get("symbol")
        }
        if not by_ticker:
            return

        sb = get_supabase()
        for row in rows:
            profile = by_ticker.get(row["ticker"].upper())
            if not profile:
                continue

            update: dict = {}
            if not row.get("sector") and profile.get("sector"):
                row["sector"] = profile["sector"]
                update["sector"] = profile["sector"]
            if not row.get("industry") and profile.get("industry"):
                row["industry"] = profile["industry"]
                update["industry"] = profile["industry"]
            if not row.get("country") and profile.get("country"):
                row["country"] = profile["country"]
                update["country"] = profile["country"]
            if row.get("market_cap") is None:
                mc = profile.get("marketCap") or profile.get("mktCap")
                if mc:
                    try:
                        row["market_cap"] = float(mc)
                        update["market_cap"] = float(mc)
                    except (TypeError, ValueError):
                        pass
            if row.get("beta") is None and profile.get("beta") is not None:
                try:
                    row["beta"] = float(profile["beta"])
                    update["beta"] = float(profile["beta"])
                except (TypeError, ValueError):
                    pass

            if not update:
                continue
            try:
                sb.table("watchlist_items").update(update).eq(
                    "user_id", user_id
                ).eq("ticker", row["ticker"]).execute()
            except Exception as e:
                logger.warning(
                    "[portfolio_insights] Enrichment write-back failed for %s: %s: %s",
                    row["ticker"], type(e).__name__, e,
                )

    async def _fetch_prices(self, tickers: List[str]) -> Dict[str, float]:
        """Fetch current FMP prices for the given tickers in parallel. Returns
        a ticker→price map; tickers whose quote fails are omitted — the caller
        falls back to the stored ``market_value`` for those rows."""
        if not tickers:
            return {}

        fmp = get_fmp_client()

        async def _one(t: str) -> Tuple[str, Optional[float]]:
            try:
                quote = await fmp.get_stock_price_quote(t)
                price = quote.get("price") if quote else None
                return t, float(price) if price else None
            except Exception as e:
                logger.warning(
                    "[portfolio_insights] Quote fetch failed for %s: %s", t, e
                )
                return t, None

        results = await asyncio.gather(*[_one(t) for t in tickers])
        return {t: p for t, p in results if p is not None and p > 0}
