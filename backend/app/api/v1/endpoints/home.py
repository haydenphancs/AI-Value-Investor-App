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
from app.schemas.home import HomeFeedResponse

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
