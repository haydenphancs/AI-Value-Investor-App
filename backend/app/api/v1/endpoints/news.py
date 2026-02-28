"""
News Endpoints
Frontend: GET /news?page=&per_page=, GET /news/{articleId}
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
import logging

from app.database import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_news_feed(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, le=50),
    supabase: Client = Depends(get_supabase),
):
    """Get paginated news feed from news_articles table."""
    offset = (page - 1) * per_page

    result = supabase.table("news_articles").select(
        "id, headline, summary, source_name, source_logo_url, source_is_verified, "
        "sentiment, published_at, thumbnail_url, related_tickers, category, "
        "is_breaking, article_url, insight_summary, insight_key_points, "
        "key_takeaways, read_time_minutes, created_at"
    ).order("published_at", desc=True).range(offset, offset + per_page - 1).execute()

    # Get total count for pagination
    count_result = supabase.table("news_articles").select(
        "id", count="exact"
    ).execute()
    total = count_result.count if hasattr(count_result, 'count') and count_result.count else None

    articles = result.data or []
    has_more = len(articles) == per_page

    return {
        "articles": articles,
        "page": page,
        "per_page": per_page,
        "total": total,
        "has_more": has_more,
    }


@router.get("/{article_id}")
async def get_news_article(
    article_id: str,
    supabase: Client = Depends(get_supabase),
):
    """Get single news article by ID."""
    result = supabase.table("news_articles").select("*").eq(
        "id", article_id
    ).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Article not found")

    return result.data
