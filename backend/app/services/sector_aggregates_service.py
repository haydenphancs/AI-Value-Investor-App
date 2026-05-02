"""
Sector Aggregates Service — pre-computes sector-wide market structure
metrics (HHI, top-firm shares, 5Y revenue CAGR, total revenue) and
caches them in Supabase. Read on every ticker-report request to feed
the Industry & Competitive Moat module's `market_dynamics` block with
real numbers instead of placeholder defaults.

Two surfaces:
  - `get_sector_aggregates(sector)` — fast async read used by the
    ticker-report collector. Returns None when the row is missing or
    stale; the collector then falls back to honest defaults.
  - `compute_and_persist_all_sectors()` — heavy batch helper meant to
    run from a scheduled job (cron / Railway scheduler). Pulls S&P 500
    constituents, groups by sector, fetches market caps + 5y revenue
    history (with rate-limit-friendly bounded concurrency), computes
    aggregates, upserts to Supabase. Intentionally NOT called from
    request paths — would blow the FMP budget and stall reports.

Cache behavior:
  - Per-sector row keyed by sector text (GICS sector names from FMP).
  - 24h staleness threshold: rows older than that → `get_sector_aggregates`
    returns None and the report renders honest defaults rather than
    stale concentration / CAGR figures.
  - Failed reads / Supabase outages return None — never raise.

Concurrency safety:
  - All Supabase calls are sync; wrapped with `asyncio.to_thread` so
    callers inside the async event loop don't block.
  - The batch's FMP fan-out uses an `asyncio.Semaphore(8)` to stay well
    under FMP's per-second rate limit.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client

logger = logging.getLogger(__name__)


TABLE_NAME = "sector_aggregates"
STALENESS_HOURS = 24
# Bounded concurrency for the heavy batch path. Keep <= 10 so the FMP
# rate limiter (typically 750 req/min on Premium) has plenty of headroom
# when we fan out across ~500 S&P tickers.
_BATCH_CONCURRENCY = 8


@dataclass
class SectorAggregates:
    sector: str
    total_revenue_usd: float
    cagr_5yr_pct: float
    hhi: float
    top1_share_pct: float
    top2_share_pct: float
    num_constituents: int
    computed_at: datetime


# ── Async lookup (called per ticker-report request) ────────────────


async def get_sector_aggregates(sector: Optional[str]) -> Optional[SectorAggregates]:
    """Return cached aggregates for a sector, or None if missing/stale.

    Sector names are GICS-style strings as FMP returns them in the
    company profile (`profile["sector"]`). Sector lookups are case-
    sensitive to match how the batch wrote them.

    Returns None on:
      - missing sector (no `profile.sector` to key on)
      - no row in the cache (batch hasn't run for this sector yet)
      - row is older than `STALENESS_HOURS`
      - any Supabase exception (logged, not raised — the report should
        not 500 because the moat cache is empty)
    """
    if not sector:
        return None

    def _query() -> Optional[SectorAggregates]:
        try:
            sb = get_supabase()
            row = (
                sb.table(TABLE_NAME)
                .select(
                    "sector,total_revenue_usd,cagr_5yr_pct,hhi,"
                    "top1_share_pct,top2_share_pct,num_constituents,computed_at"
                )
                .eq("sector", sector)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None
            r = row.data[0]
            cached_at_str = r.get("computed_at")
            if not cached_at_str:
                return None
            computed_at = datetime.fromisoformat(
                cached_at_str.replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) - computed_at > timedelta(
                hours=STALENESS_HOURS
            ):
                logger.info(
                    f"sector_aggregates STALE for {sector!r} "
                    f"(computed_at={computed_at.isoformat()})"
                )
                return None
            return SectorAggregates(
                sector=r["sector"],
                total_revenue_usd=float(r.get("total_revenue_usd") or 0.0),
                cagr_5yr_pct=float(r.get("cagr_5yr_pct") or 0.0),
                hhi=float(r.get("hhi") or 0.0),
                top1_share_pct=float(r.get("top1_share_pct") or 0.0),
                top2_share_pct=float(r.get("top2_share_pct") or 0.0),
                num_constituents=int(r.get("num_constituents") or 0),
                computed_at=computed_at,
            )
        except Exception as e:
            logger.warning(
                f"sector_aggregates read failed for {sector!r}: "
                f"{type(e).__name__}: {e}"
            )
            return None

    return await asyncio.to_thread(_query)


# ── Math helpers (pure; unit-tested directly) ──────────────────────


def compute_hhi(market_caps: List[float]) -> float:
    """Herfindahl-Hirschman Index from a list of market caps.

    HHI = sum of squared market shares (in percent), so a perfectly
    competitive market with 100 equal firms scores 100, a duopoly of
    50/50 scores 5000, and a single-firm monopoly scores 10000.

    Returns 0.0 when total market cap is 0 — caller treats this as
    "data unavailable" rather than "perfectly competitive."
    """
    total = sum(c for c in market_caps if c and c > 0)
    if total <= 0:
        return 0.0
    hhi = 0.0
    for cap in market_caps:
        if not cap or cap <= 0:
            continue
        share_pct = (cap / total) * 100
        hhi += share_pct * share_pct
    return round(hhi, 1)


def compute_top_n_share(market_caps: List[float], n: int) -> float:
    """Combined market share of the top-N firms by market cap, in percent."""
    total = sum(c for c in market_caps if c and c > 0)
    if total <= 0:
        return 0.0
    sorted_caps = sorted(
        (c for c in market_caps if c and c > 0), reverse=True,
    )
    top_sum = sum(sorted_caps[:n])
    return round((top_sum / total) * 100, 1)


def compute_revenue_cagr_5y(
    revenue_history: List[Dict[str, Any]],
) -> Optional[float]:
    """5Y revenue CAGR from a sector's per-ticker revenue records.

    `revenue_history` is a list of dicts each shaped:
      {ticker: str, revenues: List[Tuple[year:int, revenue_usd:float]]}
    where the revenues span at least 5 fiscal years (oldest→newest).

    Aggregation: sum revenue per year across all constituents, then
    derive CAGR from the oldest vs. newest year:
      CAGR = (rev_end / rev_start) ^ (1/n) - 1

    Returns None when start revenue is non-positive or n <= 0
    (CAGR is undefined for those edge cases). Negative results mean
    a shrinking sector and are kept as-is — that's a real signal.
    """
    if not revenue_history:
        return None
    by_year: Dict[int, float] = defaultdict(float)
    for rec in revenue_history:
        for yr, rev in rec.get("revenues") or []:
            if rev and rev > 0:
                by_year[int(yr)] += float(rev)
    if len(by_year) < 2:
        return None
    years = sorted(by_year.keys())
    start_year, end_year = years[0], years[-1]
    n = end_year - start_year
    if n <= 0:
        return None
    start_rev = by_year[start_year]
    end_rev = by_year[end_year]
    if start_rev <= 0 or end_rev <= 0:
        return None
    cagr = (end_rev / start_rev) ** (1 / n) - 1
    return round(cagr * 100, 2)


# ── Heavy batch (run from a scheduled job, NOT a request handler) ──


async def compute_and_persist_all_sectors() -> int:
    """Recompute aggregates for every GICS sector in the S&P 500 and
    upsert them into Supabase. Returns the number of sectors written.

    Heavy: ~500 ticker fetches per run. Use bounded concurrency
    (`_BATCH_CONCURRENCY`) to stay rate-limit-safe. Designed to be
    invoked from a scheduled task / cron, not an HTTP request.
    """
    fmp = get_fmp_client()
    constituents = await fmp.get_sp500_constituents()
    if not constituents:
        logger.warning("compute_and_persist_all_sectors: empty S&P 500 list")
        return 0

    by_sector: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in constituents:
        sector = c.get("sector")
        if sector:
            by_sector[sector].append(c)

    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def _fetch_for_ticker(ticker: str) -> Dict[str, Any]:
        async with sem:
            try:
                profile_task = fmp.get_company_profile(ticker)
                income_task = fmp.get_income_statement(ticker, "annual", 6)
                profile, income = await asyncio.gather(
                    profile_task, income_task, return_exceptions=True,
                )
                if isinstance(profile, Exception) or not isinstance(profile, dict):
                    profile = {}
                if isinstance(income, Exception) or not isinstance(income, list):
                    income = []
                mkt_cap = float(profile.get("mktCap") or 0.0)
                revenues: List = []
                for row in income:
                    yr = row.get("calendarYear") or row.get("date", "")[:4]
                    rev = row.get("revenue")
                    try:
                        revenues.append((int(yr), float(rev or 0.0)))
                    except (TypeError, ValueError):
                        continue
                return {
                    "ticker": ticker,
                    "mkt_cap": mkt_cap,
                    "revenues": revenues,
                }
            except Exception as e:
                logger.warning(
                    f"compute_and_persist_all_sectors: fetch failed for "
                    f"{ticker}: {type(e).__name__}: {e}"
                )
                return {"ticker": ticker, "mkt_cap": 0.0, "revenues": []}

    written = 0
    for sector, members in by_sector.items():
        tickers = [m.get("symbol") for m in members if m.get("symbol")]
        if not tickers:
            continue
        results = await asyncio.gather(
            *[_fetch_for_ticker(t) for t in tickers]
        )
        market_caps = [r["mkt_cap"] for r in results]
        cagr = compute_revenue_cagr_5y(results)
        hhi = compute_hhi(market_caps)
        top1 = compute_top_n_share(market_caps, 1)
        top2 = compute_top_n_share(market_caps, 2)
        total_revenue = 0.0
        for r in results:
            revs = r.get("revenues") or []
            if revs:
                latest = sorted(revs, key=lambda x: x[0])[-1]
                total_revenue += float(latest[1] or 0.0)

        row = {
            "sector": sector,
            "total_revenue_usd": total_revenue,
            "cagr_5yr_pct": cagr if cagr is not None else 0.0,
            "hhi": hhi,
            "top1_share_pct": top1,
            "top2_share_pct": top2,
            "num_constituents": len(tickers),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

        def _upsert(payload=row) -> bool:
            try:
                sb = get_supabase()
                sb.table(TABLE_NAME).upsert(
                    payload, on_conflict="sector"
                ).execute()
                return True
            except Exception as e:
                logger.warning(
                    f"sector_aggregates upsert failed for {payload['sector']!r}: "
                    f"{type(e).__name__}: {e}"
                )
                return False

        if await asyncio.to_thread(_upsert):
            written += 1

    logger.info(
        f"compute_and_persist_all_sectors: wrote {written}/{len(by_sector)} sectors"
    )
    return written
