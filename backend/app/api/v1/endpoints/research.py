"""
Deep Research Endpoints
Frontend: POST /research/generate, GET /research/reports/{id}/status,
          GET /research/reports/{id}, GET /research/reports,
          POST /research/reports/{id}/rate, DELETE /research/reports/{id},
          GET /research/personas
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
import asyncio
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, StandardRateLimit
from app.schemas.research import (
    GenerateResearchRequest, ResearchGenerationResponse,
    ResearchStatusResponse, RateReportRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate", response_model=ResearchGenerationResponse)
async def generate_research_report(
    request: GenerateResearchRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    _rate_limit=StandardRateLimit,
):
    """
    Generate deep research report. Creates DB row, launches async task,
    returns immediately for frontend polling.
    """
    # Check credits
    credits = supabase.table("user_credits").select(
        "remaining"
    ).eq("user_id", user["id"]).single().execute()

    if not credits.data or credits.data["remaining"] < 1:
        raise HTTPException(
            status_code=403,
            detail="Insufficient credits. Upgrade your tier or wait for monthly reset.",
        )

    # Validate persona exists
    persona_check = supabase.table("agent_personas").select("key").eq(
        "key", request.persona
    ).eq("is_active", True).execute()

    if not persona_check.data:
        raise HTTPException(status_code=400, detail=f"Invalid persona: {request.persona}")

    ticker = request.stock_id.upper()

    # Fetch company name from FMP
    company_name = ticker
    try:
        from app.integrations.fmp import get_fmp_client
        fmp = get_fmp_client()
        profile = await fmp.get_company_profile(ticker)
        if profile:
            company_name = profile.get("companyName", ticker)
    except Exception:
        pass

    # Create report row
    report_data = {
        "user_id": user["id"],
        "ticker": ticker,
        "company_name": company_name,
        "investor_persona": request.persona,
        "status": "pending",
        "progress": 0,
        "current_step": "Initializing research...",
    }

    result = supabase.table("research_reports").insert(report_data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create report")

    report = result.data[0]

    # Launch async background task
    asyncio.create_task(
        _run_research_task(report["id"], ticker, request.persona, user["id"])
    )

    return ResearchGenerationResponse(
        report_id=report["id"],
        status="pending",
        estimated_seconds=60,
        poll_url=f"/api/v1/research/reports/{report['id']}/status",
    )


@router.get("/reports/{report_id}/status", response_model=ResearchStatusResponse)
async def get_research_status(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Poll research report status (called every 3s by frontend)."""
    result = supabase.table("research_reports").select(
        "id, status, progress, current_step, error_message, estimated_time_remaining"
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Report not found")

    return ResearchStatusResponse(
        report_id=result.data["id"],
        status=result.data["status"],
        progress=result.data.get("progress", 0),
        current_step=result.data.get("current_step"),
        error_message=result.data.get("error_message"),
        estimated_time_remaining=result.data.get("estimated_time_remaining"),
    )


@router.get("/reports/{report_id}")
async def get_research_report(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Get full research report detail."""
    result = supabase.table("research_reports").select("*").eq(
        "id", report_id
    ).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Report not found")

    return result.data


@router.get("/reports")
async def get_my_reports(
    limit: int = Query(20, le=100),
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Get current user's research reports."""
    result = supabase.table("research_reports").select(
        "id, ticker, company_name, investor_persona, status, title, "
        "executive_summary, created_at, completed_at, user_rating"
    ).eq("user_id", user["id"]).order(
        "created_at", desc=True
    ).limit(limit).execute()

    return result.data


@router.post("/reports/{report_id}/rate")
async def rate_report(
    report_id: str,
    request: RateReportRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Rate a research report (1-5 stars)."""
    if not 1 <= request.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    update = {"user_rating": request.rating}
    if request.feedback:
        update["user_feedback"] = request.feedback

    supabase.table("research_reports").update(update).eq(
        "id", report_id
    ).eq("user_id", user["id"]).execute()

    return {"message": "Report rated successfully"}


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Soft-delete a research report."""
    supabase.table("research_reports").update({
        "status": "deleted"
    }).eq("id", report_id).eq("user_id", user["id"]).execute()

    return {"message": "Report deleted successfully"}


@router.get("/personas")
async def get_personas(
    supabase: Client = Depends(get_supabase),
):
    """Get all active investor personas (no auth required)."""
    result = supabase.table("agent_personas").select(
        "id, key, name, title, tagline, style, description, "
        "key_principles, accent_color, icon_name, focus, famous_quotes, is_active"
    ).eq("is_active", True).execute()

    return result.data


# Background task
async def _run_research_task(
    report_id: str, ticker: str, persona: str, user_id: str
):
    """Async background task: generate research report, update DB, decrement credits."""
    try:
        from app.services.research_service import ResearchService
        service = ResearchService()
        await service.generate_report(report_id, ticker, persona, user_id)
    except Exception as e:
        logger.error(f"Research task failed for {report_id}: {e}", exc_info=True)
        try:
            from app.database import get_supabase
            supabase = get_supabase()
            supabase.table("research_reports").update({
                "status": "failed",
                "error_message": str(e),
                "progress": 0,
            }).eq("id", report_id).execute()
        except Exception:
            pass
