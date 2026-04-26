"""
Trending Analyses Service.

Aggregates `research_reports` to surface which sectors/themes users are most
actively researching. Powers the iOS Research tab "Trending Analyses" section.

Algorithm:
  1. Pull completed reports from the last 30 days (current window) and the
     30 days before that (prior window).
  2. Group both windows by ticker -> sector via FMP company profile.
  3. Per sector compute: report count (current), unique tickers, growth %
     vs prior window.
  4. Return top N sectors sorted by current count desc.

Sector profile lookups are bounded (top tickers only) and cached at the
FMP integration layer, so cost is roughly one batch call per refresh.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client

logger = logging.getLogger(__name__)


WINDOW_DAYS = 30
TOP_SECTORS = 5
COMPANIES_PER_SECTOR = 12
MAX_TICKERS_TO_PROFILE = 60


# Map sector name to display metadata for the iOS card.
# Backend stays the source of truth so the iOS app can render any sector
# without code changes — unknown sectors fall through to a default style.
_SECTOR_THEMES: Dict[str, Dict[str, str]] = {
    "Technology": {
        "title": "Technology Stocks",
        "description": "Most-researched technology names this month",
        "system_icon_name": "cpu",
        "icon_background_color": "3B82F6",
    },
    "Communication Services": {
        "title": "Communication & Media",
        "description": "Trending names across telecom, media, and platforms",
        "system_icon_name": "antenna.radiowaves.left.and.right",
        "icon_background_color": "06B6D4",
    },
    "Consumer Cyclical": {
        "title": "Consumer Cyclical",
        "description": "Discretionary spending stories driving research interest",
        "system_icon_name": "cart.fill",
        "icon_background_color": "EAB308",
    },
    "Consumer Defensive": {
        "title": "Consumer Defensive",
        "description": "Staples and household names attracting attention",
        "system_icon_name": "leaf.fill",
        "icon_background_color": "22C55E",
    },
    "Healthcare": {
        "title": "Healthcare & Biotech",
        "description": "Pharma, devices, and biotech under the microscope",
        "system_icon_name": "cross.case.fill",
        "icon_background_color": "EC4899",
    },
    "Financial Services": {
        "title": "Financial Services",
        "description": "Banks, insurers, and fintech in the spotlight",
        "system_icon_name": "banknote.fill",
        "icon_background_color": "10B981",
    },
    "Industrials": {
        "title": "Industrials",
        "description": "Capital goods and logistics drawing investor focus",
        "system_icon_name": "gearshape.2.fill",
        "icon_background_color": "F97316",
    },
    "Energy": {
        "title": "Energy",
        "description": "Oil, gas, and renewables shaping the sector mix",
        "system_icon_name": "bolt.fill",
        "icon_background_color": "EF4444",
    },
    "Basic Materials": {
        "title": "Basic Materials",
        "description": "Mining, chemicals, and resources gaining traction",
        "system_icon_name": "hammer.fill",
        "icon_background_color": "A16207",
    },
    "Real Estate": {
        "title": "Real Estate",
        "description": "REITs and property names in active research",
        "system_icon_name": "building.2.fill",
        "icon_background_color": "8B5CF6",
    },
    "Utilities": {
        "title": "Utilities",
        "description": "Power and water utilities being analyzed",
        "system_icon_name": "lightbulb.fill",
        "icon_background_color": "0EA5E9",
    },
}

_DEFAULT_THEME = {
    "title": "Trending Sector",
    "description": "Most-researched names this month",
    "system_icon_name": "chart.bar.fill",
    "icon_background_color": "6366F1",
}


class TrendingService:
    def __init__(self):
        self.supabase = get_supabase()
        self.fmp = get_fmp_client()

    async def get_trending(self) -> List[Dict[str, Any]]:
        """Return the top trending sectors by recent research activity."""
        now = datetime.now(timezone.utc)
        current_start = now - timedelta(days=WINDOW_DAYS)
        prior_start = now - timedelta(days=WINDOW_DAYS * 2)

        current_rows = self._fetch_completed_reports(since=current_start)
        prior_rows = self._fetch_completed_reports(
            since=prior_start, until=current_start
        )

        if not current_rows:
            return []

        current_counts = self._count_by_ticker(current_rows)
        prior_counts = self._count_by_ticker(prior_rows)

        # Profile only the top-N tickers we actually need to bucket.
        top_tickers = sorted(
            current_counts.items(), key=lambda kv: kv[1], reverse=True
        )[:MAX_TICKERS_TO_PROFILE]
        ticker_list = [t for t, _ in top_tickers]

        profiles = await self.fmp.get_company_profiles_batch(ticker_list)
        profile_by_ticker = {
            (p.get("symbol") or "").upper(): p for p in profiles if p
        }

        # Bucket by sector
        buckets: Dict[str, Dict[str, Any]] = {}
        for ticker, count in top_tickers:
            profile = profile_by_ticker.get(ticker.upper())
            if not profile:
                continue
            sector = (profile.get("sector") or "").strip()
            if not sector:
                continue
            bucket = buckets.setdefault(
                sector,
                {"current_count": 0, "prior_count": 0, "companies": []},
            )
            bucket["current_count"] += count
            bucket["companies"].append(
                self._format_company(ticker, profile)
            )

        # Add prior counts onto the same buckets via profile lookup
        for ticker, count in prior_counts.items():
            profile = profile_by_ticker.get(ticker.upper())
            if not profile:
                continue
            sector = (profile.get("sector") or "").strip()
            if sector and sector in buckets:
                buckets[sector]["prior_count"] += count

        themed = [
            self._build_theme(sector, data)
            for sector, data in buckets.items()
        ]
        themed.sort(key=lambda t: t["raw_count"], reverse=True)
        return themed[:TOP_SECTORS]

    # ── Helpers ───────────────────────────────────────────────────────────

    def _fetch_completed_reports(
        self,
        since: datetime,
        until: datetime | None = None,
    ) -> List[Dict[str, Any]]:
        query = (
            self.supabase.table("research_reports")
            .select("ticker, created_at")
            .eq("status", "completed")
            .gte("created_at", since.isoformat())
        )
        if until is not None:
            query = query.lt("created_at", until.isoformat())
        try:
            return query.execute().data or []
        except Exception as e:
            logger.warning(f"Trending fetch failed: {e}")
            return []

    @staticmethod
    def _count_by_ticker(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in rows:
            ticker = (row.get("ticker") or "").upper()
            if not ticker:
                continue
            counts[ticker] = counts.get(ticker, 0) + 1
        return counts

    @staticmethod
    def _format_company(ticker: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        price = profile.get("price")
        market_cap = profile.get("marketCap") or profile.get("mktCap")
        return {
            "ticker": ticker,
            "name": profile.get("companyName") or ticker,
            "price": _format_price(price),
            "market_cap": _format_market_cap(market_cap),
        }

    @staticmethod
    def _build_theme(sector: str, data: Dict[str, Any]) -> Dict[str, Any]:
        theme = _SECTOR_THEMES.get(sector, _DEFAULT_THEME)
        current = int(data["current_count"])
        prior = int(data["prior_count"])

        if prior > 0:
            interest_percent = int(round(((current - prior) / prior) * 100))
        else:
            # No prior baseline -> proxy via raw count, capped
            interest_percent = min(current * 10, 200)

        companies = data["companies"][:COMPANIES_PER_SECTOR]
        return {
            "title": theme["title"],
            "description": theme["description"],
            "companies": companies,
            "interest_percent": interest_percent,
            "system_icon_name": theme["system_icon_name"],
            "icon_background_color": theme["icon_background_color"],
            "raw_count": current,
        }


def _format_price(price: Any) -> str:
    try:
        value = float(price)
    except (TypeError, ValueError):
        return ""
    return f"${value:,.2f}"


def _format_market_cap(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000_000_000:
        return f"${n / 1_000_000_000_000:.1f}T"
    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.0f}M"
    return f"${n:,.0f}"
