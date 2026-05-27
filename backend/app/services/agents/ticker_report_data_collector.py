"""
TickerReportDataCollector — single source of truth for the **non-AI**
portions of TickerReportResponse.

Both pipelines depend on this module:
  - TickerReportService (`/stocks/{ticker}/report` — single-pass)
  - ResearchAgent       (`/research/generate` — agentic loop)

Responsibilities:
  1. Fetch every FMP endpoint the report needs in parallel
  2. Reuse AnalystService + HoldersService so the report shows the same
     numbers as TickerDetailView's Analyst/Holders tabs
  3. Compute derived metrics (Altman Z, growth rates, CAGR, etc.) with
     explicit None / 0.0 fallbacks for missing inputs — no infinity, no
     divide-by-zero masking, no synthetic placeholders that look like
     real data
  4. Build the deterministic report sections (insider transactions,
     wall street consensus, hedge fund flow, key management roster,
     price-action event detection, revenue segments) directly from real
     FMP / service data
  5. Provide a single `assemble_report` entry point that merges the
     deterministic sections with whatever narrative + scoring the AI
     layer produced

Honest-placeholder policy:
  - Numeric fields default to 0.0 / 0 / [] when their source is missing.
  - String narrative fields the AI is supposed to write get a literal
    "Data unavailable for this ticker." until Stage B narrative gen
    runs (Phase 2).
  - The collector logs (with type + message) which FMP endpoint failed,
    so production debugging shows the real cause instead of silent
    fabrication.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.analyst import (
    AnalystAnalysisResponse,
    AnalystConsensus,
)
from app.schemas.holders import HoldersResponse
from app.schemas.revenue_breakdown import RevenueBreakdownResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.services._insider_common import (
    classify_insider_transaction,
    is_informative,
    normalize_insider_name,
)
from app.services.sector_aggregates_service import (
    SectorAggregates,
    get_sector_aggregates,
)

logger = logging.getLogger(__name__)


DISCLAIMER = (
    "This analysis is for educational purposes only and does not constitute "
    "financial advice. AI-generated content may be inaccurate. Always conduct "
    "your own research and consult with a qualified financial advisor before "
    "making investment decisions."
)

# Persona key (backend) → agent tag (frontend enum).
# Swift's ReportAgentPersona currently exposes buffett/wood/lynch/dalio;
# bill_ackman is mapped to "dalio" for the badge until iOS adds an
# Ackman-specific case.
_AGENT_MAP: Dict[str, str] = {
    "warren_buffett": "buffett",
    "cathie_wood": "wood",
    "peter_lynch": "lynch",
    "bill_ackman": "dalio",
    "ray_dalio": "dalio",
}

# FMP segmentation metadata keys to drop when extracting segment dicts.
_SEGMENT_META_KEYS = {
    "date", "symbol", "reportedCurrency", "cik", "fillingDate",
    "acceptedDate", "calendarYear", "period", "link", "finalLink",
    "fiscalYear", "data",
}


# ── Macro indicators (PR 4) ───────────────────────────────────────────
#
# Per-request snapshot of the FMP-tradeable macro signals that anchor
# the Macro & Geopolitical module's risk factors. Symbol formats follow
# FMP conventions (commodities: <BASE>USD, FX: <BASE><QUOTE>, indices:
# ^<TICKER>). Symbols that FMP can't resolve gracefully degrade — the
# `_build_macro_risk_factors_from_indicators` builder skips any whose
# `change_1m_pct` we couldn't fetch, rather than emitting a fake risk.
#
# Roughly ordered by signal strength for the average equity holder:
#   * WTI crude — energy costs, inflation pass-through
#   * Gold — flight-to-safety, real-rate / dollar pressure proxy
#   * Copper — global industrial demand
#   * VIX — market volatility regime
#   * 10Y Treasury — risk-free rate, the discount denominator
#   * USD Index — multinational FX translation
_MACRO_SYMBOLS: Tuple[str, ...] = (
    "CLUSD",  # WTI Crude oil
    "GCUSD",  # Gold
    "HGUSD",  # Copper
    "SIUSD",  # Silver
    "^VIX",   # CBOE Volatility Index
    "^TNX",   # 10-year Treasury yield
    "DXY",    # USD index
    "EURUSD", "USDJPY", "USDCNY",  # Major FX
)


# ── News catalyst classification ──────────────────────────────────────
#
# Module-scope keyword map so a future PR can swap to a smarter
# classifier (or model-based extractor) without touching the price-action
# builder. Match is case-insensitive substring against `title + " " + text`.
# Order matters within a tag — the first match wins, so longer / more
# specific phrases come first.
NEWS_CATALYST_KEYWORDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("FDA Approval", (
        "fda approval", "fda approved", "fda clears",
        "approved by fda", "approval to market", "phase 3 success",
    )),
    ("FDA Rejection", (
        "fda rejects", "rejection letter",
        "complete response letter", "fda denied",
    )),
    ("M&A", (
        "to acquire", "acquisition of", "merger with",
        "buyout", "agreed to be acquired", "takeover bid",
    )),
    ("Analyst Upgrade", (
        "upgrades to", "raises price target",
        "raises rating", "analyst upgrade",
    )),
    ("Analyst Downgrade", (
        "downgrades to", "cuts price target",
        "lowers rating", "analyst downgrade",
    )),
    ("Guidance Raised", (
        "raises guidance", "raises full-year",
        "boosts outlook", "raises forecast",
    )),
    ("Guidance Cut", (
        "lowers guidance", "cuts outlook",
        "lowers forecast", "warns of",
    )),
    ("Lawsuit", (
        "lawsuit", "class action", "files suit",
        "sec investigation", "doj probe",
    )),
    ("Buyback", (
        "share buyback", "stock repurchase",
        "authorizes repurchase", "expands buyback",
    )),
    ("Dividend", (
        "raises dividend", "dividend increase", "special dividend",
    )),
    ("Layoffs", (
        "layoffs", "to cut jobs",
        "workforce reduction", "restructuring",
    )),
]


def _classify_news_catalyst(title: str, text: str) -> Optional[str]:
    """Return the catalyst tag for a headline, or None if nothing matches.

    Lowercases once; iterates the module-level keyword map in order so
    more-specific tags (FDA Rejection) take priority over less-specific
    ones (Layoffs) if both somehow matched. First match wins.
    """
    blob = f"{title or ''} {text or ''}".lower()
    if not blob.strip():
        return None
    for tag, phrases in NEWS_CATALYST_KEYWORDS:
        for phrase in phrases:
            if phrase in blob:
                return tag
    return None


# ── Output dataclass ──────────────────────────────────────────────────


@dataclass
class CollectedTickerData:
    """Everything the AI prompt and the report assembler need.

    Fields prefixed `raw_` are unprocessed FMP responses (used by AI
    prompts as context). Fields named `*_section` are pre-built dicts
    that drop directly into TickerReportResponse positions.
    """

    ticker: str
    persona_key: str

    # ── Raw FMP / service data ────────────────────────────────────────
    profile: Dict[str, Any] = field(default_factory=dict)
    quote: Dict[str, Any] = field(default_factory=dict)
    income: List[Dict[str, Any]] = field(default_factory=list)
    balance: List[Dict[str, Any]] = field(default_factory=list)
    cash_flow: List[Dict[str, Any]] = field(default_factory=list)
    key_metrics: List[Dict[str, Any]] = field(default_factory=list)
    ratios: List[Dict[str, Any]] = field(default_factory=list)
    estimates: List[Dict[str, Any]] = field(default_factory=list)
    historical: Dict[str, Any] = field(default_factory=dict)
    news: List[Dict[str, Any]] = field(default_factory=list)
    insider_trades: List[Dict[str, Any]] = field(default_factory=list)
    insider_roster: List[Dict[str, Any]] = field(default_factory=list)
    # SC 13D/G filings — used to upgrade Form 4 share counts for
    # 10%+ owners whose direct holdings understate true beneficial ownership.
    beneficial_owners: List[Dict[str, Any]] = field(default_factory=list)
    segments_raw: List[Dict[str, Any]] = field(default_factory=list)
    earnings_dates: List[str] = field(default_factory=list)
    analyst_analysis: Optional[AnalystAnalysisResponse] = None
    holders_response: Optional[HoldersResponse] = None

    # ── Snapshot services (parity with TickerDetailView Financials tab) ──
    snap_profitability: Optional[SnapshotItemResponse] = None
    snap_health: Optional[SnapshotItemResponse] = None
    snap_growth: Optional[SnapshotItemResponse] = None
    snap_valuation: Optional[SnapshotItemResponse] = None
    revenue_breakdown: Optional[RevenueBreakdownResponse] = None

    # ── Peer + sector data for the Moat module (PR 2) ───────────────────
    # `peer_tickers` is fetched in pass 1 (alongside FMP financial calls).
    # `peer_profiles` and `sector_aggregates` are fetched in pass 2 since
    # they depend on `peer_tickers` and `profile.sector` resolving first.
    peer_tickers: List[str] = field(default_factory=list)
    peer_profiles: List[Dict[str, Any]] = field(default_factory=list)
    # TTM ratios keyed by peer ticker — feeds real op_margin / ROE /
    # revenue_growth into `_build_competitors` per-peer scoring (replaces
    # the old proxy that scored every peer off intraday %-change).
    peer_ratios: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sector_aggregates: Optional[SectorAggregates] = None
    # Industry-size projection from a FRED BEA value-added series, used
    # as a fallback when AI Stage A didn't extract an explicit TAM quote
    # from the earnings transcript. None when industry isn't in the
    # FRED mapping or the FRED API is misconfigured.
    industry_tam: Optional[Any] = None  # IndustryTAM (imported lazily)

    # ── Earnings call transcript (PR 3 — TAM extraction; PR 6 — guidance) ──
    # Latest available quarterly transcript text from FMP. Empty string when
    # the ticker has no transcripts (e.g. small-cap, foreign listings).
    transcript: str = ""

    # ── Macro indicators for the Macro module (PR 4) ────────────────────
    # Per-symbol dict: {symbol, current_price, change_1m_pct, change_1y_pct}
    # for commodities / FX / VIX / Treasury. Empty list when FMP is
    # unreachable — the Macro module then renders only AI-derived
    # geopolitical / regulatory factors.
    macro_indicators: List[Dict[str, Any]] = field(default_factory=list)

    # ── FRED snapshots for the Macro module (PR 5) ──────────────────────
    # Per-series snapshot: {series_id, latest, as_of, yoy_pct, ...}
    # for CPI, Fed Funds, 10Y Treasury, 10Y-2Y spread. Empty list when
    # FRED_API_KEY is missing or the API errors — risk factors derived
    # from these series simply don't appear; AI-driven factors still do.
    fred_indicators: List[Dict[str, Any]] = field(default_factory=list)

    # ── Phase 3D: pre-computed moat scoring (deterministic + grounded
    # fallback). Filled at the end of `_fetch_dependent` so assemble_report
    # stays synchronous.
    moat_grounded_pillars: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # ── Phase 3C: USPTO + FDA payload for Intangible Assets pillar.
    # None when both sources resolve empty (caller treats as absent).
    ip_intel: Optional[Dict[str, Any]] = None

    # ── Computed metrics (real numbers / None) ────────────────────────
    computed: Dict[str, Any] = field(default_factory=dict)

    # ── Pre-built report sections (real-data-grounded) ────────────────
    meta: Dict[str, Any] = field(default_factory=dict)
    valuation_vital: Dict[str, Any] = field(default_factory=dict)
    financial_health_vital: Dict[str, Any] = field(default_factory=dict)
    revenue_vital: Dict[str, Any] = field(default_factory=dict)
    forecast_vital: Dict[str, Any] = field(default_factory=dict)
    insider_vital_partial: Dict[str, Any] = field(default_factory=dict)
    wall_street_vital: Dict[str, Any] = field(default_factory=dict)
    macro_vital_seed: Dict[str, Any] = field(default_factory=dict)
    revenue_forecast_partial: Dict[str, Any] = field(default_factory=dict)
    insider_data_partial: Dict[str, Any] = field(default_factory=dict)
    key_management_partial: Dict[str, Any] = field(default_factory=dict)
    price_action_partial: Dict[str, Any] = field(default_factory=dict)
    revenue_engine_partial: Dict[str, Any] = field(default_factory=dict)
    wall_street_consensus_partial: Dict[str, Any] = field(default_factory=dict)
    fundamental_metrics_partial: List[Dict[str, Any]] = field(default_factory=list)


# ── Public API ────────────────────────────────────────────────────────


class TickerReportDataCollector:
    """Fetches and shapes every non-AI field of TickerReportResponse."""

    def __init__(
        self,
        fmp: Optional[FMPClient] = None,
    ):
        self.fmp = fmp or get_fmp_client()

    # ── Main entry point ──────────────────────────────────────────────

    async def collect(
        self, ticker: str, persona_key: str
    ) -> CollectedTickerData:
        ticker = ticker.upper().strip()
        out = CollectedTickerData(ticker=ticker, persona_key=persona_key)

        await self._fetch_all(out)
        if not out.profile:
            # Profile is the only non-recoverable miss — without it we
            # don't even know the company name.
            raise ValueError(f"No company profile found for ticker: {ticker}")

        self._compute_metrics(out)
        self._build_sections(out)
        return out

    # ── Phase 1: parallel fetch ───────────────────────────────────────

    async def _fetch_all(self, out: CollectedTickerData) -> None:
        """Fetch FMP endpoints + AnalystService + HoldersService in parallel.

        Each call is logged on failure and falls back to an empty
        default. Only the company-profile miss aborts the pipeline (in
        `collect`); everything else degrades gracefully.
        """
        ticker = out.ticker

        # Lazy imports avoid circulars (HoldersService imports get_supabase
        # which we don't want at module-import time in tests).
        from app.services.analyst_service import AnalystService
        from app.services.holders_service import HoldersService
        from app.services.profitability_snapshot_service import (
            get_profitability_snapshot_service,
        )
        from app.services.health_snapshot_service import (
            get_health_snapshot_service,
        )
        from app.services.growth_snapshot_service import (
            get_growth_snapshot_service,
        )
        from app.services.valuation_snapshot_service import (
            get_valuation_snapshot_service,
        )
        from app.services.revenue_breakdown_service import (
            get_revenue_breakdown_service,
        )

        analyst_service = AnalystService()
        holders_service = HoldersService()

        # Each entry: (attribute_name, awaitable, default_on_failure)
        tasks: List[Tuple[str, Any, Any]] = [
            ("profile", self.fmp.get_company_profile(ticker), {}),
            ("quote", self.fmp.get_stock_price_quote(ticker), {}),
            ("income", self.fmp.get_income_statement(ticker, "annual", 5), []),
            ("balance", self.fmp.get_balance_sheet(ticker, "annual", 5), []),
            ("cash_flow", self.fmp.get_cash_flow_statement(ticker, "annual", 5), []),
            ("key_metrics", self.fmp.get_key_metrics(ticker, "annual", 5), []),
            ("ratios", self.fmp.get_financial_ratios(ticker, "annual", 5), []),
            # Pull 10 years of analyst estimates so we have current FY + 3
            # future on-screen (4 bars), plus the FY immediately before the
            # leftmost visible bar as its off-screen YoY anchor. FMP returns
            # newest-by-date first; sorted-ascending we get past actuals on
            # the left and forward estimates on the right, which lets the
            # window helper pick "current = first FY with date >= today".
            ("estimates", self.fmp.get_analyst_estimates(ticker, "annual", 10), []),
            ("historical", self.fmp.get_historical_prices(ticker), {}),
            ("news", self.fmp.get_stock_news(ticker, 20), []),
            ("insider_trades", self.fmp.get_insider_trading(ticker, limit=200), []),
            ("insider_roster", self.fmp.get_insider_roster(ticker), []),
            ("beneficial_owners", self.fmp.get_beneficial_ownership(ticker), []),
            (
                "segments_raw",
                self.fmp.get_revenue_product_segmentation(ticker, "annual", "flat"),
                [],
            ),
            ("earnings_dates", self.fmp.get_historical_earnings_dates(ticker), []),
            ("analyst_analysis", analyst_service.get_analysis(ticker), None),
            ("holders_response", holders_service.get_holders(ticker), None),
            # Snapshot services — same data the Financials tab shows in
            # TickerDetailView. Fetching here gives the report cards the
            # exact same numbers the user already sees on the other view.
            (
                "snap_profitability",
                get_profitability_snapshot_service().get_profitability_snapshot(ticker),
                None,
            ),
            (
                "snap_health",
                get_health_snapshot_service().get_health_snapshot(ticker),
                None,
            ),
            (
                "snap_growth",
                get_growth_snapshot_service().get_growth_snapshot(ticker),
                None,
            ),
            (
                "snap_valuation",
                get_valuation_snapshot_service().get_valuation_snapshot(ticker),
                None,
            ),
            (
                "revenue_breakdown",
                get_revenue_breakdown_service().get_revenue_breakdown(ticker),
                None,
            ),
            # Peer ticker list — needed by pass 2 to fan out for peer
            # profiles. Cheap (single FMP call); kept in pass 1 so we
            # don't pay an extra round-trip when peers are empty.
            ("peer_tickers", self.fmp.get_stock_peers(ticker), []),
            # Earnings-call transcript — primary source for TAM extraction
            # (PR 3) and management-guidance extraction (PR 6). Two FMP
            # calls under the hood (list + content) but small payloads.
            ("transcript", self.fmp.get_earning_call_transcript(ticker), ""),
            # Macro indicators (PR 4) — commodities + FX + VIX + 10Y.
            # One FMP call per symbol via stock-price-change which already
            # returns 1D / 5D / 1M / 1Y % changes; no historical fetch
            # needed. Total ~10 extra parallel calls per request.
            ("macro_indicators", self._fetch_macro_indicators(), []),
            # FRED indicators (PR 5) — CPI / Fed Funds / 10Y / yield curve.
            # 4 FRED API calls behind a 6h in-memory cache, so usually 0
            # network calls per request after a worker warms up. No-op
            # when FRED_API_KEY is missing.
            ("fred_indicators", self._fetch_fred_indicators(), []),
        ]

        results = await asyncio.gather(
            *(t[1] for t in tasks), return_exceptions=True
        )

        for (attr, _coro, default), result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.warning(
                    f"Collector: {attr} failed for {ticker}: "
                    f"{type(result).__name__}: {result}"
                )
                if attr == "profile":
                    # Profile is non-recoverable — re-raise so the endpoint's
                    # classifier sees the real upstream exception (FMPAuthException
                    # / FMPRateLimitException / httpx error) and maps to
                    # FMP_UNAVAILABLE / FMP_RATE_LIMITED. Falling through to the
                    # empty-dict default would masquerade as TICKER_NOT_FOUND.
                    raise result
                setattr(out, attr, default)
            else:
                setattr(out, attr, result if result is not None else default)

        # ── Pass 2: fetches that depend on pass-1 results ─────────────
        # peer_profiles needs peer_tickers; sector_aggregates needs
        # profile.sector. Both can run in parallel within the second pass.
        await self._fetch_dependent(out)

    async def _fetch_fred_indicators(self) -> List[Dict[str, Any]]:
        """Fetch FRED snapshots (CPI, Fed Funds, 10Y, T10Y2Y) in parallel.

        Returns dicts mirroring `FREDSeriesSnapshot` shape so downstream
        code can stay JSON-friendly without importing the dataclass.
        Empty list when the FRED API key is missing — caller treats that
        as "FRED unavailable" and skips the corresponding risk factors.
        """
        # Lazy import — keeps the FRED dependency out of test paths that
        # don't exercise the macro module (e.g. schema-parity tests).
        from app.integrations.fred import (
            MACRO_SERIES,
            FREDSeriesSnapshot,
            get_fred_client,
        )

        client = get_fred_client()
        if not client.is_configured:
            return []

        async def _one(series_id: str) -> Optional[FREDSeriesSnapshot]:
            try:
                return await client.get_snapshot(series_id)
            except Exception as e:
                logger.warning(
                    f"FRED snapshot failed for {series_id}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

        snaps = await asyncio.gather(
            *[_one(sid) for sid in MACRO_SERIES.keys()]
        )
        out: List[Dict[str, Any]] = []
        for snap in snaps:
            if snap is None:
                continue
            out.append({
                "series_id": snap.series_id,
                "latest": snap.latest,
                "as_of": snap.as_of,
                "yoy_pct": snap.yoy_pct,
                "change_6mo_pct": snap.change_6mo_pct,
                "change_6mo_relative_pct": snap.change_6mo_relative_pct,
            })
        return out

    async def _fetch_macro_indicators(self) -> List[Dict[str, Any]]:
        """Fetch the latest 1M/1Y change for every macro symbol in one pass.

        Each lookup is a single FMP `stock-price-change` call that
        returns intraday + multi-period percentages, so we don't pay
        for a separate historical fetch per symbol. Failures degrade
        silently — missing symbols just drop out of the output list
        rather than corrupt the parallel gather.
        """
        async def _one(sym: str) -> Optional[Dict[str, Any]]:
            try:
                row = await self.fmp.get_stock_price_change(sym)
                if not isinstance(row, dict) or not row:
                    return None
                # FMP returns numeric % changes already — no math here.
                return {
                    "symbol": sym,
                    "change_1m_pct": _num_or_none(row.get("1M")),
                    "change_1y_pct": _num_or_none(row.get("1Y")),
                    "change_5d_pct": _num_or_none(row.get("5D")),
                }
            except Exception as e:
                logger.warning(
                    f"macro indicator fetch failed for {sym}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

        results = await asyncio.gather(*[_one(s) for s in _MACRO_SYMBOLS])
        return [r for r in results if r is not None]

    async def _fetch_dependent(self, out: CollectedTickerData) -> None:
        """Second-pass fetches that depend on first-pass data resolving.

        Kept separate so the first pass stays a flat parallel gather
        (easy to reason about, no per-task ordering bugs). All sub-tasks
        here degrade gracefully — peer profiles missing means the Moat
        module's competitor list is empty, sector aggregates missing
        falls back to a peer-derived computation, FRED TAM missing
        falls back to "hide the TAM column".
        """
        ticker = out.ticker
        profile_data = out.profile or {}
        sector = profile_data.get("sector")
        industry = profile_data.get("industry")

        # ── Phase 2 (revenue-mix-aware): Gemini grounded research ──
        #
        # Lazy import so the test paths and any code path that doesn't
        # touch Moat data don't pay the Gemini-client import cost.
        peers: List[str] = []
        try:
            from app.services.competitor_intel_service import (
                get_competitor_intel_service,
            )
            intel_peers = await get_competitor_intel_service().get_competitors(
                ticker, profile_data,
            )
        except Exception as exc:
            logger.warning(
                "Collector pass 2: competitor_intel call failed for %s: "
                "%s — falling back to Phase 1 deterministic path",
                ticker, exc,
            )
            intel_peers = None

        if intel_peers:
            # Trust Phase-2 list verbatim — already FMP-validated +
            # capped at 7 inside the service. Downstream
            # `_build_competitors()` still computes per-peer scores +
            # market-share, just from this curated list.
            peers = intel_peers
        else:
            # ── Phase 1 fallback (deterministic peer assembly) ──
            #
            # FMP's `/stock-peers` is unreliable — micro-cap noise,
            # mega-cap misclassifications, or too few candidates. We
            # always augment from same-industry universe constituents
            # when industry is known; FMP peers stay first in the
            # dedup'd list, universe peers are supplemental. The
            # "don't fabricate" guarantee is preserved by the $27.3B
            # floor + 7-row cap in `_build_competitors()`.
            peers = (out.peer_tickers or [])[:8]
            if industry:
                augment = _industry_universe_peers(
                    industry,
                    exclude={ticker.upper(), *(p.upper() for p in peers)},
                )
                peers = list(dict.fromkeys(peers + augment))[:12]

        peer_profiles_task = (
            self.fmp.get_company_profiles_batch(peers)
            if peers else asyncio.sleep(0, result=[])
        )
        peer_ratios_task = (
            self._fetch_peer_ratios(peers)
            if peers else asyncio.sleep(0, result={})
        )
        sector_agg_task = (
            get_sector_aggregates(sector)
            if sector else asyncio.sleep(0, result=None)
        )
        # Industry dossier read — GUARANTEES every ticker gets data via
        # the service's 4-tier fallback (Census → industry-FRED →
        # sector-FRED → all-industry USNGSP). The weekly batch
        # pre-computes most industries; the read path computes any
        # missing ones live (one-time cost, memoized for 5 min). Imported
        # lazily so the FRED dependency stays out of test paths that
        # don't exercise Moat data.
        from app.services.industry_dossier_service import (
            get_industry_dossier_service,
        )

        industry_tam_task = (
            get_industry_dossier_service().get_or_compute_dossier(
                industry=industry, sector=sector,
            )
            if industry else asyncio.sleep(0, result=None)
        )

        peer_profiles, peer_ratios, sector_agg, industry_tam = await asyncio.gather(
            peer_profiles_task,
            peer_ratios_task,
            sector_agg_task,
            industry_tam_task,
            return_exceptions=True,
        )

        if isinstance(peer_profiles, Exception):
            logger.warning(
                f"Collector pass 2: peer_profiles failed for {ticker}: "
                f"{type(peer_profiles).__name__}: {peer_profiles}"
            )
            out.peer_profiles = []
        else:
            out.peer_profiles = peer_profiles or []

        if isinstance(peer_ratios, Exception):
            logger.warning(
                f"Collector pass 2: peer_ratios failed for {ticker}: "
                f"{type(peer_ratios).__name__}: {peer_ratios}"
            )
            out.peer_ratios = {}
        else:
            out.peer_ratios = peer_ratios or {}

        if isinstance(sector_agg, Exception):
            logger.warning(
                f"Collector pass 2: sector_aggregates failed for {ticker}: "
                f"{type(sector_agg).__name__}: {sector_agg}"
            )
            out.sector_aggregates = None
        else:
            out.sector_aggregates = sector_agg

        if isinstance(industry_tam, Exception):
            logger.warning(
                f"Collector pass 2: industry_tam failed for {ticker}: "
                f"{type(industry_tam).__name__}: {industry_tam}"
            )
            out.industry_tam = None
        else:
            out.industry_tam = industry_tam

        # ── Phase 3C: fetch USPTO patents + FDA approvals for the
        # Intangible Assets pillar. Cached 180-day in ip_intel_cache so
        # the second user benefits. Runs BEFORE _precompute_moat_grounded
        # so the deterministic scorer can use the IP data when deciding
        # whether grounded fallback is needed.
        try:
            from app.services.ip_intel_service import get_ip_intel_service
            out.ip_intel = await get_ip_intel_service().get_ip_intel(
                out.ticker, out.profile or {},
            )
        except Exception as exc:
            logger.warning(
                "Collector pass 2: ip_intel fetch failed for %s: %s",
                out.ticker, exc,
            )
            out.ip_intel = None

        # ── Phase 3D: precompute Gemini grounded fallback for pillars
        # the deterministic moat scorer would leave at low confidence.
        # Runs here (async context) so the synchronous assemble_report
        # can just read out.moat_grounded_pillars. The full deterministic
        # scoring also re-runs inside assemble_report (cheap — sector
        # benchmark lookup is in-memory cached for 1h), so we use the
        # same logic here to decide whether grounded is needed.
        await self._precompute_moat_grounded(out)

    async def _precompute_moat_grounded(self, out: "CollectedTickerData") -> None:
        """Run the deterministic moat scorer once to decide which pillars
        would fall back; if any need fallback, call Gemini grounded
        research and store the result on `out.moat_grounded_pillars`.
        Safe to call even when there's no profile / sector data — silent
        no-op in that case.
        """
        if not (out.profile and (out.profile.get("sector") or out.profile.get("industry"))):
            return
        try:
            from app.services.moat_scoring_service import (
                PILLAR_ORDER as _PILLAR_ORDER,
                get_moat_scoring_service,
                score_moat_dimensions,
            )
        except Exception as exc:
            logger.warning(
                "Moat precompute: import failed for %s: %s", out.ticker, exc,
            )
            return

        try:
            det_pillars = await asyncio.to_thread(
                score_moat_dimensions,
                sector=out.profile.get("sector"),
                industry=out.profile.get("industry"),
                profile=out.profile or {},
                income=out.income or [],
                balance=out.balance or [],
                ratios=out.ratios or [],
                industry_tam=out.industry_tam,
                transcript=out.transcript or None,
                ip_intel=out.ip_intel,
            )
        except Exception as exc:
            logger.warning(
                "Moat precompute: deterministic scoring failed for %s: %s",
                out.ticker, exc,
            )
            return

        low_conf = [
            p for p in _PILLAR_ORDER
            if (det_pillars.get(p) is None
                or getattr(det_pillars[p], "score", None) is None)
        ]
        if not low_conf:
            return

        try:
            grounded = await get_moat_scoring_service().gemini_grounded_fallback(
                out.ticker, out.profile,
            )
            out.moat_grounded_pillars = grounded or {}
            resolved = sorted(out.moat_grounded_pillars.keys())
            still_missing = sorted(set(low_conf) - set(resolved))
            logger.info(
                "Moat grounded fallback for %s: requested=%s resolved=%s "
                "still_missing=%s (those fall to legacy AI)",
                out.ticker, low_conf, resolved, still_missing,
            )
        except Exception as exc:
            logger.warning(
                "Moat precompute: grounded fallback failed for %s: %s",
                out.ticker, exc,
            )

    async def _fetch_peer_ratios(
        self, peers: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch the three peer signals (op margin, ROE, revenue growth)
        plus TTM revenue, keyed by ticker.

        FMP split these across three endpoints in late 2025:
          * /ratios-ttm           → operatingProfitMarginTTM
          * /key-metrics-ttm      → returnOnEquityTTM, marketCapTTM,
                                    priceToSalesRatioTTM
          * /financial-growth     → revenueGrowth

        TTM revenue is derived as marketCapTTM / priceToSalesRatioTTM
        (P/S is mktCap/revenue by definition, so the inverse gives
        revenue without an extra FMP call). Used by `_build_competitors`
        as a more honest "Market Share" denominator than mkt cap alone.

        We re-emit ratios under the legacy unsuffixed names so
        `_build_competitors` keeps working with no signature change.
        Returns {} for any peer whose three calls all failed.
        """
        sem = asyncio.Semaphore(8)

        async def _one(sym: str) -> Tuple[str, Dict[str, Any]]:
            async with sem:
                ratios_task = self.fmp.get_ratios_ttm(sym)
                km_task = self.fmp.get_key_metrics_ttm(sym)
                growth_task = self.fmp._make_request(
                    "financial-growth",
                    params={"symbol": sym.upper(), "period": "annual", "limit": 1},
                )
                ratios, km, growth = await asyncio.gather(
                    ratios_task, km_task, growth_task,
                    return_exceptions=True,
                )
                merged: Dict[str, Any] = {}
                if isinstance(ratios, list) and ratios:
                    op = ratios[0].get("operatingProfitMarginTTM")
                    if op is not None:
                        merged["operatingProfitMargin"] = op
                if isinstance(km, list) and km:
                    km0 = km[0]
                    roe = km0.get("returnOnEquityTTM")
                    if roe is not None:
                        merged["returnOnEquity"] = roe
                    mc_ttm = km0.get("marketCapTTM")
                    ps_ttm = km0.get("priceToSalesRatioTTM")
                    try:
                        if mc_ttm is not None and ps_ttm is not None:
                            ps_f = float(ps_ttm)
                            if ps_f > 0:
                                merged["revenue_ttm"] = float(mc_ttm) / ps_f
                    except (TypeError, ValueError):
                        pass
                if isinstance(growth, list) and growth:
                    rg = growth[0].get("revenueGrowth")
                    if rg is not None:
                        merged["revenueGrowth"] = rg
                if not merged:
                    for label, result in (
                        ("ratios-ttm", ratios),
                        ("key-metrics-ttm", km),
                        ("financial-growth", growth),
                    ):
                        if isinstance(result, Exception):
                            logger.debug(
                                f"peer {label} failed for {sym}: "
                                f"{type(result).__name__}: {result}"
                            )
                return sym.upper(), merged

        results = await asyncio.gather(*[_one(s) for s in peers])
        return {sym: row for sym, row in results}

    # ── Phase 2: derived metrics with edge-case correctness ───────────

    def _compute_metrics(self, out: CollectedTickerData) -> None:
        """Compute Altman Z, growth rates, ratios, fair value, etc.

        All helpers return None when input is missing/zero rather than
        substituting a sentinel that masquerades as real data.
        Downstream callers convert None → 0.0 for the JSON response and
        attach a "Data unavailable" label for the user.
        """
        c: Dict[str, Any] = {}
        profile, quote = out.profile, out.quote
        income, balance, cash_flow = out.income, out.balance, out.cash_flow
        ratios, estimates = out.ratios, out.estimates
        key_metrics = out.key_metrics

        # ── Current price ─────────────────────────────────────────────
        current_price = _safe_float(quote, "price")
        c["current_price"] = current_price

        # ── Altman Z-Score (manufacturing formula) ────────────────────
        c["altman_z"] = _altman_z(balance, income, profile)

        # ── Revenue growth YoY ────────────────────────────────────────
        if len(income) >= 2:
            rev_curr = _safe_float(income[0], "revenue")
            rev_prev = _safe_float(income[1], "revenue")
            c["revenue_growth_yoy"] = _safe_pct_change(rev_curr, rev_prev)
            c["total_revenue"] = rev_curr
        else:
            c["revenue_growth_yoy"] = None
            c["total_revenue"] = _safe_float(income[0], "revenue") if income else 0.0

        # ── Free cash flow ────────────────────────────────────────────
        if cash_flow:
            fcf = _safe_float(cash_flow[0], "freeCashFlow")
            c["fcf"] = fcf
            c["fcf_negative"] = fcf < 0
        else:
            c["fcf"] = None
            c["fcf_negative"] = False

        # ── Key ratios ────────────────────────────────────────────────
        # FMP /stable/ratios renamed several fields in late 2025 (PE, EV/EBITDA,
        # debt/equity, interest coverage) and moved ROE/ROA to /key-metrics.
        # We try the new name first, then the legacy name as fallback so this
        # keeps working if the upstream reverts.
        if ratios:
            r0 = ratios[0]
            km0 = key_metrics[0] if key_metrics else {}
            c["gross_margin"] = _pct_or_none(r0.get("grossProfitMargin"))
            c["net_margin"] = _pct_or_none(r0.get("netProfitMargin"))
            c["operating_margin"] = _pct_or_none(r0.get("operatingProfitMargin"))
            c["roe"] = _pct_or_none(
                r0.get("returnOnEquity") or km0.get("returnOnEquity")
            )
            c["roa"] = _pct_or_none(
                r0.get("returnOnAssets") or km0.get("returnOnAssets")
            )
            c["pe_ratio"] = _num_or_none(
                r0.get("priceToEarningsRatio") or r0.get("priceEarningsRatio")
            )
            c["pb_ratio"] = _num_or_none(r0.get("priceToBookRatio"))
            c["ps_ratio"] = _num_or_none(r0.get("priceToSalesRatio"))
            c["pfcf_ratio"] = _num_or_none(
                r0.get("priceToFreeCashFlowRatio")
                or r0.get("priceToFreeCashFlowsRatio")
            )
            c["ev_ebitda"] = _num_or_none(
                r0.get("enterpriseValueMultiple")
                or r0.get("enterpriseValueOverEBITDA")
            )
            c["debt_equity"] = _num_or_none(
                r0.get("debtToEquityRatio") or r0.get("debtEquityRatio")
            )
            c["current_ratio"] = _num_or_none(r0.get("currentRatio"))
            c["interest_coverage"] = _num_or_none(
                r0.get("interestCoverageRatio") or r0.get("interestCoverage")
            )
        else:
            for k in (
                "gross_margin", "net_margin", "operating_margin", "roe", "roa",
                "pe_ratio", "pb_ratio", "ps_ratio", "pfcf_ratio", "ev_ebitda",
                "debt_equity", "current_ratio", "interest_coverage",
            ):
                c[k] = None

        # Earnings Yield = 1/PE * 100. None for negative or zero PE — a
        # negative E/Y from negative earnings is meaningless and would
        # mislead the user; surface as "N/A" downstream.
        c["earnings_yield"] = compute_earnings_yield(c)

        # ── Fair value from FMP DCF + upside ──────────────────────────
        dcf = _num_or_none(profile.get("dcf"))
        c["fair_value"] = dcf if dcf and dcf > 0 else None
        c["upside_pct"] = _safe_pct_change(c["fair_value"], current_price) \
            if c["fair_value"] is not None else None

        # ── Analyst forecast CAGRs ────────────────────────────────────
        # Sort by ISO date string ascending so start=oldest, end=newest
        # regardless of FMP's response order (newest-first vs oldest-first
        # has flipped historically across endpoint versions).
        if estimates and len(estimates) >= 2:
            # CAGR spans the 4 visible chart years so the legend caption
            # matches what the user sees. The off-screen anchor returned
            # by the helper is consumed only by _build_revenue_forecast_
            # partial for the leftmost bar's YoY chip — it must NOT
            # stretch the CAGR span.
            visible_est, _ = _select_visible_forecast_window(estimates)
            n = len(visible_est)
            if n >= 2:
                est0_rev = _est_revenue(visible_est[0])
                estn_rev = _est_revenue(visible_est[-1])
                c["revenue_cagr"] = _safe_cagr(est0_rev, estn_rev, n)
                est0_eps = _est_eps(visible_est[0])
                estn_eps = _est_eps(visible_est[-1])
                c["eps_cagr"] = _safe_cagr(est0_eps, estn_eps, n)
            else:
                c["revenue_cagr"] = None
                c["eps_cagr"] = None
        else:
            c["revenue_cagr"] = None
            c["eps_cagr"] = None

        # ── Historical price chart data ───────────────────────────────
        # Pull 200 closes to support the 180-day baseline σ used by the
        # price-action volatility classifier. The sparkline still renders
        # only the chosen evaluation window (7/15/30/45 days) — the rest
        # is baseline data the user never sees but the math needs.
        #
        # We also carry the trading-day date for each close so the window
        # selector can map "Last 30 Days" to a true 30-calendar-day window
        # (close on/before today−30 days) instead of indexing 30 entries
        # back in the array, which would land ~42 calendar days back.
        hist_list = _hist_list(out.historical)
        recent_pairs: List[Tuple[date, float]] = []
        for p in hist_list[:200]:
            close = p.get("close")
            if close is None:
                continue
            date_str = p.get("date") or ""
            try:
                d = date.fromisoformat(date_str[:10])
            except ValueError:
                continue
            recent_pairs.append((d, float(close)))
        recent_pairs.reverse()  # FMP returns newest-first; we want chronological.
        c["recent_prices"] = [px for _, px in recent_pairs]
        c["recent_price_dates"] = [d for d, _ in recent_pairs]

        # ── Monthly closes for the Wall Street chart ──────────────────
        c["monthly_prices"] = _monthly_closes(hist_list, count=12)

        out.computed = c

    # ── Phase 3: deterministic section assembly ───────────────────────

    def _build_sections(self, out: CollectedTickerData) -> None:
        """Build every TickerReportResponse section that does not require AI.

        AI-derived structural fields (moat dimensions, market dynamics,
        macro risk factors, fundamental_metrics ratings, core_thesis,
        executive_summary, critical_factors, quality_score) are NOT
        produced here — those merge in via `assemble_report`.
        """
        c = out.computed
        profile, quote = out.profile, out.quote
        current_price = c.get("current_price") or 0.0
        fair_value = c.get("fair_value")
        upside = c.get("upside_pct")

        now = datetime.now(timezone.utc)

        # ── Meta block ────────────────────────────────────────────────
        out.meta = {
            "symbol": out.ticker,
            "company_name": profile.get("companyName") or out.ticker,
            "exchange": profile.get("exchangeShortName") or "",
            "logo_url": profile.get("image"),
            "live_date": _format_live_date(now),
            "agent": _AGENT_MAP.get(out.persona_key, "buffett"),
        }

        # ── Valuation vital ───────────────────────────────────────────
        out.valuation_vital = _build_valuation_vital(
            current_price, fair_value, upside, out.snap_valuation
        )

        # ── Financial health vital ────────────────────────────────────
        out.financial_health_vital = _build_health_vital(
            c.get("altman_z"), c.get("debt_equity"), c.get("fcf_negative")
        )

        # ── Revenue vital — top segment hooked from real segments ─────
        # Prefer RevenueBreakdownService (parity with TickerDetailView's
        # Financials tab) and fall back to direct FMP segmentation.
        segments_built = _segments_from_breakdown(
            out.revenue_breakdown, out.segments_raw,
        )
        if not segments_built:
            segments_built = _build_revenue_segments(out.segments_raw)
        top_segment_name = (
            segments_built[0]["name"] if segments_built else "Primary"
        )
        top_segment_growth = _segment_growth_pct(segments_built)
        out.revenue_vital = _build_revenue_vital(
            c.get("total_revenue") or 0.0,
            c.get("revenue_growth_yoy"),
            top_segment_name,
            top_segment_growth,
        )

        # ── Forecast vital ────────────────────────────────────────────
        out.forecast_vital = _build_forecast_vital(
            c.get("revenue_cagr"), c.get("eps_cagr")
        )

        # ── Wall Street vital + consensus from AnalystService ────────
        out.wall_street_vital, out.wall_street_consensus_partial = \
            _build_wall_street_sections(
                out.analyst_analysis,
                out.holders_response,
                current_price,
                fair_value,
                c.get("monthly_prices") or [],
            )
        # Macro vital seed: real risk factor count placeholder (fully
        # populated after AI risk_factors arrive in assemble_report).
        out.macro_vital_seed = {
            "score": {"value": 5.0, "status": "neutral"},
            "threat_level": "low",
            "top_risk": "No Major Risks",
            "risk_trend": "stable",
            "active_risk_count": 0,
        }

        # ── Insider transactions, sentiment, and vital ────────────────
        insider_data, insider_vital_partial = _build_insider_sections(
            out.insider_trades
        )
        out.insider_data_partial = insider_data
        out.insider_vital_partial = insider_vital_partial

        # ── Key management roster ─────────────────────────────────────
        out.key_management_partial = _build_key_management(
            out.insider_roster,
            profile,
            current_price,
            beneficial_owners=out.beneficial_owners,
        )

        # ── Price action: deterministic earnings + news-catalyst event ──
        out.price_action_partial = _build_price_action(
            c.get("recent_prices") or [],
            current_price,
            out.earnings_dates,
            out.news,
            recent_price_dates=c.get("recent_price_dates") or [],
        )

        # ── Revenue engine segments ───────────────────────────────────
        out.revenue_engine_partial = _build_revenue_engine(
            segments_built, now,
        )

        # ── Revenue forecast (projections + CAGR — guidance from AI) ─
        out.revenue_forecast_partial = _build_revenue_forecast_partial(
            out.estimates, c.get("revenue_cagr"), c.get("eps_cagr")
        )

        # ── Fundamental metrics (4 cards) — deterministic from the
        #    same snapshot services that power TickerDetailView's
        #    Financials tab. AI's Stage A version is discarded. ────────
        out.fundamental_metrics_partial = _build_fundamental_metrics_from_snapshots(
            profitability=out.snap_profitability,
            growth=out.snap_growth,
            valuation=out.snap_valuation,
            health=out.snap_health,
        )

    # ── Phase 4: merge with AI output into final TickerReportResponse ─

    def assemble_report(
        self,
        out: CollectedTickerData,
        ai: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge real-data sections with AI narrative + scoring.

        `ai` is the dict produced by the AI generation step (Stage A in
        Phase 2; the legacy single-shot prompt today). Real data wins
        for every numeric/structural field; AI fills narrative and the
        few inherently-qualitative scores (moat dimensions, etc.).
        """
        c = out.computed
        meta = out.meta

        # ── Quality score ─────────────────────────────────────────────
        quality_score = _coerce_score(ai.get("quality_score"), default=50.0)

        # ── Overall assessment ─ numerics deterministic from cards;
        #    text comes from Stage B narrative or AI fallback. ────────
        ai_assessment = ai.get("overall_assessment") if isinstance(
            ai.get("overall_assessment"), dict
        ) else {}
        ai_assessment_text = (ai_assessment or {}).get("text") if ai_assessment else None
        if out.fundamental_metrics_partial:
            overall_assessment = _overall_assessment_from_cards(
                out.fundamental_metrics_partial, ai_assessment_text,
            )
        else:
            overall_assessment = ai_assessment or {
                "text": "Data unavailable for this ticker.",
                "average_rating": 0.0,
                "strong_count": 0,
                "weak_count": 0,
            }

        # ── Insider sentiment / ownership note (AI sets sentiment too,
        #    but we trust real net dollar volume from the collector). ─
        insider_data = dict(out.insider_data_partial)
        insider_ai = ai.get("insider_analysis") or {}
        insider_data["ownership_note"] = (
            insider_ai.get("ownership_note")
            or insider_data.get("ownership_note")
        )

        # ── Insider vital: AI provides only key_insight ──────────────
        insider_vital = dict(out.insider_vital_partial)
        insider_vital["key_insight"] = (
            insider_ai.get("key_insight")
            or insider_vital.get("key_insight")
            or "Insider activity reflected above."
        )

        # ── Key management: AI provides ownership_insight ────────────
        key_management = dict(out.key_management_partial)
        ai_km = ai.get("key_management") or {}
        key_management["ownership_insight"] = (
            ai_km.get("ownership_insight")
            or key_management.get("ownership_insight")
            or "Data unavailable for this ticker."
        )

        # ── Price action: AI provides narrative ───────────────────────
        price_action = dict(out.price_action_partial)
        ai_pa = ai.get("price_action") or {}
        price_action["narrative"] = (
            ai_pa.get("narrative")
            or "Recent price action reflected above."
        )

        # ── Revenue engine: AI provides analysis_note ────────────────
        revenue_engine = dict(out.revenue_engine_partial)
        ai_re = ai.get("revenue_engine") or {}
        revenue_engine["analysis_note"] = ai_re.get("analysis_note")

        # ── Revenue forecast: AI extracts guidance from transcript (PR 6).
        # Anti-fabrication overlay mirrors the TAM logic — when AI
        # claims "raised"/"lowered" without a verbatim source quote,
        # we coerce to "maintained" and null the attribution metadata.
        # This matches the Stage A prompt rule that requires a quote
        # for any non-default status.
        revenue_forecast = dict(out.revenue_forecast_partial)
        ai_rf = ai.get("revenue_forecast") or {}
        _overlay_ai_guidance(revenue_forecast, ai_rf)

        # ── Wall Street consensus: AI fills only hedge_fund_note ─────
        wall_street_consensus = dict(out.wall_street_consensus_partial)
        ai_ws = ai.get("wall_street") or {}
        wall_street_consensus["hedge_fund_note"] = ai_ws.get("hedge_fund_note")

        # ── Moat: dimensions, durability_note, competitive_insight come ─
        # from Stage A AI (qualitative). market_dynamics + competitors
        # are now collector-derived from real FMP data — AI's versions
        # are intentionally discarded for parity with the rest of the
        # "real data wins" policy that already governs fundamental_metrics
        # and the wall-street consensus.
        ai_moat = ai.get("moat_competition") or {}
        # ── Phase 3A: deterministic moat scoring grounded in real
        # FMP financials + sector benchmarks. Per-pillar: when ≥2
        # metrics resolve (confidence high/medium), use the
        # deterministic score; when <2 resolve (confidence low for
        # that pillar), fall through to the legacy AI Stage A dimension
        # — to be replaced in sub-phase 3D with Gemini grounded research
        # (web-search-cited) rather than ungrounded LLM judgment.
        from app.services.moat_scoring_service import (
            score_moat_dimensions,
            PILLAR_ORDER,
        )
        try:
            deterministic_pillars = score_moat_dimensions(
                sector=(out.profile or {}).get("sector"),
                industry=(out.profile or {}).get("industry"),
                profile=out.profile or {},
                income=out.income or [],
                balance=out.balance or [],
                ratios=out.ratios or [],
                industry_tam=out.industry_tam,
                transcript=out.transcript or None,
                ip_intel=out.ip_intel,
            )
        except Exception as exc:
            logger.warning(
                "Moat scoring failed for %s: %s — falling through to "
                "legacy AI dimensions for all pillars", out.ticker, exc,
            )
            deterministic_pillars = {}
        ai_dims_by_name = {
            (d.get("name") or ""): d
            for d in (ai_moat.get("dimensions") or [])
            if isinstance(d, dict)
        }
        # Phase 3D: grounded fallback for pillars deterministic left at
        # low confidence. Both `deterministic_pillars` and the grounded
        # scores were precomputed during the async `_fetch_dependent`
        # pass — assemble_report stays sync.
        grounded_scores: Dict[str, Dict[str, Any]] = out.moat_grounded_pillars or {}

        merged_dims: List[Dict[str, Any]] = []
        for pillar_name in PILLAR_ORDER:
            det = deterministic_pillars.get(pillar_name)
            if det is not None and det.score is not None:
                dim = det.to_dict()
                dim["source"] = "deterministic"
                merged_dims.append(dim)
                continue
            grounded = grounded_scores.get(pillar_name)
            if isinstance(grounded, dict) and grounded.get("score") is not None:
                grounded["source"] = "grounded"
                merged_dims.append(grounded)
                continue
            # Final fallback — legacy AI Stage A dimension. The
            # peer-score floor still applies so the gray polygon doesn't
            # collapse to center.
            ai_dim = ai_dims_by_name.get(pillar_name) or {
                "name": pillar_name, "score": 0.0, "peer_score": 5.0,
            }
            ai_dim["source"] = "ai_legacy"
            merged_dims.append(ai_dim)
        # Overlay real industry peer averages onto each dimension's
        # peer_score. Without this the gray "Peer Avg" pentagon on the
        # iOS radar collapses to a flat 5.0 (the legacy sector-median
        # anchor). Falls back to that 5.0 floor when the industry has
        # no benchmark row yet — new ticker, niche industry, or before
        # the first recompute_all() bootstrap completes.
        focal_industry = (out.profile or {}).get("industry")
        if focal_industry:
            try:
                from app.services.industry_moat_benchmark_service import (
                    get_industry_moat_benchmark_lookup,
                )
                peer_avgs = get_industry_moat_benchmark_lookup().get_pillar_benchmarks(
                    focal_industry,
                )
                for dim in merged_dims:
                    ind_avg = peer_avgs.get(dim.get("name"))
                    if ind_avg is not None:
                        dim["peer_score"] = ind_avg
            except Exception as exc:
                logger.warning(
                    "Moat peer-average overlay failed for %s / %s: %s — "
                    "falling through to 5.0 baseline",
                    out.ticker, focal_industry, exc,
                )
        moat_dims = _apply_peer_score_baseline(merged_dims)
        deterministic_market_dynamics = _build_market_dynamics(
            out.profile, out.sector_aggregates, out.peer_profiles,
        )
        # Two-tier TAM source priority: AI-extracted transcript quote
        # (highest trust — explicit, grounded), then FRED industry
        # value-added proxy (BEA series). If neither resolves, TAM stays
        # at 0.0 and iOS hides the column.
        _apply_tam_source(
            deterministic_market_dynamics,
            ai_moat.get("market_dynamics"),
            out.industry_tam,
        )
        deterministic_competitors = _build_competitors(
            my_ticker=out.ticker,
            my_profile=out.profile,
            my_ratios=out.ratios,
            my_revenue_growth=c.get("revenue_growth_yoy"),
            peer_profiles=out.peer_profiles,
            peer_ratios=out.peer_ratios,
            my_key_metrics=out.key_metrics,
        )
        moat_competition = {
            "market_dynamics": deterministic_market_dynamics,
            "dimensions": moat_dims,
            "durability_note": (
                ai_moat.get("durability_note")
                or "Data unavailable for this ticker."
            ),
            "competitors": deterministic_competitors,
            "competitive_insight": (
                ai_moat.get("competitive_insight")
                or "Data unavailable for this ticker."
            ),
        }
        moat_vital = _derive_moat_vital(moat_dims)

        # ── Macro: deterministic numeric factors merge into AI's qualitative
        # ones. Order of priority (real data wins on category collision):
        #   1. FRED snapshots (CPI / Fed Funds / yield curve) — PR 5
        #   2. FMP commodities/FX/VIX/rates — PR 4
        #   3. AI-generated geopolitical / regulatory / sector factors
        # FRED comes first because the underlying series are the most
        # authoritative (BLS / Treasury) and least noisy.
        ai_macro = ai.get("macro_data") or {}
        ai_risk_factors = [
            _sanitize_risk_factor(rf) for rf in (ai_macro.get("risk_factors") or [])
        ]
        fred_factors = _build_macro_risk_factors_from_fred(out.fred_indicators)
        fmp_factors = _build_macro_risk_factors_from_indicators(
            out.macro_indicators
        )
        # Two-pass merge: FRED first (most authoritative), then FMP
        # commodities, then AI. The merge helper already de-dupes by
        # category, so a FRED `interest_rates` entry will block both an
        # FMP yield-curve factor and an AI rate-policy factor.
        merged_after_fred = _merge_macro_risk_factors(fred_factors, fmp_factors)
        risk_factors = _merge_macro_risk_factors(merged_after_fred, ai_risk_factors)
        threat_level = ai_macro.get("overall_threat_level") or "low"
        macro_data = {
            "overall_threat_level": threat_level,
            "headline": ai_macro.get("headline") or "Macro overview unavailable.",
            "risk_factors": risk_factors,
            "intelligence_brief": (
                ai_macro.get("intelligence_brief")
                or "Data unavailable for this ticker."
            ),
            "last_updated": datetime.now(timezone.utc).strftime("Updated %b %d, %Y"),
        }
        macro_vital = _derive_macro_vital(risk_factors, threat_level)

        # ── Key vitals (assembled) ────────────────────────────────────
        key_vitals = {
            "valuation": out.valuation_vital,
            "moat": moat_vital,
            "financial_health": out.financial_health_vital,
            "revenue": out.revenue_vital,
            "insider": insider_vital,
            "macro": macro_vital,
            "forecast": out.forecast_vital,
            "wall_street": out.wall_street_vital,
        }

        # ── Top-level assembly ────────────────────────────────────────
        report: Dict[str, Any] = {
            "symbol": meta["symbol"],
            "company_name": meta["company_name"],
            "exchange": meta["exchange"],
            "logo_url": meta["logo_url"],
            "live_date": meta["live_date"],
            "agent": meta["agent"],
            "quality_score": quality_score,

            "executive_summary_text": (
                ai.get("executive_summary_text")
                or "Data unavailable for this ticker."
            ),
            "executive_summary_bullets": list(
                ai.get("executive_summary_bullets") or []
            ),

            "key_vitals": key_vitals,

            "core_thesis": _sanitize_thesis(ai.get("core_thesis")),

            # fundamental_metrics is now collector-derived (matches the
            # snapshot data the user sees in TickerDetailView's
            # Financials tab). AI's array is intentionally discarded.
            "fundamental_metrics": list(out.fundamental_metrics_partial or []),
            "overall_assessment": overall_assessment,

            "revenue_forecast": revenue_forecast,
            "insider_data": insider_data,
            "key_management": key_management,
            "price_action": price_action,
            "revenue_engine": revenue_engine,
            "moat_competition": moat_competition,
            "macro_data": macro_data,
            "wall_street_consensus": wall_street_consensus,

            "critical_factors": list(ai.get("critical_factors") or []),

            "disclaimer_text": DISCLAIMER,
        }

        return report


# ── Numeric helpers (None-safe) ───────────────────────────────────────


def _safe_float(d: Any, key: str, default: float = 0.0) -> float:
    if not isinstance(d, dict):
        return default
    v = d.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _est_revenue(est: Dict[str, Any]) -> float:
    """Pull the average revenue estimate off an analyst-estimates row.

    FMP `/stable` returns `revenueAvg`; the deprecated `/api/v3` shape
    returned `estimatedRevenueAvg`. Try the current name first, fall
    back so older test fixtures and any straggler responses still parse.
    The same pattern is used in stock_overview_service and the stocks
    endpoint — keep them in sync.
    """
    return (
        _safe_float(est, "revenueAvg")
        or _safe_float(est, "estimatedRevenueAvg")
    )


def _est_eps(est: Dict[str, Any]) -> float:
    """Pull the average EPS estimate off an analyst-estimates row.
    See `_est_revenue` for the rationale on the two-name lookup."""
    return (
        _safe_float(est, "epsAvg")
        or _safe_float(est, "estimatedEpsAvg")
    )


def _num_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f


def _pct_or_none(v: Any) -> Optional[float]:
    """FMP returns ratios as fractions (0.32 = 32%); convert to display %."""
    n = _num_or_none(v)
    return round(n * 100, 1) if n is not None else None


def compute_earnings_yield(computed: Dict[str, Any]) -> Optional[float]:
    """Earnings Yield = 1/PE * 100. None when PE is missing or non-positive.

    A negative PE (negative earnings) produces a meaningless negative
    yield, so we surface None and let the renderer show "N/A" instead.
    """
    pe = computed.get("pe_ratio")
    if pe is None or pe <= 0:
        return None
    return round(100.0 / pe, 2)


def _safe_pct_change(
    current: Optional[float], previous: Optional[float]
) -> Optional[float]:
    """Percentage change. Returns None when prior is missing or zero."""
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 1)


def _safe_cagr(
    start: Optional[float], end: Optional[float], periods: int
) -> Optional[float]:
    """CAGR. Returns None when either endpoint is non-positive."""
    if start is None or end is None:
        return None
    if start <= 0 or end <= 0 or periods <= 1:
        return None
    return round(((end / start) ** (1.0 / max(periods - 1, 1)) - 1) * 100, 1)


def _select_visible_forecast_window(
    estimates: List[Dict[str, Any]],
    today: Optional[date] = None,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Pick the 4 chart-visible projections plus the off-screen anchor.

    The leftmost visible projection is the earliest FY whose `date` field
    is >= today (i.e., the FY currently in progress, or the next FY when
    the prior one just closed). We then take that and the next 3 forward
    FYs (4 total). The FY immediately before the leftmost visible serves
    as the YoY anchor for the first bar so the chip on bar 1 has a real
    prior-year baseline.

    Fallback: if FMP didn't return enough forward entries to fill the
    4-wide window, return the last 4 entries by date with the 5th-from-
    last as the anchor. This keeps the synthetic test fixtures (which
    pass 3-entry past-dated lists) producing the same projections count
    as before.
    """
    if not estimates:
        return [], None
    today = today or datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    sorted_all = sorted(estimates, key=lambda e: (e.get("date") or ""))
    first_future = next(
        (
            i for i, e in enumerate(sorted_all)
            if (e.get("date") or "")[:10] >= today_iso
        ),
        None,
    )
    if first_future is not None and first_future + 4 <= len(sorted_all):
        visible = sorted_all[first_future:first_future + 4]
        anchor = (
            sorted_all[first_future - 1] if first_future > 0 else None
        )
        return visible, anchor
    visible = sorted_all[-4:]
    anchor = sorted_all[-5] if len(sorted_all) >= 5 else None
    return visible, anchor


def _coerce_score(v: Any, default: float = 50.0) -> float:
    """Force AI's quality_score into [0, 100] float, even when sent as a string."""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:
        return default
    return max(0.0, min(100.0, f))


def _altman_z(
    balance: List[Dict[str, Any]],
    income: List[Dict[str, Any]],
    profile: Dict[str, Any],
) -> Optional[float]:
    """Altman Z (manufacturing). Returns None when inputs are unusable.

    No more silent `or 1` divide-by-zero masks — a zero-asset balance
    sheet returns None and the report renders 0.0 with a "Data
    unavailable" label, instead of a fake "safe zone" rating.
    """
    if not balance or not income:
        return None
    b, i = balance[0], income[0]
    total_assets = _safe_float(b, "totalAssets")
    if total_assets <= 0:
        return None
    total_liab = _num_or_none(b.get("totalLiabilities"))
    if total_liab is None or total_liab <= 0:
        return None

    wc = (
        _safe_float(b, "totalCurrentAssets")
        - _safe_float(b, "totalCurrentLiabilities")
    )
    re_earn = _safe_float(b, "retainedEarnings")
    ebit = _safe_float(i, "operatingIncome")
    mkt_cap = _safe_float(profile, "mktCap")
    sales = _safe_float(i, "revenue")

    z = (
        1.2 * (wc / total_assets)
        + 1.4 * (re_earn / total_assets)
        + 3.3 * (ebit / total_assets)
        + 0.6 * (mkt_cap / total_liab)
        + 1.0 * (sales / total_assets)
    )
    return round(z, 2)


def _hist_list(historical: Any) -> List[Dict[str, Any]]:
    """FMP /historical-price-eod/full returns either a flat list or a
    {"historical": [...]} dict depending on plan tier. Normalize."""
    if isinstance(historical, list):
        return historical
    if isinstance(historical, dict):
        return historical.get("historical", []) or []
    return []


def _monthly_closes(
    hist_list: List[Dict[str, Any]], count: int = 12
) -> List[Dict[str, Any]]:
    """Last `count` monthly closes formatted as {"month": "MM/YYYY", "price": close}.

    FMP returns daily prices newest-first; we walk forward picking the
    first close we encounter for each YYYY-MM, then reverse to chart
    order (oldest → newest) for the iOS chart.
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for p in hist_list[: 365 * 2]:  # ~2 years of trading days max
        d = (p.get("date") or "")[:7]
        if not d or d in seen:
            continue
        seen.add(d)
        try:
            month_fmt = f"{d[5:7]}/{d[:4]}"
        except (IndexError, ValueError):
            continue
        out.append({"month": month_fmt, "price": _safe_float(p, "close")})
        if len(out) >= count:
            break
    out.reverse()
    return out


# ── Section builders (deterministic from real data) ──────────────────


def _format_live_date(now: datetime) -> str:
    """Platform-safe replacement for strftime('%-d') / '%-I'."""
    day = now.day
    hour = now.hour % 12 or 12
    ampm = "AM" if now.hour < 12 else "PM"
    return f"Live Data as of {now.strftime('%b')} {day}, {hour}:{now.strftime('%M')} {ampm}"


def _format_revenue(rev: Optional[float]) -> str:
    if not rev or rev <= 0:
        return "$0"
    if rev >= 1e12:
        return f"${rev / 1e12:.1f}T"
    if rev >= 1e9:
        return f"${rev / 1e9:.1f}B"
    if rev >= 1e6:
        return f"${rev / 1e6:.0f}M"
    return f"${rev:,.0f}"


def _format_currency_short(v: float) -> str:
    """For insider transaction value column ($K/M/B)."""
    if v <= 0:
        return "$0"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


def _format_shares_short(v: float) -> str:
    """Compact share count for the insider summary card."""
    if v <= 0:
        return "0"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{int(v):,}"


def _format_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}%"


_VALUATION_STATUS_LEVEL = {
    "deep_undervalued": 4,
    "underpriced": 3,
    "fair_value": 2,
    "overpriced": 1,
}
_LEVEL_TO_STATUS = {v: k for k, v in _VALUATION_STATUS_LEVEL.items()}


def _snapshot_to_valuation_status(rating: int) -> Tuple[str, float]:
    """Map a 1-5 snapshot rating to a (status, upside_potential) pair."""
    if rating >= 5:
        return "underpriced", 10.0
    if rating == 4:
        return "underpriced", 5.0
    if rating == 3:
        return "fair_value", 0.0
    if rating == 2:
        return "overpriced", -5.0
    if rating == 1:
        return "overpriced", -15.0
    return "fair_value", 0.0  # rating=0 → unavailable, neutral default


def _build_valuation_vital(
    current_price: float,
    fair_value: Optional[float],
    upside: Optional[float],
    valuation_snapshot: Optional[SnapshotItemResponse] = None,
) -> Dict[str, Any]:
    """Sets status from real DCF upside, with multi-metric snapshot as
    a tiebreaker. When DCF is missing, snapshot rating drives the
    decision instead of defaulting to "fair_value" with 0% upside.
    """
    snap_rating = int(valuation_snapshot.rating) if (
        valuation_snapshot is not None and valuation_snapshot.rating
    ) else 0

    if upside is None or fair_value is None:
        # No DCF — defer to the multi-metric snapshot when available.
        if snap_rating > 0:
            status, snap_upside = _snapshot_to_valuation_status(snap_rating)
            return {
                "status": status,
                "current_price": round(current_price, 2),
                "fair_value": round(current_price, 2),
                "upside_potential": snap_upside,
            }
        # Neither DCF nor snapshot — keep the honest fair_value default.
        return {
            "status": "fair_value",
            "current_price": round(current_price, 2),
            "fair_value": round(current_price, 2),
            "upside_potential": 0.0,
        }

    # DCF present — pick the DCF-implied status first.
    if upside >= 30:
        status = "deep_undervalued"
    elif upside >= 10:
        status = "underpriced"
    elif upside >= -10:
        status = "fair_value"
    else:
        status = "overpriced"

    # Reconcile with the snapshot when both signals are available. If
    # the multi-metric snapshot disagrees by ≥2 levels (e.g., DCF says
    # deep_undervalued but P/E + EV/EBITDA + P/FCF say overpriced),
    # downgrade one level milder rather than silently trusting a stale
    # DCF.
    if snap_rating > 0:
        snap_status, _ = _snapshot_to_valuation_status(snap_rating)
        dcf_level = _VALUATION_STATUS_LEVEL.get(status, 2)
        snap_level = _VALUATION_STATUS_LEVEL.get(snap_status, 2)
        if abs(dcf_level - snap_level) >= 2:
            # Move one level toward the snapshot — never wholesale flip.
            adjusted = dcf_level + (1 if snap_level > dcf_level else -1)
            status = _LEVEL_TO_STATUS.get(adjusted, status)

    return {
        "status": status,
        "current_price": round(current_price, 2),
        "fair_value": round(fair_value, 2),
        "upside_potential": round(upside, 1),
    }


def _build_health_vital(
    altman_z: Optional[float],
    debt_equity: Optional[float],
    fcf_negative: bool,
) -> Dict[str, Any]:
    if altman_z is None:
        return {
            "level": "moderate",
            "altman_z_score": 0.0,
            "altman_z_label": "Data unavailable",
            "additional_metric": "Leverage data unavailable"
            if debt_equity is None else _leverage_label(debt_equity),
            "additional_metric_status": "neutral",
            "fcf_note": "FCF data unavailable",
        }

    if altman_z < 1.8:
        level, z_label = "critical", "Distress Zone (Below 1.8)"
    elif altman_z < 2.4:
        level, z_label = "weak", "Grey Zone (1.8-3.0)"
    elif altman_z < 3.0:
        level, z_label = "moderate", "Grey Zone (1.8-3.0)"
    else:
        level, z_label = "strong", "Safe Zone (Above 3.0)"

    return {
        "level": level,
        "altman_z_score": altman_z,
        "altman_z_label": z_label,
        "additional_metric": (
            "Leverage data unavailable" if debt_equity is None
            else _leverage_label(debt_equity)
        ),
        "additional_metric_status": level,
        "fcf_note": "Negative FCF" if fcf_negative else "Positive FCF",
    }


def _leverage_label(de: float) -> str:
    if de > 2.5:
        return "High Leverage"
    if de > 1.0:
        return "Moderate Leverage"
    return "Low Leverage"


def _build_revenue_vital(
    total_revenue: float,
    yoy_growth: Optional[float],
    top_segment: str,
    top_segment_growth: Optional[float],
) -> Dict[str, Any]:
    growth = yoy_growth if yoy_growth is not None else 0.0
    if growth > 15:
        status = "good"
    elif growth < -15:
        status = "critical"
    elif growth < -5:
        status = "warning"
    elif growth >= 5:
        status = "good"
    else:
        status = "neutral"

    score = max(1.0, min(10.0, 5.0 + growth / 5.0))
    return {
        "score": {"value": score, "status": status},
        "total_revenue": _format_revenue(total_revenue),
        "revenue_growth": growth,
        "top_segment": top_segment,
        "top_segment_growth": top_segment_growth if top_segment_growth is not None else 0.0,
    }


def _build_forecast_vital(
    revenue_cagr: Optional[float], eps_cagr: Optional[float]
) -> Dict[str, Any]:
    rev = revenue_cagr if revenue_cagr is not None else 0.0
    eps = eps_cagr if eps_cagr is not None else 0.0
    if rev >= 15:
        status, outlook = "good", "Accelerating Growth"
    elif rev < 0:
        status, outlook = "warning", "Decelerating"
    else:
        status, outlook = "neutral", "Steady Growth"
    return {
        "score": {"value": 7.0, "status": status},
        "revenue_cagr": rev,
        "eps_cagr": eps,
        "guidance": "maintained",  # AI overrides with real management_guidance
        "outlook": outlook,
    }


def _build_wall_street_sections(
    analyst: Optional[AnalystAnalysisResponse],
    holders: Optional[HoldersResponse],
    current_price: float,
    fair_value: Optional[float],
    monthly_prices: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Real wall-street sections from AnalystService + HoldersService.

    Returns (wall_street_vital, wall_street_consensus_partial). The
    consensus partial is missing only `hedge_fund_note` (AI-written).
    """
    # ── Defaults if AnalystService is missing ─────────────────────────
    if analyst is None:
        consensus_rating = "hold"
        target_price = 0.0
        low_target = 0.0
        high_target = 0.0
        upgrades = 0
        downgrades = 0
    else:
        consensus_rating = _consensus_to_key(analyst.consensus)
        target_price = float(analyst.target_price or 0.0)
        low_target = float(analyst.price_target.low_price or 0.0)
        high_target = float(analyst.price_target.high_price or 0.0)
        upgrades = int(analyst.actions_summary.upgrades or 0)
        downgrades = int(analyst.actions_summary.downgrades or 0)

    # ── Vital status ──────────────────────────────────────────────────
    target_or_fair = target_price if target_price > 0 else (fair_value or 0.0)
    ws_upside = (
        round(((target_or_fair - current_price) / current_price) * 100, 1)
        if current_price > 0 else 0.0
    )
    if ws_upside > 20:
        ws_status = "good"
    elif consensus_rating == "strong_sell":
        ws_status = "critical"
    elif consensus_rating == "sell":
        ws_status = "warning"
    else:
        ws_status = "neutral"

    wall_street_vital = {
        "score": {"value": 7.0, "status": ws_status},
        "consensus_rating": consensus_rating,
        "price_target": round(target_price if target_price > 0 else (fair_value or 0.0), 2),
        "current_price": round(current_price, 2),
        "upgrades": upgrades,
        "downgrades": downgrades,
    }

    # ── Hedge fund flow data: real institutional from HoldersService ─
    hf_price_data = [
        {"month": p["month"], "price": p["price"]}
        for p in monthly_prices
    ]
    # Pin the last (most recent) point to live `current_price` so the chart
    # line terminates exactly where the iOS `$<currentPrice>` badge sits —
    # otherwise the line ends at the prior month's close while the badge is
    # at currentPrice.y, leaving a visible gap. Preserves 12-point parity
    # with `hf_flow_data`.
    if hf_price_data and current_price > 0:
        hf_price_data[-1] = {
            "month": hf_price_data[-1]["month"],
            "price": round(current_price, 2),
        }
    hf_flow_data = _hedge_fund_flow_from_holders(holders, monthly_prices)

    # ── Valuation status uses DCF upside (model-implied), distinct ───
    # from analyst-implied target upside.
    if fair_value is None:
        val_status = "fair_value"
        discount_pct = 0.0
    else:
        if current_price <= 0:
            val_status = "fair_value"
            discount_pct = 0.0
        else:
            upside_pct = ((fair_value - current_price) / current_price) * 100
            if upside_pct >= 30:
                val_status = "deep_undervalued"
            elif upside_pct >= 10:
                val_status = "underpriced"
            elif upside_pct >= -10:
                val_status = "fair_value"
            else:
                val_status = "overpriced"
            discount_pct = round(
                ((fair_value - current_price) / max(fair_value, 1e-6)) * 100, 1
            )

    consensus_partial = {
        "rating": consensus_rating,
        "current_price": round(current_price, 2),
        "target_price": round(target_price if target_price > 0 else (fair_value or current_price), 2),
        "low_target": round(low_target if low_target > 0 else current_price * 0.85, 2),
        "high_target": round(high_target if high_target > 0 else (fair_value or current_price) * 1.3, 2),
        "valuation_status": val_status,
        "discount_percent": max(0.0, discount_pct),
        "hedge_fund_note": None,  # filled by AI in assemble_report
        "hedge_fund_price_data": hf_price_data,
        "hedge_fund_flow_data": hf_flow_data,
        "momentum_upgrades": upgrades,
        "momentum_downgrades": downgrades,
    }

    return wall_street_vital, consensus_partial


def _consensus_to_key(c: Optional[AnalystConsensus]) -> str:
    if c is None:
        return "hold"
    mapping = {
        AnalystConsensus.STRONG_BUY: "strong_buy",
        AnalystConsensus.BUY: "buy",
        AnalystConsensus.HOLD: "hold",
        AnalystConsensus.SELL: "sell",
        AnalystConsensus.STRONG_SELL: "strong_sell",
    }
    return mapping.get(c, "hold")


def _hedge_fund_flow_from_holders(
    holders: Optional[HoldersResponse],
    monthly_prices: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map HoldersService hedge_funds_data quarterly flow → 12 monthly points.

    HoldersService returns up to 8 quarters with `buy_volume` /
    `sell_volume` in millions. We align them to the 12 monthly chart
    slots by spreading each quarter's volume across its 3 months
    proportional to that month's price position (so a quarter labeled
    Q1'25 contributes to Jan/Feb/Mar 2025 entries in the chart).

    When no real data is available, returns zero-volume points (NOT
    hash-derived synthetic noise) — the iOS chart renders empty bars,
    which honestly conveys missing data instead of a fake pattern.
    """
    if not monthly_prices:
        return []

    # Default: empty volume (honest placeholder when HoldersService missing)
    out: List[Dict[str, Any]] = [
        {"month": p["month"], "buy_volume": 0.0, "sell_volume": 0.0}
        for p in monthly_prices
    ]

    if holders is None or not holders.hedge_funds_data \
            or not holders.hedge_funds_data.flow_data:
        return out

    # Build (year, quarter) → (buy, sell) lookup from holders flow data.
    qtr_flow: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for fp in holders.hedge_funds_data.flow_data:
        # HoldersService formats the label as "Q3\n'25"
        label = fp.month.strip()
        try:
            q_part, y_part = label.split("\n")
            quarter = int(q_part.replace("Q", "").strip())
            year = 2000 + int(y_part.replace("'", "").strip())
        except (ValueError, IndexError):
            continue
        qtr_flow[(year, quarter)] = (float(fp.buy_volume or 0.0), float(fp.sell_volume or 0.0))

    # Spread each quarter evenly across its 3 months in the chart.
    for i, p in enumerate(monthly_prices):
        try:
            mm, yyyy = p["month"].split("/")
            month_num = int(mm)
            year_num = int(yyyy)
        except (ValueError, KeyError):
            continue
        quarter = (month_num - 1) // 3 + 1
        flow = qtr_flow.get((year_num, quarter))
        if not flow:
            continue
        buy_v, sell_v = flow
        # Even split across the 3 months in the quarter so the bar
        # height per month makes sense visually.
        out[i]["buy_volume"] = round(buy_v / 3.0, 2)
        out[i]["sell_volume"] = round(sell_v / 3.0, 2)

    return out


def _build_revenue_segments(
    segments_raw: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Real product-segment revenue from FMP.

    Returns a list of `{name, current_revenue, previous_revenue, total_revenue}`
    dicts — currency in dollars (not pre-divided). The revenue_engine
    section then converts to the appropriate display unit.
    """
    if not segments_raw:
        return []

    def _segments_for_record(rec: Dict[str, Any]) -> Dict[str, float]:
        nested = rec.get("data")
        if isinstance(nested, dict):
            seg = {
                k: v for k, v in nested.items()
                if k not in _SEGMENT_META_KEYS
            }
        else:
            seg = {
                k: v for k, v in rec.items()
                if k not in _SEGMENT_META_KEYS
            }
        cleaned: Dict[str, float] = {}
        for k, v in seg.items():
            try:
                amount = float(v)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            # Skip values that look like calendar years (FMP sometimes
            # leaks fiscalYear into the data dict).
            if 1900 <= amount <= 2100:
                continue
            cleaned[k] = amount
        return cleaned

    # FMP returns newest-first; latest = [0], prior = [1].
    latest = _segments_for_record(segments_raw[0])
    prior = (
        _segments_for_record(segments_raw[1])
        if len(segments_raw) >= 2 else {}
    )

    if not latest:
        return []

    total = sum(latest.values())
    if total <= 0:
        return []

    # Sort largest first.
    items = sorted(latest.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {
            "name": name,
            "current_revenue": amount,
            "previous_revenue": float(prior.get(name, 0.0)),
            "total_revenue": total,
        }
        for name, amount in items
    ]


def _format_earnings_yield(ey: Optional[float]) -> str:
    """Render earnings yield for a DeepDiveMetric.value cell."""
    if ey is None:
        return "N/A"
    return f"{ey:.2f}%"


def _snapshot_to_card(
    title: str,
    snap: Optional[SnapshotItemResponse],
    extra_metrics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Map a SnapshotItemResponse onto a FundamentalMetricCardResponse dict.

    Honest fallback when the snapshot is missing: star_rating=0, empty
    metrics, quality_label="Data unavailable" — Pydantic still validates
    and the iOS card simply shows the unavailable state.
    """
    if snap is None:
        metrics = list(extra_metrics or [])
        return {
            "title": title,
            "star_rating": 0,
            "metrics": metrics,
            "quality_label": "Data unavailable",
        }

    metrics = [
        {"label": m.name, "value": m.value, "trend": None}
        for m in snap.metrics
    ]
    if extra_metrics:
        metrics.extend(extra_metrics)
    return {
        "title": title,
        "star_rating": int(snap.rating or 0),
        "metrics": metrics,
        "quality_label": "",  # Stage B narrative writes this
    }


def _build_fundamental_metrics_from_snapshots(
    profitability: Optional[SnapshotItemResponse],
    growth: Optional[SnapshotItemResponse],
    valuation: Optional[SnapshotItemResponse],
    health: Optional[SnapshotItemResponse],
) -> List[Dict[str, Any]]:
    """Build the 4 fundamental cards from the same snapshot services
    TickerDetailView's Financials tab uses, so the values match exactly.

    Order matches the existing iOS card order: Profitability, Growth,
    Valuation, Health. Earnings Yield is part of the Valuation snapshot
    itself (with sector context), so no `extra_metrics` is needed.
    """
    return [
        _snapshot_to_card("Profitability", profitability),
        _snapshot_to_card("Growth", growth),
        _snapshot_to_card("Valuation", valuation),
        _snapshot_to_card("Health", health),
    ]


def _overall_assessment_from_cards(
    cards: List[Dict[str, Any]], ai_text: Optional[str],
) -> Dict[str, Any]:
    """Recompute the four numeric fields from the deterministic cards.

    text comes from AI/Stage B narrative when present, otherwise an
    honest sentinel. The numerics are always recomputed so they can't
    contradict the per-card star ratings.
    """
    ratings = [int(c.get("star_rating") or 0) for c in cards]
    valid = [r for r in ratings if r > 0]
    avg = round(sum(valid) / len(valid), 1) if valid else 0.0
    strong = sum(1 for r in ratings if r >= 4)
    weak = sum(1 for r in ratings if 0 < r <= 2)
    return {
        "text": ai_text or "Data unavailable for this ticker.",
        "average_rating": avg,
        "strong_count": strong,
        "weak_count": weak,
    }


def _segments_from_breakdown(
    breakdown: Optional[RevenueBreakdownResponse],
    segments_raw: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Use RevenueBreakdownService output for cross-view parity.

    Returns [] when the service either hit no data or fell back to its
    single "Total Revenue" placeholder — caller then falls back to the
    direct FMP segmentation path.

    `segments_raw` is the per-period FMP product-segmentation list that
    the collector already fetches alongside the breakdown. We use index
    [1] (the prior fiscal year) to back-fill `previous_revenue` for each
    segment — without this, every segment's YoY shows +0% because the
    cached breakdown is single-period.
    """
    if breakdown is None or not breakdown.revenue_sources:
        return []

    sources = [
        s for s in breakdown.revenue_sources
        if s.name and s.name != "Total Revenue" and s.value > 0
    ]
    if not sources:
        return []

    total = sum(s.value for s in sources)
    if total <= 0:
        return []

    # Prior-period lookup from FMP segmentation (newest-first; [1] = prior).
    # Segment names from the breakdown service match FMP keys verbatim,
    # so a direct dict lookup is the right join key. Missing prior keys
    # (e.g. a segment introduced this year) yield previous_revenue=0,
    # which renders as "YoY n/a" rather than a misleading +∞ growth.
    prior_lookup: Dict[str, float] = {}
    if segments_raw and len(segments_raw) >= 2:
        prior_rec = segments_raw[1]
        nested = prior_rec.get("data")
        if isinstance(nested, dict):
            prior_seg_dict = nested
        else:
            prior_seg_dict = prior_rec
        for k, v in prior_seg_dict.items():
            if k in _SEGMENT_META_KEYS:
                continue
            try:
                amount = float(v)
            except (TypeError, ValueError):
                continue
            if amount <= 0 or (1900 <= amount <= 2100):
                continue
            prior_lookup[k] = amount

    # Sort largest first, mirror the FMP-direct path's contract.
    sources.sort(key=lambda s: s.value, reverse=True)
    return [
        {
            "name": s.name,
            "current_revenue": float(s.value),
            "previous_revenue": float(prior_lookup.get(s.name, 0.0)),
            "total_revenue": float(total),
        }
        for s in sources
    ]


def _segment_growth_pct(
    segments: List[Dict[str, Any]],
) -> Optional[float]:
    if not segments:
        return None
    s = segments[0]
    prev = s.get("previous_revenue") or 0.0
    curr = s.get("current_revenue") or 0.0
    return _safe_pct_change(curr, prev)


def _build_revenue_engine(
    segments: List[Dict[str, Any]], now: datetime,
) -> Dict[str, Any]:
    """Emit segments in MILLIONS — iOS decides how to render (M / B / T).

    Older versions of this function pre-divided by the chosen display unit
    (1e9 for big-cap, 1e12 for mega-cap), which silently broke the iOS
    formatter — it assumes millions and infers the user-facing tier from
    magnitude. Always emitting in millions keeps the API contract single-
    unit; iOS handles the display branch. `revenue_unit` is still surfaced
    so the AI insight prompt can phrase magnitudes correctly without doing
    its own arithmetic.
    """
    if not segments:
        return {
            "segments": [],
            "total_revenue": 0.0,
            "revenue_unit": "Millions",
            "period": f"FY {now.year}",
            "analysis_note": None,  # filled by AI
        }

    total = sum(s["current_revenue"] for s in segments)
    if total >= 1e12:
        unit = "Trillions"
    elif total >= 1e9:
        unit = "Billions"
    else:
        unit = "Millions"

    divisor = 1e6  # always emit in millions
    scaled = []
    for s in segments:
        scaled.append({
            "name": s["name"],
            "current_revenue": round(s["current_revenue"] / divisor, 2),
            "previous_revenue": round(s["previous_revenue"] / divisor, 2),
            "total_revenue": round(total / divisor, 2),
        })

    return {
        "segments": scaled,
        "total_revenue": round(total / divisor, 2),
        "revenue_unit": unit,
        "period": f"FY {now.year}",
        "analysis_note": None,  # AI fills
    }


def _build_revenue_forecast_partial(
    estimates: List[Dict[str, Any]],
    revenue_cagr: Optional[float],
    eps_cagr: Optional[float],
) -> Dict[str, Any]:
    # Pick 4 chart-visible projections (current FY + 3 forward) plus the
    # off-screen anchor (FY before "current") for the leftmost bar's YoY
    # chip. Falls back to the last 4 entries when FMP doesn't return
    # enough forward data — see `_select_visible_forecast_window`.
    sorted_estimates, anchor = _select_visible_forecast_window(estimates)

    # Pick a single divisor across all bars so they're visually comparable.
    revs = [_est_revenue(est) for est in sorted_estimates]
    max_rev = max(revs) if revs else 0.0
    if max_rev >= 1e12:
        divisor = 1e12
    elif max_rev >= 1e9:
        divisor = 1e9
    else:
        divisor = 1e6

    def _yoy(curr: float, prior: float) -> Optional[float]:
        """YoY % change. None when prior is non-positive so we don't emit
        a misleading +∞ for a segment that started from zero."""
        if prior is None or prior <= 0:
            return None
        return round((curr - prior) / prior * 100, 1)

    projections: List[Dict[str, Any]] = []
    for i, est in enumerate(sorted_estimates):
        date_str = est.get("date") or ""
        period = date_str[:4] if len(date_str) >= 4 else f"FY{i}"
        rev = _est_revenue(est)
        eps = _est_eps(est)
        # Prior year is the previous visible projection, or the anchor
        # for the first visible year. Anchor may be None in tests with
        # only 3 estimates — first year's YoY then comes back as null.
        if i == 0:
            prior_rev = _est_revenue(anchor) if anchor else None
            prior_eps = _est_eps(anchor) if anchor else None
        else:
            prior_est = sorted_estimates[i - 1]
            prior_rev = _est_revenue(prior_est)
            prior_eps = _est_eps(prior_est)
        projections.append({
            "period": period,
            "revenue": round(rev / divisor, 2) if rev else 0.0,
            "revenue_label": _format_revenue(rev),
            "revenue_yoy_pct": _yoy(rev, prior_rev) if rev else None,
            "eps": round(eps, 2) if eps else 0.0,
            "eps_label": f"${eps:.2f}" if eps else "$0",
            "eps_yoy_pct": _yoy(eps, prior_eps) if eps else None,
            # FMP `analyst-estimates` is forward-looking only — every entry
            # is a future-period analyst estimate, never an actual.
            "is_forecast": True,
        })
    return {
        "cagr": revenue_cagr if revenue_cagr is not None else 0.0,
        "eps_growth": eps_cagr if eps_cagr is not None else 0.0,
        "management_guidance": "maintained",  # AI overrides via Stage A
        "projections": projections,
        "guidance_quote": None,         # AI fills via Stage A (PR 6)
        "guidance_speaker": None,       # AI fills via Stage A (PR 6)
        "guidance_period": None,        # AI fills via Stage A (PR 6)
    }


def _build_insider_sections(
    insider_trades: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Aggregate the last 90 days of real insider trades.

    Sentiment is derived from net dollar value (buys − sells), not raw
    counts — a single $50M sell from the CEO outweighs three $100K
    buys from junior officers. When no trades are available, returns
    honest zeros + neutral status.

    Returns (insider_data_partial, insider_vital_partial). The partial
    insider_data only lacks `ownership_note`, and the partial vital
    only lacks `key_insight` — both filled in `assemble_report`.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    def _is_in_window(t: Dict[str, Any]) -> bool:
        date_str = (t.get("transactionDate") or t.get("filingDate") or "")[:10]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            return False
        return dt >= cutoff

    recent = [t for t in insider_trades if _is_in_window(t)]

    # Match HoldersService Smart Money classification: keep only common-stock
    # rows + only Informative trades (open-market P-Purchase / pure S-Sale).
    # Drops RSU vesting, option exercises, tax withholding, gifts — i.e.
    # compensation mechanics that don't carry sentiment signal. Without
    # this, the report's buy/sell counts disagree with the Holders tab.
    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []
    for t in recent:
        sec = (t.get("securityName") or "").lower()
        if sec and "common stock" not in sec:
            continue
        classification = classify_insider_transaction(
            t.get("transactionType") or ""
        )
        if not is_informative(classification):
            continue
        if classification == "Informative Buy":
            buys.append(t)
        else:  # "Informative Sell"
            sells.append(t)

    def _aggregate(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
        shares = 0.0
        value = 0.0
        for r in rows:
            sh = abs(_safe_float(r, "securitiesTransacted"))
            pr = _safe_float(r, "price")
            shares += sh
            value += sh * pr
        return shares, value

    buy_shares, buy_value = _aggregate(buys)
    sell_shares, sell_value = _aggregate(sells)

    # Sentiment from net dollar value (threshold: $100K to ignore noise).
    net = buy_value - sell_value
    if net > 1e5:
        sentiment, status, net_label = "positive", "good", "Net Buying"
    elif net < -1e5:
        sentiment, status, net_label = "negative", "critical", "Net Selling"
    else:
        sentiment, status, net_label = "neutral", "neutral", "Balanced"

    transactions = [
        {
            "type": "Buys",
            "count": len(buys),
            "shares": _format_shares_short(buy_shares),
            "value": _format_currency_short(buy_value),
        },
        {
            "type": "Sells",
            "count": len(sells),
            "shares": _format_shares_short(sell_shares),
            "value": _format_currency_short(sell_value),
        },
    ]

    insider_data_partial = {
        "sentiment": sentiment,
        "timeframe": "Last 90 Days",
        "transactions": transactions,
        "ownership_note": None,  # AI fills
    }

    # Vital score: 1 (heavy selling) → 10 (heavy buying), centered at 5.
    if (buy_value + sell_value) > 0:
        ratio = (buy_value - sell_value) / (buy_value + sell_value)
        score = max(1.0, min(10.0, 5.0 + ratio * 5.0))
    else:
        score = 5.0

    insider_vital_partial = {
        "score": {"value": score, "status": status},
        "sentiment": sentiment,
        "net_activity": net_label,
        "buy_count": len(buys),
        "sell_count": len(sells),
        "key_insight": None,  # AI fills
    }

    return insider_data_partial, insider_vital_partial


_OFFICER_PREFIX_RE = re.compile(r"\s*officer:\s*", flags=re.IGNORECASE)


def _clean_role_title(title: Optional[str]) -> str:
    """Strip the FMP `officer:` tag from a roster title so it reads
    cleanly in the UI ("officer: Chief Executive Officer" →
    "Chief Executive Officer"). Other tags (`director,`,
    `10 percent owner,`) are preserved.
    """
    if not title:
        return "Officer"
    cleaned = _OFFICER_PREFIX_RE.sub(" ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(",").strip()
    return cleaned or "Officer"


def _role_rank(cleaned_title: str, raw_type: str) -> int:
    """Numeric priority for ordering the Officers sub-section
    (lower = higher in the list). Pulls CEO/CFO/COO/President to the
    top regardless of share count, then other C-level, then other
    officers, then directors-only.
    """
    title_l = (cleaned_title or "").lower()
    type_l = (raw_type or "").lower()

    if "chief executive" in title_l or title_l == "ceo":
        return 1
    if "chief financial" in title_l or title_l == "cfo":
        return 2
    if "chief operating" in title_l or title_l == "coo":
        return 3
    if "president" in title_l:
        return 4
    if "chair" in title_l:
        return 5
    if "chief" in title_l:
        return 6
    # Directors above rank-and-file officers — governance role (hire/fire
    # CEO, approve strategy) is material even when they have no officer tag.
    if "director" in type_l:
        return 7
    if "officer" in type_l:
        return 10
    return 99


def _build_key_management(
    insider_roster: List[Dict[str, Any]],
    profile: Dict[str, Any],
    current_price: float = 0.0,
    beneficial_owners: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Split key management into two sub-sections so the UI can render
    them under separate sub-headers:

    - **top_holders**: roster entries tagged `"10 percent owner"` AND
      paired with an IN-type 13G filing. iOS shows these first with
      the green "N% owner" chip — these are the people who *control*
      the company.
    - **officers**: everyone else, ordered by canonical role rank
      (CEO → CFO → COO → President → Chair → other C-level → other
      officer → director), then shares desc as tiebreaker. These are
      the people who *run* the company day-to-day.

    A person who qualifies as a Top Holder is dedup-removed from the
    Officers list (CIK-keyed). Caps: top_holders=3, officers=5.

    Names are normalized via `_insider_common.normalize_insider_name`.
    Titles are passed through `_clean_role_title` to drop the FMP
    `officer:` tag prefix.

    Empty-roster fallback: a single placeholder row goes into
    `officers` so iOS never sees both sub-sections empty (the view
    assumes ≥1 row across the whole table).

    Founders/major holders whose Form 4 `securitiesOwned` understates
    true beneficial ownership (Ellison @ ORCL: 571K direct vs 1.157B
    beneficial) get their shares replaced with the 13G `soleVotingPower`
    and `percent_ownership` attached for the chip.
    """
    # Queue of individual (IN-type) 13G filers, largest first. We pop
    # from this in roster order to upgrade Form 4 holdings for 10%+
    # owners. CO-type filers (companies, e.g. legacy Oracle acquisition
    # disclosures) are ignored — they're not individual insiders.
    #
    # FMP returns the full filing history for each CIK (Ellison has 6
    # filings spanning ~2010-2022 with different share counts as he
    # gifted/transferred). Dedupe by CIK and keep only the MOST RECENT
    # filing per filer — older filings are historical and shouldn't be
    # treated as competing rows.
    latest_by_cik: Dict[str, Dict[str, Any]] = {}
    for f in beneficial_owners or []:
        if (f.get("typeOfReportingPerson") or "").upper() != "IN":
            continue
        sole = _safe_float(f, "soleVotingPower")
        if sole <= 0:
            continue
        cik = f.get("cik") or ""
        if not cik:
            continue
        filing_date = f.get("filingDate") or f.get("acceptedDate") or ""
        prev = latest_by_cik.get(cik)
        if prev is None or filing_date > (prev.get("date") or ""):
            latest_by_cik[cik] = {
                "shares": sole,
                "pct": _safe_float(f, "percentOfClass"),
                "date": filing_date,
            }
    ind_queue = sorted(
        latest_by_cik.values(), key=lambda x: x["shares"], reverse=True,
    )

    top_holders: List[Dict[str, Any]] = []
    officers: List[Dict[str, Any]] = []
    seen_top_ciks: Set[str] = set()

    if insider_roster:
        # Walk the roster in shares-desc order so the 13G queue is
        # consumed deterministically (largest filer paired with largest
        # 10%-tagged insider). After top-holder extraction the officers
        # are re-sorted by role rank below.
        ranked = sorted(
            insider_roster,
            key=lambda r: _safe_float(r, "numberOfShares"),
            reverse=True,
        )
        for r in ranked:
            raw_type = (
                (r.get("title") or "") + " " + (r.get("typeOfOwner") or "")
            ).lower()
            is_major = (
                "10 percent owner" in raw_type
                or "10% owner" in raw_type
            )
            shares = _safe_float(r, "numberOfShares")
            pct: Optional[float] = None
            in_top = False
            if is_major and ind_queue:
                override = ind_queue.pop(0)
                shares = override["shares"]
                pct = override["pct"] or None
                in_top = True

            if shares > 0 and current_price > 0:
                value_str = _format_currency_short(shares * current_price)
            else:
                value_str = "—"

            cleaned_title = _clean_role_title(
                r.get("title") or r.get("typeOfOwner")
            )
            row = {
                "name": normalize_insider_name(r.get("owner")),
                "title": cleaned_title,
                "ownership": _format_shares_short(shares),
                "ownership_value": value_str,
                "percent_ownership": round(pct, 1) if pct else None,
            }

            cik = r.get("cik") or ""
            if in_top:
                top_holders.append(row)
                if cik:
                    seen_top_ciks.add(cik)
            else:
                if cik and cik in seen_top_ciks:
                    continue
                row["_rank"] = _role_rank(
                    cleaned_title, r.get("typeOfOwner") or ""
                )
                row["_shares"] = shares
                officers.append(row)

    top_holders.sort(
        key=lambda r: r.get("percent_ownership") or 0, reverse=True,
    )
    officers.sort(key=lambda r: (r["_rank"], -r["_shares"]))

    top_holders = top_holders[:3]
    officers = officers[:5]
    for o in officers:
        o.pop("_rank", None)
        o.pop("_shares", None)

    if not top_holders and not officers:
        ceo = profile.get("ceo")
        officers.append({
            "name": normalize_insider_name(ceo) if ceo else "Data unavailable",
            "title": "CEO" if ceo else "Officer",
            "ownership": "—",
            "ownership_value": "—",
            "percent_ownership": None,
        })

    return {
        "top_holders": top_holders,
        "officers": officers,
        "ownership_insight": None,  # AI fills
    }


def _price_change_at_index(
    recent_prices: List[float], idx: int,
) -> float:
    """Percent change at the given chart index using the same
    `(after - before) / before * 100` formula the earnings path uses
    so the two candidate types are scored on equal terms.

    Returns 0.0 when `before` is missing/zero so we don't accidentally
    score a divide-by-zero as a giant move.
    """
    if not recent_prices or idx < 0 or idx >= len(recent_prices):
        return 0.0
    before = recent_prices[max(0, idx - 1)]
    after = recent_prices[min(len(recent_prices) - 1, idx + 1)]
    if not before:
        return 0.0
    return (after - before) / before * 100


def _index_for_date(
    target: date, today: date, recent_prices: List[float],
) -> int:
    """Map a calendar date to a chart-array index.

    The chart's right edge is "today", so days_ago=0 → last index, and
    older dates step backwards. Clamps to the array bounds rather than
    raising for off-by-one robustness.
    """
    days_ago = (today - target).days
    return max(0, min(len(recent_prices) - 1, len(recent_prices) - days_ago - 1))


def _detect_news_catalysts(
    news: List[Dict[str, Any]],
    recent_prices: List[float],
    today: date,
    window_start: date,
) -> List[Dict[str, Any]]:
    """Scan FMP news within the chart window and return scored candidates.

    Each candidate carries `tag/date/index/abs_move_pct/title/site/url`
    so the price-action builder can compare against an earnings event
    and also so the narrative prompt can cite real headlines.

    Pass-through items missing a parseable date are skipped (rather than
    snapped to "today") because a misdated catalyst would corrupt the
    priority comparison with the earnings event.
    """
    if not news or not recent_prices:
        return []
    candidates: List[Dict[str, Any]] = []
    for item in news:
        title = item.get("title") or ""
        text = item.get("text") or ""
        tag = _classify_news_catalyst(title, text)
        if tag is None:
            continue
        published = item.get("publishedDate") or item.get("date") or ""
        try:
            d = datetime.strptime(published[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if not (window_start <= d <= today):
            continue
        idx = _index_for_date(d, today, recent_prices)
        move = _price_change_at_index(recent_prices, idx)
        candidates.append({
            "tag": tag,
            "date": d,
            "index": idx,
            "abs_move_pct": abs(move),
            "title": title,
            "site": item.get("site") or "",
            "url": item.get("url") or "",
        })
    return candidates


_EVAL_WINDOWS: Tuple[int, ...] = (7, 15, 30, 45)
_BASELINE_DAYS: int = 180
_DEFAULT_WINDOW: int = 30


def _daily_returns(prices: List[float]) -> List[float]:
    """Daily simple returns from a price array (oldest→newest).
    Skips pairs where the prior close is zero or missing.
    """
    out: List[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        curr = prices[i]
        if prev and prev > 0 and curr is not None:
            out.append((curr - prev) / prev)
    return out


def _std_dev_pop(values: List[float]) -> Optional[float]:
    """Population standard deviation. None if <2 values."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


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
        return "Typical"
    if z >= 3:
        return "Extreme"
    if z >= 2:
        return "Unusual"
    if z >= 1:
        return "Notable"
    return "Typical"


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
        "tier": "Typical",
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
    if sigma_daily is None or sigma_daily <= 0:
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


def _build_price_action(
    recent_prices: List[float],
    current_price: float,
    earnings_dates: List[str],
    news: Optional[List[Dict[str, Any]]] = None,
    recent_price_dates: Optional[List[date]] = None,
) -> Dict[str, Any]:
    """Volatility-aware price-movement section with dynamic window selection.

    Pipeline:
      1. Compute σ_daily over the 180-day baseline, then per-window z-scores
         for the evaluation windows (7, 15, 30, 45 calendar days). Windows
         are calendar-day when `recent_price_dates` is provided (production)
         and trading-day otherwise (legacy test path).
      2. If a catalyst event (earnings or news headline) fires within the
         last 45 days, the chart anchors to the event date and the tier is
         computed for the exact since-event span. Otherwise, the chart
         shows the argmax|z| window (or 30 days when nothing is unusual).
      3. Trim the sparkline to the chosen window's length so iOS renders
         the right span without needing the full baseline.

    Also emits `_news_headlines` (top 20 most-recent matched headlines)
    so the Stage B narrative prompt can name a real catalyst instead of
    speculating. The underscore prefix flags it as Pydantic-ignored.
    """
    if not recent_prices:
        return _empty_price_action(current_price)

    today = datetime.now(timezone.utc).date()

    # ── Volatility & dynamic window selection ─────────────────────────
    vol = _compute_price_volatility(recent_prices, recent_price_dates)
    sigma_daily = vol["sigma_daily"]
    chosen_window = vol["chosen_window"]
    chosen_ref_idx = vol["chosen_ref_idx"]

    # ── Scan for catalysts within the larger of (45 days, chosen window)
    # so we don't miss an old-but-significant event.
    max_scan = max(45, chosen_window)
    scan_start = today - timedelta(days=max_scan)

    earnings_candidate: Optional[Dict[str, Any]] = None
    if earnings_dates:
        for ed in earnings_dates:
            try:
                d = datetime.strptime(ed[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
            if not (scan_start <= d <= today):
                continue
            idx = _index_for_date(d, today, recent_prices)
            change = _price_change_at_index(recent_prices, idx)
            if change > 3:
                tag = "Earnings Beat"
            elif change < -3:
                tag = "Earnings Miss"
            else:
                tag = "Earnings Reaction"
            earnings_candidate = {
                "tag": tag,
                "date": d,
                "index": idx,
                "abs_move_pct": abs(change),
                "_source": "earnings",
            }
            break

    news_candidates = _detect_news_catalysts(
        news or [], recent_prices, today, scan_start,
    )

    # Priority: largest absolute move wins. Ties → earnings (higher
    # confidence). Catalyst-only ties → most recent date.
    best_news: Optional[Dict[str, Any]] = None
    if news_candidates:
        news_candidates.sort(
            key=lambda c: (c["abs_move_pct"], c["date"]),
            reverse=True,
        )
        best_news = news_candidates[0]

    chosen_event: Optional[Dict[str, Any]] = None
    if earnings_candidate and best_news:
        chosen_event = (
            best_news
            if best_news["abs_move_pct"] > earnings_candidate["abs_move_pct"]
            else earnings_candidate
        )
    elif earnings_candidate:
        chosen_event = earnings_candidate
    elif best_news:
        chosen_event = best_news

    # ── Window choice: event date overrides volatility window ─────────
    if chosen_event:
        event_date = chosen_event["date"]
        event_days_ago = max(1, (today - event_date).days)
        # Pad the chart with a tiny lead-in so the user can see context
        # before the event marker.
        window_days = max(event_days_ago, 7)
    else:
        window_days = chosen_window

    # ── Ground truth: change_pct + reference price ────────────────────
    if chosen_event and 0 <= chosen_event.get("index", -1) < len(recent_prices):
        ref_price = recent_prices[chosen_event["index"]]
        change_days = max(1, (today - chosen_event["date"]).days)
    elif chosen_ref_idx is not None:
        # Pin to the exact close the volatility function selected. In
        # calendar-day mode this is the close on/before today−N days; in
        # trading-day mode it's the Nth from end. Either way, change_pct
        # and the σ band agree because they both anchor here.
        ref_price = recent_prices[chosen_ref_idx]
        change_days = max(1, len(recent_prices) - 1 - chosen_ref_idx)
    else:
        ref_idx = max(0, len(recent_prices) - (window_days + 1))
        ref_price = recent_prices[ref_idx]
        change_days = window_days

    change_pct = 0.0
    if ref_price:
        change_pct = (current_price - ref_price) / ref_price * 100
    change_pct = round(change_pct, 1)

    if abs(change_pct) < 1.0:
        direction = "flat"
    elif change_pct > 0:
        direction = "up"
    else:
        direction = "down"

    # ── Tier: based on the actual span the user sees ──────────────────
    z_score = _z_score_for_window(change_pct, sigma_daily, change_days)
    tier = _tier_for_z(z_score)

    # ── Tag + window label ────────────────────────────────────────────
    if chosen_event:
        date_label = chosen_event["date"].strftime("%b ") + str(chosen_event["date"].day)
        window_label = f"Since {date_label}"
        tag = chosen_event["tag"]  # event tag wins over tier tag
    else:
        window_label = f"Last {window_days} Days"
        tag = tier  # Typical / Notable / Unusual / Extreme

    # ── Trim sparkline to the chosen window so iOS doesn't ship 200
    # closes when the chart only renders ~30. For the no-event path with
    # calendar-day windows we anchor to chosen_ref_idx so the chart shows
    # exactly the closes that fell inside the labelled window.
    if not chosen_event and chosen_ref_idx is not None:
        sparkline = recent_prices[chosen_ref_idx:]
        offset = chosen_ref_idx
    else:
        sparkline_len = window_days + 1
        if len(recent_prices) >= sparkline_len:
            sparkline = recent_prices[-sparkline_len:]
            offset = len(recent_prices) - sparkline_len
        else:
            sparkline = list(recent_prices)
            offset = 0

    # Re-index the event marker against the trimmed sparkline.
    event_out: Optional[Dict[str, Any]] = None
    if chosen_event:
        new_idx = chosen_event["index"] - offset
        if 0 <= new_idx < len(sparkline):
            event_out = {
                "tag": chosen_event["tag"],
                "date": chosen_event["date"].strftime("%b ") + str(chosen_event["date"].day),
                "index": new_idx,
            }

    # ── Headlines for the AI narrative prompt — 20 most-recent matches
    headline_evidence = sorted(
        news_candidates, key=lambda c: c["date"], reverse=True,
    )[:20]
    evidence_payload = [
        {
            "tag": c["tag"],
            "date": c["date"].isoformat(),
            "title": c["title"],
            "site": c["site"],
        }
        for c in headline_evidence
    ]

    # ── Sigma context exposed to iOS for the sub-label ────────────────
    sigma_daily_pct = round(sigma_daily * 100, 2) if sigma_daily else None
    expected_band_pct = None
    if sigma_daily:
        expected_band_pct = round(sigma_daily * (change_days ** 0.5) * 100 * 2, 2)
    z_out = round(z_score, 2) if z_score is not None else None

    return {
        "prices": sparkline,
        "current_price": round(current_price, 2),
        "event": event_out,
        "narrative": None,  # AI fills
        "change_pct": change_pct,
        "direction": direction,
        "window_label": window_label,
        "tag": tag,
        "tier": tier,
        "z_score": z_out,
        "sigma_daily_pct": sigma_daily_pct,
        "expected_band_pct": expected_band_pct,
        "_news_headlines": evidence_payload,  # Pydantic-ignored
    }


def _empty_price_action(current_price: float) -> Dict[str, Any]:
    """Honest empty state for tickers without enough price history."""
    return {
        "prices": [],
        "current_price": round(current_price, 2) if current_price else 0.0,
        "event": None,
        "narrative": None,
        "change_pct": 0.0,
        "direction": "flat",
        "window_label": f"Last {_DEFAULT_WINDOW} Days",
        "tag": "Typical",
        "tier": "Typical",
        "z_score": None,
        "sigma_daily_pct": None,
        "expected_band_pct": None,
        "_news_headlines": [],
    }


def _default_market_dynamics(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Honest empty state when neither cached sector aggregates nor
    in-hand peer profiles are available.

    `cagr_5yr` and `tam_*` are None so iOS renders "—" placeholders
    rather than misleading zeros. Concentration defaults to `fragmented`
    (rather than the more pessimistic `mature` cycle phase) because
    absence-of-data should not look like presence-of-bad-data.
    """
    now_year = datetime.now(timezone.utc).year
    return {
        "industry": profile.get("industry") or "Unknown",
        "concentration": "fragmented",
        "cagr_5yr": None,
        "current_tam": 0.0,
        "future_tam": 0.0,
        "current_year": str(now_year),
        "future_year": str(now_year + 5),
        "lifecycle_phase": "mature",
        "tam_source_quote": None,
        "tam_source_label": None,
        "source_grain": None,
    }


def _aggregates_from_peers(
    focal_profile: Dict[str, Any],
    peer_profiles: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Derive concentration metrics from in-hand peer market caps.

    Used as the fallback when the `sector_aggregates` cache table is
    empty for this sector (batch hasn't run, or the row expired). The
    focal ticker is included in the cap list so the top-1/top-2 share
    math reflects competitive dynamics, not just peer-among-peer ranking.

    Skips CAGR — we'd need historical revenue per peer (5+ years × N
    peers worth of FMP calls) which is too expensive to do inline.
    Caller leaves `cagr_5yr=None` when this is the source.

    Returns None when fewer than 3 valid market caps are available —
    HHI on 1-2 points isn't informative.
    """
    from app.services.sector_aggregates_service import compute_hhi

    caps: List[float] = []
    focal_cap = float((focal_profile or {}).get("mktCap") or 0.0)
    if focal_cap > 0:
        caps.append(focal_cap)
    for p in peer_profiles or []:
        c = float(p.get("mktCap") or 0.0)
        if c > 0:
            caps.append(c)

    if len(caps) < 3:
        return None

    total = sum(caps)
    caps_sorted = sorted(caps, reverse=True)
    top1_share = (caps_sorted[0] / total) * 100.0
    top2_share = ((caps_sorted[0] + caps_sorted[1]) / total) * 100.0

    return {
        "hhi": compute_hhi(caps),
        "top1_share_pct": top1_share,
        "top2_share_pct": top2_share,
        "num_constituents": len(caps),
    }


def _apply_tam_source(
    market_dynamics: Dict[str, Any],
    ai_md: Optional[Dict[str, Any]],
    industry_tam: Optional[Any],
) -> None:
    """Apply TAM with priority chain: AI quote → industry proxy → leave 0.

    Mutates `market_dynamics` in place. Sets `tam_source_label` to a
    short caption iOS shows under the TAM row so users know which
    source produced the figure.

    Priority 1 (highest trust): AI-extracted explicit quote from the
    earnings transcript. Requires both a positive number AND a non-empty
    source quote — strict to prevent fabrication.

    Priority 2: industry-level proxy (Census 4-digit NAICS preferred,
    FRED 2-digit sector as fallback — chain resolved upstream in
    `industry_tam_service.get_industry_tam`). Caption is the source's
    own label so the user sees which dataset produced the figure.

    The industry proxy ALSO sets `cagr_5yr` when the sector_aggregates
    batch hasn't produced one — keeps the CAGR cell from rendering as
    "—" when we have a defensible sector growth rate.

    Priority 3: leave 0.0 / null. iOS renders "—" instead of "$0B".
    """
    # Priority 1: AI-extracted transcript quote
    if isinstance(ai_md, dict):
        quote_raw = ai_md.get("tam_source_quote")
        quote = quote_raw.strip() if isinstance(quote_raw, str) else ""
        if quote:
            def _safe_positive(v: Any) -> Optional[float]:
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    return None
                return f if f > 0 else None

            current = _safe_positive(ai_md.get("current_tam"))
            future = _safe_positive(ai_md.get("future_tam"))

            if current is not None or future is not None:
                if current is not None:
                    market_dynamics["current_tam"] = round(current, 0)
                if future is not None:
                    market_dynamics["future_tam"] = round(future, 0)
                market_dynamics["tam_source_quote"] = quote[:200]
                market_dynamics["tam_source_label"] = "Earnings call quote"

                fy = ai_md.get("future_year")
                if isinstance(fy, (int, str)):
                    s = str(fy).strip()
                    if s.isdigit() and len(s) == 4:
                        market_dynamics["future_year"] = s
                return

    # Priority 2: industry-level proxy (Census → FRED chain, or
    # pre-computed industry_dossier row when available).
    if industry_tam is not None:
        market_dynamics["current_tam"] = industry_tam.current_tam
        market_dynamics["future_tam"] = industry_tam.future_tam
        market_dynamics["current_year"] = industry_tam.current_year
        market_dynamics["future_year"] = industry_tam.future_year
        market_dynamics["tam_source_label"] = industry_tam.source_label

        # `source_grain` is set when the proxy is an IndustryDossier
        # (i.e., pre-computed weekly batch). iOS reads it to render the
        # "⚠ Broader than industry" chip when fallback was used.
        grain = getattr(industry_tam, "source_grain", None)
        if grain:
            market_dynamics["source_grain"] = grain

        # Industry-wide concentration (HHI from ALL constituents in this
        # industry, computed weekly) is more authoritative than the focal
        # ticker's peer-set HHI computed live. Override when present.
        dossier_concentration = getattr(industry_tam, "concentration_label", None)
        if dossier_concentration:
            market_dynamics["concentration"] = dossier_concentration

        # Dossier-derived lifecycle wins outright when present (already
        # incorporates CAGR + constituent count). Otherwise fall back to
        # the legacy CAGR-based promotion below.
        dossier_lifecycle = getattr(industry_tam, "lifecycle_phase", None)
        if dossier_lifecycle and dossier_lifecycle != "mature":
            # `mature` is the dataclass default — only override the
            # current lifecycle when the dossier produced a non-default
            # classification (the dossier saw a real signal).
            market_dynamics["lifecycle_phase"] = dossier_lifecycle

        # Surface the industry's realized CAGR only when sector_aggregates
        # didn't already produce one — preserves higher-trust source.
        if market_dynamics.get("cagr_5yr") is None:
            cagr = getattr(industry_tam, "cagr_5y_pct", None)
            if cagr is not None:
                market_dynamics["cagr_5yr"] = cagr
                # Legacy promotion path — only fires when the proxy
                # didn't already publish a lifecycle (plain IndustryTAM,
                # not a dossier).
                if not dossier_lifecycle and market_dynamics.get("lifecycle_phase") == "mature":
                    if cagr > 15.0:
                        market_dynamics["lifecycle_phase"] = "secular_growth"
                    elif cagr < 0.0:
                        market_dynamics["lifecycle_phase"] = "declining"
        return

    # Priority 3: leave at 0.0 / null (iOS hides)


def _apply_peer_score_baseline(
    dims: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Ensure every dimension has a visible peer_score baseline.

    AI Stage A frequently leaves `peer_score` at the 0.0 default from
    the JSON template. A 0.0 collapses the gray "Peer Avg" polygon to
    the radar chart center, making it invisible. Replace any `<= 0`
    value with 5.0 (midpoint of the 0-10 scale) so the polygon is
    always anchored at a sensible reference — AI is free to write
    higher / lower values when it actually has signal.
    """
    for d in dims:
        peer = d.get("peer_score")
        try:
            if peer is None or float(peer) <= 0:
                d["peer_score"] = 5.0
        except (TypeError, ValueError):
            d["peer_score"] = 5.0
    return dims


# ── Macro risk-factor derivation (PR 4) ──────────────────────────────


def _classify_macro_severity(
    abs_change: float, mild: float, elevated: float, severe: float,
) -> Tuple[str, float]:
    """Return (severity_enum, impact_0_to_1) for a |%| change.

    Banded thresholds keep the iOS color coding stable: a 0.1% bump
    won't flip a card from "low" to "high". Impact is a 0-1 score the
    iOS view uses to set bar fill width — saturating at the top band
    so a one-week oil shock still maxes out the indicator.
    """
    a = abs(abs_change)
    if a >= severe:
        return "severe", 1.0
    if a >= elevated:
        return "high", min(1.0, 0.5 + (a - elevated) / max(severe - elevated, 1e-6) * 0.5)
    if a >= mild:
        return "elevated", 0.4
    return "low", 0.2


def _macro_trend(change_pct: Optional[float]) -> str:
    """Map a signed 1M change into the iOS trend enum.

    Positive change → 'worsening' for risk-shaped indicators (oil,
    gold rallying, VIX spiking). Caller may invert when the indicator
    semantics are reversed (e.g. copper: a decline is the worry).
    """
    if change_pct is None:
        return "stable"
    if change_pct > 1.0:
        return "worsening"
    if change_pct < -1.0:
        return "improving"
    return "stable"


def _build_macro_risk_factors_from_indicators(
    indicators: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Translate the macro indicator snapshot into MacroRiskFactor entries.

    Per-indicator thresholds are calibrated to the typical month-over-
    month behavior of each series — an 8% oil move is normal noise,
    a 25% move is a regime shift. Indicators with unavailable 1M data
    are skipped (no fabricated risks).

    Returns a list of dicts shaped for `MacroRiskFactorResponse`. The
    `assemble_report` step merges these with AI-generated qualitative
    factors (geopolitical events, regulation), with deterministic
    factors winning when the categories collide.
    """
    if not indicators:
        return []

    by_sym = {row["symbol"]: row for row in indicators}
    out: List[Dict[str, Any]] = []

    def _add(*, symbol: str, factor: Dict[str, Any]) -> None:
        row = by_sym.get(symbol)
        if not row or row.get("change_1m_pct") is None:
            return
        out.append(factor)

    # ── WTI Crude oil ───────────────────────────────────────────────
    oil = by_sym.get("CLUSD")
    if oil and oil.get("change_1m_pct") is not None:
        change = float(oil["change_1m_pct"])
        sev, impact = _classify_macro_severity(
            change, mild=5.0, elevated=15.0, severe=30.0,
        )
        out.append({
            "category": "energy",
            "title": "Oil Price Pressure",
            "impact": round(impact, 2),
            "trend": _macro_trend(change),
            "severity": sev if change > 0 else ("low" if sev == "low" else "elevated"),
            "description": (
                f"WTI crude is {'+' if change >= 0 else ''}{change:.1f}% over the "
                f"last month. {'Energy costs feed CPI and weigh on margin.' if change > 5 else 'Energy backdrop neutral.'}"
            ),
        })

    # ── Gold (flight-to-safety / real-rate / dollar-weakness proxy) ──
    gold = by_sym.get("GCUSD")
    if gold and gold.get("change_1m_pct") is not None:
        change = float(gold["change_1m_pct"])
        if abs(change) >= 3.0:  # below this, gold is just noise
            sev, impact = _classify_macro_severity(
                change, mild=3.0, elevated=8.0, severe=15.0,
            )
            out.append({
                "category": "currency",
                "title": "Gold / Safe-Haven Flow",
                "impact": round(impact, 2),
                "trend": "worsening" if change > 0 else "improving",
                "severity": sev,
                "description": (
                    f"Gold is {'+' if change >= 0 else ''}{change:.1f}% MoM — "
                    f"{'classic flight-to-safety signal.' if change > 3 else 'risk appetite improving.'}"
                ),
            })

    # ── Copper (global industrial demand) ───────────────────────────
    copper = by_sym.get("HGUSD")
    if copper and copper.get("change_1m_pct") is not None:
        change = float(copper["change_1m_pct"])
        if change <= -5.0:
            sev, impact = _classify_macro_severity(
                change, mild=5.0, elevated=10.0, severe=20.0,
            )
            out.append({
                "category": "supply_chain",
                "title": "Industrial Demand Weakness",
                "impact": round(impact, 2),
                "trend": "worsening",
                "severity": sev,
                "description": (
                    f"Copper {change:.1f}% MoM — Dr. Copper signaling slowing "
                    f"industrial activity."
                ),
            })

    # ── VIX (volatility regime) ──────────────────────────────────────
    vix = by_sym.get("^VIX")
    if vix and vix.get("change_1m_pct") is not None:
        change = float(vix["change_1m_pct"])
        # VIX cares about absolute level too, but stock-price-change
        # gives us only deltas. A large positive 1M move is the signal.
        if change >= 15.0:
            sev, impact = _classify_macro_severity(
                change, mild=15.0, elevated=30.0, severe=60.0,
            )
            out.append({
                "category": "regulation",  # closest enum slot for "market regime"
                "title": "Volatility Spike",
                "impact": round(impact, 2),
                "trend": "worsening",
                "severity": sev,
                "description": (
                    f"VIX up {change:.1f}% MoM — equity volatility regime "
                    f"shifting risk-off."
                ),
            })

    # ── 10Y Treasury (rates / discount rate) ─────────────────────────
    tnx = by_sym.get("^TNX")
    if tnx and tnx.get("change_1m_pct") is not None:
        change = float(tnx["change_1m_pct"])
        if abs(change) >= 5.0:
            sev, impact = _classify_macro_severity(
                change, mild=5.0, elevated=12.0, severe=25.0,
            )
            out.append({
                "category": "interest_rates",
                "title": "Yield Curve Move",
                "impact": round(impact, 2),
                "trend": "worsening" if change > 0 else "improving",
                "severity": sev,
                "description": (
                    f"10Y Treasury yield {'+' if change >= 0 else ''}{change:.1f}% MoM. "
                    f"{'Higher rates pressure equity multiples.' if change > 0 else 'Lower rates support multiples.'}"
                ),
            })

    # ── USD index (multinational FX translation risk) ────────────────
    dxy = by_sym.get("DXY")
    if dxy and dxy.get("change_1m_pct") is not None:
        change = float(dxy["change_1m_pct"])
        if abs(change) >= 2.0:
            sev, impact = _classify_macro_severity(
                change, mild=2.0, elevated=4.0, severe=8.0,
            )
            out.append({
                "category": "currency",
                "title": "USD Strength" if change > 0 else "USD Weakness",
                "impact": round(impact, 2),
                "trend": "worsening" if change > 0 else "improving",
                "severity": sev,
                "description": (
                    f"DXY {'+' if change >= 0 else ''}{change:.1f}% MoM — "
                    f"{'foreign revenue translation drag.' if change > 0 else 'tailwind for international revenue.'}"
                ),
            })

    return out


def _build_macro_risk_factors_from_fred(
    fred: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Translate FRED snapshots into MacroRiskFactor entries.

    Each snapshot becomes at most one factor — silenced entirely when
    the latest value isn't actionable (e.g. CPI YoY in the 0-2% normal
    range). Embeds the actual number in `description` so iOS users see
    the source figure ("CPI: 4.2% YoY").

    Calibration:
      * CPI YoY > 4% → high (cost-of-capital / margin compression)
      * CPI YoY 2.5-4% → elevated
      * Fed Funds 6-month rise >= 100 bps → elevated (tightening cycle)
      * 10Y-2Y spread < 0 → severe (recession signal historically reliable)
      * 10Y-2Y 0-0.5% → elevated (warning band)
      * 10Y yield > 5% → elevated (discount-rate pressure)
    """
    if not fred:
        return []
    by_id: Dict[str, Dict[str, Any]] = {row["series_id"]: row for row in fred}
    out: List[Dict[str, Any]] = []

    # ── CPI (CPIAUCSL) ──────────────────────────────────────────────
    cpi = by_id.get("CPIAUCSL")
    if cpi and cpi.get("yoy_pct") is not None:
        yoy = float(cpi["yoy_pct"])
        if yoy >= 4.0:
            sev, impact, label = "high", min(1.0, 0.5 + (yoy - 4.0) / 4.0), "Elevated Inflation"
        elif yoy >= 2.5:
            sev, impact, label = "elevated", 0.5, "Above-Target Inflation"
        else:
            sev, impact, label = None, None, None
        if sev is not None:
            out.append({
                "category": "inflation",
                "title": label,
                "impact": round(impact, 2),
                "trend": "worsening" if yoy >= 4 else "stable",
                "severity": sev,
                "description": (
                    f"CPI is +{yoy:.1f}% YoY (as of {cpi.get('as_of', 'recent')}) — "
                    f"{'pressure on margins and consumer spend.' if yoy >= 4 else 'still above the Fed 2% target.'}"
                ),
            })

    # ── Fed Funds (FEDFUNDS): rate of change matters ────────────────
    ff = by_id.get("FEDFUNDS")
    if ff and ff.get("change_6mo_pct") is not None:
        delta = float(ff["change_6mo_pct"])
        latest = float(ff.get("latest") or 0.0)
        if abs(delta) >= 1.0:
            sev = "elevated" if abs(delta) < 2.0 else "high"
            out.append({
                "category": "interest_rates",
                "title": "Fed Funds Tightening" if delta > 0 else "Fed Funds Easing",
                "impact": min(1.0, 0.4 + abs(delta) / 4.0),
                "trend": "worsening" if delta > 0 else "improving",
                "severity": sev,
                "description": (
                    f"Effective Fed Funds Rate is {latest:.2f}% — "
                    f"{'+' if delta >= 0 else ''}{delta:.2f}pp over the last 6 months."
                ),
            })

    # ── 10Y-2Y Treasury spread: inversion = recession signal ────────
    spread = by_id.get("T10Y2Y")
    if spread and spread.get("latest") is not None:
        s = float(spread["latest"])
        if s < 0:
            out.append({
                "category": "interest_rates",
                "title": "Inverted Yield Curve",
                "impact": min(1.0, 0.7 + abs(s) / 2.0),
                "trend": "worsening",
                "severity": "severe",
                "description": (
                    f"10Y-2Y spread at {s:+.2f}% — historically a leading "
                    f"recession indicator."
                ),
            })
        elif s < 0.5:
            out.append({
                "category": "interest_rates",
                "title": "Flat Yield Curve",
                "impact": 0.5,
                "trend": "worsening",
                "severity": "elevated",
                "description": (
                    f"10Y-2Y spread at {s:+.2f}% — flattening curve signals "
                    f"slowdown risk."
                ),
            })

    # ── 10Y Treasury level (DGS10): only emit when very high ────────
    tnx = by_id.get("DGS10")
    if tnx and tnx.get("latest") is not None:
        level = float(tnx["latest"])
        if level >= 5.0:
            out.append({
                "category": "interest_rates",
                "title": "High Long-Term Rates",
                "impact": min(1.0, 0.4 + (level - 5.0) / 2.0),
                "trend": "stable",
                "severity": "elevated",
                "description": (
                    f"10Y Treasury yield at {level:.2f}% — discount-rate "
                    f"pressure on equity multiples."
                ),
            })

    return out


def _merge_macro_risk_factors(
    deterministic: List[Dict[str, Any]],
    ai_factors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Combine the two risk-factor lists; deterministic wins on overlap.

    Deterministic factors come first (tradeable / numeric). AI-generated
    factors are appended only when they don't duplicate a category we
    already covered with real numbers — this prevents the AI from
    overwriting a sourced "Oil Price Pressure" entry with a vaguer
    "energy market dynamics" one. Caps total at 6 (iOS shows up to 6).
    """
    out: List[Dict[str, Any]] = list(deterministic)
    seen_categories = {f.get("category") for f in deterministic}
    for f in ai_factors or []:
        if not isinstance(f, dict):
            continue
        cat = f.get("category")
        if cat in seen_categories:
            continue
        out.append(f)
        seen_categories.add(cat)
        if len(out) >= 6:
            break
    return out[:6]


_VALID_GUIDANCE_STATUSES = ("raised", "maintained", "lowered")
_VALID_GUIDANCE_SPEAKERS = ("CFO", "CEO", "IR")


def _overlay_ai_guidance(
    revenue_forecast: Dict[str, Any], ai_rf: Optional[Dict[str, Any]],
) -> None:
    """Mutate `revenue_forecast` in place with the AI-extracted guidance fields.

    Anti-fabrication rules (mirror the TAM overlay design):
      1. Status defaults to "maintained" — the safe, low-information
         answer. We only escalate to "raised"/"lowered" when AI provided
         BOTH a non-default status AND a non-empty source quote.
      2. `guidance_quote` is required for non-default status. Without
         it, the entire attribution payload is rejected — speaker /
         period drop to null and status falls back to "maintained".
      3. Quote is truncated at 280 chars (transcript sentences can
         get long; iOS bubble caps at this width).
      4. `guidance_speaker` is normalized to one of the iOS-supported
         labels ("CFO" / "CEO" / "IR"). Anything else → null.
      5. `guidance_period` is taken at face value (any short string)
         and capped at 30 chars.
    """
    if not isinstance(ai_rf, dict):
        # No AI output → keep collector defaults (maintained / nulls).
        return

    raw_status = ai_rf.get("management_guidance")
    status = (
        raw_status.lower().strip()
        if isinstance(raw_status, str) else "maintained"
    )
    if status not in _VALID_GUIDANCE_STATUSES:
        status = "maintained"

    raw_quote = ai_rf.get("guidance_quote")
    quote: Optional[str] = None
    if isinstance(raw_quote, str):
        cleaned = raw_quote.strip()
        if cleaned and cleaned.lower() not in ("null", "none", "n/a"):
            quote = cleaned[:280]

    # Anti-fabrication: a non-default status without a quote is a hallucination.
    if status in ("raised", "lowered") and not quote:
        status = "maintained"
        quote = None

    speaker_raw = ai_rf.get("guidance_speaker")
    speaker: Optional[str] = None
    if isinstance(speaker_raw, str):
        s = speaker_raw.strip().upper()
        if s in _VALID_GUIDANCE_SPEAKERS:
            speaker = s

    period_raw = ai_rf.get("guidance_period")
    period: Optional[str] = None
    if isinstance(period_raw, str):
        p = period_raw.strip()
        if p and p.lower() not in ("null", "none", "n/a"):
            period = p[:30]

    # Speaker / period only meaningful when we actually have a quote.
    if quote is None:
        speaker = None
        period = None

    revenue_forecast["management_guidance"] = status
    revenue_forecast["guidance_quote"] = quote
    revenue_forecast["guidance_speaker"] = speaker
    revenue_forecast["guidance_period"] = period


def _classify_concentration(
    top1_share_pct: float, top2_share_pct: float, hhi: float,
) -> str:
    """Map structural concentration metrics to the iOS enum value.

    Prioritizes top-N share thresholds (which capture market shape —
    one dominant firm vs. duopoly — better than HHI alone). HHI is the
    tiebreaker for sectors where no firm dominates but the upper few
    still account for most of the value.
    """
    if top1_share_pct > 50.0:
        return "monopoly"
    if top2_share_pct > 70.0:
        return "duopoly"
    if hhi >= 1500.0:
        return "oligopoly"
    return "fragmented"


def _classify_lifecycle(
    cagr_5yr: Optional[float], num_constituents: int,
) -> str:
    """Map a sector CAGR + constituent count to the iOS lifecycle enum.

    `emerging` wins on low constituent count (a brand-new niche with
    only a few public players is "emerging" regardless of growth rate).
    Past that, CAGR drives: > 15% → `secular_growth`, < 0% → `declining`,
    otherwise `mature`. CAGR=None falls through to `mature` since we
    have no growth signal to differentiate.
    """
    if 0 < num_constituents < 5:
        return "emerging"
    if cagr_5yr is None:
        return "mature"
    if cagr_5yr > 15.0:
        return "secular_growth"
    if cagr_5yr < 0.0:
        return "declining"
    return "mature"


def _build_market_dynamics(
    profile: Dict[str, Any],
    sector_agg: Optional[SectorAggregates],
    peer_profiles: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Real `MarketDynamicsResponse` payload, with cascading data sources.

    Priority chain for concentration / lifecycle:
      1. Cached `SectorAggregates` row (full data including CAGR).
      2. Inline `_aggregates_from_peers` (concentration only, no CAGR).
      3. `_default_market_dynamics` (all unknown).

    Lifecycle enum (Swift `LifecyclePhase`):
      - `secular_growth` — sector 5Y CAGR > 15% (only when CAGR known)
      - `declining`      — CAGR < 0
      - `emerging`       — fewer than 5 sector constituents
      - `mature`         — default

    TAM stays 0.0 here — `_apply_tam_source` overlays the AI-extracted
    quote OR the FRED industry proxy in `assemble_report`.
    """
    now_year = datetime.now(timezone.utc).year
    industry = profile.get("industry") or "Unknown"

    # Priority 1: cached sector aggregates (has real CAGR)
    if sector_agg is not None:
        concentration = _classify_concentration(
            sector_agg.top1_share_pct,
            sector_agg.top2_share_pct,
            sector_agg.hhi,
        )
        cagr = sector_agg.cagr_5yr_pct
        return {
            "industry": industry,
            "concentration": concentration,
            "cagr_5yr": round(cagr, 1),
            "current_tam": 0.0,
            "future_tam": 0.0,
            "current_year": str(now_year),
            "future_year": str(now_year + 5),
            "lifecycle_phase": _classify_lifecycle(
                cagr, sector_agg.num_constituents,
            ),
            "tam_source_quote": None,
            "tam_source_label": None,
            "source_grain": None,
        }

    # Priority 2: derive concentration from in-hand peer market caps
    peer_agg = _aggregates_from_peers(profile, peer_profiles or [])
    if peer_agg is not None:
        concentration = _classify_concentration(
            peer_agg["top1_share_pct"],
            peer_agg["top2_share_pct"],
            peer_agg["hhi"],
        )
        # CAGR is None at this point (peer profiles alone don't carry
        # historical revenue). Lifecycle may still get promoted from
        # "mature" once `_apply_tam_source` overlays a Census/FRED CAGR.
        return {
            "industry": industry,
            "concentration": concentration,
            "cagr_5yr": None,
            "current_tam": 0.0,
            "future_tam": 0.0,
            "current_year": str(now_year),
            "future_year": str(now_year + 5),
            "lifecycle_phase": _classify_lifecycle(
                None, peer_agg["num_constituents"],
            ),
            "tam_source_quote": None,
            "tam_source_label": None,
            "source_grain": None,
        }

    # Priority 3: honest empty state
    return _default_market_dynamics(profile)


def _peer_score(
    op_margin: Optional[float],
    roe: Optional[float],
    revenue_growth: Optional[float],
) -> Optional[float]:
    """Composite signal for a peer's competitive strength.

    Combines profitability (operating margin), capital efficiency (ROE),
    and momentum (revenue growth). Each component contributes equally
    after percentile-clamping so a single outlier doesn't dominate.

    Returns None when all three inputs are missing — the peer can't
    be ranked and is shown without a score downstream.
    """
    parts: List[float] = []
    if op_margin is not None:
        parts.append(max(-50.0, min(50.0, op_margin)))
    if roe is not None:
        parts.append(max(-50.0, min(50.0, roe)))
    if revenue_growth is not None:
        parts.append(max(-50.0, min(50.0, revenue_growth)))
    if not parts:
        return None
    return sum(parts) / len(parts)


def _normalize_to_0_10(values: List[float]) -> List[float]:
    """Map an arbitrary numeric list onto a 0-10 scale by min-max scaling.

    Used to project the peer-score distribution onto the iOS moat-score
    range (0-10). When all peers tie or only one peer exists, every
    score is 5.0 (the neutral midpoint) — neither the chart nor the
    threat-level math benefits from spurious differentiation.
    """
    if not values:
        return []
    if len(values) == 1:
        return [5.0]
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [5.0] * len(values)
    return [round(((v - lo) / (hi - lo)) * 10.0, 1) for v in values]


_COMPETITOR_MKT_CAP_FLOOR_RATIO = 0.05    # 5% of focal mkt cap
_COMPETITOR_MKT_CAP_FLOOR_ABS = 5_000_000_000.0   # $5B hard floor
# Variable count: take EVERY peer that survives the mkt-cap floor, up to
# this ceiling. Industries with many same-tier rivals (mega-cap software
# like ORCL → MSFT/PLTR/PANW/CRWD/SNPS/FTNT/NET) naturally show 6-7;
# niche or small-cap tickers with fewer comparable peers show 3-5;
# outliers (no peer survives the floor) show 0 with the iOS empty state.
# The floor is the quality gate — this cap just bounds the UI list.
_COMPETITOR_MAX_N = 7


# ── Industry-universe peer fallback ────────────────────────────────────
#
# FMP's `/stock-peers` endpoint is unreliable for mega-caps — it can
# return micro-cap misclassifications (e.g. "Helport AI" for ORCL) or
# nothing at all. When the FMP-supplied peer list is sparse, augment
# with same-FMP-industry constituents from
# `backend/data/industry_universe.json` (the file already maintained by
# `scripts/discover_industries.py` and consumed by
# `industry_dossier_service`). Augmented peers still flow through the
# existing `_build_competitors()` floor + scoring, so the "don't
# fabricate" guarantee is preserved.

_INDUSTRY_UNIVERSE_PATH = (
    Path(__file__).resolve().parents[3]  # backend/
    / "data" / "industry_universe.json"
)
_INDUSTRY_PEERS_CACHE: Dict[str, List[str]] = {}


def _load_industry_peers_from_universe(industry: str) -> List[str]:
    """One-off file read + market-cap sort. Returns every ticker in
    `industry` sorted by market cap descending. Called once per
    industry per process (cached by `_industry_universe_peers`).
    """
    try:
        data = json.loads(_INDUSTRY_UNIVERSE_PATH.read_text())
    except Exception as exc:
        logger.warning(
            "industry_universe peers: failed to read %s: %s",
            _INDUSTRY_UNIVERSE_PATH, exc,
        )
        return []
    for entry in data.get("industries", []) or []:
        if entry.get("industry") == industry:
            mcaps = entry.get("market_caps") or {}
            return [
                t for t, _ in sorted(
                    mcaps.items(),
                    key=lambda x: x[1] or 0,
                    reverse=True,
                )
            ]
    return []


def _industry_universe_peers(industry: str, exclude: Set[str]) -> List[str]:
    """Tickers in `industry` sorted by market cap desc, excluding any
    in `exclude`. Returns up to 20 candidates; caller trims further.
    Cached in-process — universe file is static between deploys.
    """
    if not industry:
        return []
    sorted_tickers = _INDUSTRY_PEERS_CACHE.get(industry)
    if sorted_tickers is None:
        sorted_tickers = _load_industry_peers_from_universe(industry)
        _INDUSTRY_PEERS_CACHE[industry] = sorted_tickers
    return [t for t in sorted_tickers if t not in exclude][:20]


def _build_competitors(
    my_ticker: str,
    my_profile: Dict[str, Any],
    my_ratios: List[Dict[str, Any]],
    my_revenue_growth: Optional[float],
    peer_profiles: List[Dict[str, Any]],
    peer_ratios: Optional[Dict[str, Dict[str, Any]]] = None,
    my_key_metrics: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Real competitor list from FMP peer profiles, top-N by market cap.

    FMP's `stock-peers` endpoint is noisy for mega-caps — it can include
    obvious misclassifications (Helport AI for ORCL, etc.). To keep the
    UI signal-heavy:
      1. Drop any peer whose mktCap is below max(focal × 5%, $5B).
      2. Sort survivors by mktCap desc and cap at `_COMPETITOR_MAX_N`.
      3. Drop peers we cannot score (score_raw is None) — avoids the
         "0.0 by definition" card that happens when a peer's TTM ratios
         are empty in FMP and min-max scaling pins them to the floor.
      4. Compute `market_share_percent` from peer TTM revenue
         (peer revenue / peer-set revenue), falling back to mkt-cap
         share when FMP's key-metrics-ttm coverage is patchy. Revenue is
         a more honest "Market Share" proxy than market cap; mkt-cap
         share over-rewards investor enthusiasm and under-counts steady
         revenue earners.
      5. Drop sub-1% peers and recompute share so the visible cards
         still sum to ~100% — a peer that is <1% of the peer set isn't
         a meaningful comparator at this scale.

    Per-peer scoring uses real financial ratios from the peers' own
    `/stable/ratios-ttm` responses (passed in via `peer_ratios`, keyed
    by ticker).

    Returns [] when no peer survives the floor or the rankable-data
    drop.
    """
    if not peer_profiles:
        return []
    peer_ratios = peer_ratios or {}

    focal_mkt_cap = float((my_profile or {}).get("mktCap") or 0.0)
    floor = max(
        focal_mkt_cap * _COMPETITOR_MKT_CAP_FLOOR_RATIO,
        _COMPETITOR_MKT_CAP_FLOOR_ABS,
    )

    # ── 1. Filter + cap to top N by mktCap ────────────────────────────
    survivors: List[Dict[str, Any]] = []
    for p in peer_profiles:
        sym = (p.get("symbol") or "").upper()
        if not sym or sym == my_ticker.upper():
            continue
        mkt_cap = float(p.get("mktCap") or 0.0)
        if mkt_cap < floor:
            continue
        survivors.append({"profile": p, "symbol": sym, "mkt_cap": mkt_cap})

    if not survivors:
        logger.info(
            f"_build_competitors({my_ticker}): no peers passed mkt-cap "
            f"floor ${floor / 1e9:.1f}B (had {len(peer_profiles)} candidates)"
        )
        return []

    survivors.sort(key=lambda s: s["mkt_cap"], reverse=True)
    survivors = survivors[:_COMPETITOR_MAX_N]

    total_peer_cap = sum(s["mkt_cap"] for s in survivors)
    if total_peer_cap <= 0:
        # Defensive: should never hit because the floor is > 0, but logs
        # surface a field-name regression in FMP profile responses.
        logger.warning(
            f"_build_competitors({my_ticker}): total_peer_cap is 0 after "
            f"filtering — peer profiles may be missing mktCap field"
        )
        return []

    # ── 2. Score the focal so we can compute threat-level deltas ──────
    my_op_margin = None
    my_roe = None
    if my_ratios:
        r0 = my_ratios[0]
        omp = r0.get("operatingProfitMargin")
        if omp is not None:
            my_op_margin = float(omp) * 100  # ratios endpoint uses 0-1
        roe = r0.get("returnOnEquity")
        if roe is not None:
            my_roe = float(roe) * 100
    # FMP stopped emitting returnOnEquity on /ratios in late 2025;
    # /key-metrics still carries it. Fall through so the focal isn't
    # under-scored vs peers (who already get ROE via /key-metrics-ttm).
    if my_roe is None and my_key_metrics:
        km0 = my_key_metrics[0]
        km_roe = km0.get("returnOnEquity")
        if km_roe is not None:
            my_roe = float(km_roe) * 100
    my_raw_score = _peer_score(
        my_op_margin, my_roe,
        (my_revenue_growth * 100) if my_revenue_growth is not None else None,
    )

    # ── 3. Build peer_data: per-peer raw score + TTM revenue ─────────
    peer_data: List[Dict[str, Any]] = []
    for s in survivors:
        sym = s["symbol"]
        p = s["profile"]
        ratios_row = peer_ratios.get(sym, {}) or {}

        op_margin = None
        roe_val = None
        rev_growth = None
        omp = ratios_row.get("operatingProfitMargin")
        if omp is not None:
            op_margin = float(omp) * 100
        roe_v = ratios_row.get("returnOnEquity")
        if roe_v is not None:
            roe_val = float(roe_v) * 100
        rg = ratios_row.get("revenueGrowth")
        if rg is not None:
            rev_growth = float(rg) * 100

        rev_ttm: Optional[float] = None
        rev_raw = ratios_row.get("revenue_ttm")
        if rev_raw is not None:
            try:
                v = float(rev_raw)
                if v > 0:
                    rev_ttm = v
            except (TypeError, ValueError):
                pass

        peer_data.append({
            "name": p.get("companyName") or sym,
            "ticker": sym,
            "mkt_cap": s["mkt_cap"],
            "revenue_ttm": rev_ttm,
            "score_raw": _peer_score(op_margin, roe_val, rev_growth),
        })

    # ── 4. Drop peers we cannot score ────────────────────────────────
    # A peer with no rankable signal becomes the min-max floor and gets
    # shipped to iOS as "0.0" — visually punishing a real company for
    # an FMP coverage gap. Better to render fewer confident rows.
    peer_data = [p for p in peer_data if p["score_raw"] is not None]
    if not peer_data:
        logger.info(
            f"_build_competitors({my_ticker}): no peers had rankable "
            f"ratio data — returning empty list"
        )
        return []

    # ── 5. Pick the Market Share denominator ──────────────────────────
    # Prefer TTM revenue (honest "market share" proxy). Fall back to
    # mkt-cap share when no peer in the set has revenue data — keeps
    # the metric available during patchy FMP coverage windows.
    total_peer_revenue = sum(
        p["revenue_ttm"] for p in peer_data if p["revenue_ttm"]
    )
    use_revenue = total_peer_revenue > 0
    if use_revenue:
        # Drop peers without revenue_ttm — can't honestly include them
        # in a revenue-share denominator.
        peer_data = [p for p in peer_data if p["revenue_ttm"]]
        total_peer_revenue = sum(p["revenue_ttm"] for p in peer_data)
        for p in peer_data:
            p["_share_pct"] = (p["revenue_ttm"] / total_peer_revenue) * 100
    else:
        for p in peer_data:
            p["_share_pct"] = (p["mkt_cap"] / total_peer_cap) * 100

    # ── 6. Drop sub-1% noise peers and recompute share ────────────────
    peer_data = [p for p in peer_data if p["_share_pct"] >= 1.0]
    if not peer_data:
        logger.info(
            f"_build_competitors({my_ticker}): no peers with share >= 1% "
            f"— returning empty list"
        )
        return []
    if use_revenue:
        total_peer_revenue = sum(p["revenue_ttm"] for p in peer_data)
        for p in peer_data:
            p["_share_pct"] = (p["revenue_ttm"] / total_peer_revenue) * 100
    else:
        total_filtered_cap = sum(p["mkt_cap"] for p in peer_data)
        for p in peer_data:
            p["_share_pct"] = (p["mkt_cap"] / total_filtered_cap) * 100

    # ── 7. Min-max scale on the FINAL peer set ────────────────────────
    # Include the focal so its scaled score drives threat-level deltas.
    raw_scores: List[float] = [p["score_raw"] for p in peer_data]
    raw_scores.append(my_raw_score if my_raw_score is not None else 0.0)
    scaled = _normalize_to_0_10(raw_scores)
    my_scaled = scaled[-1] if scaled else 5.0
    peer_scaled = scaled[:-1]

    # ── 8. Emit final rows with threat-level deltas ──────────────────
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(peer_data):
        moat_score = peer_scaled[i] if i < len(peer_scaled) else 5.0
        share_pct = round(p["_share_pct"], 1)
        delta = moat_score - my_scaled
        if delta >= 1.5:
            threat = "high"
        elif delta <= -1.5:
            threat = "low"
        else:
            threat = "moderate"
        out.append({
            "name": p["name"],
            "ticker": p["ticker"],
            "moat_score": moat_score,
            "market_share_percent": share_pct,
            "threat_level": threat,
        })

    out.sort(key=lambda c: c["market_share_percent"], reverse=True)
    return out


def _derive_moat_vital(moat_dims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive overall_rating, primary_source, and tags from real scores."""
    if not moat_dims:
        return {
            "overall_rating": "none",
            "primary_source": "Data unavailable",
            "tags": [{"label": "Data unavailable", "strength": "none"}],
            "value_label": "Weak",
            "stability_label": "At Risk",
        }
    max_score = max((float(d.get("score") or 0.0) for d in moat_dims), default=0.0)
    if max_score >= 8.5:
        overall_rating = "wide"
    elif max_score >= 7.0:
        overall_rating = "narrow"
    else:
        overall_rating = "none"

    tags = []
    for d in moat_dims:
        s = float(d.get("score") or 0.0)
        if s >= 6.0:
            tags.append({
                "label": d.get("name") or "Moat Source",
                "strength": "wide" if s >= 8.5 else "narrow",
            })
    primary_source = "Unknown"
    for d in moat_dims:
        if float(d.get("score") or 0.0) == max_score:
            primary_source = d.get("name") or primary_source
            break

    return {
        "overall_rating": overall_rating,
        "primary_source": primary_source,
        "tags": tags or [{"label": primary_source, "strength": overall_rating}],
        "value_label": (
            "Durable" if overall_rating == "wide"
            else "Moderate" if overall_rating == "narrow" else "Weak"
        ),
        "stability_label": "Stable" if max_score >= 7.0 else "At Risk",
    }


def _sanitize_risk_factor(rf: Dict[str, Any]) -> Dict[str, Any]:
    """Clamp impact to [0,1] and provide defaults so Pydantic doesn't reject."""
    impact = rf.get("impact")
    try:
        impact = max(0.0, min(1.0, float(impact)))
    except (TypeError, ValueError):
        impact = 0.5
    return {
        "category": rf.get("category") or "regulation",
        "title": rf.get("title") or "Unknown Risk",
        "impact": impact,
        "description": rf.get("description") or "Data unavailable.",
        "trend": rf.get("trend") or "stable",
        "severity": rf.get("severity") or "elevated",
    }


def _derive_macro_vital(
    risk_factors: List[Dict[str, Any]], threat_level: str,
) -> Dict[str, Any]:
    if threat_level in ("severe", "critical"):
        status = "critical"
    elif threat_level in ("high", "elevated"):
        status = "warning"
    else:
        status = "good"

    top_risk = risk_factors[0]["title"] if risk_factors else "No Major Risks"

    if risk_factors:
        trends = [r.get("trend", "stable") for r in risk_factors]
        if trends.count("worsening") > len(trends) / 2:
            dominant_trend = "worsening"
        elif trends.count("improving") > len(trends) / 2:
            dominant_trend = "improving"
        else:
            dominant_trend = "stable"
    else:
        dominant_trend = "stable"

    active = sum(
        1 for r in risk_factors
        if r.get("severity") in ("elevated", "high", "severe", "critical")
    )

    return {
        "score": {"value": 7.0, "status": status},
        "threat_level": threat_level,
        "top_risk": top_risk,
        "risk_trend": dominant_trend,
        "active_risk_count": active,
    }


def _sanitize_thesis(thesis: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pydantic-safe core_thesis. Bull/bear cap to 4 each (Phase 2 also
    enforces this in the prompt; this is the post-write defense)."""
    if not isinstance(thesis, dict):
        return {"bull_case": [], "bear_case": []}
    bull = list(thesis.get("bull_case") or [])[:4]
    bear = list(thesis.get("bear_case") or [])[:4]
    return {"bull_case": bull, "bear_case": bear}


# ── Singleton accessor (matches AnalystService / HoldersService) ─────


_collector: Optional[TickerReportDataCollector] = None


def get_collector() -> TickerReportDataCollector:
    global _collector
    if _collector is None:
        _collector = TickerReportDataCollector()
    return _collector


# ── Shared evidence/context builder for AI prompts ────────────────────


def build_financial_context(out: CollectedTickerData) -> str:
    """Compact, fact-only evidence string for Stage A and Stage B prompts.

    Every line should be a number/string the AI can ground its
    narrative in. Used by both `TickerReportService` (direct path) and
    `ResearchAgent` (deep-research path) so prompts see the same
    grounding regardless of which entry point the user hit.
    """
    c = out.computed
    profile, quote = out.profile, out.quote
    income, ratios, estimates = out.income, out.ratios, out.estimates
    balance, cash_flow = out.balance, out.cash_flow

    parts: List[str] = []

    if profile:
        parts.append(f"Company: {profile.get('companyName', out.ticker)}")
        parts.append(
            f"Sector: {profile.get('sector', 'N/A')} | "
            f"Industry: {profile.get('industry', 'N/A')}"
        )
        parts.append(
            f"Market Cap: ${(profile.get('mktCap', 0) or 0):,.0f}"
        )
        parts.append(f"CEO: {profile.get('ceo', 'N/A')}")
        parts.append(
            f"Employees: {profile.get('fullTimeEmployees', 'N/A')}"
        )
        if profile.get("description"):
            parts.append(f"Description: {profile['description'][:400]}")

    if quote:
        parts.append(f"\nPrice: ${quote.get('price', 0):.2f}")
        parts.append(
            f"52W Range: ${quote.get('yearLow', 0):.2f} - "
            f"${quote.get('yearHigh', 0):.2f}"
        )
        parts.append(f"P/E (quote): {quote.get('pe', 'N/A')}")

    parts.append(f"\nAltman Z-Score: {_fmt_or_na(c.get('altman_z'))}")
    parts.append(
        f"Revenue Growth YoY: {_fmt_pct_or_na(c.get('revenue_growth_yoy'))}"
    )
    ey = c.get("earnings_yield")
    parts.append(
        f"Earnings Yield: {_fmt_pct_or_na(ey) if ey is not None else 'N/A — negative or missing earnings'}"
    )
    parts.append(f"Gross Margin: {_fmt_pct_or_na(c.get('gross_margin'))}")
    parts.append(f"Net Margin: {_fmt_pct_or_na(c.get('net_margin'))}")
    parts.append(f"Operating Margin: {_fmt_pct_or_na(c.get('operating_margin'))}")
    parts.append(f"ROE: {_fmt_pct_or_na(c.get('roe'))}")
    parts.append(f"D/E: {_fmt_or_na(c.get('debt_equity'))}")
    parts.append(f"Current Ratio: {_fmt_or_na(c.get('current_ratio'))}")
    parts.append(f"DCF Fair Value: {_fmt_currency_or_na(c.get('fair_value'))}")
    parts.append(f"DCF Upside: {_fmt_pct_or_na(c.get('upside_pct'))}")
    parts.append(
        f"Revenue CAGR (analyst est.): {_fmt_pct_or_na(c.get('revenue_cagr'))}"
    )
    parts.append(f"EPS CAGR (analyst est.): {_fmt_pct_or_na(c.get('eps_cagr'))}")

    if income:
        for stmt in income[:3]:
            yr = stmt.get("calendarYear", "?")
            parts.append(
                f"\n[{yr}] Revenue: ${stmt.get('revenue', 0):,.0f} | "
                f"Net Income: ${stmt.get('netIncome', 0):,.0f}"
            )

    if balance:
        b = balance[0]
        parts.append(f"\nTotal Assets: ${b.get('totalAssets', 0):,.0f}")
        parts.append(f"Total Debt: ${b.get('totalDebt', 0):,.0f}")
        parts.append(f"Cash: ${b.get('cashAndCashEquivalents', 0):,.0f}")

    if cash_flow:
        cf = cash_flow[0]
        parts.append(f"\nOperating CF: ${cf.get('operatingCashFlow', 0):,.0f}")
        parts.append(f"Free CF: ${cf.get('freeCashFlow', 0):,.0f}")
        parts.append(f"Buybacks: ${cf.get('commonStockRepurchased', 0):,.0f}")

    if ratios:
        r0 = ratios[0]
        pe = r0.get("priceToEarningsRatio") or r0.get("priceEarningsRatio") or "N/A"
        ev = r0.get("enterpriseValueMultiple") or r0.get("enterpriseValueOverEBITDA") or "N/A"
        pfcf = r0.get("priceToFreeCashFlowRatio") or r0.get("priceToFreeCashFlowsRatio") or "N/A"
        parts.append(f"\nP/E: {pe}")
        parts.append(f"EV/EBITDA: {ev}")
        parts.append(f"P/FCF: {pfcf}")

    if estimates:
        parts.append("\nAnalyst Estimates:")
        for est in estimates[:2]:
            parts.append(
                f"  {est.get('date', '?')}: "
                f"Rev ${_est_revenue(est):,.0f}, "
                f"EPS ${_est_eps(est):.2f}"
            )

    if out.analyst_analysis:
        a = out.analyst_analysis
        parts.append(
            f"\nAnalyst Consensus: {a.consensus.value} "
            f"({a.total_analysts} analysts)"
        )
        parts.append(
            f"  Target avg/low/high: ${a.target_price:.2f} / "
            f"${a.price_target.low_price:.2f} / "
            f"${a.price_target.high_price:.2f}"
        )
        parts.append(
            f"  Last 12mo: {a.actions_summary.upgrades} upgrades, "
            f"{a.actions_summary.maintains} maintains, "
            f"{a.actions_summary.downgrades} downgrades"
        )

    if out.insider_data_partial:
        i = out.insider_data_partial
        txns = i.get("transactions", [])
        tx_strs = [
            f"{t['type']} {t['count']} ({t['shares']} sh, {t['value']})"
            for t in txns
        ]
        parts.append(
            f"\nInsider Activity (90d): {i.get('sentiment', 'neutral')} "
            f"— " + ", ".join(tx_strs)
        )

    if out.revenue_engine_partial.get("segments"):
        parts.append(
            f"\nRevenue Segments "
            f"({out.revenue_engine_partial.get('period', '?')}, "
            f"{out.revenue_engine_partial.get('revenue_unit', '?')}):"
        )
        for seg in out.revenue_engine_partial["segments"][:6]:
            parts.append(
                f"  {seg['name']}: {seg['current_revenue']} "
                f"(prior: {seg['previous_revenue']})"
            )

    if out.news:
        parts.append("\nRecent News:")
        for a in out.news[:5]:
            title = a.get("title", "")
            date = (a.get("publishedDate", "") or "")[:10]
            if title:
                parts.append(f"  [{date}] {title[:120]}")

    # Earnings-call transcript excerpt for TAM extraction (PR 3) and
    # guidance extraction (PR 6 — landing later). We send the *first
    # 2K chars* (prepared remarks where executives front-load the
    # company's TAM/market-size story) plus *every paragraph that
    # contains a TAM keyword* up to 3K more chars. That gives the AI
    # the high-signal portion of the call without inflating the prompt
    # with the Q&A section, which is rarely where a clean TAM lives.
    if out.transcript:
        excerpt = _extract_tam_relevant_excerpt(out.transcript)
        if excerpt:
            parts.append("\n\nEARNINGS-CALL TRANSCRIPT EXCERPT (verbatim — use only quoted figures):")
            parts.append(excerpt)

    # Card values block — the EXACT numbers iOS renders for the four
    # Fundamentals & Growth cards. The Insight narrative must cite these
    # rather than the raw FMP-derived values above, because two parallel
    # computation paths (snapshot service vs. data collector) can produce
    # subtly different numbers (e.g. Altman Z manufacturing-formula vs.
    # universal). When the AI sees both, it cherry-picks; pinning it to
    # the card values eliminates the contradiction.
    card_block = _format_snapshot_card_values(out)
    if card_block:
        parts.append(card_block)

    return "\n".join(parts)


def _format_snapshot_card_values(out: "CollectedTickerData") -> str:
    """Render the four Fundamentals & Growth cards as a single ground-truth
    block. Returns '' when no snapshot succeeded so we don't waste tokens
    on an empty header."""
    snaps = [
        ("Profitability", out.snap_profitability),
        ("Growth", out.snap_growth),
        ("Valuation", out.snap_valuation),
        ("Financial Health", out.snap_health),
    ]
    rendered: List[str] = []
    for title, snap in snaps:
        if snap is None or not snap.metrics:
            continue
        rendered.append(f"\n{title} ({int(snap.rating or 0)}/5):")
        for m in snap.metrics:
            rendered.append(f"  {m.name}: {m.value}")
    if not rendered:
        return ""
    header = (
        "\n\n========== CARD VALUES (AS DISPLAYED TO USER) ==========\n"
        "These are the exact values the user sees on the Fundamentals & Growth\n"
        "cards. The Insight narrative MUST cite numbers from this block — never\n"
        "invent a different value for a metric listed here."
    )
    footer = (
        "\n========================================================"
    )
    return header + "".join(rendered) + footer


_TAM_KEYWORDS = (
    "addressable market",
    "total addressable market",
    "tam",
    "sam",  # serviceable addressable market
    "market opportunity",
    "market size",
    "billion-dollar market",
    "billion market",
    "trillion market",
    "industry size",
    "market is estimated",
    "market is projected",
)


def _extract_tam_relevant_excerpt(transcript: str, head_chars: int = 2000) -> str:
    """Build a compact transcript excerpt focused on TAM-relevant content.

    Returns the first ``head_chars`` characters (prepared remarks) plus
    every paragraph that contains a TAM keyword (so the Q&A section's
    market-size signal isn't lost), capped at ~5K chars total. This is
    a heuristic snippet — strict prompt rules in Stage A guard against
    fabrication when no real TAM appears.
    """
    if not transcript:
        return ""
    text = transcript.strip()
    head = text[:head_chars]

    # Pull paragraphs containing TAM keywords from the rest of the call.
    rest = text[head_chars:]
    paragraphs = rest.split("\n")
    extra_chunks: List[str] = []
    extra_budget = 3000
    spent = 0
    lower = lambda s: s.lower()
    for para in paragraphs:
        if spent >= extra_budget:
            break
        ll = lower(para)
        if any(kw in ll for kw in _TAM_KEYWORDS):
            chunk = para.strip()
            if not chunk:
                continue
            extra_chunks.append(chunk)
            spent += len(chunk) + 1

    body = head
    if extra_chunks:
        body += "\n\n[TAM-mention paragraphs from later in the call]\n" + "\n\n".join(extra_chunks)
    return body[:5000]


def _fmt_or_na(v: Any) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _fmt_pct_or_na(v: Any) -> str:
    if v is None:
        return "N/A"
    return f"{float(v):.1f}%"


def _fmt_currency_or_na(v: Any) -> str:
    if v is None:
        return "N/A"
    return f"${float(v):.2f}"
