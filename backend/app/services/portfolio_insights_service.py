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
    NudgeResponse,
    PortfolioHoldingResponse,
    PortfolioInsightsResponse,
)
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)


# ── Tunable thresholds ──────────────────────────────────────────────

MIN_HOLDINGS = 2

# Composite weights (must sum to 1.0). The two concentration dimensions
# dominate because that's what users feel; cap-mix / geography refine.
WEIGHT_POSITION = 0.30
WEIGHT_SECTOR = 0.30
WEIGHT_SINGLE_TOP5 = 0.20
WEIGHT_MARKETCAP = 0.10
WEIGHT_REGION = 0.10

# Market-cap bucket cutoffs (USD).
CAP_MEGA = 200_000_000_000.0
CAP_LARGE = 10_000_000_000.0
CAP_MID = 2_000_000_000.0

# Nudge thresholds.
SINGLE_NAME_ALERT = 0.40       # one position above 40% of the book
SECTOR_DOMINANT_ALERT = 0.80   # one sector above 80%
EFFECTIVE_HOLDINGS_ALERT = 2.0 # behaves like <2 bets despite >=3 names


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


def _region(country: Optional[str]) -> str:
    c = (country or "US").strip().upper()
    if c in ("US", "USA", "UNITED STATES", "U.S.", "U.S.A."):
        return "United States"
    return "International"


