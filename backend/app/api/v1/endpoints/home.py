"""
Home Feed Endpoint
Frontend: GET /home/feed

Returns aggregated data for the HomeView screen in a single request:
- Market tickers with real-time prices + sparkline data (FMP)
- AI-generated market insight summary
- Daily briefing alerts (earnings calendar, whale alerts)
- User's recent research reports (if authenticated)

Auth is optional — unauthenticated users receive public market data;
authenticated users also receive personalised research reports.
"""

from fastapi import APIRouter, Depends
from typing import Optional
import logging

from app.dependencies import get_optional_user_id
from app.services.home_service import HomeService
from app.services.home_dashboard_service import get_home_dashboard_service
from app.services.signals_service import get_signals_service
from app.schemas.home import HomeFeedResponse
from app.schemas.home_dashboard import HomeDashboardResponse
from app.schemas.signals_detail import SignalTickerDetailResponse
from app.api.error_response import (
    error_response_from_exception,
    make_error_response,
    ErrorCode,
)

_VALID_SIGNAL_KINDS = {"whale", "congress"}

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/feed", response_model=HomeFeedResponse)
async def get_home_feed(
    user_id: Optional[str] = Depends(get_optional_user_id),
):
    """
    Aggregated home feed — single request for the entire home screen.

    Fetches market tickers, insight summary, daily briefings, and recent
    research concurrently.  Each section degrades gracefully on failure.
    """
    service = HomeService()
    return await service.get_home_feed(user_id)


@router.get("/dashboard", response_model=HomeDashboardResponse)
async def get_home_dashboard():
    """
    Aggregated Caydex Home dashboard — single request for the redesigned
    `HomeDashboardView` (distinct from the legacy `/home/feed`).

    Today this returns the market-status header and the top "Market Pulse"
    strip (major indices + Bitcoin + commodities), each with a live quote and
    a daily-close sparkline. Public (no auth). Degrades gracefully — a failed
    symbol is dropped rather than failing the whole strip; only an unexpected
    failure surfaces a structured error.
    """
    try:
        service = get_home_dashboard_service()
        return await service.get_dashboard()
    except Exception as e:
        logger.error(
            "Home dashboard failed: %s: %s", type(e).__name__, e, exc_info=True
        )
        return error_response_from_exception(e, step="home_dashboard")


@router.get("/signals/{kind}/{ticker}", response_model=SignalTickerDetailResponse)
async def get_signal_ticker_detail(kind: str, ticker: str):
    """Per-ticker drill-down for a Home signal card — WHO bought/added `ticker`,
    WHEN, HOW MUCH. ``kind`` ∈ {whale, congress}. Public (no auth). The service
    degrades to an empty holder list rather than failing; only an unexpected
    error surfaces a structured response.
    """
    if kind not in _VALID_SIGNAL_KINDS:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Unsupported signal kind: {kind!r}",
            details={"kind": kind, "valid": sorted(_VALID_SIGNAL_KINDS)},
        )
    if not ticker or len(ticker) > 12:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Invalid ticker symbol: {ticker!r}",
            details={"ticker": ticker},
        )
    try:
        return await get_signals_service().get_ticker_detail(kind, ticker)
    except Exception as e:
        logger.error(
            "Signal detail failed (%s/%s): %s: %s",
            kind, ticker, type(e).__name__, e, exc_info=True,
        )
        return error_response_from_exception(e, ticker=ticker, step="signal_detail")
