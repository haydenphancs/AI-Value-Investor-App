"""
Deep Research Endpoints
Handles AI-generated company analysis with investor personas.
Requirements: Section 4.3 - Deep Research Agents (Investor Replication)
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from supabase import Client
from pydantic import BaseModel
from typing import Optional
import logging

from app.database import get_supabase
from app.dependencies import (
    get_current_user,
    check_deep_research_limit,
    StandardRateLimit
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
# =======================

class ResearchRequest(BaseModel):
    stock_id: str
    investor_persona: str  # buffett, ackman, munger, lynch, graham
    analysis_period: Optional[str] = "annual"


class ResearchResponse(BaseModel):
    id: str
    stock_id: str
    investor_persona: str
    status: str
    title: Optional[str]
    executive_summary: Optional[str]
    created_at: str


# Endpoints
# =========

@router.post("/generate", response_model=ResearchResponse)
async def generate_research_report(
    request: ResearchRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(check_deep_research_limit),
    supabase: Client = Depends(get_supabase),
    _rate_limit=StandardRateLimit
):
    """
    Generate a deep research report for a stock.
    Section 4.3.3 - REQ-6: Use large context window model (Gemini)
    Section 4.3.3 - REQ-7: Ignore short-term price volatility
    Section 5.1 - Must complete within 30 seconds

    Args:
        request: Research request parameters
        background_tasks: FastAPI background tasks
        user: Current user data (with quota check)
        supabase: Supabase client

    Returns:
        ResearchResponse: Report metadata (processing in background)
    """
    # Validate investor persona
    valid_personas = ["buffett", "ackman", "munger", "lynch", "graham"]
    if request.investor_persona not in valid_personas:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid investor persona. Choose from: {', '.join(valid_personas)}"
        )

    # Create report record
    report_data = {
        "user_id": user["id"],
        "stock_id": request.stock_id,
        "investor_persona": request.investor_persona,
        "analysis_period": request.analysis_period,
        "status": "pending"
    }

    result = supabase.table("deep_research_reports").insert(report_data).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create report")

    report = result.data[0]

    # Increment user's usage counter
    supabase.table("users").update({
        "monthly_deep_research_used": user["monthly_deep_research_used"] + 1
    }).eq("id", user["id"]).execute()

    # Schedule background task to generate report
    background_tasks.add_task(
        generate_report_task,
        report["id"],
        request.stock_id,
        request.investor_persona
    )

    return ResearchResponse(**report)


@router.get("/reports")
async def get_my_reports(
    limit: int = 20,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get user's research reports.

    Args:
        limit: Number of reports to return
        user: Current user data
        supabase: Supabase client

    Returns:
        list: User's research reports
    """
    result = supabase.table("deep_research_reports").select(
        """
        id, stock_id, investor_persona, status, title,
        executive_summary, created_at, completed_at, user_rating,
        stock:stocks(ticker, company_name, logo_url)
        """
    ).eq("user_id", user["id"]).is_("deleted_at", "null").order(
        "created_at", desc=True
    ).limit(limit).execute()

    return result.data


@router.get("/reports/{report_id}")
async def get_report_detail(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get full research report details.

    Args:
        report_id: Report ID
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Full report data
    """
    result = supabase.table("deep_research_reports").select(
        """
        *,
        stock:stocks(*)
        """
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Report not found")

    # Increment view count
    supabase.table("deep_research_reports").update({
        "views_count": result.data["views_count"] + 1
    }).eq("id", report_id).execute()

    return result.data


@router.post("/reports/{report_id}/rate")
async def rate_report(
    report_id: str,
    rating: int,
    feedback: Optional[str] = None,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Rate a research report (1-5 stars).

    Args:
        report_id: Report ID
        rating: Rating (1-5)
        feedback: Optional feedback text
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Success message
    """
    if not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    update_data = {"user_rating": rating}
    if feedback:
        update_data["user_feedback"] = feedback

    supabase.table("deep_research_reports").update(update_data).eq(
        "id", report_id
    ).eq("user_id", user["id"]).execute()

    return {"message": "Report rated successfully"}


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Soft delete a research report.

    Args:
        report_id: Report ID
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Success message
    """
    supabase.table("deep_research_reports").update({
        "deleted_at": "now()"
    }).eq("id", report_id).eq("user_id", user["id"]).execute()

    return {"message": "Report deleted successfully"}


# Background Task Function
# ========================

async def generate_report_task(
    report_id: str,
    stock_id: str,
    investor_persona: str
):
    """
    Background task to generate research report.
    This will call the research service to generate the actual report.

    Args:
        report_id: Report ID
        stock_id: Stock ID
        investor_persona: Investor persona to use
    """
    try:
        # Import here to avoid circular dependencies
        from app.services.research_service import ResearchService
        from app.integrations.gemini import GeminiClient

        # Initialize services
        gemini_client = GeminiClient()
        research_service = ResearchService(gemini_client)

        # Generate report
        await research_service.generate_report(
            report_id=report_id,
            stock_id=stock_id,
            investor_persona=investor_persona
        )

    except Exception as e:
        logger.error(f"Research report generation failed: {e}", exc_info=True)

        # Update report status to failed
        from app.database import get_supabase
        supabase = get_supabase()
        supabase.table("deep_research_reports").update({
            "status": "failed",
            "error_message": str(e)
        }).eq("id", report_id).execute()
