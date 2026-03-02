"""
Ticker Report Endpoints — generates a comprehensive stock analysis report
for the TickerReportView screen, plus a chat endpoint for follow-up Q&A.

Cache Layer: Checks for recent completed research reports before generating
a fresh report. If a deep-research report was generated in the last 24 hours
for the same ticker+persona, returns the cached ticker_report_data instantly.

Endpoints:
  GET  /stocks/{ticker}/report?persona=warren_buffett
  POST /stocks/{ticker}/report/chat
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, ValidationError
from app.services.ticker_report_service import TickerReportService
from app.schemas.ticker_report import TickerReportResponse
from app.database import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_PERSONAS = {"warren_buffett", "cathie_wood", "peter_lynch", "bill_ackman"}

# Cache TTL: serve cached research report data for this duration
CACHE_TTL_HOURS = 24


@router.get("/{ticker}/report", response_model=TickerReportResponse)
async def get_ticker_report(
    ticker: str,
    persona: str = Query("warren_buffett", description="Investor persona key"),
):
    """
    Generate a comprehensive ticker report.

    Cache strategy: First checks for a recent completed research report
    with ticker_report_data. If found and fresh (< 24h), returns it
    instantly. Otherwise generates a fresh report via FMP + Gemini.

    No auth required — allows quick access from any screen.
    """
    ticker = ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    if persona not in VALID_PERSONAS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid persona. Must be one of: {', '.join(VALID_PERSONAS)}",
        )

    # ── Check cache: recent research report with full ticker_report_data
    try:
        cached = await _check_report_cache(ticker, persona)
        if cached:
            logger.info(f"Cache HIT for {ticker}/{persona} — serving stored report")
            return cached
    except Exception as e:
        logger.warning(f"Cache check failed for {ticker}: {e}")

    # ── Cache miss: generate fresh report
    try:
        service = TickerReportService()
        report = await service.generate_ticker_report(ticker, persona)

        # Validate through Pydantic before returning to catch schema issues early
        try:
            validated = TickerReportResponse(**report)
            return validated
        except ValidationError as ve:
            logger.error(f"Report schema validation failed for {ticker}: {ve}")
            # Return the raw dict and let FastAPI's response_model handle it
            # (it may fill defaults for Optional fields)
            return report

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ticker report generation failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Report generation failed: {str(e)[:200]}",
        )


async def _check_report_cache(ticker: str, persona: str):
    """
    Check research_reports for a recent completed report with ticker_report_data.
    Returns the cached TickerReportResponse dict if found, else None.

    Runs the synchronous Supabase call in a thread to avoid blocking the event loop.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()

    def _query():
        supabase = get_supabase()
        result = supabase.table("research_reports").select(
            "ticker_report_data, completed_at"
        ).eq(
            "ticker", ticker
        ).eq(
            "investor_persona", persona
        ).eq(
            "status", "completed"
        ).gte(
            "completed_at", cutoff
        ).order(
            "completed_at", desc=True
        ).limit(1).execute()

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


@router.post("/{ticker}/report/chat", response_model=TickerReportChatResponseModel)
async def chat_with_ticker_report(
    ticker: str,
    body: TickerReportChatRequest,
):
    """
    Ask a follow-up question about a stock using the same persona.

    This is a lightweight AI Q&A endpoint — it fetches a quick snapshot of
    FMP data and sends the user's question to Gemini with the persona context.
    Much faster than a full report (5-15 seconds).
    """
    ticker = ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    persona = body.persona
    if persona not in VALID_PERSONAS:
        persona = "warren_buffett"

    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="Message too long (max 2000 chars)")

    try:
        service = TickerReportService()
        reply = await service.chat_about_ticker(ticker, message, persona)
        return TickerReportChatResponseModel(reply=reply, ticker=ticker)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ticker report chat failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Chat failed: {str(e)[:200]}",
        )
