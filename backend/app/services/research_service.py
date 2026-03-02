"""
Research Service — Multi-Agent Deep Research Orchestrator.

Upgraded from single-pass Gemini prompt to a true agentic pipeline:
  1. Spawn a ResearchAgent with the chosen investor persona
  2. Agent autonomously gathers FMP data via Gemini function calling
  3. Agent produces the full TickerReportResponse JSON (matching Swift UI)
  4. Service stores the result in research_reports + ticker_report_data JSONB
  5. Service also extracts legacy fields (title, executive_summary, etc.) for
     backward compatibility with the research reports list view

On ANY failure → status = "failed", error_message saved to DB.
"""

import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.integrations.fmp import get_fmp_client
from app.services.user_service import UserService
from app.services.agents.research_agent import ResearchAgent
from app.services.agents.persona_config import get_persona_config

logger = logging.getLogger(__name__)


class ResearchService:
    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()

    # ── Main Pipeline ─────────────────────────────────────────────────────

    async def generate_report(
        self,
        report_id: str,
        ticker: str,
        persona_key: str,
        user_id: str,
    ):
        """
        Full multi-agent pipeline:
          1. Spawn ResearchAgent with persona
          2. Agent runs agentic loop (data gathering + analysis)
          3. Store full TickerReportResponse + legacy fields
          4. Decrement user credits
        """
        start = datetime.now(timezone.utc)

        try:
            # Mark as processing
            self._update_status(report_id, "processing", 2, "Initializing research agent...")

            # Create the agent
            agent = ResearchAgent(
                persona_key=persona_key,
                fmp=self.fmp,
                gemini=self.gemini,
            )

            persona = get_persona_config(persona_key)

            # Progress callback bound to this report
            async def on_progress(progress: int, step: str):
                self._update_status(report_id, "processing", progress, step)

            # Run the full agentic pipeline
            ticker_report_data = await agent.run(
                ticker=ticker,
                progress_cb=on_progress,
            )

            # Extract legacy fields for backward compatibility
            self._update_status(report_id, "processing", 92, "Saving report...")

            generation_time = int((datetime.now(timezone.utc) - start).total_seconds())

            # Build update payload
            update_data = {
                "status": "completed",
                "progress": 100,
                "current_step": "Complete",

                # Full TickerReportResponse stored as JSONB
                "ticker_report_data": ticker_report_data,

                # Legacy fields for list view + backward compatibility
                "title": self._extract_title(ticker_report_data, ticker, persona),
                "executive_summary": ticker_report_data.get("executive_summary_text"),
                "full_report": agent.research_findings[:10000] if agent.research_findings else None,

                # Extract structured components from the report
                "investment_thesis": self._extract_thesis(ticker_report_data),
                "pros": ticker_report_data.get("core_thesis", {}).get("bull_case", []),
                "cons": ticker_report_data.get("core_thesis", {}).get("bear_case", []),
                "moat_analysis": self._extract_moat(ticker_report_data),
                "valuation_analysis": self._extract_valuation(ticker_report_data),
                "risk_assessment": self._extract_risk(ticker_report_data),
                "key_takeaways": self._extract_takeaways(ticker_report_data),
                "action_recommendation": self._derive_recommendation(ticker_report_data),

                # Scoring
                "overall_score": ticker_report_data.get("quality_score"),
                "fair_value_estimate": (
                    ticker_report_data.get("key_vitals", {})
                    .get("valuation", {})
                    .get("fair_value")
                ),

                # Generation metadata
                "generation_time_seconds": generation_time,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

            self.supabase.table("research_reports").update(update_data).eq(
                "id", report_id
            ).execute()

            # Decrement credits
            user_service = UserService()
            await user_service.decrement_credits(user_id, 1)

            logger.info(
                f"Report {report_id} completed in {generation_time}s "
                f"(persona={persona_key}, ticker={ticker})"
            )

        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            self._update_status(
                report_id, "failed", 0,
                error_message=f"Research failed: {str(e)[:400]}"
            )
            raise

    # ── Status Helper ─────────────────────────────────────────────────────

    def _update_status(
        self,
        report_id: str,
        status: str,
        progress: int,
        current_step: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        update: Dict[str, Any] = {"status": status, "progress": progress}
        if current_step:
            update["current_step"] = current_step
        if error_message:
            update["error_message"] = error_message
        try:
            self.supabase.table("research_reports").update(update).eq(
                "id", report_id
            ).execute()
        except Exception as e:
            logger.error(f"Status update failed for {report_id}: {e}")

    # ── Legacy Field Extractors ───────────────────────────────────────────
    # These extract simplified fields from the full TickerReportResponse
    # for the research reports list view and backward compatibility.

    def _extract_title(
        self, data: Dict[str, Any], ticker: str, persona
    ) -> str:
        """Generate a concise report title."""
        company = data.get("company_name", ticker)
        return f"{persona.display_name} Analysis: {company}"

    def _extract_thesis(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract investment thesis from core_thesis."""
        thesis = data.get("core_thesis", {})
        if not thesis:
            return None
        return {
            "summary": data.get("executive_summary_text", ""),
            "key_drivers": thesis.get("bull_case", [])[:3],
            "risks": thesis.get("bear_case", [])[:3],
            "time_horizon": "3-5 years",
            "conviction_level": self._derive_conviction(data),
        }

    def _extract_moat(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract moat analysis from key_vitals and moat_competition."""
        moat_vital = data.get("key_vitals", {}).get("moat", {})
        moat_comp = data.get("moat_competition", {})
        if not moat_vital:
            return None
        return {
            "moat_rating": (moat_vital.get("overall_rating", "none") or "none").capitalize(),
            "moat_sources": [t.get("label", "") for t in moat_vital.get("tags", [])],
            "moat_sustainability": moat_comp.get("durability_note", ""),
            "competitive_position": moat_comp.get("competitive_insight", ""),
            "barriers_to_entry": [],
        }

    def _extract_valuation(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract valuation analysis from key_vitals."""
        val = data.get("key_vitals", {}).get("valuation", {})
        if not val:
            return None
        status = val.get("status", "fair_value")
        rating_map = {
            "overpriced": "Overvalued",
            "fair_value": "Fair Value",
            "underpriced": "Undervalued",
            "deep_undervalued": "Undervalued",
        }
        return {
            "valuation_rating": rating_map.get(status, "Fair Value"),
            "key_metrics": {},
            "historical_context": "",
            "margin_of_safety": f"{val.get('upside_potential', 0):.1f}% upside",
        }

    def _extract_risk(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract risk assessment from macro_data and critical_factors."""
        macro = data.get("macro_data", {})
        factors = data.get("critical_factors", [])
        threat = macro.get("overall_threat_level", "low")
        risk_map = {"low": "Low", "elevated": "Medium", "high": "High", "severe": "High", "critical": "High"}
        return {
            "overall_risk": risk_map.get(threat, "Medium"),
            "business_risks": [f.get("description", "") for f in factors if f.get("severity") == "high"],
            "financial_risks": [],
            "market_risks": [rf.get("title", "") for rf in macro.get("risk_factors", [])[:3]],
        }

    def _extract_takeaways(self, data: Dict[str, Any]) -> list:
        """Extract key takeaways from executive summary bullets."""
        bullets = data.get("executive_summary_bullets", [])
        return [b.get("text", "") for b in bullets[:5] if b.get("text")]

    def _derive_recommendation(self, data: Dict[str, Any]) -> str:
        """Derive Buy/Hold/Sell from quality score and valuation."""
        score = data.get("quality_score", 50)
        val_status = data.get("key_vitals", {}).get("valuation", {}).get("status", "fair_value")
        if isinstance(score, str):
            try:
                score = float(score)
            except ValueError:
                score = 50
        if score >= 75 and val_status in ("underpriced", "deep_undervalued"):
            return "Buy"
        elif score <= 35 or val_status == "overpriced":
            return "Sell"
        elif score >= 60:
            return "Hold"
        else:
            return "Watch"

    def _derive_conviction(self, data: Dict[str, Any]) -> str:
        """Derive conviction level from quality score."""
        score = data.get("quality_score", 50)
        if isinstance(score, str):
            try:
                score = float(score)
            except ValueError:
                score = 50
        if score >= 80:
            return "High"
        elif score >= 55:
            return "Medium"
        else:
            return "Low"