def _grade(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _zone(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 40:
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


def _message(score: int, sector_count: int) -> str:
    if score >= 85:
        return f"Excellent diversification across {sector_count} sectors."
    if score >= 70:
        return f"Well diversified across {sector_count} sectors."
    if score >= 55:
        return "Reasonably diversified — a few areas to tighten up."
    if score >= 40:
        return "Concentrated — spreading out would lower your risk."
    return (
        "Highly concentrated — diversifying across sectors and positions "
        "would meaningfully cut your risk."
    )


def _build_nudges(
    holdings: List[PortfolioHoldingResponse],
    weights: List[float],
    sector_group: Dict[str, float],
    cap_group: Dict[str, float],
    marketcap_available: bool,
    effective: float,
) -> List[NudgeResponse]:
    """Severity-ordered, capped at 4."""
    critical: List[NudgeResponse] = []
    warning: List[NudgeResponse] = []
    info: List[NudgeResponse] = []

    # Largest single position.
    top_idx = max(range(len(weights)), key=lambda i: weights[i])
    top_w = weights[top_idx]
    if top_w > SINGLE_NAME_ALERT:
        pct = int(round(top_w * 100))
        critical.append(NudgeResponse(
            severity="critical",
            title=f"Trim {holdings[top_idx].ticker}",
            detail=(
                f"{holdings[top_idx].ticker} is {pct}% of your portfolio — a "
                f"drop there would hit you hard. Consider trimming it."
            ),
        ))

    # Sector concentration.
    if sector_group:
        top_sector, top_sector_w = max(sector_group.items(), key=lambda kv: kv[1])
        if len(sector_group) == 1:
            warning.append(NudgeResponse(
                severity="warning",
                title="Add another sector",
                detail=(
                    f"Every holding is in {top_sector}. Adding a position from "
                    f"a different sector would lower your risk."
                ),
            ))
        elif top_sector_w >= SECTOR_DOMINANT_ALERT:
            pct = int(round(top_sector_w * 100))
            warning.append(NudgeResponse(
                severity="warning",
                title=f"Heavy in {top_sector}",
                detail=(
                    f"{pct}% of your money is in {top_sector}. Spreading across "
                    f"more sectors would balance your exposure."
                ),
            ))

    # Geography.
    intl_weight = sum(
        w for h, w in zip(holdings, weights)
        if _region(h.country) != "United States"
    )
    if intl_weight <= 0:
        info.append(NudgeResponse(
            severity="info",
            title="Consider international exposure",
            detail=(
                "You're 100% U.S. Adding international names can smooth out "
                "country-specific risk."
            ),
        ))

    # Market-cap mix.
    if marketcap_available and len(cap_group) == 1:
        only_bucket = next(iter(cap_group))
        info.append(NudgeResponse(
            severity="info",
            title="Mix in other company sizes",
            detail=(
                f"Everything is {only_bucket.lower()}. Mid- and small-cap names "
                f"add a different growth and risk profile."
            ),
        ))

    # Effective concentration despite multiple names.
    if len(holdings) >= 3 and effective < EFFECTIVE_HOLDINGS_ALERT:
        warning.append(NudgeResponse(
            severity="warning",
            title="Effectively concentrated",
            detail=(
                f"Your {len(holdings)} holdings behave like only "
                f"~{effective:.1f} because the weights are lopsided."
            ),
        ))

    return (critical + warning + info)[:4]


def score_holdings(
    holdings: List[PortfolioHoldingResponse],
) -> Optional[PortfolioInsightsResponse]:
    """Pure diversification scoring. Returns ``None`` when there are fewer than
    ``MIN_HOLDINGS`` positions or the total value is non-positive. No I/O — the
    caller supplies fully-resolved holdings (price-refreshed + enriched)."""
    if len(holdings) < MIN_HOLDINGS:
        return None

    total_value = sum(h.market_value for h in holdings)
    if total_value <= 0:
        return None

    weights = [h.market_value / total_value for h in holdings]
    n = len(holdings)

    # ── Sub-scores ──────────────────────────────────────────────────
    position_score = normalized_hhi_score(weights, n)

    sector_group = _weighted_group(
        [(_normalize_sector(h.sector) if h.sector else "Other", w)
         for h, w in zip(holdings, weights)]
    )
    sector_score = normalized_hhi_score(
        list(sector_group.values()), len(sector_group)
    )

    max_w = max(weights)
    single = _clamp(100.0 - max_w * 100.0, 0.0, 100.0)
    top5 = _top5_score(weights, n)
    single_top5_score = 0.5 * single + 0.5 * top5

    # Market-cap mix (scored only over holdings with a known cap).
    cap_group: Dict[str, float] = {}
    known_cap_weight = 0.0
    for h, w in zip(holdings, weights):
        bucket = _cap_bucket(h.market_cap)
        if bucket:
            cap_group[bucket] = cap_group.get(bucket, 0.0) + w
            known_cap_weight += w
    marketcap_available = known_cap_weight > 0
    if marketcap_available:
        norm_cap = [w / known_cap_weight for w in cap_group.values()]
        marketcap_score = normalized_hhi_score(norm_cap, len(cap_group))
    else:
        marketcap_score = 0.0

    region_group = _weighted_group(
        [(_region(h.country), w) for h, w in zip(holdings, weights)]
    )
    region_score = normalized_hhi_score(
        list(region_group.values()), len(region_group)
    )

    # ── Composite (renormalize if market-cap data is unavailable) ────
    dims: List[Tuple[str, str, float, float]] = [
        ("position", "Position Balance", position_score, WEIGHT_POSITION),
        ("sector", "Sector Spread", sector_score, WEIGHT_SECTOR),
        ("single_top5", "Concentration", single_top5_score, WEIGHT_SINGLE_TOP5),
    ]
    if marketcap_available:
        dims.append(
            ("marketcap", "Market-Cap Mix", marketcap_score, WEIGHT_MARKETCAP)
        )
    dims.append(("region", "Geography", region_score, WEIGHT_REGION))

    total_weight = sum(w for *_, w in dims)
    composite = int(max(0, min(100, round(
        sum(score * w for _, _, score, w in dims) / total_weight
    ))))

    sub_scores = [
        DiversificationSubScoreResponse(
            key=key,
            label=label,
            score=int(round(score)),
            zone=_zone(int(round(score))),
        )
        for key, label, score, _ in dims
    ]

    # ── Allocations (donuts) ────────────────────────────────────────
    sector_allocations = _allocations(sector_group)
    marketcap_allocations = _allocations(
        _weighted_group(
            [(_cap_bucket(h.market_cap) or "Unknown", w)
             for h, w in zip(holdings, weights)]
        )
    )
    region_allocations = _allocations(region_group)

    eff = effective_holdings(weights)
    nudges = _build_nudges(
        holdings=holdings,
        weights=weights,
        sector_group=sector_group,
        cap_group=cap_group,
        marketcap_available=marketcap_available,
        effective=eff,
    )

    return PortfolioInsightsResponse(
        score=composite,
        grade=_grade(composite),
        zone=_zone(composite),
        effective_holdings=round(eff, 1),
        message=_message(composite, len(sector_group)),
        sector_count=len(sector_group),
        sub_scores=sub_scores,
        sector_allocations=sector_allocations,
        marketcap_allocations=marketcap_allocations,
        region_allocations=region_allocations,
        nudges=nudges,
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
