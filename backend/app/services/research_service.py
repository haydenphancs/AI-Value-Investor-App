"""
Research Service — orchestrates FMP data gathering + Gemini AI generation
for comprehensive deep-research reports.

Pipeline:
  1. Mark report as "processing"
  2. Gather financial data from FMP (parallel async calls)
  3. Load persona system prompt from DB (fallback to hardcoded defaults)
  4. Generate full analysis via Gemini with rich financial context
  5. Extract structured JSON components + overall_score + fair_value_estimate
  6. Save everything to research_reports table, decrement user credits
  7. On ANY failure → status = "failed", error_message saved
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

    # ── Main Pipeline ────────────────────────────────────────────────────

    async def generate_report(
        self,
        report_id: str,
        ticker: str,
        persona_key: str,
        user_id: str,
    ):
        """Full pipeline: gather data → generate analysis → extract → save."""
        start = datetime.now(timezone.utc)

        try:
            # Step 1: Gather financial data (10% → 25%)
            self._update_status(report_id, "processing", 5, "Connecting to market data...")
            financial_data = await self._gather_financial_data(ticker)
            self._update_status(report_id, "processing", 20, "Financial data collected")

            # Step 2: Load persona
            self._update_status(report_id, "processing", 25, "Loading investor persona...")
            persona_prompt = await self._get_persona_prompt(persona_key)

            # Step 3: Generate full analysis via Gemini
            self._update_status(report_id, "processing", 35, "Analyzing fundamentals...")
            analysis = await self._generate_analysis(
                ticker, financial_data, persona_prompt, persona_key
            )
            self._update_status(report_id, "processing", 65, "Analysis generated")

            # Step 4: Extract structured components + score
            self._update_status(report_id, "processing", 70, "Extracting key insights...")
            components = await self._extract_components(
                analysis["text"], ticker, persona_key, financial_data
            )

            # Step 5: Save report to DB
            self._update_status(report_id, "processing", 90, "Finalizing report...")

            generation_time = int((datetime.now(timezone.utc) - start).total_seconds())

            update_data = {
                "status": "completed",
                "progress": 100,
                "current_step": "Complete",
                "title": components.get("title", f"{ticker} Investment Analysis"),
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
                "overall_score": components.get("overall_score"),
                "fair_value_estimate": components.get("fair_value_estimate"),
                "generation_time_seconds": generation_time,
                "tokens_used": analysis.get("tokens_used"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

            self.supabase.table("research_reports").update(update_data).eq(
                "id", report_id
            ).execute()

            # Step 6: Decrement credits
            user_service = UserService()
            await user_service.decrement_credits(user_id, 1)

            logger.info(f"Report {report_id} completed in {generation_time}s")

        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            self._update_status(report_id, "failed", 0, error_message=str(e))
            raise

    # ── Status Helper ────────────────────────────────────────────────────

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

    # ── Data Gathering (FMP) ─────────────────────────────────────────────

    async def _gather_financial_data(self, ticker: str) -> Dict[str, Any]:
        """Parallel FMP calls for comprehensive data. Failures are non-fatal."""
        tasks = {
            "profile": self.fmp.get_company_profile(ticker),
            "quote": self.fmp.get_stock_price_quote(ticker),
            "income": self.fmp.get_income_statement(ticker, "annual", 5),
            "balance": self.fmp.get_balance_sheet(ticker, "annual", 5),
            "cash_flow": self.fmp.get_cash_flow_statement(ticker, "annual", 5),
            "metrics": self.fmp.get_key_metrics(ticker, "annual", 5),
            "ratios": self.fmp.get_financial_ratios(ticker, "annual", 5),
            "estimates": self.fmp.get_analyst_estimates(ticker, "annual", 3),
            "news": self.fmp.get_stock_news(ticker, 5),
        }

        keys = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        data: Dict[str, Any] = {}
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                logger.warning(f"FMP {key} failed for {ticker}: {result}")
                data[key] = {} if key in ("profile", "quote") else []
            else:
                data[key] = result

        return data

    # ── Persona Prompt ───────────────────────────────────────────────────

    async def _get_persona_prompt(self, persona_key: str) -> str:
        """Load persona system prompt from DB, fallback to default."""
        try:
            result = self.supabase.table("agent_personas").select(
                "persona_prompt"
            ).eq("key", persona_key).single().execute()

            if result.data and result.data.get("persona_prompt"):
                return result.data["persona_prompt"]
        except Exception:
            pass

        return self._default_persona_prompt(persona_key)

    def _default_persona_prompt(self, persona_key: str) -> str:
        defaults = {
            "warren_buffett": (
                "You are Warren Buffett analyzing a company for Berkshire Hathaway's portfolio. "
                "Focus relentlessly on durable competitive advantages (moats), management integrity "
                "and capital allocation skill, owner earnings, long-term orientation (10+ year "
                "holding period), and margin of safety. Use clear, folksy wisdom backed by "
                "rigorous business analysis. Prefer wonderful companies at fair prices over "
                "fair companies at wonderful prices."
            ),
            "cathie_wood": (
                "You are Cathie Wood analyzing a company for ARK Invest. "
                "Focus on disruptive innovation potential, convergence of technology platforms, "
                "Wright's Law cost declines, total addressable market expansion, and 5-year "
                "exponential growth trajectories. Accept higher near-term volatility for "
                "transformative long-term upside."
            ),
            "peter_lynch": (
                "You are Peter Lynch analyzing a company as you would at Fidelity Magellan. "
                "Classify the stock (fast grower, stalwart, slow grower, cyclical, turnaround, "
                "or asset play). Focus on the PEG ratio, earnings growth sustainability, balance "
                "sheet strength, and whether an average person can understand the business. "
                "Look for hidden gems the Street has overlooked."
            ),
            "bill_ackman": (
                "You are Bill Ackman analyzing a company for Pershing Square. "
                "Focus on high-quality businesses with predictable free cash flows, hidden or "
                "misunderstood value, activist catalysts for unlocking value, downside protection, "
                "and operational improvement opportunities. Take concentrated, high-conviction "
                "positions backed by exhaustive due diligence."
            ),
        }
        return defaults.get(persona_key, defaults["warren_buffett"])

    # ── Analysis Generation (Gemini) ─────────────────────────────────────

    async def _generate_analysis(
        self,
        ticker: str,
        data: Dict[str, Any],
        system_prompt: str,
        persona_key: str,
    ) -> Dict[str, Any]:
        """Generate full analysis via Gemini with rich financial context."""
        context = self._build_context(ticker, data)

        prompt = f"""Produce a comprehensive investment research report for {data.get("profile", {}).get("companyName", ticker)} ({ticker}).

