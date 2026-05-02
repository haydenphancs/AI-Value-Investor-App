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
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
)
from app.database import get_supabase
from app.schemas.ticker_report import TickerReportResponse
from app.services.ticker_report_cache import CACHE_SCHEMA_FLOOR
from app.services.ticker_report_service import TickerReportService

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_PERSONAS = {"warren_buffett", "cathie_wood", "peter_lynch", "bill_ackman"}

# Legacy cache TTL (kept for back-compat with older research_reports rows
# that pre-date the dedicated `ticker_report_cache` table).
LEGACY_CACHE_TTL_HOURS = 24


@router.get("/{ticker}/report")
async def get_ticker_report(
    ticker: str,
    persona: str = Query("warren_buffett", description="Investor persona key"),
):
    """
    Generate a comprehensive ticker report.

    Cache strategy: `TickerReportService` consults `ticker_report_cache`
    (24h TTL) internally. This endpoint additionally falls back to a
    recent completed `research_reports` row so legacy reports surface
    during the migration window.

    No auth required — allows quick access from any screen.
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

    # ── Legacy back-compat cache: recent completed research_reports row
    try:
        cached = await _check_legacy_report_cache(ticker, persona)
        if cached:
            logger.info(
                f"Legacy cache HIT for {ticker}/{persona} — serving stored report"
            )
            return cached
    except Exception as e:
        # Cache lookup failures must never break the request — log and
        # fall through to fresh generation.
        logger.warning(
            f"Legacy cache check failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )

    # ── Cache miss: generate fresh report (TickerReportService also
    # checks ticker_report_cache itself before any FMP/Gemini calls).
    try:
        service = TickerReportService()
        report = await service.generate_ticker_report(ticker, persona)
    except ValueError as e:
        # Collector raises ValueError when FMP profile lookup is empty —
        # that's a "ticker not found" condition, not a server error.
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

    # Validate the dict the service returned. If schema drift sneaks in
    # we return a structured 502 instead of a Pydantic 500 stack trace.
    try:
        validated = TickerReportResponse(**report)
        return validated.model_dump()
    except ValidationError as ve:
        logger.error(
            f"Report schema validation failed for {ticker}/{persona}: {ve}"
        )
        return make_error_response(
            ErrorCode.DATA_INCOMPLETE,
            message=f"Report shape failed validation: {ve.error_count()} issues",
            details={
                "ticker": ticker,
                "persona": persona,
                "step": "schema_validation",
                "issues": ve.error_count(),
            },
        )


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
            return result.data[0]["ticker_report_data"]
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
