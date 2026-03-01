"""
Ticker Report Endpoints — generates a comprehensive stock analysis report
for the TickerReportView screen, plus a chat endpoint for follow-up Q&A.

Endpoints:
  GET  /stocks/{ticker}/report?persona=warren_buffett
  POST /stocks/{ticker}/report/chat
"""

import logging
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from app.services.ticker_report_service import TickerReportService
from app.schemas.ticker_report import TickerReportResponse

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_PERSONAS = {"warren_buffett", "cathie_wood", "peter_lynch", "bill_ackman"}


@router.get("/{ticker}/report", response_model=TickerReportResponse)
async def get_ticker_report(
    ticker: str,
    persona: str = Query("warren_buffett", description="Investor persona key"),
):
    """
    Generate a comprehensive ticker report.

    This endpoint fetches real financial data from FMP, computes key metrics,
    and uses Gemini AI to produce a full investment analysis.

    No auth required — allows quick access from any screen.
    Takes 15-45 seconds depending on AI response time.
    """
    ticker = ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    if persona not in VALID_PERSONAS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid persona. Must be one of: {', '.join(VALID_PERSONAS)}",
        )

    try:
        service = TickerReportService()
        report = await service.generate_ticker_report(ticker, persona)
        return report

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Ticker report generation failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Report generation failed: {str(e)[:200]}",
        )


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
