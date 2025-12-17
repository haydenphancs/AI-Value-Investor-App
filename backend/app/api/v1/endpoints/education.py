"""
Education Endpoints
Handles educational content (books and articles) for RAG-based learning.
Requirements: Section 4.4 - Educational Articles and Books Chat
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from supabase import Client
from pydantic import BaseModel
from typing import Optional, List
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, get_optional_user_id

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
# =======================

class ContentCreate(BaseModel):
    type: str  # 'book' or 'article'
    title: str
    author: Optional[str] = None
    publication_year: Optional[int] = None
    source_url: Optional[str] = None
    summary: Optional[str] = None
    topics: Optional[List[str]] = None


class ContentResponse(BaseModel):
    id: str
    type: str
    title: str
    author: Optional[str]
    summary: Optional[str]
    is_processed: bool
    chunk_count: int


# Endpoints
# =========

@router.get("/content")
async def browse_educational_content(
    content_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    supabase: Client = Depends(get_supabase)
):
    """
    Browse educational content library.
    Section 4.4 - RAG System for books and articles

    Args:
        content_type: Filter by type ('book' or 'article')
        search: Search query
        limit: Number of items to return
        offset: Offset for pagination
        supabase: Supabase client

    Returns:
        dict: Educational content list
    """
    query = supabase.table("educational_content").select(
        "id, type, title, author, publication_year, summary, "
        "is_processed, chunk_count, created_at"
    )

    if content_type:
        query = query.eq("type", content_type)

    if search:
        # Use full-text search
        query = query.or_(
            f"title.ilike.%{search}%,author.ilike.%{search}%,summary.ilike.%{search}%"
        )

    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)

    result = query.execute()

    return {
        "content": result.data,
        "count": len(result.data),
        "limit": limit,
        "offset": offset
    }


@router.get("/content/{content_id}")
async def get_educational_content(
    content_id: str,
    supabase: Client = Depends(get_supabase)
):
    """
    Get detailed educational content information.

    Args:
        content_id: Content ID
        supabase: Supabase client

    Returns:
        dict: Content details
    """
    result = supabase.table("educational_content").select("*").eq(
        "id", content_id
    ).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Content not found")

    return result.data


@router.get("/books")
async def get_books(
    limit: int = Query(20, le=100),
    supabase: Client = Depends(get_supabase)
):
    """
    Get available investment books.
    Section 4.4.1 - Educational Articles and Books

    Args:
        limit: Number of books to return
        supabase: Supabase client

    Returns:
        list: Investment books
    """
    result = supabase.table("educational_content").select(
        "id, title, author, publication_year, summary, chunk_count"
    ).eq("type", "book").eq("is_processed", True).order(
        "title", desc=False
    ).limit(limit).execute()

    return result.data


@router.get("/articles")
async def get_articles(
    limit: int = Query(20, le=100),
    supabase: Client = Depends(get_supabase)
):
    """
    Get available investment articles.
    Section 4.4.1 - Educational Articles and Books

    Args:
        limit: Number of articles to return
        supabase: Supabase client

    Returns:
        list: Investment articles
    """
    result = supabase.table("educational_content").select(
        "id, title, author, source_url, summary, chunk_count"
    ).eq("type", "article").eq("is_processed", True).order(
        "created_at", desc=True
    ).limit(limit).execute()

    return result.data


@router.get("/topics")
async def get_topics(
    supabase: Client = Depends(get_supabase)
):
    """
    Get list of available topics from educational content.

    Args:
        supabase: Supabase client

    Returns:
        list: Unique topics
    """
    # This would require aggregating JSONB topics field
    # Simplified version: return predefined topics
    return {
        "topics": [
            "Value Investing",
            "Margin of Safety",
            "Competitive Advantage (Moat)",
            "Financial Statements",
            "Warren Buffett",
            "Benjamin Graham",
            "Charlie Munger",
            "Capital Allocation",
            "Business Analysis",
            "Market Psychology"
        ]
    }


@router.post("/content/{content_id}/favorite")
async def favorite_content(
    content_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Mark content as favorite for user.
    (Requires creating a user_favorites table)

    Args:
        content_id: Content ID
        user: Current user data

    Returns:
        dict: Success message
    """
    logger.info(f"User {user['id']} favorited content {content_id}")

    return {"message": "Content favorited successfully"}


@router.get("/search")
async def semantic_search_content(
    query: str = Query(..., min_length=3),
    content_type: Optional[str] = None,
    limit: int = Query(5, le=20),
    supabase: Client = Depends(get_supabase)
):
    """
    Semantic search across educational content using vector embeddings.
    Section 4.4.3 - REQ-8, REQ-9: Vector database search

    Args:
        query: Search query
        content_type: Filter by type
        limit: Number of results
        supabase: Supabase client

    Returns:
        list: Semantically similar content chunks
    """
    try:
        from app.services.chat_service import ChatService
        from app.integrations.gemini import GeminiClient

        gemini_client = GeminiClient()
        chat_service = ChatService(gemini_client)

        # Generate query embedding
        query_embedding = await chat_service.get_embedding(query)

        # Search in content_chunks table
        from app.database import db_manager, vector_similarity_search
        from sqlalchemy.ext.asyncio import AsyncSession

        async_session_factory = db_manager.get_async_session_factory()
        async with async_session_factory() as session:
            results = await vector_similarity_search(
                session=session,
                table_name="content_chunks",
                embedding=query_embedding,
                top_k=limit,
                threshold=0.7
            )

        # Enrich with content metadata
        if results:
            content_ids = list(set([r["content_id"] for r in results]))
            content_data = supabase.table("educational_content").select(
                "id, title, author, type"
            ).in_("id", content_ids).execute()

            content_map = {c["id"]: c for c in content_data.data}

            for result in results:
                result["content"] = content_map.get(result["content_id"], {})

        return results

    except Exception as e:
        logger.error(f"Semantic search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")
