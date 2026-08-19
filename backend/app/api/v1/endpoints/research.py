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

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from supabase import Client
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import json
import logging
import uuid

from app.api.error_response import (
    ErrorCode,
    error_body_from_exception,
    make_error_response,
)
from app.database import get_supabase
from app.dependencies import (
    get_current_user,
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
from app.services.credit_service import refund_did_not_happen, CreditService, CreditServiceUnavailable
from app.services.research_reconciliation_service import claim_and_mark_failed
from app.services.ticker_report_cache import (
    current_close_cycle_start,
    patch_legacy_price_action,
)

logger = logging.getLogger(__name__)

# Statuses whose credits are still owed back if the row leaves the pipeline. Mirrors
# `research_reconciliation_service._CLAIMABLE_STATUSES` — kept as its own name because the two
# answer different questions (that one is "can the sweep claim this?", this one is "does
# deleting this owe a refund?") and coupling them would tie a UI action to a sweeper detail.
_REFUNDABLE_ON_DELETE = ("pending", "processing", "failed")


def _outcome_name(refunded) -> str:
    """Label for a `refund_ledgered` result in a log line, whatever shape it arrived in."""
    if refunded is None:
        return "rpc_failed"
    return refunded.get("outcome", "unknown") if isinstance(refunded, dict) else "legacy_int"

router = APIRouter()

# Strong references to in-flight report workers. See the `create_task` call in
# `generate_research_report` for why this is not optional.
_RESEARCH_TASKS: set["asyncio.Task"] = set()


# ── Trigger Endpoint ─────────────────────────────────────────────────────────


@router.post("/generate", response_model=ResearchGenerationResponse)
async def generate_research_report(
    request: GenerateResearchRequest,
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
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

    # Atomic credit charge via the unified gate (report cost =
    # CreditService.DEEP_RESEARCH_COST): reset-if-due + atomic check-and-debit + ledger,
    # one round-trip (migration 101). NULL → INSUFFICIENT_CREDITS (no row mutated, no
    # race window) → 402.
    credit_service = CreditService()

    # Every caller here holds an account now, so the credit charge is unconditional.
    #
    # This replaced a guest branch that claimed against `guest_report_budget` (migration 106),
    # keyed on `identity_key(user, x_guest_id)` — a UUID5 of a header the CLIENT supplies. A
    # caller rotating `X-Guest-Id` got a fresh monthly allowance on every request, so the single
    # most expensive operation in the product (~17 Gemini + ~20 FMP calls) was effectively
    # unmetered, with no per-IP limit behind it. Requiring an account replaces that with the
    # credit system, which is FK-bound to a real `public.users` row and cannot be rotated.
    #
    # A free account is seeded 50 credits against a 20-credit report, so a signed-up user gets
    # 2/month where a guest previously got 1.
    try:
        new_remaining = credit_service.precharge(
            user["id"], CreditService.DEEP_RESEARCH_COST,
            reason="report_charge", ref_id=request.stock_id.upper(),
        )
    except CreditServiceUnavailable:
        # Transient Supabase/RPC failure — retryable SYSTEM_BUSY (409), never
        # INSUFFICIENT_CREDITS: a DB blip must not tell a paying user they're broke.
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=409,
            message="spend_credits RPC unavailable (transient)",
            details={"user_id": user["id"], "step": "credit_charge"},
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
    #
    # The id is minted HERE rather than left to the column default. It is the only
    # thing that makes the insert-failure claim below able to name the row THIS call
    # tried to create; see the comment on that claim for what the alternative cost.
    # `research_reports.id` is `uuid DEFAULT gen_random_uuid()`, so supplying it is
    # a no-op for every other path.
    new_report_id = str(uuid.uuid4())
    report_data = {
        "id": new_report_id,
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

    try:
        result = supabase.table("research_reports").insert(report_data).execute()
        insert_ok = bool(result.data)
    except Exception as e:
        # supabase-py raises APIError on a non-2xx insert (RLS / constraint / PostgREST blip)
        # instead of returning empty .data — treat "insert raised" IDENTICALLY to "no rows" so
        # the refund backstop below fires (otherwise the precharge leaks with no research_reports
        # row for the reconciliation sweep to recover, and the raw exception breaks the contract).
        logger.error(
            "research_reports insert raised for %s: %s: %s",
            ticker, type(e).__name__, e, exc_info=True,
        )
        result = None
        insert_ok = False
    if not insert_ok:
        # DB insert failed AFTER we charged credits — refund so the user isn't out the
        # charged credits for a row that never existed.
        #
        # ⚠️ "the row never existed" IS NOT SAFE TO ASSUME, and assuming it double-refunded.
        # `insert` raising says nothing about whether Postgres committed: a Cloudflare edge 520
        # or a read timeout after the commit lands here with the row PRESENT (status='pending',
        # is_refunded=False, credits_charged=20) — which matches every filter the reconciliation
        # sweep uses. The sweep then wins its own per-row claim, knows nothing about this
        # refund, and issues a SECOND one. `refund_credits` finds no un-reversed debit by then,
        # falls through to its granted-first fallback bounded only by the user's CURRENT `used`,
        # and pays out again from unrelated spend — minting up to `credits_charged` credits.
        #
        # So arm the SAME at-most-once guard the sweep uses before refunding. If a row did
        # commit, this claim wins it (is_refunded=True) and the sweep can never touch it; if no
        # row exists the claim matches nothing and we refund directly, as before. Either branch
        # refunds exactly once.
        #
        # ⚠️ THE CLAIM MUST NAME THIS ROW BY ID. It used to select on
        # (user_id, ticker, investor_persona, is_refunded=False, status∈pending/processing,
        # created_at >= cycle_start) — which has no way to distinguish the row this call tried
        # to create from any OTHER live report the same user already has for the same
        # (ticker, persona). Nothing forbids that: the client caps only the NUMBER of
        # concurrent generations (4) and there is no uniqueness constraint, so tapping Generate
        # twice for ORCL/Quality — or the Retry path, which regenerates the same pair — leaves
        # two in-flight rows. A failed second insert then claimed the FIRST, HEALTHY row:
        # marked it failed + is_refunded, so its worker's conditional completion write became a
        # no-op and the report was silently dropped. And because the refund below pays exactly
        # one DEEP_RESEARCH_COST regardless of how many rows the UPDATE matched, a two-row match
        # returned 20 of the 40 charged. Net: 40 spent, 20 back, zero reports.
        try:
            orphan = (
                supabase.table("research_reports")
                .update({"status": "failed", "is_refunded": True})
                .eq("id", new_report_id)
                .eq("user_id", user["id"])
                .eq("is_refunded", False)
                .in_("status", ["pending", "processing"])
                .execute()
            )
            claimed_orphan = bool(getattr(orphan, "data", None))
        except Exception as e:
            # Best-effort. If this fails the sweep may still refund later; that is the
            # pre-existing behaviour and strictly better than not refunding at all here.
            logger.warning(
                "research_reports orphan claim failed for %s (user=%s): %s: %s",
                ticker, user["id"], type(e).__name__, e,
            )
            claimed_orphan = False
        if claimed_orphan:
            logger.warning(
                "research_reports insert reported failure for %s (user=%s) but a row HAD "
                "committed — claimed it for refund so the reconciliation sweep cannot refund "
                "it a second time",
                ticker, user["id"],
            )

        # If the best-effort refund ALSO fails we log a greppable REFUND LEAK marker with
        # everything a human needs to correct it manually (mirrors claim_and_mark_failed's).
        refunded = credit_service.refund_ledgered(
            user["id"], CreditService.DEEP_RESEARCH_COST,
            reason="report_refund", ref_id=ticker,
        )
        # `None` is a transport fault; a business no-op arrives as an outcome. Both leave the
        # user charged, so both belong on this line — checking only `is None` missed the
        # no-op entirely, which is the case migration 142 exists to surface.
        if refund_did_not_happen(refunded):
            logger.error(
                "REFUND LEAK: charged %s credits to user=%s for %s but the "
                "research_reports insert AND the refund both failed (outcome=%s) — no row "
                "for the reconciliation sweep to catch; manual credit correction needed",
                CreditService.DEEP_RESEARCH_COST, user["id"], ticker,
                _outcome_name(refunded),
            )
        return make_error_response(
            ErrorCode.REPORT_GENERATION_FAILED,
            message="Failed to insert research_reports row",
            details={"ticker": ticker, "step": "db_insert"},
        )

    report = result.data[0]

    # Launch the worker. The handle is RETAINED in a module-level set until the task
    # finishes: `asyncio.create_task` keeps only a WEAK reference, so an untracked
    # task can be garbage-collected mid-execution — the documented CPython caveat that
    # `main.py` calls out for the lifespan loops and that `stocks.py` already solves
    # this exact way for the pre-warm. It matters more here than anywhere else in the
    # app: this task owns a report the user has ALREADY been charged 20 credits for,
    # and if it vanishes before `_on_started` stamps `processing_started_at` the row
    # sits `pending` with a NULL start time, which the reconciliation sweep will not
    # touch until `RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS` (~3.2h) has passed.
    task = asyncio.create_task(
        _run_research_task(
            report["id"], ticker, request.investor_persona, user["id"]
        ),
        name=f"research:{report['id']}",
    )
    _RESEARCH_TASKS.add(task)
    task.add_done_callback(_RESEARCH_TASKS.discard)

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
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
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
    ).eq("id", report_id).eq("user_id", user["id"]).maybe_single().execute()

    if not result or not result.data:
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
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
    supabase: Client = Depends(get_supabase),
):
    """Fetch the full research report. RLS enforced via user_id check."""
    result = supabase.table("research_reports").select("*").eq(
        "id", report_id
    ).eq("user_id", user["id"]).maybe_single().execute()

    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Report not found")

    row = result.data
    # Inject stock_id = ticker so iOS ResearchReportDetail.stockId resolves.
    row["stock_id"] = row["ticker"]
    # And guarantee company_name. The insert path defaults it to the ticker
    # (`generate_research` above), but the column is nullable and rows written by any other
    # path — a backfill, a migration, a manual fix — need not carry it. Swift declares
    # `companyName: String` NON-optional, so a null is a decode failure on the screen the user
    # just paid 20 credits to reach. Both fields are `str` (required) on the response model, so
    # a miss here surfaces as our own 500 rather than as a crash on a device we cannot see.
    row["company_name"] = row.get("company_name") or row["ticker"]

    return row


# ── Ticker Report Data for completed research ────────────────────────────────


@router.get("/reports/{report_id}/ticker-report")
async def get_research_ticker_report(
    report_id: str,
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
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
    ).eq("id", report_id).eq("user_id", user["id"]).maybe_single().execute()

    if not result or not result.data:
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
    payload = patch_legacy_price_action(ticker_report)

    # Type-check what we are about to serve, but DO NOT withhold it.
    #
    # This is the user's own paid report and the DEEP pipeline never validated it
    # before storing, so a drift here is our bug, not theirs. Answering
    # DATA_INCOMPLETE would be actively harmful: iOS lists that code among the three
    # "the report genuinely isn't there" outcomes it is allowed to fall through on,
    # and the fall-through is the BILLABLE regeneration — so a schema regression would
    # quietly re-charge 20 credits every time an affected report was opened. Log it
    # loudly instead (this is the only signal that deep-path output has drifted) and
    # serve the payload, exactly as before.
    _validation_probe(payload, ticker, report_id)
    return payload


def _validation_probe(payload: Any, ticker: str, report_id: str) -> None:
    """Log (never raise, never withhold) when a stored report no longer matches the
    response contract. Pure observability for the one path that serves raw JSONB."""
    try:
        from pydantic import ValidationError

        from app.schemas.ticker_report import TickerReportResponse

        try:
            TickerReportResponse(**payload)
        except ValidationError as ve:
            logger.error(
                "STORED REPORT SCHEMA DRIFT: report_id=%s ticker=%s has %d field(s) that "
                "no longer satisfy TickerReportResponse — iOS decodes this shape "
                "all-or-nothing, so the report screen will fail to render: %s",
                report_id, ticker, ve.error_count(), ve,
            )
    except Exception as e:   # the probe must never affect the response
        logger.debug(
            "validation probe skipped for %s: %s: %s", report_id, type(e).__name__, e,
        )


# ── Detailed-Analysis PDF ────────────────────────────────────────────────────


@router.get("/reports/{report_id}/pdf")
async def get_research_report_pdf(
    report_id: str,
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
    supabase: Client = Depends(get_supabase),
):
    """Stream the detailed-analysis PDF for a completed report.

    Ownership is re-checked per request (the bucket is private and served only
    through this proxy). Returns the structured error contract so iOS can tell
    "still preparing" (REPORT_NOT_READY) from "doesn't exist" (REPORT_NOT_FOUND).
    """
    result = supabase.table("research_reports").select(
        "id, pdf_path, pdf_status"
    ).eq("id", report_id).eq("user_id", user["id"]).maybe_single().execute()

    if not result or not result.data:
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
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
    supabase: Client = Depends(get_supabase),
):
    """Generate (or re-generate) the PDF inline. Backfills reports created
    before the PDF feature and recovers pdf_status='failed'. Returns the
    resulting {pdf_status}."""
    result = supabase.table("research_reports").select(
        "id, status"
    ).eq("id", report_id).eq("user_id", user["id"]).maybe_single().execute()

    if not result or not result.data:
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
    ).eq("user_id", user["id"]).maybe_single().execute()
    status = ((row.data if row else None) or {}).get("pdf_status", "failed")
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
    # `ge=1`, not just `le=100`: `?limit=0` returned an empty list, which the Reports
    # tab renders as its first-run "No analyses yet · Generate your first analysis"
    # zero state — to a user who owns twenty paid reports — and a negative value
    # reached PostgREST as `LIMIT -1` and surfaced as an unhandled 500.
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
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
        "credits_charged, pdf_status"
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
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
    supabase: Client = Depends(get_supabase),
):
    """Rate a research report (1-5 stars with optional feedback).

    Reports 404 when the UPDATE matches nothing. It used to answer
    "Report rated successfully" unconditionally, so rating a report that had been
    deleted moments earlier — the Reports list reloads every few seconds and rows are
    reminted on each load — or one belonging to a previous signed-in identity looked
    like it worked and simply did not persist. Silent no-ops on a user-initiated
    mutation are exactly what the client cannot detect.
    """
    update = {"user_rating": request.rating}
    if request.feedback:
        update["user_feedback"] = request.feedback

    result = supabase.table("research_reports").update(update).eq(
        "id", report_id
    ).eq("user_id", user["id"]).execute()

    if not getattr(result, "data", None):
        return make_error_response(
            ErrorCode.REPORT_NOT_FOUND,
            message=f"No research_reports row for id={report_id} owned by this user",
            details={"report_id": report_id},
        )

    return {"message": "Report rated successfully"}


