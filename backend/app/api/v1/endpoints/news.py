"""
News Endpoints
Handles news aggregation, summarization, and breaking news.
Requirements: Section 4.1 - Automated News Summarization
"""

from fastapi import APIRouter, Depends, Query
from supabase import Client
from typing import Optional
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, get_optional_user_id

logger = logging.getLogger(__name__)

router = APIRouter()


# Endpoints
# =========

@router.get("/feed")
async def get_news_feed(
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    sentiment: Optional[str] = None,
    user_id: Optional[str] = Depends(get_optional_user_id),
    supabase: Client = Depends(get_supabase)
):
    """
    Get news feed with summaries.
    Section 4.1.3 - REQ-1: Sentiment categorization
    Section 4.1.3 - REQ-2: Summaries limited to 3 bullet points

    Args:
        limit: Number of articles to return
        offset: Offset for pagination
        sentiment: Filter by sentiment (bullish, bearish, neutral)
        user_id: Optional user ID for personalized feed
        supabase: Supabase client

    Returns:
        list: News articles with summaries
    """
    query = supabase.table("news_articles").select(
        "id, title, summary, ai_summary, ai_summary_bullets, sentiment, "
        "published_at, source_name, image_url"
    ).order("published_at", desc=True)

    if sentiment:
        query = query.eq("sentiment", sentiment)

    query = query.range(offset, offset + limit - 1)

    result = query.execute()

    return {
        "articles": result.data,
        "count": len(result.data),
        "limit": limit,
        "offset": offset
    }


@router.get("/breaking")
async def get_breaking_news(
    user_id: Optional[str] = Depends(get_optional_user_id),
    supabase: Client = Depends(get_supabase)
):
    """
    Get breaking news for user's watchlist.
    Section 4.1.3 - REQ-4: Show all current breaking news

    Args:
        user_id: Optional user ID
        supabase: Supabase client

    Returns:
        list: Breaking news items
    """
    # Get active breaking news (not expired)
    query = supabase.table("breaking_news").select(
        """
        *,
        news:news_articles(*),
        stock:stocks(ticker, company_name, logo_url)
        """
    ).filter("expires_at", "gt", "now()").order(
        "created_at", desc=True
    ).limit(10)

    # If user is authenticated, filter by their watchlist
    if user_id:
        # Get user's watchlist stock IDs
        watchlist = supabase.table("watchlists").select("stock_id").eq(
            "user_id", user_id
        ).execute()

        if watchlist.data:
            stock_ids = [item["stock_id"] for item in watchlist.data]
            query = query.in_("stock_id", stock_ids)

    result = query.execute()

    return result.data


@router.get("/{news_id}")
async def get_news_detail(
    news_id: str,
    supabase: Client = Depends(get_supabase)
):
    """
    Get detailed news article.

    Args:
        news_id: News article ID
        supabase: Supabase client

    Returns:
        dict: News article details
    """
    result = supabase.table("news_articles").select(
        """
        *,
        news_stocks(
            stock:stocks(ticker, company_name, logo_url)
        )
        """
    ).eq("id", news_id).single().execute()

    if not result.data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="News article not found")

    return result.data


@router.get("/stock/{ticker}")
async def get_stock_news(
    ticker: str,
    limit: int = Query(20, le=100),
    supabase: Client = Depends(get_supabase)
):
    """
    Get news articles for a specific stock.

    Args:
        ticker: Stock ticker symbol
        limit: Number of articles to return
        supabase: Supabase client

    Returns:
        list: News articles
    """
    # Get stock ID
    stock = supabase.table("stocks").select("id").eq(
        "ticker", ticker.upper()
    ).single().execute()

    if not stock.data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Stock not found")

    # Get news for this stock
    result = supabase.table("news_stocks").select(
        """
        news:news_articles(
            id, title, summary, ai_summary, ai_summary_bullets,
            sentiment, published_at, source_name, image_url
        )
        """
    ).eq("stock_id", stock.data["id"]).order(
        "created_at", desc=True
    ).limit(limit).execute()

    # Extract news articles from nested structure
    articles = [item["news"] for item in result.data if item.get("news")]

    return articles


@router.post("/{news_id}/mark-read")
async def mark_news_as_read(
    news_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Mark a news article as read by user.
    Can be used for analytics and personalization.

    Args:
        news_id: News article ID
        user: Current user data

    Returns:
        dict: Success message
    """
    # Log activity (you could create a separate user_news_interactions table)
    logger.info(f"User {user['id']} read news {news_id}")

    return {"message": "News marked as read"}
