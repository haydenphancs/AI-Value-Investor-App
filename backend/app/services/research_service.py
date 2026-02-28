"""
Research Service — orchestrates Gemini + FMP for deep research reports.
Updates research_reports table with progress/status for frontend polling.
"""

import logging
import asyncio
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.integrations.fmp import get_fmp_client
from app.services.user_service import UserService
from app.config import settings

logger = logging.getLogger(__name__)


class ResearchService:
    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()

    async def generate_report(
        self,
        report_id: str,
        ticker: str,
        persona_key: str,
        user_id: str,
    ):
        """Full pipeline: gather data, generate analysis, extract components, save."""
        start = datetime.now(timezone.utc)

        try:
            # Step 1: Gathering financial data
            self._update_status(report_id, "processing", 10, "Gathering financial data...")
            financial_data = await self._gather_financial_data(ticker)

            # Step 2: Loading persona
            self._update_status(report_id, "processing", 25, "Loading investor persona...")
            persona_prompt = await self._get_persona_prompt(persona_key)

            # Step 3: Generating analysis
            self._update_status(report_id, "processing", 40, "Generating investment analysis...")
            analysis = await self._generate_analysis(ticker, financial_data, persona_prompt)

            # Step 4: Extracting structured components
            self._update_status(report_id, "processing", 70, "Extracting key insights...")
            components = await self._extract_components(analysis["text"], ticker, persona_key)

            # Step 5: Saving report
            self._update_status(report_id, "processing", 90, "Finalizing report...")

            generation_time = int((datetime.now(timezone.utc) - start).total_seconds())

            update_data = {
                "status": "completed",
                "progress": 100,
                "current_step": "Complete",
                "title": components.get("title", f"{ticker} Analysis"),
                "executive_summary": components.get("executive_summary"),
                "investment_thesis": components.get("investment_thesis"),
                "pros": components.get("pros"),
                "cons": components.get("cons"),
                "moat_analysis": components.get("moat_analysis"),
                "valuation_analysis": components.get("valuation_analysis"),
                "risk_assessment": components.get("risk_assessment"),
                "full_report": analysis["text"],
                "key_takeaways": components.get("key_takeaways"),
                "action_recommendation": components.get("action_recommendation"),
                "generation_time_seconds": generation_time,
                "tokens_used": analysis.get("tokens_used"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

            self.supabase.table("research_reports").update(update_data).eq(
                "id", report_id
            ).execute()

            # Decrement credits after success
            user_service = UserService()
            await user_service.decrement_credits(user_id, 1)

            logger.info(f"Report {report_id} completed in {generation_time}s")

        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            self._update_status(report_id, "failed", 0, error_message=str(e))
            raise

    def _update_status(
        self, report_id: str, status: str, progress: int,
        current_step: Optional[str] = None, error_message: Optional[str] = None,
    ):
        update = {"status": status, "progress": progress}
        if current_step:
            update["current_step"] = current_step
        if error_message:
            update["error_message"] = error_message
        try:
            self.supabase.table("research_reports").update(update).eq(
                "id", report_id
            ).execute()
        except Exception as e:
            logger.error(f"Status update failed: {e}")

    async def _gather_financial_data(self, ticker: str) -> Dict[str, Any]:
        """Parallel FMP data gathering."""
        tasks = [
            self.fmp.get_company_profile(ticker),
            self.fmp.get_income_statement(ticker, "annual", 5),
            self.fmp.get_balance_sheet(ticker, "annual", 5),
            self.fmp.get_cash_flow_statement(ticker, "annual", 5),
            self.fmp.get_key_metrics(ticker, "annual", 5),
            self.fmp.get_financial_ratios(ticker, "annual", 5),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        profile = results[0] if not isinstance(results[0], Exception) else {}
        income = results[1] if not isinstance(results[1], Exception) else []
        balance = results[2] if not isinstance(results[2], Exception) else []
        cash_flow = results[3] if not isinstance(results[3], Exception) else []
        metrics = results[4] if not isinstance(results[4], Exception) else []
        ratios = results[5] if not isinstance(results[5], Exception) else []

        return {
            "company_name": profile.get("companyName", ticker),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "description": profile.get("description"),
            "market_cap": profile.get("mktCap"),
            "income_statements": income,
            "balance_sheets": balance,
            "cash_flows": cash_flow,
            "key_metrics": metrics,
            "ratios": ratios,
        }

    async def _get_persona_prompt(self, persona_key: str) -> str:
        """Get persona system prompt from DB, fallback to default."""
        try:
            result = self.supabase.table("agent_personas").select(
                "persona_prompt"
            ).eq("key", persona_key).single().execute()

            if result.data and result.data.get("persona_prompt"):
                return result.data["persona_prompt"]
        except Exception:
            pass

        # Fallback default prompt
        return self._default_persona_prompt(persona_key)

    def _default_persona_prompt(self, persona_key: str) -> str:
        defaults = {
            "warren_buffett": (
                "You are analyzing companies through Warren Buffett's investment lens. "
                "Focus on durable competitive advantages (moats), management quality, "
                "capital allocation, long-term orientation (10+ years), and margin of safety. "
                "Use clear, folksy wisdom backed by rigorous business analysis."
            ),
            "bill_ackman": (
                "You are analyzing companies through Bill Ackman's activist investor lens. "
                "Focus on high-quality businesses with hidden value, catalyst potential, "
                "downside protection, and operational improvement opportunities."
            ),
            "peter_lynch": (
                "You are analyzing companies through Peter Lynch's growth-at-reasonable-price lens. "
                "Classify the stock (fast grower, stalwart, turnaround, etc.), focus on PEG ratio, "
                "and look for simple, understandable businesses with sustainable growth."
            ),
            "cathie_wood": (
                "You are analyzing companies through Cathie Wood's disruptive innovation lens. "
                "Focus on exponential growth potential, convergence of technologies, "
                "total addressable market expansion, and long-term vision."
            ),
        }
        return defaults.get(persona_key, defaults["warren_buffett"])

    async def _generate_analysis(
        self, ticker: str, data: Dict[str, Any], system_prompt: str,
    ) -> Dict[str, Any]:
        """Generate full analysis via Gemini."""
        context = self._build_context(data)
        prompt = f"""Analyze {data.get('company_name', ticker)} ({ticker}) comprehensively.

FINANCIAL DATA:
{context}

Provide a complete investment research report including:
1. Business overview (in plain English)
2. Competitive advantages / moat analysis
3. Management quality assessment
4. Financial strength analysis
5. Pros (3-5 specific strengths)
6. Cons (3-5 specific risks/weaknesses)
7. Valuation assessment with margin of safety
8. Risk assessment (business, financial, market risks)
9. Investment thesis with key drivers
10. Final verdict with conviction level and recommended action

IMPORTANT: Ignore short-term price movements. Focus on long-term fundamentals.
Be specific with numbers and evidence from the financial data provided."""

        return await self.gemini.generate_text(
            prompt=prompt,
            system_instruction=system_prompt,
        )

    def _build_context(self, data: Dict[str, Any]) -> str:
        parts = []
        if data.get("description"):
            parts.append(f"Company: {data['description'][:500]}")
        if data.get("sector"):
            parts.append(f"Sector: {data['sector']} | Industry: {data.get('industry')}")
        if data.get("income_statements"):
            recent = data["income_statements"][0]
            parts.append(f"\nRecent Financials:")
            parts.append(f"  Revenue: ${recent.get('revenue', 0):,.0f}")
            parts.append(f"  Net Income: ${recent.get('netIncome', 0):,.0f}")
            parts.append(f"  EPS: ${recent.get('eps', 0):.2f}")
        if data.get("ratios"):
            r = data["ratios"][0]
            parts.append(f"\nKey Ratios:")
            parts.append(f"  ROE: {r.get('returnOnEquity', 0)*100:.1f}%")
            parts.append(f"  P/E: {r.get('priceEarningsRatio', 0):.1f}")
            parts.append(f"  Debt/Equity: {r.get('debtEquityRatio', 0):.2f}")
        if data.get("key_metrics"):
            m = data["key_metrics"][0]
            parts.append(f"  FCF/Share: ${m.get('freeCashFlowPerShare', 0):.2f}")
            parts.append(f"  Book Value/Share: ${m.get('bookValuePerShare', 0):.2f}")
        return "\n".join(parts)

    async def _extract_components(
        self, full_text: str, ticker: str, persona_key: str,
    ) -> Dict[str, Any]:
        """Extract structured JSON components from the full report text using Gemini."""
        extraction_prompt = f"""Given this investment research report, extract structured data as JSON.

REPORT:
{full_text[:6000]}

Return ONLY valid JSON with these keys:
{{
  "title": "string - report title",
  "executive_summary": "string - 2-3 sentence summary",
  "investment_thesis": {{
    "summary": "string",
    "key_drivers": ["string"],
    "risks": ["string"],
    "time_horizon": "string",
    "conviction_level": "High/Medium/Low"
  }},
  "pros": ["string - 3-5 items"],
  "cons": ["string - 3-5 items"],
  "moat_analysis": {{
    "moat_rating": "Wide/Narrow/None",
    "moat_sources": ["string"],
    "moat_sustainability": "string",
    "competitive_position": "string",
    "barriers_to_entry": "string"
  }},
  "valuation_analysis": {{
    "valuation_rating": "Undervalued/Fair/Overvalued",
    "key_metrics": {{}},
    "historical_context": "string",
    "margin_of_safety": "string"
  }},
  "risk_assessment": {{
    "overall_risk": "Low/Medium/High",
    "business_risks": ["string"],
    "financial_risks": ["string"],
    "market_risks": ["string"]
  }},
  "key_takeaways": ["string - 3-5 items"],
  "action_recommendation": "Buy/Hold/Sell/Watch"
}}"""

        try:
            result = await self.gemini.generate_text(
                prompt=extraction_prompt,
                system_instruction="You are a JSON extraction assistant. Return ONLY valid JSON, no markdown.",
            )
            text = result["text"].strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            if text.startswith("json"):
                text = text[4:]

            return json.loads(text.strip())
        except Exception as e:
            logger.warning(f"Component extraction failed, using fallback: {e}")
            return {
                "title": f"{ticker} Investment Analysis",
                "executive_summary": full_text[:500],
                "pros": [],
                "cons": [],
                "key_takeaways": [],
                "action_recommendation": "Watch",
            }
