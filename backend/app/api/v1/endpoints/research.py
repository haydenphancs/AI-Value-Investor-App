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
from typing import List, Optional
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
    TrendingAnalysisResponse,
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

    # Resolve company name + industry from FMP (non-blocking best-effort).
    # Industry is surfaced on the Reports list card ("TSLA • Automotive").
    company_name = ticker
    industry: Optional[str] = None
    try:
        from app.integrations.fmp import get_fmp_client
        fmp = get_fmp_client()
        profile = await fmp.get_company_profile(ticker)
        if profile:
            company_name = profile.get("companyName", ticker)
            industry = profile.get("industry") or profile.get("sector")
    except Exception:
        pass

    # Insert pending report row
    report_data = {
        "user_id": user["id"],
        "ticker": ticker,
        "company_name": company_name,
        "industry": industry,
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
    """Get current user's research reports (lightweight list).

    `industry` and `current_step` are surfaced so the iOS Reports tab
    card can render the industry subtitle and the live progress text
    while a report is in-flight.
    """
    result = supabase.table("research_reports").select(
        "id, ticker, company_name, industry, investor_persona, status, title, "
        "executive_summary, overall_score, fair_value_estimate, progress, "
        "current_step, created_at, completed_at, user_rating"
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

# Hardcoded fallback that mirrors the iOS AnalysisPersona.allCases keys
# (warren_buffett / cathie_wood / peter_lynch / bill_ackman). Returned when
# the agent_personas Supabase query fails so the iOS app keeps working
# instead of falling back to its own offline defaults. Field names are
# snake_case to match the iOS BackendPersona CodingKeys.
_FALLBACK_PERSONAS: List[dict] = [
    {
        "id": "fallback-warren_buffett",
        "key": "warren_buffett",
        "name": "Warren Buffett",
        "tagline": "Safe, Long-term Value",
        "description": (
            "Focuses on fundamental value, strong moats, consistent earnings, "
            "and long-term competitive advantages. Ideal for conservative investors."
        ),
        "icon_name": "building.columns.fill",
        "accent_color": "3B82F6",
        "is_active": True,
    },
    {
        "id": "fallback-cathie_wood",
        "key": "cathie_wood",
        "name": "Cathie Wood",
        "tagline": "Disruptive Innovation",
        "description": (
            "Emphasizes disruptive innovation, emerging technologies, and "
            "high-growth potential companies that could reshape industries."
        ),
        "icon_name": "bolt.fill",
        "accent_color": "A855F7",
        "is_active": True,
    },
    {
        "id": "fallback-peter_lynch",
        "key": "peter_lynch",
        "name": "Peter Lynch",
        "tagline": "Growth at Value",
        "description": (
            "Looks for growth at a reasonable price (GARP), with focus on "
            "companies you understand and can spot in everyday life."
        ),
        "icon_name": "chart.line.uptrend.xyaxis",
        "accent_color": "06B6D4",
        "is_active": True,
    },
    {
        "id": "fallback-bill_ackman",
        "key": "bill_ackman",
        "name": "Bill Ackman",
        "tagline": "Activist Value",
        "description": (
            "Takes concentrated positions in high-quality businesses, uses "
            "activist strategies to unlock value, and focuses on companies "
            "with durable competitive advantages."
        ),
        "icon_name": "megaphone.fill",
        "accent_color": "F97316",
        "is_active": True,
    },
]


@router.get("/personas")
async def get_personas(
    supabase: Client = Depends(get_supabase),
):
    """Get all active investor personas (no auth required).

    Resilient to DB failures: if the Supabase query throws (missing
    column, RLS deny, network blip), the endpoint logs the underlying
    error verbatim and returns the static fallback list so the iOS app
    keeps rendering valid persona keys instead of seeing a 500.

    The SELECT lists only the 8 columns iOS actually consumes (matches
    BackendPersona Decodable in ResearchModels.swift). Don't add
    columns here unless iOS reads them — every extra column widens the
    "column does not exist" failure surface on production.
    """
    try:
        result = supabase.table("agent_personas").select(
            "id, key, name, tagline, description, "
            "icon_name, accent_color, is_active"
        ).eq("is_active", True).execute()

        if result.data:
            return result.data

        # Table reachable but empty — log + serve fallback so iOS still
        # gets the four core personas. Common when production DB hasn't
        # been seeded yet.
        logger.warning(
            "agent_personas query returned no active rows — serving "
            "hardcoded fallback list. Seed the table to make this go away."
        )
        return _FALLBACK_PERSONAS

    except Exception as e:
        # Verbose logging so Railway logs show the real cause (missing
        # column, RLS, etc.) rather than a generic 500.
        logger.error(
            f"agent_personas query failed: {type(e).__name__}: {e} — "
            f"serving hardcoded fallback list",
            exc_info=True,
        )
        return _FALLBACK_PERSONAS


# ── Trending Analyses ────────────────────────────────────────────────────────


@router.get("/trending", response_model=List[TrendingAnalysisResponse])
async def get_trending_analyses():
    """
    Get trending sectors based on recent research activity.
    Aggregates the last 30 days of completed reports, grouped by sector.
    Returns the top sectors with their most-researched companies.
    Public endpoint — no auth required.
    """
    from app.services.trending_service import TrendingService

    service = TrendingService()
    themes = await service.get_trending()

    # Strip internal `raw_count` field before returning
    return [
        {k: v for k, v in theme.items() if k != "raw_count"}
        for theme in themes
    ]


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
        # Include the exception type so future debugging shows e.g.
        # "KeyError: profile" instead of just "profile" — the type is
        # what tells you whether it's an FMP miss, a JSON parse, etc.
        logger.error(
            f"Research task failed for {report_id} ({ticker}/{persona_key}): "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        try:
            from app.database import get_supabase

            supabase = get_supabase()
            supabase.table("research_reports").update({
                "status": "failed",
                "error_message": f"{type(e).__name__}: {str(e)[:450]}",
                "progress": 0,
            }).eq("id", report_id).execute()
        except Exception:
            logger.error(f"Failed to update error status for {report_id}")
