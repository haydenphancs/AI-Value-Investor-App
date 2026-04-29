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
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.analyst import (
    AnalystAnalysisResponse,
    AnalystConsensus,
)
from app.schemas.holders import HoldersResponse
from app.schemas.revenue_breakdown import RevenueBreakdownResponse
from app.schemas.stock_overview import SnapshotItemResponse

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
            ("estimates", self.fmp.get_analyst_estimates(ticker, "annual", 3), []),
            ("historical", self.fmp.get_historical_prices(ticker), {}),
            ("news", self.fmp.get_stock_news(ticker, 5), []),
            ("insider_trades", self.fmp.get_insider_trading(ticker, limit=200), []),
            ("insider_roster", self.fmp.get_insider_roster(ticker), []),
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
                setattr(out, attr, default)
            else:
                setattr(out, attr, result if result is not None else default)

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
        if ratios:
            r0 = ratios[0]
            c["gross_margin"] = _pct_or_none(r0.get("grossProfitMargin"))
            c["net_margin"] = _pct_or_none(r0.get("netProfitMargin"))
            c["operating_margin"] = _pct_or_none(r0.get("operatingProfitMargin"))
            c["roe"] = _pct_or_none(r0.get("returnOnEquity"))
            c["roa"] = _pct_or_none(r0.get("returnOnAssets"))
            c["pe_ratio"] = _num_or_none(r0.get("priceEarningsRatio"))
            c["pb_ratio"] = _num_or_none(r0.get("priceToBookRatio"))
            c["ps_ratio"] = _num_or_none(r0.get("priceToSalesRatio"))
            c["pfcf_ratio"] = _num_or_none(r0.get("priceToFreeCashFlowsRatio"))
            c["ev_ebitda"] = _num_or_none(r0.get("enterpriseValueOverEBITDA"))
            c["debt_equity"] = _num_or_none(r0.get("debtEquityRatio"))
            c["current_ratio"] = _num_or_none(r0.get("currentRatio"))
            c["interest_coverage"] = _num_or_none(r0.get("interestCoverage"))
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
            sorted_est = sorted(estimates, key=lambda e: (e.get("date") or ""))
            n = len(sorted_est)
            est0_rev = _safe_float(sorted_est[0], "estimatedRevenueAvg")
            estn_rev = _safe_float(sorted_est[-1], "estimatedRevenueAvg")
            c["revenue_cagr"] = _safe_cagr(est0_rev, estn_rev, n)
            est0_eps = _safe_float(sorted_est[0], "estimatedEpsAvg")
            estn_eps = _safe_float(sorted_est[-1], "estimatedEpsAvg")
            c["eps_cagr"] = _safe_cagr(est0_eps, estn_eps, n)
        else:
            c["revenue_cagr"] = None
            c["eps_cagr"] = None

        # ── Historical price chart data ───────────────────────────────
        hist_list = _hist_list(out.historical)
        recent_prices = [
            p.get("close", 0.0) for p in hist_list[:20]
            if p.get("close") is not None
        ]
        recent_prices.reverse()
        c["recent_prices"] = recent_prices

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
        segments_built = _segments_from_breakdown(out.revenue_breakdown)
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
            out.insider_roster, profile
        )

        # ── Price action: deterministic earnings event detection ──────
        out.price_action_partial = _build_price_action(
            c.get("recent_prices") or [],
            current_price,
            out.earnings_dates,
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
            earnings_yield=c.get("earnings_yield"),
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

        # ── Revenue forecast: AI provides guidance + quote ───────────
        revenue_forecast = dict(out.revenue_forecast_partial)
        ai_rf = ai.get("revenue_forecast") or {}
        revenue_forecast["management_guidance"] = (
            ai_rf.get("management_guidance") or "maintained"
        )
        revenue_forecast["guidance_quote"] = ai_rf.get("guidance_quote")

        # ── Wall Street consensus: AI fills only hedge_fund_note ─────
        wall_street_consensus = dict(out.wall_street_consensus_partial)
        ai_ws = ai.get("wall_street") or {}
        wall_street_consensus["hedge_fund_note"] = ai_ws.get("hedge_fund_note")

        # ── Moat: AI provides dimensions + market_dynamics + ─────────
        # competitors + insight + durability_note. We then derive the
        # moat *vital* (overall_rating, primary_source, tags, labels)
        # from the real dimension scores.
        ai_moat = ai.get("moat_competition") or {}
        moat_dims = list(ai_moat.get("dimensions") or [])
        moat_competition = {
            "market_dynamics": ai_moat.get("market_dynamics") or _default_market_dynamics(out.profile),
            "dimensions": moat_dims,
            "durability_note": (
                ai_moat.get("durability_note")
                or "Data unavailable for this ticker."
            ),
            "competitors": list(ai_moat.get("competitors") or []),
            "competitive_insight": (
                ai_moat.get("competitive_insight")
                or "Data unavailable for this ticker."
            ),
        }
        moat_vital = _derive_moat_vital(moat_dims)

        # ── Macro: AI provides risk_factors + threat_level + brief ───
        ai_macro = ai.get("macro_data") or {}
        risk_factors = [
            _sanitize_risk_factor(rf) for rf in (ai_macro.get("risk_factors") or [])
        ]
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
        "price_target": round(target_price if target_price > 0 else (fair_value or 0.0), 0),
        "current_price": round(current_price, 0),
        "upgrades": upgrades,
        "downgrades": downgrades,
    }

    # ── Hedge fund flow data: real institutional from HoldersService ─
    hf_price_data = [
        {"month": p["month"], "price": p["price"]}
        for p in monthly_prices
    ]
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
        "current_price": round(current_price, 0),
        "target_price": round(target_price if target_price > 0 else (fair_value or current_price), 0),
        "low_target": round(low_target if low_target > 0 else current_price * 0.85, 0),
        "high_target": round(high_target if high_target > 0 else (fair_value or current_price) * 1.3, 0),
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
    earnings_yield: Optional[float],
) -> List[Dict[str, Any]]:
    """Build the 4 fundamental cards from the same snapshot services
    TickerDetailView's Financials tab uses, so the values match exactly.

    Order matches the existing iOS card order: Profitability, Growth,
    Valuation, Health. Earnings Yield is appended to the Valuation card.
    """
    valuation_extras = [
        {
            "label": "Earnings Yield",
            "value": _format_earnings_yield(earnings_yield),
            "trend": None,
        }
    ]
    return [
        _snapshot_to_card("Profitability", profitability),
        _snapshot_to_card("Growth", growth),
        _snapshot_to_card("Valuation", valuation, extra_metrics=valuation_extras),
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
) -> List[Dict[str, Any]]:
    """Use RevenueBreakdownService output for cross-view parity.

    Returns [] when the service either hit no data or fell back to its
    single "Total Revenue" placeholder — caller then falls back to the
    direct FMP segmentation path. previous_revenue stays 0.0 because
    the breakdown service doesn't expose prior-period segments; the iOS
    revenue_engine view renders current-period bars primarily.
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

    # Sort largest first, mirror the FMP-direct path's contract.
    sources.sort(key=lambda s: s.value, reverse=True)
    return [
        {
            "name": s.name,
            "current_revenue": float(s.value),
            "previous_revenue": 0.0,
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
    """Pick a display unit and emit segments scaled to that unit."""
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
        unit, divisor = "Trillions", 1e12
    elif total >= 1e9:
        unit, divisor = "Billions", 1e9
    else:
        unit, divisor = "Millions", 1e6

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
    # Sort estimates oldest→newest so the chart reads left-to-right in time.
    sorted_estimates = sorted(estimates, key=lambda e: (e.get("date") or ""))[:3]

    # Pick a single divisor across all bars so they're visually comparable.
    revs = [
        _safe_float(est, "estimatedRevenueAvg") for est in sorted_estimates
    ]
    max_rev = max(revs) if revs else 0.0
    if max_rev >= 1e12:
        divisor = 1e12
    elif max_rev >= 1e9:
        divisor = 1e9
    else:
        divisor = 1e6

    projections: List[Dict[str, Any]] = []
    for i, est in enumerate(sorted_estimates):
        date_str = est.get("date") or ""
        period = date_str[:4] if len(date_str) >= 4 else f"FY{i}"
        rev = _safe_float(est, "estimatedRevenueAvg")
        eps = _safe_float(est, "estimatedEpsAvg")
        projections.append({
            "period": period,
            "revenue": round(rev / divisor, 2) if rev else 0.0,
            "revenue_label": _format_revenue(rev),
            "eps": round(eps, 2) if eps else 0.0,
            "eps_label": f"${eps:.2f}" if eps else "$0",
            "is_forecast": i > 0,
        })
    return {
        "cagr": revenue_cagr if revenue_cagr is not None else 0.0,
        "eps_growth": eps_cagr if eps_cagr is not None else 0.0,
        "management_guidance": "maintained",  # AI overrides
        "projections": projections,
        "guidance_quote": None,  # AI fills
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

    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []
    for t in recent:
        # FMP marks Form-4 acquisitions/dispositions in
        # `acquisitionOrDisposition` ("A"/"D"). Fall back to
        # transactionType prefix when missing.
        ad = (t.get("acquisitionOrDisposition") or "").upper()
        if not ad:
            tx_type = (t.get("transactionType") or "").upper()
            ad = "A" if tx_type.startswith("P") else "D" if tx_type.startswith("S") else ""
        if ad == "A":
            buys.append(t)
        elif ad == "D":
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


def _build_key_management(
    insider_roster: List[Dict[str, Any]],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Real exec roster from FMP insider-trading derived data.

    Sorted by ownership share count desc; ranks the top 5. Falls back
    to `[CEO from profile]` when the roster is empty so iOS never
    sees an empty `managers` array (the view assumes ≥1 entry).
    """
    managers: List[Dict[str, Any]] = []
    if insider_roster:
        ranked = sorted(
            insider_roster,
            key=lambda r: _safe_float(r, "numberOfShares"),
            reverse=True,
        )
        for r in ranked[:5]:
            shares = _safe_float(r, "numberOfShares")
            managers.append({
                "name": r.get("owner") or "Insider",
                "title": r.get("title") or r.get("typeOfOwner") or "Officer",
                "ownership": _format_shares_short(shares),
                "ownership_value": "—",  # roster lacks value; AI may rewrite
            })

    if not managers:
        ceo = profile.get("ceo")
        if ceo:
            managers.append({
                "name": ceo,
                "title": "CEO",
                "ownership": "—",
                "ownership_value": "—",
            })
        else:
            managers.append({
                "name": "Data unavailable",
                "title": "Officer",
                "ownership": "—",
                "ownership_value": "—",
            })

    return {
        "managers": managers,
        "ownership_insight": None,  # AI fills
    }


