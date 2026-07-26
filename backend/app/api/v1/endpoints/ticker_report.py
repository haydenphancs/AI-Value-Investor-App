"""
Ticker Report Endpoints — generates a comprehensive stock analysis report
for the TickerReportView screen, plus a chat endpoint for follow-up Q&A.

Cache layer:
  - `ticker_report_cache` table (24h TTL, keyed by ticker+persona) is
    consulted inside `TickerReportService.generate_ticker_report`.
  - Legacy fallback below also peeks at recent completed
    `research_reports` rows so reports generated before the new cache
    table existed still serve instantly during the migration window.

Phase 3 error contract:
  All non-200 responses use the structured shape from
  `app.api.error_response` so iOS sees `{error_code, message,
  user_message, action, details}` instead of "HTTP 502". Endpoint code
  delegates exception classification to `error_response_from_exception`
  which inspects exception class + message keywords (FMP rate limits,
  Gemini quota, ValueError "no profile", etc.).

Endpoints:
  GET  /stocks/{ticker}/report?persona=warren_buffett
  POST /stocks/{ticker}/report/chat
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
)
from app.database import get_supabase
from app.dependencies import get_current_user_or_guest  # for the fresh-generation charge
from app.schemas.ticker_report import TickerReportResponse
from app.services.credit_service import CreditService, CreditServiceUnavailable
from app.services.agents.persona_config import PERSONA_KEYS
from app.services.agents.ticker_report_data_collector import (
    patch_wall_street_consensus_live,
)
from app.services.ticker_report_cache import (
    CACHE_SCHEMA_FLOOR,
    _short_interest_payload_stale,
    get_cached_report,
    patch_legacy_price_action,
)
from app.services.ticker_report_service import TickerReportService

logger = logging.getLogger(__name__)

router = APIRouter()

# Single source of truth for valid persona keys — alias the research agent's
# registry (persona_config.PERSONA_KEYS) so this endpoint and
# /research/generate can never validate against diverging sets when a new
# persona is added. Parity is guarded by tests/test_persona_set_parity.py.
VALID_PERSONAS = PERSONA_KEYS

# Legacy cache TTL (kept for back-compat with older research_reports rows
# that pre-date the dedicated `ticker_report_cache` table).
LEGACY_CACHE_TTL_HOURS = 24


@router.get("/{ticker}/report")
async def get_ticker_report(
    ticker: str,
    persona: str = Query("warren_buffett", description="Investor persona key"),
    user: dict = Depends(get_current_user_or_guest),  # for the fresh-generation charge
):
    """
    Generate a comprehensive ticker report.

    Billing: a cache HIT is free; a cache MISS runs the full AI pipeline and costs
    `CreditService.DEEP_RESEARCH_COST` credits, charged upfront and refunded on ANY
    non-delivery (generation error, schema drift, or client disconnect). Guests
    resolve to the shared guest balance, so this has no real billing effect until
    real accounts are enabled.

    Cache strategy: a recent completed `research_reports` row (legacy) AND the
    dedicated `ticker_report_cache` (24h close-aligned) are both checked here — so
    the charge fires strictly on a genuine generation, never on a cache hit.
    """
    ticker = ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid ticker symbol: {ticker!r}",
            details={"ticker": ticker},
        )

    if persona not in VALID_PERSONAS:
        return make_error_response(
            ErrorCode.INVALID_PERSONA,
            message=f"Unsupported persona key: {persona!r}",
            details={"persona": persona, "valid": sorted(VALID_PERSONAS)},
        )

    # ── FREE PATH 1: legacy back-compat cache (recent completed research_reports).
    try:
        cached = await _check_legacy_report_cache(ticker, persona)
        if cached:
            logger.info(
                f"Legacy cache HIT for {ticker}/{persona} — serving stored report"
            )
            # Overlay live Wall Street Consensus so saved reports match
            # what `/stocks/{ticker}/analyst-analysis` and
            # `/stocks/{ticker}/holders` are showing right now.
            cached = await patch_wall_street_consensus_live(cached, ticker)
            return patch_legacy_price_action(cached)
    except Exception as e:
        # Cache lookup failures must never break the request — log and fall through.
        logger.warning(
            f"Legacy cache check failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )

    # ── FREE PATH 2: the dedicated 24h ticker_report_cache. Checking it HERE (not
    #    only inside the service) is what lets us charge strictly on a real
    #    generation: a hit returns free, a miss is billable. get_cached_report
    #    never raises (returns None on any error).
    cached = await get_cached_report(ticker, persona)
    if cached is not None:
        logger.info(f"ticker_report_cache HIT for {ticker}/{persona} — free serve")
        result, err = _validate_report(cached, ticker, persona)
        return err if err is not None else result

    # ── BILLABLE PATH: cache miss = a fresh AI generation. Charge upfront, then
    #    refund on ANY non-delivery via the finally below. This endpoint is
    #    synchronous, so the refund fires at most once per request — the
    #    non-idempotent refund RPC is safe here without the is_refunded
    #    compare-and-set the async /research/generate path needs.
    credit_service = CreditService()
    try:
        new_remaining = credit_service.try_charge(
            user["id"], CreditService.DEEP_RESEARCH_COST
        )
    except CreditServiceUnavailable:
        # Transient Supabase/RPC failure while charging — surface a retryable
        # SYSTEM_BUSY (never INSUFFICIENT_CREDITS: a DB blip must not tell a
        # paying user they're broke).
        return make_error_response(
            ErrorCode.SYSTEM_BUSY,
            status_code=409,
            message="charge_user_credits RPC unavailable (transient)",
            details={"user_id": user["id"], "ticker": ticker, "step": "credit_charge"},
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

    delivered = False
    try:
        service = TickerReportService()
        report = await service.generate_fresh_report(ticker, persona)

        # A malformed report is a non-delivery → the finally refunds (the user
        # must not pay for a report they can't use).
        result, err = _validate_report(report, ticker, persona)
        if err is not None:
            return err
        delivered = True
        return result
    except ValueError as e:
        # Collector raises ValueError when the FMP profile lookup is empty —
        # a "ticker not found" condition, not a server error. Refunded in finally.
        logger.info(
            f"Ticker {ticker} rejected at collector (profile lookup empty): {e}"
        )
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="collector",
        )
    except Exception as e:
        logger.error(
            f"Ticker report generation failed for {ticker}/{persona}: "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="report_generation",
        )
    finally:
        if not delivered:
            # Non-delivery: generation error, schema drift, or a client disconnect
            # / CancelledError (a BaseException that `except` misses, but `finally`
            # always runs). Refund the charge. This is the ONLY backstop — unlike
            # /research/generate there is no research_reports row for the
            # reconciliation sweep — so a failed refund gets a greppable REFUND
            # LEAK marker for manual correction.
            refunded = credit_service.refund(
                user["id"], CreditService.DEEP_RESEARCH_COST
            )
            if refunded is None:
                logger.error(
                    "REFUND LEAK: charged %s credits to user=%s for a %s ticker "
                    "report that was not delivered, and the refund ALSO failed — "
                    "manual credit correction needed",
                    CreditService.DEEP_RESEARCH_COST, user["id"], ticker,
                )


def _validate_report(report: dict, ticker: str, persona: str):
    """Validate a report dict against TickerReportResponse.

    Returns (dumped_dict, None) on success, or (None, error_response) on schema
    drift — so callers return a structured DATA_INCOMPLETE instead of a Pydantic
    500. model_dump() also strips internal-only fields (e.g. _scoring_inputs).
    """
    try:
        validated = TickerReportResponse(**report)
    except ValidationError as ve:
        logger.error(
            f"Report schema validation failed for {ticker}/{persona}: {ve}"
        )
        return None, make_error_response(
            ErrorCode.DATA_INCOMPLETE,
            message=f"Report shape failed validation: {ve.error_count()} issues",
            details={
                "ticker": ticker,
                "persona": persona,
                "step": "schema_validation",
                "issues": ve.error_count(),
            },
        )
    return validated.model_dump(), None


async def _check_legacy_report_cache(ticker: str, persona: str):
    """Legacy fallback that reads `research_reports` directly.

    Used only as a back-compat bridge for reports generated before the
    dedicated `ticker_report_cache` table existed. The new cache lives
    inside `TickerReportService` and `ResearchService`.
    """
    # Honor the same schema floor as `ticker_report_cache`: when the floor
    # is more recent than the 24h TTL cutoff, use it instead so legacy rows
    # generated under an older payload shape don't get served.
    ttl_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=LEGACY_CACHE_TTL_HOURS)
    )
    cutoff = max(ttl_cutoff, CACHE_SCHEMA_FLOOR).isoformat()

    def _query():
        supabase = get_supabase()
        result = (
            supabase.table("research_reports")
            .select("ticker_report_data, completed_at")
            .eq("ticker", ticker)
            .eq("investor_persona", persona)
            .eq("status", "completed")
            .gte("completed_at", cutoff)
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data and result.data[0].get("ticker_report_data"):
            rpt = result.data[0]["ticker_report_data"]
            if _short_interest_payload_stale(rpt):
                logger.info(
                    f"Legacy report for {ticker}/{persona} has short-interest "
                    f"change_3m but empty history — skipping stale row"
                )
                return None
            return rpt
        return None

    return await asyncio.to_thread(_query)


# ── Chat with Report ──────────────────────────────────────────────────────────


class TickerReportChatRequest(BaseModel):
    ticker: str
    message: str
    persona: str = "warren_buffett"


class TickerReportChatResponseModel(BaseModel):
    reply: str
    ticker: str


@router.post("/{ticker}/report/chat")
async def chat_with_ticker_report(
    ticker: str,
    body: TickerReportChatRequest,
):
    """
    Ask a follow-up question about a stock using the same persona.

    This is a lightweight AI Q&A endpoint — it fetches a quick snapshot
    of FMP data and sends the user's question to Gemini with the
    persona context. Much faster than a full report (5-15 seconds).
    """
    ticker = ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid ticker symbol: {ticker!r}",
            details={"ticker": ticker},
        )

    persona = body.persona
    if persona not in VALID_PERSONAS:
        # Soft-coerce for chat — keep the prior behavior of falling back
        # to Buffett rather than rejecting outright. Logged so we still
        # see the bad request in production.
        logger.info(
            f"chat_with_ticker_report: unknown persona {persona!r}, "
            f"falling back to warren_buffett"
        )
        persona = "warren_buffett"

    message = body.message.strip()
    if not message:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message="Empty chat message",
            user_message="Type a question to send.",
        )

    if len(message) > 2000:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Chat message too long ({len(message)} chars > 2000)",
            user_message="Your message is too long — keep it under 2000 characters.",
            details={"length": len(message), "limit": 2000},
        )

    try:
        service = TickerReportService()
        reply = await service.chat_about_ticker(ticker, message, persona)
        return TickerReportChatResponseModel(reply=reply, ticker=ticker).model_dump()
    except ValueError as e:
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="chat_collector",
        )
    except Exception as e:
        logger.error(
            f"Ticker report chat failed for {ticker}/{persona}: "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(
            e, ticker=ticker, persona=persona, step="chat_generation",
        )
