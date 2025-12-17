"""
Widget Endpoints
Handles iOS home screen widget data updates.
Requirements: Section 4.2 - Live Widget
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
import logging

from app.database import get_supabase
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
# =======================

class WidgetUpdate(BaseModel):
    headline: str
    sentiment: str  # bullish, bearish, neutral
    emoji: str
    daily_trend: str
    market_summary: Optional[str] = None
    top_movers: Optional[dict] = None
    deep_link_url: Optional[str] = None


class WidgetResponse(BaseModel):
    id: str
    headline: str
    sentiment: str
    emoji: str
    daily_trend: str
    market_summary: Optional[str]
    published_at: str


# Endpoints
# =========

@router.get("/latest")
async def get_latest_widget_update(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get the latest widget update for user.
    Section 4.2.3 - REQ-5: Widget updates at least twice daily

    Args:
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Latest widget update
    """
    result = supabase.table("widget_updates").select("*").eq(
        "user_id", user["id"]
    ).not_.is_(
        "published_at", "null"
    ).order(
        "published_at", desc=True
    ).limit(1).execute()

    if not result.data:
        # Return a default widget if no updates exist
        return {
            "headline": "Welcome to AI Value Investor",
            "sentiment": "neutral",
            "emoji": "📊",
            "daily_trend": "Stay tuned for market updates",
            "market_summary": "Your personalized market summary will appear here.",
            "published_at": datetime.utcnow().isoformat()
        }

    return result.data[0]


@router.get("/timeline")
async def get_widget_timeline(
    hours: int = 24,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get widget update timeline for iOS WidgetKit.
    Returns multiple updates for WidgetKit to display over time.
    Section 4.2 - WidgetKit timeline support

    Args:
        hours: Hours to look ahead
        user: Current user data
        supabase: Supabase client

    Returns:
        list: Widget timeline entries
    """
    # Get published updates from the last 24 hours
    past_cutoff = datetime.utcnow() - timedelta(hours=hours)

    past_updates = supabase.table("widget_updates").select("*").eq(
        "user_id", user["id"]
    ).not_.is_("published_at", "null").gte(
        "published_at", past_cutoff.isoformat()
    ).order("published_at", desc=False).execute()

    # Get scheduled future updates
    future_updates = supabase.table("widget_updates").select("*").eq(
        "user_id", user["id"]
    ).not_.is_("scheduled_for", "null").gte(
        "scheduled_for", datetime.utcnow().isoformat()
    ).order("scheduled_for", desc=False).limit(5).execute()

    return {
        "past_updates": past_updates.data,
        "future_updates": future_updates.data,
        "timeline_hours": hours
    }


@router.get("/history")
async def get_widget_history(
    limit: int = 50,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get historical widget updates.

    Args:
        limit: Number of updates to return
        user: Current user data
        supabase: Supabase client

    Returns:
        list: Historical widget updates
    """
    result = supabase.table("widget_updates").select("*").eq(
        "user_id", user["id"]
    ).not_.is_("published_at", "null").order(
        "published_at", desc=True
    ).limit(limit).execute()

    return result.data


@router.get("/{update_id}")
async def get_widget_update(
    update_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get specific widget update details.
    Section 4.2.3 - REQ-4: Deep linking support

    Args:
        update_id: Widget update ID
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Widget update with linked content
    """
    result = supabase.table("widget_updates").select(
        """
        *,
        linked_report:deep_research_reports(
            id, title, executive_summary, stock_id,
            stock:stocks(ticker, company_name, logo_url)
        )
        """
    ).eq("id", update_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Widget update not found")

    return result.data


# Admin/Background Job Endpoints
# ==============================

@router.post("/generate")
async def generate_widget_update_for_user(
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Manually trigger widget update generation.
    Normally this would be done via background jobs.

    Args:
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Generated widget update
    """
    try:
        from app.services.widget_service import WidgetService
        from app.integrations.gemini import GeminiClient

        gemini_client = GeminiClient()
        widget_service = WidgetService(gemini_client)

        # Generate widget update
        widget_data = await widget_service.generate_update_for_user(
            user_id=user["id"]
        )

        # Save to database
        result = supabase.table("widget_updates").insert({
            "user_id": user["id"],
            **widget_data,
            "published_at": datetime.utcnow().isoformat()
        }).execute()

        return result.data[0] if result.data else {}

    except Exception as e:
        logger.error(f"Widget generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate widget update")
