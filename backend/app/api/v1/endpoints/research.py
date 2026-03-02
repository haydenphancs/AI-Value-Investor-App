"""
Deep Research Endpoints — aligned with Swift frontend API layer.

Endpoints (matching iOS APIEndpoint enum):
  POST   /research/generate                    → trigger report generation
  GET    /research/reports/{report_id}/status   → poll progress
  GET    /research/reports/{report_id}          → fetch completed report
  GET    /research/reports                      → list user's reports
  POST   /research/reports/{report_id}/rate     → rate a report
  DELETE /research/reports/{report_id}          → soft-delete
  GET    /research/personas                     → list active personas

iOS sends camelCase via .convertToSnakeCase encoder → backend receives snake_case.
Backend returns snake_case → iOS decodes via .convertFromSnakeCase decoder.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
import asyncio
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, StandardRateLimit
from app.schemas.research import (
    GenerateResearchRequest,
    ResearchGenerationResponse,
    ResearchStatusResponse,
    ResearchReportDetail,
    ResearchReportListItem,
    RateReportRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Trigger Endpoint ─────────────────────────────────────────────────────────


@router.post("/generate", response_model=ResearchGenerationResponse)
async def generate_research_report(
    request: GenerateResearchRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    _rate_limit=StandardRateLimit,
):
    """
    Trigger deep research report generation.
    Validates credits + persona, inserts a 'pending' DB row,
    launches an async background task, and returns immediately.
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
        "key", request.investor_persona
    ).eq("is_active", True).execute()

    if not persona_check.data:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid persona: {request.investor_persona}",
        )

    ticker = request.stock_id.upper()

    # Resolve company name from FMP (non-blocking best-effort)
    company_name = ticker
    try:
        from app.integrations.fmp import get_fmp_client
        fmp = get_fmp_client()
        profile = await fmp.get_company_profile(ticker)
        if profile:
            company_name = profile.get("companyName", ticker)
    except Exception:
        pass

    # Insert pending report row
    report_data = {
        "user_id": user["id"],
        "ticker": ticker,
        "company_name": company_name,
        "investor_persona": request.investor_persona,
        "status": "pending",
        "progress": 0,
        "current_step": "Initializing research...",
    }

    result = supabase.table("research_reports").insert(report_data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create report")

    report = result.data[0]

    # Launch async background task (fire-and-forget)
    asyncio.create_task(
        _run_research_task(
            report["id"], ticker, request.investor_persona, user["id"]
        )
    )

    return ResearchGenerationResponse(
        report_id=report["id"],
        status="pending",
        estimated_seconds=90,
        poll_url=f"/api/v1/research/reports/{report['id']}/status",
    )


# ── Status Polling ───────────────────────────────────────────────────────────


@router.get("/reports/{report_id}/status", response_model=ResearchStatusResponse)
async def get_research_status(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Poll report generation status (frontend calls every ~3s)."""
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


# ── Full Report Retrieval ────────────────────────────────────────────────────


@router.get("/reports/{report_id}", response_model=ResearchReportDetail)
async def get_research_report(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Fetch the full research report. RLS enforced via user_id check."""
    result = supabase.table("research_reports").select("*").eq(
        "id", report_id
    ).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Report not found")

    row = result.data
    # Inject stock_id = ticker so iOS ResearchReportDetail.stockId resolves
    row["stock_id"] = row["ticker"]

    return row


# ── Ticker Report Data for completed research ────────────────────────────────


@router.get("/reports/{report_id}/ticker-report")
async def get_research_ticker_report(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """
    Get the full TickerReportResponse data from a completed research report.
    This endpoint returns the same JSON shape as GET /stocks/{ticker}/report,
    enabling the iOS app to display it in TickerReportView.
    """
    result = supabase.table("research_reports").select(
        "id, status, ticker_report_data"
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Report not found")

    if result.data["status"] != "completed":
        raise HTTPException(status_code=409, detail="Report is not yet completed")

    ticker_report = result.data.get("ticker_report_data")
    if not ticker_report:
        raise HTTPException(
            status_code=404,
            detail="Full ticker report data not available for this report",
        )

    return ticker_report


# ── List User Reports ────────────────────────────────────────────────────────


@router.get("/reports")
async def get_my_reports(
    limit: int = Query(20, le=100),
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Get current user's research reports (lightweight list)."""
    result = supabase.table("research_reports").select(
        "id, ticker, company_name, investor_persona, status, title, "
        "executive_summary, overall_score, fair_value_estimate, progress, "
        "created_at, completed_at, user_rating"
    ).eq("user_id", user["id"]).neq(
        "status", "deleted"
    ).order(
        "created_at", desc=True
    ).limit(limit).execute()

    # Inject stock_id on each row for iOS compatibility
    items = []
    for row in result.data or []:
        row["stock_id"] = row["ticker"]
        items.append(row)

    return items


# ── Rate Report ──────────────────────────────────────────────────────────────


@router.post("/reports/{report_id}/rate")
async def rate_report(
    report_id: str,
    request: RateReportRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Rate a research report (1-5 stars with optional feedback)."""
    update = {"user_rating": request.rating}
    if request.feedback:
        update["user_feedback"] = request.feedback

    supabase.table("research_reports").update(update).eq(
        "id", report_id
    ).eq("user_id", user["id"]).execute()

    return {"message": "Report rated successfully"}


# ── Delete Report ────────────────────────────────────────────────────────────


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Soft-delete a research report (sets status = 'deleted')."""
    supabase.table("research_reports").update({
        "status": "deleted"
    }).eq("id", report_id).eq("user_id", user["id"]).execute()

    return {"message": "Report deleted successfully"}


# ── List Personas ────────────────────────────────────────────────────────────


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


# ── Background Task ──────────────────────────────────────────────────────────


async def _run_research_task(
    report_id: str, ticker: str, persona_key: str, user_id: str
):
    """
    Async background task: runs the full multi-agent research pipeline.
    If anything fails, marks the report as 'failed' with error_message.
    """
    try:
        from app.services.research_service import ResearchService

        service = ResearchService()
        await service.generate_report(report_id, ticker, persona_key, user_id)
    except Exception as e:
        logger.error(f"Research task failed for {report_id}: {e}", exc_info=True)
        try:
            from app.database import get_supabase

            supabase = get_supabase()
            supabase.table("research_reports").update({
                "status": "failed",
                "error_message": str(e)[:500],
                "progress": 0,
            }).eq("id", report_id).execute()
        except Exception:
            logger.error(f"Failed to update error status for {report_id}")
