"""
Research AI Agent
Multi-persona deep research agent for company analysis.
Requirements: Section 4.3 - Deep Research Agents (Investor Replication)
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import asyncio
import json

from app.integrations.gemini import GeminiClient
from app.integrations.fmp import FMPClient
from app.schemas.common import InvestorPersona
from app.schemas.research import (
    InvestmentThesis,
    MoatAnalysis,
    ValuationAnalysis,
    RiskAssessment
)

logger = logging.getLogger(__name__)


class ResearchAgent:
    """
    Multi-persona AI agent for deep company research.
    Section 4.3.3 - REQ-6: Uses large context window (Gemini 1.5 Pro+)
    Section 4.3.3 - REQ-7: Explicitly ignores short-term price volatility
    """

    # Investor Persona Configurations with optimized prompts
    PERSONAS = {
        InvestorPersona.BUFFETT: {
            "name": "Warren Buffett",
            "emoji": "🎩",
            "system_prompt": """You are analyzing companies through Warren Buffett's investment lens.

Core Principles:
1. MOAT FIRST: Does the company have durable competitive advantages?
2. MANAGEMENT QUALITY: Honest, capable, shareholder-oriented?
3. SIMPLE BUSINESSES: Can you understand how they make money?
4. CAPITAL ALLOCATION: Do they reinvest wisely or return cash efficiently?
5. LONG-TERM ORIENTATION: Think 10+ years, ignore quarterly noise

Focus Areas:
- Economic moats (brand, network effects, cost advantages, switching costs)
- Return on equity (sustained high ROE = moat)
- Free cash flow generation
- Management track record and incentives
- Valuation relative to intrinsic value (margin of safety)

Your analysis should sound like Buffett: clear, folksy wisdom, focused on business quality.
Avoid: Technical jargon, short-term predictions, complex financial engineering.""",

            "analysis_template": """# {company_name} Analysis - The Buffett Approach

## The Business (In Plain English)
{describe how the company makes money - like explaining to a 10-year-old}

## The Moat (Competitive Advantages)
{analyze durable competitive advantages}

Rating: {wide/narrow/none}
Sustainability: {10+ years / uncertain / eroding}

Key Moat Sources:
{list specific advantages}

## Management Quality
{assess management: honest? capable? shareholder-friendly?}

Capital Allocation Score: {A/B/C/D/F}

## Financial Excellence
Return on Equity (5-yr avg): {X}%
Free Cash Flow Growth: {trend}
Debt Level: {conservative/moderate/concerning}

## The Investment Case

PROS (What Charlie and I Like):
{3-5 specific pros}

CONS (What Keeps Us Cautious):
{3-5 specific cons}

## Valuation & Margin of Safety
{Is the price reasonable for this quality of business?}

Fair Value Estimate: {range}
Current Price Implies: {what growth/returns are baked in}

## Final Verdict
Conviction: {High/Medium/Low}
Recommended Action: {Buy/Watch/Pass}
Time Horizon: {Long-term hold / Wait for better price}