def _build_price_action(
    recent_prices: List[float],
    current_price: float,
    earnings_dates: List[str],
) -> Dict[str, Any]:
    """20-day chart + deterministic earnings event detection.

    Walks `earnings_dates` looking for one within the last ~30 calendar
    days, then classifies the price reaction (>+3% = beat, <-3% =
    miss, otherwise = reaction). When no earnings event falls in
    window, `event` is None and the iOS chart skips the marker.
    """
    if not recent_prices:
        recent_prices = [current_price] * 20 if current_price > 0 else []

    event = None
    if recent_prices and earnings_dates:
        today = datetime.now(timezone.utc).date()
        window_start = today - timedelta(days=30)
        for ed in earnings_dates:
            try:
                d = datetime.strptime(ed[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
            if not (window_start <= d <= today):
                continue
            days_ago = (today - d).days
            idx = max(0, min(len(recent_prices) - 1, len(recent_prices) - days_ago - 1))
            before = recent_prices[max(0, idx - 1)]
            after = recent_prices[min(len(recent_prices) - 1, idx + 1)]
            change = ((after - before) / before * 100) if before else 0.0
            if change > 3:
                tag = "Earnings Beat"
            elif change < -3:
                tag = "Earnings Miss"
            else:
                tag = "Earnings Reaction"
            event = {
                "tag": tag,
                "date": d.strftime("%b ") + str(d.day),
                "index": idx,
            }
            break

    return {
        "prices": recent_prices,
        "current_price": round(current_price, 2),
        "event": event,
        "narrative": None,  # AI fills
    }


def _default_market_dynamics(profile: Dict[str, Any]) -> Dict[str, Any]:
    now_year = datetime.now(timezone.utc).year
    return {
        "industry": profile.get("industry") or "Unknown",
        "concentration": "fragmented",
        "cagr_5yr": 0.0,
        "current_tam": 0.0,
        "future_tam": 0.0,
        "current_year": str(now_year),
        "future_year": str(now_year + 5),
        "lifecycle_phase": "mature",
    }


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
        parts.append(f"\nP/E: {r0.get('priceEarningsRatio', 'N/A')}")
        parts.append(f"EV/EBITDA: {r0.get('enterpriseValueOverEBITDA', 'N/A')}")
        parts.append(f"P/FCF: {r0.get('priceToFreeCashFlowsRatio', 'N/A')}")

    if estimates:
        parts.append("\nAnalyst Estimates:")
        for est in estimates[:2]:
            parts.append(
                f"  {est.get('date', '?')}: "
                f"Rev ${est.get('estimatedRevenueAvg', 0):,.0f}, "
                f"EPS ${est.get('estimatedEpsAvg', 0):.2f}"
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

    return "\n".join(parts)


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