═══════════════════════════════════════════════
FINANCIAL DATA (verified from Financial Modeling Prep)
═══════════════════════════════════════════════
{context}
═══════════════════════════════════════════════

REQUIRED SECTIONS (write each thoroughly):

1. **Executive Summary** — 2-3 sentences capturing the core investment case.

2. **Business Overview** — What the company does in plain English. Revenue model, customers, competitive landscape.

3. **Competitive Advantages / Moat Analysis** — Rate the moat (Wide / Narrow / None). Identify sources: brand, switching costs, network effects, cost advantages, intangible assets. Assess sustainability over 10+ years.

4. **Financial Strength** — Analyze profitability trends (margins, ROE, ROIC), balance sheet health (debt levels, interest coverage, Altman Z-score if calculable), and free cash flow generation.

5. **Growth Assessment** — Revenue & earnings growth trajectory. Compare historical growth to analyst forward estimates. Identify the primary growth drivers.

6. **Valuation Analysis** — Current P/E, P/FCF, EV/EBITDA vs. historical averages and sector peers. Estimate fair value per share with reasoning. Calculate margin of safety from current price.

7. **Pros** — 3-5 specific, evidence-backed strengths.

8. **Cons** — 3-5 specific, evidence-backed risks/weaknesses.

9. **Risk Assessment** — Categorize into business risks, financial risks, and macro/market risks.

10. **Investment Thesis** — Summarize the bull and bear cases. State key drivers, time horizon, and conviction level (High/Medium/Low).

11. **Overall Score** — Rate the stock 0-100 where: 0-20 = Avoid, 21-40 = Below Average, 41-60 = Average, 61-80 = Above Average, 81-100 = Exceptional. Base this on moat quality, financial health, valuation, and growth.

12. **Fair Value Estimate** — Provide your estimated fair value per share as a single number in USD. Show your reasoning briefly.

13. **Final Verdict** — Buy / Hold / Sell / Watch with a one-sentence justification.

