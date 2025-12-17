"""
Research Service
Business logic for generating deep research reports with investor personas.
Requirements: Section 4.3 - Deep Research Agents
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
import asyncio

from supabase import Client

from app.agents.research_agent import ResearchAgent
from app.schemas.common import InvestorPersona, ReportStatus
from app.config import settings

logger = logging.getLogger(__name__)


class ResearchService:
    """
    Service for generating AI-powered deep research reports.
    Section 4.3 - Investor persona-based company analysis.
    """

    def __init__(
        self,
        supabase: Client,
        research_agent: Optional[ResearchAgent] = None
    ):
        """
        Initialize research service.

        Args:
            supabase: Supabase client
            research_agent: Research agent
        """
        self.supabase = supabase
        self.research_agent = research_agent or ResearchAgent()
        logger.info("ResearchService initialized")

    async def generate_report(
        self,
        report_id: str,
        stock_id: str,
        investor_persona: InvestorPersona,
        analysis_period: str = "annual",
        custom_instructions: Optional[str] = None
    ) -> str:
        """
        Generate deep research report (background task).
        Section 4.3.1 - Deep Research Agents
        Section 5.1 - Must complete within 30 seconds (timeout)

        Args:
            report_id: Report ID (pre-created)
            stock_id: Stock ID
            investor_persona: Investor persona
            analysis_period: Analysis period
            custom_instructions: Optional custom instructions

        Returns:
            str: Report ID

        Example:
            report_id = await service.generate_report(
                report_id="report-123",
                stock_id="stock-456",
                investor_persona=InvestorPersona.BUFFETT
            )
        """
        try:
            logger.info(f"Generating report {report_id} for stock {stock_id} using {investor_persona.value}")

            # Update status to processing
            self._update_report_status(report_id, ReportStatus.PROCESSING)

            # Get stock details
            stock = self.supabase.table("stocks").select("*").eq("id", stock_id).single().execute()

            if not stock.data:
                raise ValueError(f"Stock {stock_id} not found")

            ticker = stock.data["ticker"]
            company_name = stock.data["company_name"]

            # Generate report with timeout (Section 5.1 - 30 second requirement)
            try:
                report_data = await asyncio.wait_for(
                    self.research_agent.generate_research_report(
                        ticker=ticker,
                        persona=investor_persona,
                        analysis_period=analysis_period,
                        custom_instructions=custom_instructions
                    ),
                    timeout=settings.DEEP_RESEARCH_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.error(f"Report generation timed out after {settings.DEEP_RESEARCH_TIMEOUT_SECONDS}s")
                self._update_report_status(
                    report_id,
                    ReportStatus.FAILED,
                    error_message="Report generation timed out. Please try again."
                )
                raise

            # Update report with generated content
            update_data = {
                "status": ReportStatus.COMPLETED.value,
                "title": f"{company_name} Analysis - {report_data['persona_name']} Style",
                "executive_summary": report_data.get("executive_summary"),
                "pros": report_data.get("pros"),
                "cons": report_data.get("cons"),
                "moat_analysis": report_data.get("moat_analysis"),
                "valuation_notes": report_data.get("valuation_notes"),
                "risk_factors": report_data.get("risk_factors"),
                "investment_thesis": report_data.get("investment_thesis"),
                "full_report": report_data.get("full_report"),
                "report_metadata": {
                    "persona_emoji": report_data.get("persona_emoji"),
                    "analysis_period": analysis_period
                },
                "generation_time_seconds": report_data.get("generation_time_seconds"),
                "tokens_used": report_data.get("tokens_used"),
                "cost_usd": self._estimate_cost(report_data.get("tokens_used", 0)),
                "completed_at": datetime.utcnow().isoformat()
            }

            self.supabase.table("deep_research_reports").update(update_data).eq(
                "id", report_id
            ).execute()

            logger.info(f"Report {report_id} completed successfully")

            return report_id

        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)

            # Update status to failed
            self._update_report_status(
                report_id,
                ReportStatus.FAILED,
                error_message=str(e)
            )

            raise

    def _update_report_status(
        self,
        report_id: str,
        status: ReportStatus,
        error_message: Optional[str] = None
    ):
        """
        Update report status.

        Args:
            report_id: Report ID
            status: New status
            error_message: Optional error message
        """
        try:
            update_data = {"status": status.value}

            if error_message:
                update_data["error_message"] = error_message

            self.supabase.table("deep_research_reports").update(update_data).eq(
                "id", report_id
            ).execute()

        except Exception as e:
            logger.error(f"Failed to update report status: {e}")

    def _estimate_cost(self, tokens_used: int) -> float:
        """
        Estimate cost based on tokens used.
        Gemini pricing (example rates):
        - Input: $0.00025 / 1K tokens
        - Output: $0.0005 / 1K tokens
        Assuming 50/50 split

        Args:
            tokens_used: Total tokens

        Returns:
            float: Estimated cost in USD
        """
        if not tokens_used:
            return 0.0

        # Rough estimate: $0.000375 per 1K tokens (average)
        cost_per_1k = 0.000375
        return (tokens_used / 1000) * cost_per_1k

    async def get_report_by_id(
        self,
        report_id: str,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get report by ID with access check.

        Args:
            report_id: Report ID
            user_id: User ID (for access control)

        Returns:
            dict: Report data or None
        """
        try:
            result = self.supabase.table("deep_research_reports").select(
                "*,stock:stocks(*)"
            ).eq("id", report_id).eq("user_id", user_id).single().execute()

            if result.data:
                # Increment view count
                self.supabase.table("deep_research_reports").update({
                    "views_count": result.data["views_count"] + 1
                }).eq("id", report_id).execute()

            return result.data

        except Exception as e:
            logger.error(f"Failed to get report: {e}")
            return None

    async def get_user_reports(
        self,
        user_id: str,
        limit: int = 20,
        status: Optional[ReportStatus] = None
    ) -> List[Dict[str, Any]]:
        """
        Get user's reports.

        Args:
            user_id: User ID
            limit: Maximum results
            status: Optional status filter

        Returns:
            list: Reports
        """
        try:
            query = self.supabase.table("deep_research_reports").select(
                "id,stock_id,investor_persona,status,title,executive_summary,"
                "created_at,completed_at,user_rating,stock:stocks(ticker,company_name,logo_url)"
            ).eq("user_id", user_id).is_("deleted_at", "null")

            if status:
                query = query.eq("status", status.value)

            query = query.order("created_at", desc=True).limit(limit)

            result = query.execute()

            return result.data

        except Exception as e:
            logger.error(f"Failed to get user reports: {e}")
            return []
