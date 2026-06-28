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
from fastapi.responses import Response
from supabase import Client
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import json
import logging

from app.api.error_response import (
    ErrorCode,
    error_body_from_exception,
    make_error_response,
)
from app.database import get_supabase
from app.dependencies import (
    get_current_user,
    get_current_user_or_guest,  # TEMP: guest fallback while login UI not built
    StandardRateLimit,
)
from app.schemas.research import (
    GenerateResearchRequest,
    ResearchGenerationResponse,
    ResearchStatusResponse,
    ResearchReportDetail,
    ResearchReportListItem,
    RateReportRequest,
    TrendingAnalysisResponse,
)
from app.config import settings
from app.services.agents.persona_config import PERSONA_KEYS
from app.services.agents.ticker_report_data_collector import (
    patch_wall_street_consensus_live,
)
from app.services.credit_service import CreditService
from app.services.research_reconciliation_service import claim_and_mark_failed
from app.services.ticker_report_cache import (
    current_close_cycle_start,
    patch_legacy_price_action,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Trigger Endpoint ─────────────────────────────────────────────────────────


@router.post("/generate", response_model=ResearchGenerationResponse)
async def generate_research_report(
    request: GenerateResearchRequest,
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
    _rate_limit=StandardRateLimit,
):
    """
    Trigger deep research report generation.
    Validates credits + persona, inserts a 'pending' DB row,
    launches an async background task, and returns immediately.
    """
    # Validate persona exists BEFORE charging — a bad persona key is
    # caller error and should never burn credits.
    #
    # Source of truth is the hardcoded PERSONA_KEYS registry — the same
    # set the research agent dispatches on. Don't gate on agent_personas
    # DB rows: that table is decorative metadata for the iOS persona
    # picker, can be empty in fresh environments, and /personas already
    # serves a hardcoded fallback when it is. Gating /generate on a DB
    # row that /personas papers over would silently break the whole
    # feature whenever the table is unseeded or grants drift.
    if request.investor_persona not in PERSONA_KEYS:
        return make_error_response(
            ErrorCode.INVALID_PERSONA,
            message=f"Unknown persona key: {request.investor_persona!r}",
            details={"persona": request.investor_persona},
        )

    # ── Per-user concurrency cap (pre-charge: a caller error like the persona
    #    check above, so it must NOT burn credits). At most
    #    MAX_CONCURRENT_REPORTS_PER_USER reports may be in flight
    #    (pending/processing) within the current close cycle — e.g. 4 personas
    #    on one ticker, or 1 persona on 4 tickers. The persona-neutral FMP
    #    collection cache (_INFLIGHT, keyed by ticker) keeps a same-ticker
    #    fan-out to ONE fetch, so this only bounds the count. Returns 409 (NOT
    #    429 — iOS swallows 429 bodies) so the cap user_message is surfaced.
    cap = settings.MAX_CONCURRENT_REPORTS_PER_USER
    cycle_start = current_close_cycle_start().isoformat()
    inflight = (
        supabase.table("research_reports")
        .select("id", count="exact")
        .eq("user_id", user["id"])
        .in_("status", ["pending", "processing"])
        .gte("created_at", cycle_start)
        .execute()
    )
    inflight_count = inflight.count or 0
    if inflight_count >= cap:
        return make_error_response(
            ErrorCode.TOO_MANY_CONCURRENT_REPORTS,
            status_code=409,
            user_message=(
                f"You can run up to {cap} analyses at once — "
                f"wait for one to finish, then try again."
            ),
            message=f"user {user['id']} has {inflight_count} reports in flight (cap {cap})",
            details={"in_flight": inflight_count, "max": cap},
        )

    # ── Global admission backstop (fast-fail under overload). Beyond the
    #    per-user cap above, bound the TOTAL reports in flight across ALL users
    #    in this close cycle so a multi-user burst can't pile unbounded agent
    #    runs onto the single event loop (protects request latency + RAM). Like
    #    the per-user cap this is pre-charge (no credits burned on rejection)
    #    and returns 409 (NOT 429) so the SYSTEM_BUSY user_message reaches iOS.
    #    The real pacing is the agent-run semaphore in research_service; this
    #    just sheds load past a safe backlog instead of accepting it.
    global_cap = settings.MAX_GLOBAL_INFLIGHT_REPORTS
    if global_cap > 0:
        global_inflight = (
            supabase.table("research_reports")
            .select("id", count="exact")
            .in_("status", ["pending", "processing"])
            .gte("created_at", cycle_start)
            .execute()
        )
        global_count = global_inflight.count or 0
        if global_count >= global_cap:
            return make_error_response(
                ErrorCode.SYSTEM_BUSY,
                status_code=409,
                message=(
                    f"global in-flight {global_count} >= cap {global_cap}"
                ),
                details={"in_flight": global_count, "max": global_cap},
            )

    # Atomic 5-credit charge. The Postgres function returns the new
    # `remaining` balance, or NULL when the user has fewer than 5
    # credits available. NULL → INSUFFICIENT_CREDITS, no row was
    # mutated (no race window between check and decrement).
    credit_service = CreditService()
    new_remaining = credit_service.try_charge(
        user["id"], CreditService.DEEP_RESEARCH_COST
    )
    if new_remaining is None:
        return make_error_response(
            ErrorCode.INSUFFICIENT_CREDITS,
            message=(
                f"User has fewer than {CreditService.DEEP_RESEARCH_COST} "
                f"credits remaining"
            ),
            details={
                "user_id": user["id"],
                "required": CreditService.DEEP_RESEARCH_COST,
            },
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

    # Insert pending report row. credits_charged stamps how much we
    # debited so a future tier change can't lose track of historical
    # billing; is_refunded starts False and is flipped by
    # _run_research_task on failure.
    report_data = {
        "user_id": user["id"],
        "ticker": ticker,
        "company_name": company_name,
        "industry": industry,
        "investor_persona": request.investor_persona,
        "status": "pending",
        "progress": 0,
        "current_step": "Initializing research...",
        "credits_charged": CreditService.DEEP_RESEARCH_COST,
        "is_refunded": False,
    }

    result = supabase.table("research_reports").insert(report_data).execute()
    if not result.data:
        # DB insert failed AFTER we charged credits — refund immediately
        # so the user isn't out 5 credits for a row that never existed.
        credit_service.refund(user["id"], CreditService.DEEP_RESEARCH_COST)
        return make_error_response(
            ErrorCode.REPORT_GENERATION_FAILED,
            message="Failed to insert research_reports row",
            details={"ticker": ticker, "step": "db_insert"},
        )

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
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
):
    """Poll report generation status (frontend calls every ~3s).

    `error_message` may be either a plain string (legacy rows) or a
    JSON-encoded structured error blob written by `_run_research_task`.
    We split it into `error_code` + a human `error_message` here so iOS
    sees a stable contract even though the DB column is a single TEXT.
    """
    result = supabase.table("research_reports").select(
        "id, status, progress, current_step, error_message, estimated_time_remaining"
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Report not found")

    raw_error = result.data.get("error_message")
    error_code, human_error = _split_structured_error(raw_error)

    return ResearchStatusResponse(
        report_id=result.data["id"],
        status=result.data["status"],
        progress=result.data.get("progress", 0),
        current_step=result.data.get("current_step"),
        error_message=human_error,
        error_code=error_code,
        estimated_time_remaining=result.data.get("estimated_time_remaining"),
    )


def _split_structured_error(
    raw: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Decode `error_message` into (error_code, human_message).

    Phase 3 stores failures as JSON like
    `{"error_code": "...", "user_message": "..."}` so iOS gets a
    machine-readable code without needing a new DB column. Legacy rows
    that pre-date this change are plain strings — we pass them through
    as `(None, raw)` so the iOS UI keeps showing whatever was recorded.
    """
    if not raw:
        return None, None
    if not isinstance(raw, str):
        return None, str(raw)
    stripped = raw.strip()
    if not stripped.startswith("{"):
        return None, raw
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None, raw
    if not isinstance(parsed, dict):
        return None, raw
    code = parsed.get("error_code")
    msg = (
        parsed.get("user_message")
        or parsed.get("message")
        or raw
    )
    return (
        code if isinstance(code, str) else None,
        msg if isinstance(msg, str) else raw,
    )


# ── Full Report Retrieval ────────────────────────────────────────────────────


@router.get("/reports/{report_id}", response_model=ResearchReportDetail)
async def get_research_report(
    report_id: str,
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
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
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
):
    """
    Get the full TickerReportResponse data from a completed research report.
    This endpoint returns the same JSON shape as GET /stocks/{ticker}/report,
    enabling the iOS app to display it in TickerReportView.

    Errors return the structured `APIErrorResponse` shape so iOS can
    distinguish "still generating" (REPORT_NOT_READY → poll again)
    from "doesn't exist" (REPORT_NOT_FOUND).
    """
    result = supabase.table("research_reports").select(
        "id, status, ticker, ticker_report_data"
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        return make_error_response(
            ErrorCode.REPORT_NOT_FOUND,
            message=f"No research_reports row for id={report_id}",
            details={"report_id": report_id},
        )

    if result.data["status"] != "completed":
        return make_error_response(
            ErrorCode.REPORT_NOT_READY,
            message=f"Report status={result.data['status']!r}",
            details={
                "report_id": report_id,
                "status": result.data["status"],
            },
        )

    ticker_report = result.data.get("ticker_report_data")
    if not ticker_report:
        return make_error_response(
            ErrorCode.DATA_INCOMPLETE,
            message="ticker_report_data column was empty for completed report",
            details={"report_id": report_id, "step": "db_lookup"},
        )

    # Overlay live Wall Street Consensus so saved reports match what
    # `/stocks/{ticker}/analyst-analysis` and `/stocks/{ticker}/holders`
    # are showing right now. Best-effort: silently no-ops on FMP /
    # service failure.
    ticker = result.data.get("ticker") or ""
    if ticker:
        ticker_report = await patch_wall_street_consensus_live(
            ticker_report, ticker,
        )
    return patch_legacy_price_action(ticker_report)


# ── Detailed-Analysis PDF ────────────────────────────────────────────────────


@router.get("/reports/{report_id}/pdf")
async def get_research_report_pdf(
    report_id: str,
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
):
    """Stream the detailed-analysis PDF for a completed report.

    Ownership is re-checked per request (the bucket is private and served only
    through this proxy). Returns the structured error contract so iOS can tell
    "still preparing" (REPORT_NOT_READY) from "doesn't exist" (REPORT_NOT_FOUND).
    """
    result = supabase.table("research_reports").select(
        "id, pdf_path, pdf_status"
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        return make_error_response(
            ErrorCode.REPORT_NOT_FOUND,
            message=f"No research_reports row for id={report_id}",
            details={"report_id": report_id},
        )

    row = result.data
    if row.get("pdf_status") != "ready" or not row.get("pdf_path"):
        return make_error_response(
            ErrorCode.REPORT_NOT_READY,
            message=f"PDF status={row.get('pdf_status')!r}",
            details={"report_id": report_id, "pdf_status": row.get("pdf_status")},
        )

    try:
        pdf_bytes = await asyncio.to_thread(
            supabase.storage.from_("research-pdfs").download, row["pdf_path"]
        )
    except Exception as e:
        logger.error("PDF download failed for %s: %s", report_id, e)
        return make_error_response(
            ErrorCode.DATA_INCOMPLETE,
            message="Stored PDF could not be retrieved",
            details={"report_id": report_id},
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="caydex_{report_id}.pdf"'},
    )


@router.post("/reports/{report_id}/pdf/regenerate")
async def regenerate_research_report_pdf(
    report_id: str,
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
):
    """Generate (or re-generate) the PDF inline. Backfills reports created
    before the PDF feature and recovers pdf_status='failed'. Returns the
    resulting {pdf_status}."""
    result = supabase.table("research_reports").select(
        "id, status"
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        return make_error_response(
            ErrorCode.REPORT_NOT_FOUND,
            message=f"No research_reports row for id={report_id}",
            details={"report_id": report_id},
        )
    if result.data["status"] != "completed":
        return make_error_response(
            ErrorCode.REPORT_NOT_READY,
            message=f"Report status={result.data['status']!r}",
            details={"report_id": report_id, "status": result.data["status"]},
        )

    await _generate_report_pdf(report_id, user["id"])

    row = supabase.table("research_reports").select("pdf_status").eq(
        "id", report_id
    ).eq("user_id", user["id"]).single().execute()
    status = (row.data or {}).get("pdf_status", "failed")
    if status != "ready":
        return make_error_response(
            ErrorCode.DATA_INCOMPLETE,
            message="PDF generation did not complete",
            details={"report_id": report_id, "pdf_status": status},
        )
    return {"report_id": report_id, "pdf_status": status}


# ── List User Reports ────────────────────────────────────────────────────────


@router.get("/reports")
async def get_my_reports(
    limit: int = Query(20, le=100),
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
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
        "current_step, created_at, completed_at, user_rating, is_refunded, "
        "pdf_status"
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
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
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
    user: dict = Depends(get_current_user_or_guest),  # TEMP: guest fallback
    supabase: Client = Depends(get_supabase),
):
    """Soft-delete a research report (sets status = 'deleted')."""
    supabase.table("research_reports").update({
        "status": "deleted"
    }).eq("id", report_id).eq("user_id", user["id"]).execute()

    return {"message": "Report deleted successfully"}


# ── List Personas ────────────────────────────────────────────────────────────

# Hardcoded fallback that mirrors the iOS AnalysisPersona.allCases keys
# (warren_buffett / cathie_wood / peter_lynch / bill_ackman / michael_burry).
# Returned when the agent_personas Supabase query fails so the iOS app keeps working
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
    {
        "id": "fallback-michael_burry",
        "key": "michael_burry",
        "name": "Michael Burry",
        "tagline": "Contrarian Deep Value",
        "description": (
            "A contrarian skeptic who hunts deeply undervalued, out-of-favor "
            "companies with a large margin of safety, scrutinizes the balance "
            "sheet for hidden risk, and is wary of hype, crowded trades, and "
            "expensive darlings."
        ),
        "icon_name": "magnifyingglass",
        "accent_color": "DC2626",
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
        # gets the five core personas. Common when production DB hasn't
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


async def _generate_report_pdf(report_id: str, user_id: str) -> None:
    """Eagerly render + store the detailed-analysis PDF after a report
    completes. Best-effort: any failure is logged and recorded as
    pdf_status='failed' but NEVER propagates — the report is already
    'completed' and must not be rolled back."""
    from datetime import datetime, timezone

    from app.database import get_supabase

    supabase = get_supabase()
    try:
        from app.services.pdf_report_service import generate_and_store_pdf

        supabase.table("research_reports").update(
            {"pdf_status": "pending"}
        ).eq("id", report_id).execute()

        row = supabase.table("research_reports").select(
            "ticker_report_data, fair_value_estimate"
        ).eq("id", report_id).single().execute()
        data = row.data or {}
        ticker_report_data = data.get("ticker_report_data")
        if not ticker_report_data:
            logger.warning("PDF skipped — no ticker_report_data for %s", report_id)
            supabase.table("research_reports").update(
                {"pdf_status": "failed"}
            ).eq("id", report_id).execute()
            return

        path = await generate_and_store_pdf(
            report_id, ticker_report_data, data.get("fair_value_estimate"), user_id
        )
        supabase.table("research_reports").update({
            "pdf_path": path,
            "pdf_status": "ready",
            "pdf_generated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", report_id).execute()
        logger.info("Detailed-analysis PDF ready for %s → %s", report_id, path)
    except Exception as e:
        logger.error(
            "PDF generation failed for %s (report unaffected): %s: %s",
            report_id, type(e).__name__, e, exc_info=True,
        )
        try:
            supabase.table("research_reports").update(
                {"pdf_status": "failed"}
            ).eq("id", report_id).execute()
        except Exception:
            pass


async def _run_research_task(
    report_id: str, ticker: str, persona_key: str, user_id: str
):
    """
    Async background task: runs the full multi-agent research pipeline.

    On failure: marks the report 'failed', persists a structured error
    blob, refunds the 5 credits charged in /generate, and flips
    `is_refunded` so iOS renders the "[Refunded]" chip. This is the
    single refund site — every failure path lands here.
    """
    try:
        from app.services.research_service import ResearchService

        service = ResearchService()
        await service.generate_report(report_id, ticker, persona_key, user_id)

        # Eagerly render the detailed-analysis PDF now that the report is
        # 'completed'. Isolated + best-effort: _generate_report_pdf swallows
        # all its own errors, so a PDF failure never reaches the outer except
        # below (which would wrongly mark the report failed + refund credits).
        await _generate_report_pdf(report_id, user_id)
    except Exception as e:
        # Include the exception type so future debugging shows e.g.
        # "KeyError: profile" instead of just "profile" — the type is
        # what tells you whether it's an FMP miss, a JSON parse, etc.
        logger.error(
            f"Research task failed for {report_id} ({ticker}/{persona_key}): "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        # Build a structured error blob (error_code, user_message,
        # underlying, etc.) and stash it as JSON in error_message.
        # `_split_structured_error` in the status endpoint unpacks it.
        body = error_body_from_exception(
            e,
            ticker=ticker,
            persona=persona_key,
            step="research_task",
            extra_details={"report_id": report_id},
        )
        # Mark failed + refund through the shared claim-then-refund primitive.
        # ONE atomic compare-and-set on `is_refunded` flips the row and
        # decides the refund, so this worker path and the reconciliation
        # sweep can never double-refund the same report. (Note:
        # ResearchService.generate_report may have already stamped
        # status='failed' before re-raising — 'failed' is a claimable status,
        # so the refund still fires exactly once.)
        await claim_and_mark_failed(report_id, body)