RULES:
- Be specific. Cite numbers from the financial data provided.
- Ignore short-term price movements. Focus on long-term (3-10 year) fundamentals.
- If data is missing for a metric, note it and work with what is available.
- Write in clear, accessible language. Avoid unnecessary jargon."""

        return await self.gemini.generate_text(
            prompt=prompt,
            system_instruction=system_prompt,
        )

    def _build_context(self, ticker: str, data: Dict[str, Any]) -> str:
        """Build a structured financial context string from FMP data."""
        parts: List[str] = []
        profile = data.get("profile", {})
        quote = data.get("quote", {})

        # ── Company overview
        if profile:
            parts.append("COMPANY OVERVIEW")
            parts.append(f"  Name: {profile.get('companyName', ticker)}")
            parts.append(f"  Sector: {profile.get('sector', 'N/A')} | Industry: {profile.get('industry', 'N/A')}")
            parts.append(f"  Exchange: {profile.get('exchangeShortName', 'N/A')} | Country: {profile.get('country', 'N/A')}")
            parts.append(f"  CEO: {profile.get('ceo', 'N/A')}")
            parts.append(f"  Employees: {profile.get('fullTimeEmployees', 'N/A')}")
            if profile.get("description"):
                parts.append(f"  Description: {profile['description'][:600]}")
            parts.append(f"  Market Cap: ${profile.get('mktCap', 0):,.0f}")
            parts.append(f"  Beta: {profile.get('beta', 'N/A')}")
            if profile.get("dcf"):
                parts.append(f"  DCF Valuation: ${profile['dcf']:,.2f}")

        # ── Real-time quote
        if quote:
            parts.append("")
            parts.append("CURRENT PRICE DATA")
            parts.append(f"  Price: ${quote.get('price', 0):,.2f}")
            parts.append(f"  Change: {quote.get('changesPercentage', 0):+.2f}%")
            parts.append(f"  Day Range: ${quote.get('dayLow', 0):,.2f} – ${quote.get('dayHigh', 0):,.2f}")
            parts.append(f"  52-Week Range: ${quote.get('yearLow', 0):,.2f} – ${quote.get('yearHigh', 0):,.2f}")
            parts.append(f"  P/E Ratio: {quote.get('pe', 'N/A')}")
            parts.append(f"  EPS: ${quote.get('eps', 0):.2f}")
            parts.append(f"  Avg Volume: {quote.get('avgVolume', 0):,.0f}")

        # ── Income statements (most recent 3 years)
        income = data.get("income", [])
        if income:
            parts.append("")
            parts.append("INCOME STATEMENTS (Annual, most recent first)")
            for stmt in income[:3]:
                yr = stmt.get("calendarYear", stmt.get("date", "?"))
                parts.append(f"  [{yr}]")
                parts.append(f"    Revenue: ${stmt.get('revenue', 0):,.0f}")
                parts.append(f"    Gross Profit: ${stmt.get('grossProfit', 0):,.0f}")
                parts.append(f"    Operating Income: ${stmt.get('operatingIncome', 0):,.0f}")
                parts.append(f"    Net Income: ${stmt.get('netIncome', 0):,.0f}")
                parts.append(f"    EPS (diluted): ${stmt.get('epsdiluted', 0):.2f}")

            # Revenue growth calculation
            if len(income) >= 2:
                rev_recent = income[0].get("revenue", 0)
                rev_prev = income[1].get("revenue", 0)
                if rev_prev and rev_prev != 0:
                    growth = ((rev_recent - rev_prev) / abs(rev_prev)) * 100
                    parts.append(f"  YoY Revenue Growth: {growth:+.1f}%")

        # ── Balance sheet highlights
        balance = data.get("balance", [])
        if balance:
            b = balance[0]
            parts.append("")
            parts.append("BALANCE SHEET (Latest)")
            parts.append(f"  Total Assets: ${b.get('totalAssets', 0):,.0f}")
            parts.append(f"  Total Liabilities: ${b.get('totalLiabilities', 0):,.0f}")
            parts.append(f"  Total Equity: ${b.get('totalStockholdersEquity', 0):,.0f}")
            parts.append(f"  Cash & Equivalents: ${b.get('cashAndCashEquivalents', 0):,.0f}")
            parts.append(f"  Total Debt: ${b.get('totalDebt', 0):,.0f}")
            parts.append(f"  Net Debt: ${b.get('netDebt', 0):,.0f}")

        # ── Cash flow highlights
        cash_flow = data.get("cash_flow", [])
        if cash_flow:
            cf = cash_flow[0]
            parts.append("")
            parts.append("CASH FLOW (Latest Annual)")
            parts.append(f"  Operating Cash Flow: ${cf.get('operatingCashFlow', 0):,.0f}")
            parts.append(f"  Capital Expenditure: ${cf.get('capitalExpenditure', 0):,.0f}")
            parts.append(f"  Free Cash Flow: ${cf.get('freeCashFlow', 0):,.0f}")
            parts.append(f"  Dividends Paid: ${cf.get('dividendsPaid', 0):,.0f}")
            parts.append(f"  Share Buybacks: ${cf.get('commonStockRepurchased', 0):,.0f}")

        # ── Key metrics
        metrics = data.get("metrics", [])
        if metrics:
            m = metrics[0]
            parts.append("")
            parts.append("KEY METRICS (Latest)")
            parts.append(f"  Revenue Per Share: ${m.get('revenuePerShare', 0):.2f}")
            parts.append(f"  FCF Per Share: ${m.get('freeCashFlowPerShare', 0):.2f}")
            parts.append(f"  Book Value Per Share: ${m.get('bookValuePerShare', 0):.2f}")
            parts.append(f"  Tangible Book Value Per Share: ${m.get('tangibleBookValuePerShare', 0):.2f}")
            parts.append(f"  Earnings Yield: {(m.get('earningsYield', 0) or 0) * 100:.2f}%")
            parts.append(f"  FCF Yield: {(m.get('freeCashFlowYield', 0) or 0) * 100:.2f}%")
            parts.append(f"  Dividend Yield: {(m.get('dividendYield', 0) or 0) * 100:.2f}%")
            parts.append(f"  Payout Ratio: {(m.get('payoutRatio', 0) or 0) * 100:.1f}%")
            parts.append(f"  Debt-to-Equity: {m.get('debtToEquity', 'N/A')}")
            parts.append(f"  Current Ratio: {m.get('currentRatio', 'N/A')}")

        # ── Financial ratios
        ratios = data.get("ratios", [])
        if ratios:
            r = ratios[0]
            parts.append("")
            parts.append("PROFITABILITY & VALUATION RATIOS (Latest)")
            parts.append(f"  Gross Margin: {(r.get('grossProfitMargin', 0) or 0) * 100:.1f}%")
            parts.append(f"  Operating Margin: {(r.get('operatingProfitMargin', 0) or 0) * 100:.1f}%")
            parts.append(f"  Net Margin: {(r.get('netProfitMargin', 0) or 0) * 100:.1f}%")
            parts.append(f"  ROE: {(r.get('returnOnEquity', 0) or 0) * 100:.1f}%")
            parts.append(f"  ROA: {(r.get('returnOnAssets', 0) or 0) * 100:.1f}%")
            parts.append(f"  ROIC: {(r.get('returnOnCapitalEmployed', 0) or 0) * 100:.1f}%")
            parts.append(f"  P/E: {r.get('priceEarningsRatio', 'N/A')}")
            parts.append(f"  P/B: {r.get('priceToBookRatio', 'N/A')}")
            parts.append(f"  P/S: {r.get('priceToSalesRatio', 'N/A')}")
            parts.append(f"  P/FCF: {r.get('priceToFreeCashFlowsRatio', 'N/A')}")
            parts.append(f"  EV/EBITDA: {r.get('enterpriseValueOverEBITDA', 'N/A')}")
            parts.append(f"  Debt/Equity: {r.get('debtEquityRatio', 'N/A')}")
            parts.append(f"  Interest Coverage: {r.get('interestCoverage', 'N/A')}")

        # ── Analyst estimates (forward)
        estimates = data.get("estimates", [])
        if estimates:
            parts.append("")
            parts.append("ANALYST FORWARD ESTIMATES")
            for est in estimates[:2]:
                yr = est.get("date", "?")
                parts.append(f"  [{yr}]")
                parts.append(f"    Est. Revenue: ${est.get('estimatedRevenueAvg', 0):,.0f}")
                parts.append(f"    Est. EPS: ${est.get('estimatedEpsAvg', 0):.2f}")
                parts.append(f"    # Analysts: {est.get('numberAnalystsEstimatedRevenue', 'N/A')}")

        # ── Recent news headlines
        news = data.get("news", [])
        if news:
            parts.append("")
            parts.append("RECENT NEWS HEADLINES")
            for article in news[:5]:
                title = article.get("title", "")
                date = article.get("publishedDate", "")[:10]
                if title:
                    parts.append(f"  [{date}] {title[:120]}")

        return "\n".join(parts)

    # ── Component Extraction (Gemini) ────────────────────────────────────

    async def _extract_components(
        self,
        full_text: str,
        ticker: str,
        persona_key: str,
        financial_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract structured JSON from the full report text using Gemini."""

        current_price = financial_data.get("quote", {}).get("price", 0)
        price_context = f"\nCurrent stock price: ${current_price:.2f}" if current_price else ""

        extraction_prompt = f"""Given this investment research report, extract structured data as JSON.
{price_context}

REPORT:
{full_text[:8000]}

Return ONLY valid JSON (no markdown fences, no commentary) with exactly these keys:
{{
  "title": "string — concise report title",
  "executive_summary": "string — 2-3 sentence summary of the investment case",
  "investment_thesis": {{
    "summary": "string — one paragraph thesis",
    "key_drivers": ["string — each a key growth/value driver"],
    "risks": ["string — each a key risk"],
    "time_horizon": "string — e.g. 3-5 years",
    "conviction_level": "High | Medium | Low"
  }},
  "pros": ["string — 3-5 specific strengths with evidence"],
  "cons": ["string — 3-5 specific weaknesses/risks with evidence"],
  "moat_analysis": {{
    "moat_rating": "Wide | Narrow | None",
    "moat_sources": ["string — e.g. Brand, Switching Costs, Network Effects"],
    "moat_sustainability": "string — assessment of durability",
    "competitive_position": "string — market position summary",
    "barriers_to_entry": ["string — each a barrier to entry for new competitors"]
  }},
  "valuation_analysis": {{
    "valuation_rating": "Undervalued | Fair Value | Overvalued",
    "key_metrics": {{"P/E": "number", "P/FCF": "number", "EV/EBITDA": "number"}},
    "historical_context": "string — how current valuation compares to history",
    "margin_of_safety": "string — e.g. 25% below fair value"
  }},
  "risk_assessment": {{
    "overall_risk": "Low | Medium | High",
    "business_risks": ["string"],
    "financial_risks": ["string"],
    "market_risks": ["string"]
  }},
  "key_takeaways": ["string — 3-5 most important points"],
  "action_recommendation": "Buy | Hold | Sell | Watch",
  "overall_score": 0,
  "fair_value_estimate": 0.00
}}

CRITICAL RULES for overall_score and fair_value_estimate:
- overall_score must be an integer 0-100 based on your analysis quality assessment.
- fair_value_estimate must be a float representing your estimated fair value per share in USD.
- If you cannot determine fair value, use 0.
- Do NOT wrap the JSON in markdown code fences. Return raw JSON only."""

        try:
            result = await self.gemini.generate_text(
                prompt=extraction_prompt,
                system_instruction=(
                    "You are a structured data extraction assistant. "
                    "Return ONLY valid JSON. No markdown, no commentary, no code fences."
                ),
            )
            text = result["text"].strip()

            # Strip markdown code fences if Gemini adds them despite instructions
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            if text.startswith("json"):
                text = text[4:]

            parsed = json.loads(text.strip())

            # Validate score bounds
            score = parsed.get("overall_score")
            if score is not None:
                parsed["overall_score"] = max(0, min(100, int(score)))

            fv = parsed.get("fair_value_estimate")
            if fv is not None:
                parsed["fair_value_estimate"] = max(0.0, float(fv))

            return parsed

        except json.JSONDecodeError as e:
            logger.warning(f"JSON extraction failed for {ticker}: {e}")
            return self._fallback_components(full_text, ticker)
        except Exception as e:
            logger.warning(f"Component extraction failed for {ticker}: {e}")
            return self._fallback_components(full_text, ticker)

    def _fallback_components(self, full_text: str, ticker: str) -> Dict[str, Any]:
        """Graceful fallback when JSON extraction fails."""
        return {
            "title": f"{ticker} Investment Analysis",
            "executive_summary": full_text[:500] if full_text else None,
            "investment_thesis": None,
            "pros": [],
            "cons": [],
            "moat_analysis": None,
            "valuation_analysis": None,
            "risk_assessment": None,
            "key_takeaways": [],
            "action_recommendation": "Watch",
            "overall_score": None,
            "fair_value_estimate": None,
        }