# ── Delete Report ────────────────────────────────────────────────────────────


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: str,
    user: dict = Depends(get_current_user),  # account-only: AI generation costs real money
    supabase: Client = Depends(get_supabase),
):
    """Soft-delete a research report, refunding it if it was still in flight.

    ⚠️ THE REFUND IS NOT OPTIONAL HERE, because 'deleted' is a TERMINAL state no other
    mechanism can reach. `_CLAIMABLE_STATUSES` is ("pending", "processing", "failed"), so the
    moment this sets 'deleted' the reconciliation sweep and `claim_and_mark_failed` both stop
    seeing the row — and the 20 credits charged at generation time can never be returned by
    anything, ever.

    That was reachable on the ORDINARY path, not just on a worker death: iOS lets a user
    select and delete a still-generating card (selection is gated only on `backendId != nil`,
    unlike tap and retry which gate on status). The worker then finishes normally and hits the
    conditional completion write, whose filter `.in_("status", ["pending", "processing"])` no
    longer matches — it logs a warning and returns WITHOUT raising, so no except fires and no
    refund happens. On a free account that is 40% of the monthly allocation, silently.

    The claim below is the SAME compare-and-set the sweep uses (`is_refunded=False` + a
    claimable status), so this and `claim_and_mark_failed` can race safely: exactly one of them
    wins the row and exactly one refund is issued.
    """
    # Atomically: mark deleted AND claim the refund, but only for a row that is still in
    # flight, was actually charged, and has not already been refunded.
    try:
        claimed = (
            supabase.table("research_reports")
            .update({"status": "deleted", "is_refunded": True})
            .eq("id", report_id)
            .eq("user_id", user["id"])
            .eq("is_refunded", False)
            .in_("status", _REFUNDABLE_ON_DELETE)
            .gt("credits_charged", 0)
            .execute()
        )
    except Exception as e:
        logger.error(
            "delete_report: refund claim failed for report=%s user=%s: %s: %s",
            report_id, user["id"], type(e).__name__, e,
        )
        claimed = None

    row = (getattr(claimed, "data", None) or [None])[0]
    if row:
        amount = int(row.get("credits_charged") or 0)
        # ref_id MUST match the one the CHARGE used (`request.stock_id.upper()`), or
        # `refund_credits` cannot find the debit's recorded granted/purchased split and falls
        # back to granted-first — which converts permanent purchased credits into expiring
        # ones. Same rule `claim_and_mark_failed` follows.
        ticker = (row.get("ticker") or "").upper() or None
        refunded = CreditService().refund_ledgered(
            user["id"], amount, reason="report_refund_deleted", ref_id=ticker,
        )
        if refund_did_not_happen(refunded):
            logger.error(
                "REFUND LEAK: user=%s deleted in-flight report=%s (%s) charged %s credits; "
                "the row is now 'deleted' and unreachable by the sweep, and the refund did "
                "not happen (outcome=%s) — manual credit correction needed",
                user["id"], report_id, ticker, amount,
                _outcome_name(refunded),
            )
        else:
            logger.info(
                "Refunded %s credits for deleted in-flight report %s (user %s)",
                refunded.get("refunded", amount) if isinstance(refunded, dict) else amount,
                report_id, user["id"],
            )
    else:
        # Terminal (ready), already refunded, or not ours — plain soft-delete. Unconditional
        # so deleting a finished report keeps working exactly as before.
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
        "name": "The Quality Compounder",
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
        "name": "The Disruption Seeker",
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
        "name": "The Everyday Growth Hunter",
        "tagline": "Growth at a Reasonable Price",
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
        "name": "The Activist Concentrator",
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
        "name": "The Deep Value Skeptic",
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
        ).eq("id", report_id).maybe_single().execute()
        data = (row.data if row else None) or {}
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
    blob, refunds the credits charged in /generate, and flips
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