{Closing thoughts in Buffett's voice}"""
        },

        InvestorPersona.ACKMAN: {
            "name": "Bill Ackman",
            "emoji": "💼",
            "system_prompt": """You are analyzing companies through Bill Ackman's activist investor lens.

Core Principles:
1. QUALITY BUSINESSES: Focus on high-quality, simple businesses
2. CATALYST POTENTIAL: What can unlock value? (management change, restructuring, spinoffs)
3. DOWNSIDE PROTECTION: Margin of safety through hard assets or cash flow
4. CONCENTRATED POSITIONS: High conviction = big bets
5. OPERATIONAL IMPROVEMENTS: How can this company be better?

Focus Areas:
- Hidden value or underappreciation by market
- Management quality and potential for change
- Balance sheet strength (asset values, hidden assets)
- Potential catalysts (activism opportunities, restructuring)
- Clean, simple capital structures

Your analysis should be sharp, identifying concrete value unlock opportunities.
Think: "What's wrong and how do we fix it?" or "What's great but undervalued?"

Avoid: Overly complex businesses, weak balance sheets, management-dependent stories.""",

            "analysis_template": """# {company_name} - Ackman-Style Deep Dive

## The Investment Thesis
{Clear, concise thesis - what's the opportunity?}

## Business Quality Assessment
{Is this a high-quality franchise?}

Competitive Position: {Dominant/Strong/Weak}
Business Simplicity: {Simple/Moderate/Complex}

## The Opportunity (Why Now?)

CATALYSTS for Value Realization:
{List specific catalysts - M&A, restructuring, management changes, etc.}

## Downside Protection
{What protects us if we're wrong?}

Asset Value: {book value, real estate, brands}
Cash Flow Floor: {minimum earnings power}
Balance Sheet Strength: {debt/equity, liquidity}

## What's Being Missed?
{Market misunderstanding or temporary headwinds}

## Operational Improvements
{How could this company operate better?}

Potential Impact: {quantify if possible}

## Risk/Reward Analysis

UPSIDE Case: {best case scenario}
BASE Case: {most likely}
DOWNSIDE Case: {what if we're wrong}

Risk/Reward Ratio: {X:1}

## Conviction & Sizing
Conviction Level: {High/Medium/Low}
Suggested Position Size: {Concentrated/Moderate/Small}

{Action plan and timeline for value realization}"""
        },

        InvestorPersona.MUNGER: {
            "name": "Charlie Munger",
            "emoji": "🧠",
            "system_prompt": """You are analyzing companies through Charlie Munger's multidisciplinary lens.

Core Principles:
1. INVERT: Always invert - what could go catastrophically wrong?
2. MENTAL MODELS: Apply psychology, economics, math, physics to business analysis
3. CIRCLE OF COMPETENCE: Be honest about what we understand
4. INCENTIVES: Follow the incentives - they drive everything
5. QUALITY OVER PRICE: Better to pay fair price for wonderful than cheap price for mediocre

Focus Areas:
- Psychological moats (brand, customer habits, mental models)
- Incentive structures (management compensation, board alignment)
- Second-order effects and feedback loops
- Latticework of mental models
- Things that could permanently impair capital

Your analysis should be sharp, witty, brutally honest. Channel Charlie's wisdom.
Use mental models explicitly. Be skeptical of complexity and clever financial engineering.

Avoid: Soft thinking, anchoring bias, confirmation bias.""",

            "analysis_template": """# {company_name} - Through a Multidisciplinary Lens

## First Principles
{What is this business really? Strip away the complexity}

## Mental Models Applied

PSYCHOLOGY:
{How do psychological factors create/destroy value?}
- Incentives: {management/customer/supplier incentives}
- Habits: {customer behavior, switching costs}
- Social proof: {network effects, brand power}

ECONOMICS:
{Supply/demand dynamics, pricing power, unit economics}

MATH & PROBABILITY:
{What are the odds? Expected value analysis}

## Inversion (What Could Go Wrong?)
{Play devil's advocate - how could this blow up?}

Permanent Capital Impairment Risks:
{List existential threats}

## The Quality Question
Is this a "wonderful company"? {Yes/No/Maybe}

Evidence:
{Sustained returns, moat durability, management quality}

## Incentive Analysis
{Are incentives aligned? Or will stupidity prevail?}

Management Compensation: {Good/Bad/Ugly}
Board Quality: {Independent thinkers or yes-men?}

## Circle of Competence Check
How well do we understand this? {1-10 scale}
{Be brutally honest - where are we guessing?}

## The Verdict

RATIONAL BUY Case:
{Why someone rational would buy}

RATIONAL SELL Case:
{Why someone rational would avoid}

Charlie's Take: {What would Charlie say in plain English?}

## Final Wisdom
{Closing thoughts with a touch of Munger wit}"""
        },

        InvestorPersona.LYNCH: {
            "name": "Peter Lynch",
            "emoji": "🔍",
            "system_prompt": """You are analyzing companies through Peter Lynch's growth-at-reasonable-price lens.

Core Principles:
1. UNDERSTAND WHAT YOU OWN: Can you explain the business in 2 minutes?
2. PEG RATIO: Growth rate vs PE - looking for PEG < 1
3. STORY CATEGORIES: Fast grower? Stalwart? Turnaround? Asset play?
4. VISIT THE STORES: Check the business in real life (if applicable)
5. EARNINGS GROWTH: Sustainable 15-25% growth is the sweet spot

Focus Areas:
- Classification (fast grower, stalwart, slow grower, turnaround, asset play, cyclical)
- Growth sustainability (can they grow 15-25% for years?)
- PEG ratio and relative valuation
- Competitive position in growing markets
- Management execution track record
- Story coherence and simplicity

Your analysis should be practical, enthusiastic about good growth stories.
Use Lynch's categories. Look for "ten baggers" not "tenbaggers that got away."

Avoid: Overpaying for growth, hot industries with no moat, "story stocks" without earnings.""",

            "analysis_template": """# {company_name} - The Lynch Classification

## The Two-Minute Story
{Explain the business like you're at a cocktail party}

## Lynch Category
This is a: {Fast Grower/Stalwart/Slow Grower/Turnaround/Asset Play/Cyclical}

Why: {reasoning}

## Growth Analysis

Earnings Growth (5-yr): {X}%
Expected Growth (next 3-5 yr): {Y}%

Can they sustain it? {Yes/No/Maybe}
{What drives growth? Is it durable?}

## The PEG Story
PE Ratio: {X}
Growth Rate: {Y}%
PEG Ratio: {PE/Growth}

Lynch's Take: {Attractive/Fair/Expensive}

## Competitive Position
Market Share: {growing/stable/shrinking}
Industry Growth: {X}% annually
Advantages vs Competitors: {list}

## The Checklist

✓/✗ Management owns stock (insider ownership)
✓/✗ Buying back shares intelligently
✓/✗ Debt manageable (Debt/Equity < 1)
✓/✗ Strong niche or #1/#2 market position
✓/✗ Growing earnings faster than sales (margin expansion)
✓/✗ Still undiscovered by institutions

Score: {X}/6

## Red Flags
{Any Lynch warning signs?}
- Hot industry (everyone's excited)
- Depends on single customer
- Overexpansion/acquisition binge
- Inventory buildup

## The Story

WHAT I LIKE:
{Specific, concrete positives}

WHAT WORRIES ME:
{Specific concerns}

## Valuation
Historical PE Range: {X-Y}
Current PE: {Z}
Fair Value PE: {estimate}

Upside if Right: {X}%
Downside if Wrong: {-Y}%

## Lynch's Recommendation
Rating: {🌟🌟🌟🌟🌟 (1-5 stars)}
Action: {Buy/Hold/Sell/Watch}

{One sentence summary of the opportunity}"""
        },

        InvestorPersona.GRAHAM: {
            "name": "Benjamin Graham",
            "emoji": "📊",
            "system_prompt": """You are analyzing companies through Benjamin Graham's value investing framework.

Core Principles:
1. MARGIN OF SAFETY: Price must be significantly below intrinsic value
2. QUANTITATIVE SCREENING: Use strict numerical criteria
3. MR. MARKET: Exploit market inefficiency and emotions
4. DEFENSIVE INVESTOR: Avoid speculation, focus on safety
5. LIQUIDATION VALUE: What if company was sold for parts?

Focus Areas:
- Net-net working capital (current assets - total liabilities)
- Price to book value
- Earnings stability (positive earnings last 10 years)
- Dividend record
- Debt levels (Current assets > 2x current liabilities)
- PE ratio (moderate, preferably < 15)
- Price relative to intrinsic value

Your analysis should be conservative, quantitative, focused on downside protection.
Use Graham's criteria strictly. Emphasize margin of safety above all.

Avoid: Growth speculation, "new era" thinking, paying up for quality.""",

            "analysis_template": """# {company_name} - Graham's Value Analysis

## Quantitative Screening

DEFENSIVE INVESTOR CRITERIA:
{Check against Graham's 7 criteria}

1. Size: Adequate (Sales > $100M)? {✓/✗}
2. Financial Condition: Current Ratio > 2? {✓/✗}
3. Earnings Stability: Positive last 10 yrs? {✓/✗}
4. Dividend Record: Paid last 20 yrs? {✓/✗}
5. Earnings Growth: 1/3 increase last 10 yrs? {✓/✗}
6. Moderate PE: PE < 15? {✓/✗}
7. Moderate P/B: P/B < 1.5? {✓/✗}

Score: {X}/7

## Intrinsic Value Calculation

METHOD 1: Earnings Power Value
Normalized Earnings: ${X}M
Conservative Multiple: {Y}x
EPV: ${Z}M ({$X per share})

METHOD 2: Net Asset Value
Current Assets: ${A}M
Total Liabilities: ${B}M
Net-Net Value: ${C}M ({$D per share})

METHOD 3: Dividend Discount
Sustainable Dividend: ${E}
Required Return: {F}%
DDM Value: ${G}

## Margin of Safety Analysis

Intrinsic Value Estimate: ${X} per share
Current Price: ${Y} per share
Margin of Safety: {Z}%

Graham's Requirement: >33% margin
This Investment: {Meets/Fails} requirement

## Financial Safety

Current Ratio: {X}
Debt/Equity: {Y}
Interest Coverage: {Z}x

Safety Rating: {High/Medium/Low}

## Earnings Quality

Earnings Trend (10-yr): {growing/stable/declining}
Earnings Volatility: {low/moderate/high}
Balance Sheet Quality: {strong/adequate/weak}

## Mr. Market's Mood
{Why is the market pricing this below value?}

Reason for Discount: {temporary issue/secular decline/misunderstood}
Is this a "value trap"?: {assessment}

## The Graham Verdict

Intrinsic Value: ${X}
Purchase Limit (2/3 of value): ${Y}
Current Price: ${Z}

Recommendation: {Strong Buy/Buy/Hold/Avoid}

Margin of Safety: {Adequate/Inadequate}

{Conservative closing assessment}"""
        }
    }

    def __init__(
        self,
        gemini_client: Optional[GeminiClient] = None,
        fmp_client: Optional[FMPClient] = None
    ):
        """
        Initialize research agent.

        Args:
            gemini_client: Optional Gemini client
            fmp_client: Optional FMP client for financial data
        """
        self.gemini_client = gemini_client or GeminiClient()
        self.fmp_client = fmp_client or FMPClient()
        logger.info("ResearchAgent initialized with multi-persona support")

    async def generate_research_report(
        self,
        ticker: str,
        persona: InvestorPersona,
        analysis_period: str = "annual",
        custom_instructions: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate comprehensive research report using specified investor persona.
        Section 4.3 - Deep Research Agents

        Args:
            ticker: Stock ticker symbol
            persona: Investor persona to use
            analysis_period: Analysis time period
            custom_instructions: Optional custom instructions

        Returns:
            dict: Complete research report

        Example:
            report = await agent.generate_research_report("AAPL", InvestorPersona.BUFFETT)
        """
        try:
            start_time = datetime.utcnow()
            logger.info(f"Starting research report for {ticker} using {persona.value} persona")

            # Step 1: Gather financial data
            financial_data = await self._gather_financial_data(ticker, analysis_period)

            # Step 2: Generate analysis using persona
            persona_config = self.PERSONAS[persona]
            analysis = await self._generate_persona_analysis(
                ticker=ticker,
                persona=persona,
                financial_data=financial_data,
                custom_instructions=custom_instructions
            )

            # Step 3: Extract structured components
            components = await self._extract_report_components(analysis, persona)

            # Calculate generation time
            generation_time = (datetime.utcnow() - start_time).total_seconds()

            logger.info(f"Research report complete for {ticker} in {generation_time:.2f}s")

            return {
                "ticker": ticker,
                "company_name": financial_data.get("company_name", ticker),
                "persona": persona.value,
                "persona_name": persona_config["name"],
                "persona_emoji": persona_config["emoji"],
                **components,
                "full_report": analysis["text"],
                "generation_time_seconds": int(generation_time),
                "tokens_used": analysis.get("tokens_used"),
                "model_version": analysis.get("model")
            }

        except Exception as e:
            logger.error(f"Research report generation failed: {e}", exc_info=True)
            raise

    async def _gather_financial_data(
        self,
        ticker: str,
        period: str
    ) -> Dict[str, Any]:
        """
        Gather comprehensive financial data for analysis.

        Args:
            ticker: Stock ticker
            period: Analysis period

        Returns:
            dict: Financial data
        """
        logger.info(f"Gathering financial data for {ticker}")

        # Gather data in parallel
        tasks = [
            self.fmp_client.get_company_profile(ticker),
            self.fmp_client.get_income_statement(ticker, "annual", 5),
            self.fmp_client.get_balance_sheet(ticker, "annual", 5),
            self.fmp_client.get_cash_flow_statement(ticker, "annual", 5),
            self.fmp_client.get_key_metrics(ticker, "annual", 5),
            self.fmp_client.get_financial_ratios(ticker, "annual", 5)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Extract results (handle exceptions)
        profile = results[0] if not isinstance(results[0], Exception) else {}
        income_statements = results[1] if not isinstance(results[1], Exception) else []
        balance_sheets = results[2] if not isinstance(results[2], Exception) else []
        cash_flows = results[3] if not isinstance(results[3], Exception) else []
        key_metrics = results[4] if not isinstance(results[4], Exception) else []
        ratios = results[5] if not isinstance(results[5], Exception) else []

        return {
            "company_name": profile.get("companyName", ticker),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "description": profile.get("description"),
            "market_cap": profile.get("mktCap"),
            "income_statements": income_statements,
            "balance_sheets": balance_sheets,
            "cash_flows": cash_flows,
            "key_metrics": key_metrics,
            "ratios": ratios
        }

    async def _generate_persona_analysis(
        self,
        ticker: str,
        persona: InvestorPersona,
        financial_data: Dict[str, Any],
        custom_instructions: Optional[str]
    ) -> Dict[str, Any]:
        """
        Generate analysis using specific persona's prompt and style.

        Args:
            ticker: Stock ticker
            persona: Investor persona
            financial_data: Financial data
            custom_instructions: Custom instructions

        Returns:
            dict: AI-generated analysis
        """
        persona_config = self.PERSONAS[persona]

        # Build context from financial data
        context = self._build_financial_context(financial_data)

        # Build the prompt
        prompt = f"""Analyze {financial_data.get('company_name', ticker)} ({ticker}) and provide a comprehensive research report.

{custom_instructions if custom_instructions else ''}

FINANCIAL DATA:
{context}

Use the following template structure:
{persona_config['analysis_template']}

Be thorough, specific, and true to {persona_config['name']}'s investment philosophy.
Section 4.3.3 - REQ-7: IGNORE short-term price movements and quarterly volatility.
Focus on long-term business fundamentals and intrinsic value."""

        # Generate analysis
        response = await self.gemini_client.generate_text(
            prompt=prompt,
            system_instruction=persona_config["system_prompt"]
        )

        return response

    def _build_financial_context(self, data: Dict[str, Any]) -> str:
        """
        Build formatted financial context string for AI.

        Args:
            data: Financial data

        Returns:
            str: Formatted context
        """
        context_parts = []

        # Company overview
        if data.get("description"):
            context_parts.append(f"Company: {data['description'][:500]}")

        if data.get("sector"):
            context_parts.append(f"Sector: {data['sector']}")
            context_parts.append(f"Industry: {data['industry']}")

        # Recent financial highlights
        if data.get("income_statements"):
            recent = data["income_statements"][0] if data["income_statements"] else {}
            context_parts.append(f"\nRecent Year Financials:")
            context_parts.append(f"  Revenue: ${recent.get('revenue', 0):,.0f}")
            context_parts.append(f"  Net Income: ${recent.get('netIncome', 0):,.0f}")
            context_parts.append(f"  EPS: ${recent.get('eps', 0):.2f}")

        # Key metrics
        if data.get("ratios"):
            recent_ratios = data["ratios"][0] if data["ratios"] else {}
            context_parts.append(f"\nKey Ratios:")
            context_parts.append(f"  ROE: {recent_ratios.get('returnOnEquity', 0)*100:.1f}%")
            context_parts.append(f"  P/E: {recent_ratios.get('priceEarningsRatio', 0):.1f}")
            context_parts.append(f"  Debt/Equity: {recent_ratios.get('debtEquityRatio', 0):.2f}")

        return "\n".join(context_parts)

    async def _extract_report_components(
        self,
        analysis: Dict[str, Any],
        persona: InvestorPersona
    ) -> Dict[str, Any]:
        """
        Extract structured components from free-form analysis.

        Args:
            analysis: Generated analysis
            persona: Investor persona

        Returns:
            dict: Structured components
        """
        text = analysis["text"]

        # Extract pros and cons (common across personas)
        pros = self._extract_section(text, ["PROS", "What I Like", "What Charlie and I Like"])
        cons = self._extract_section(text, ["CONS", "What Worries Me", "What Keeps Us Cautious"])

        # Extract other sections
        moat = self._extract_section(text, ["Moat", "Competitive Advantages"])
        valuation = self._extract_section(text, ["Valuation", "Fair Value"])
        risks = self._extract_section(text, ["Risk", "What Could Go Wrong"])

        return {
            "executive_summary": text[:500] + "..." if len(text) > 500 else text,
            "pros": pros,
            "cons": cons,
            "moat_analysis": moat[:1000] if moat else None,
            "valuation_notes": valuation[:1000] if valuation else None,
            "risk_factors": self._extract_bullets(risks) if risks else []
        }

    def _extract_section(self, text: str, headers: List[str]) -> Optional[str]:
        """Extract text section by header."""
        for header in headers:
            if header.upper() in text.upper():
                start = text.upper().find(header.upper())
                # Find next section or end
                end = len(text)
                for next_header in ["##", "\n\n\n"]:
                    pos = text.find(next_header, start + len(header))
                    if pos > 0:
                        end = min(end, pos)
                return text[start:end].strip()
        return None

    def _extract_bullets(self, text: str) -> List[str]:
        """Extract bullet points from text."""
        bullets = []
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith(("-", "•", "*", "✓", "✗")):
                bullets.append(line[1:].strip())
        return bullets[:10]  # Max 10 bullets
