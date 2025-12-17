"""
Education Pydantic Schemas
Request and response models for educational content and RAG system.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.schemas.common import ContentType, BaseResponse, TimestampMixin


# Educational Content Models
# ===========================

class EducationalContentCreate(BaseModel):
    """Create educational content."""
    type: ContentType
    title: str = Field(..., min_length=1, max_length=500)
    author: Optional[str] = Field(None, max_length=255)
    publication_year: Optional[int] = Field(None, ge=1900, le=2100)

    # Source
    source_url: Optional[HttpUrl] = None
    isbn: Optional[str] = Field(None, pattern=r"^[\d-]{10,17}$")

    # Content
    full_text: Optional[str] = None
    summary: Optional[str] = Field(None, max_length=5000)

    # Metadata
    topics: Optional[List[str]] = None
    difficulty_level: Optional[str] = Field(None, description="beginner/intermediate/advanced")


class EducationalContentBrief(BaseResponse, TimestampMixin):
    """Brief educational content (list view)."""
    id: str
    type: ContentType
    title: str
    author: Optional[str]
    publication_year: Optional[int]

    # Processing status
    is_processed: bool
    chunk_count: int

    # Quick info
    summary: Optional[str] = Field(None, max_length=500, description="Truncated summary")
    topics: Optional[List[str]]
    difficulty_level: Optional[str]

    # Extra fields
    cover_image_url: Optional[str] = None
    estimated_read_time: Optional[int] = Field(None, description="Minutes")
    popularity_score: Optional[int] = Field(0, description="Based on views/chats")


class EducationalContentDetail(EducationalContentBrief):
    """Full educational content details."""
    # Full content
    full_text: Optional[str]
    summary: Optional[str]  # Full summary

    # Source
    source_url: Optional[str]
    isbn: Optional[str]

    # Processing details
    processed_at: Optional[datetime]

    # Stats
    total_views: Optional[int] = 0
    total_chats: Optional[int] = 0
    average_rating: Optional[float] = Field(None, ge=1.0, le=5.0)

    # Related content
    related_content: Optional[List[EducationalContentBrief]] = None


# Content Chunks (RAG)
# ====================

class ContentChunkCreate(BaseModel):
    """Create content chunk."""
    content_id: str
    chunk_index: int
    chunk_text: str = Field(..., min_length=10)

    # Optional metadata
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    token_count: Optional[int] = None


class ContentChunk(BaseResponse):
    """Content chunk for RAG."""
    id: str
    content_id: str
    chunk_index: int
    chunk_text: str

    # Metadata
    page_number: Optional[int]
    section_title: Optional[str]
    token_count: Optional[int]

    # Vector (not included in response for size)
    has_embedding: bool = Field(description="True if embedding exists")

    created_at: datetime


class ContentChunkWithSimilarity(ContentChunk):
    """Content chunk with similarity score (for search results)."""
    similarity_score: float = Field(ge=0.0, le=1.0)
    relevance_explanation: Optional[str] = None


# Semantic Search
# ===============

class SemanticSearchRequest(BaseModel):
    """Semantic search request."""
    query: str = Field(..., min_length=3, max_length=1000)
    content_type: Optional[ContentType] = None
    content_ids: Optional[List[str]] = Field(None, description="Limit search to specific content")
    top_k: int = Field(default=5, ge=1, le=20)
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)

    # Filters
    topics: Optional[List[str]] = None
    difficulty_level: Optional[str] = None
    min_publication_year: Optional[int] = None


class SemanticSearchResult(BaseModel):
    """Semantic search result."""
    query: str
    results: List[ContentChunkWithSimilarity]
    total_results: int
    search_time_ms: int

    # Search metadata
    embedding_model: str
    query_embedding_time_ms: int
    retrieval_time_ms: int

    # Suggested refinements
    suggested_topics: Optional[List[str]] = None
    related_queries: Optional[List[str]] = None


# Educational Topics
# ==================

class Topic(BaseModel):
    """Educational topic."""
    name: str
    category: str = Field(description="value_investing/analysis/psychology/etc.")
    description: Optional[str] = None
    content_count: int = 0
    difficulty_level: str = Field(description="beginner/intermediate/advanced")

    # Visual
    emoji: Optional[str] = None
    color: Optional[str] = None


class TopicHierarchy(BaseModel):
    """Hierarchical topic structure."""
    topic: Topic
    subtopics: Optional[List['TopicHierarchy']] = None
    parent_topic: Optional[str] = None


# Content Processing
# ==================

class ContentProcessingRequest(BaseModel):
    """Request to process educational content."""
    content_id: str
    chunk_size: int = Field(default=1000, ge=100, le=5000, description="Tokens per chunk")
    chunk_overlap: int = Field(default=200, ge=0, le=1000, description="Token overlap between chunks")
    generate_embeddings: bool = True


class ContentProcessingStatus(BaseModel):
    """Status of content processing."""
    content_id: str
    status: str = Field(description="pending/processing/completed/failed")
    progress_percent: int = Field(ge=0, le=100)
    chunks_created: int
    chunks_embedded: int
    error_message: Optional[str] = None
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


# Reading Lists
# =============

class ReadingList(BaseModel):
    """User reading list."""
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    content_ids: List[str]
    is_public: bool = False

    # Stats
    total_items: int
    completed_items: int
    progress_percent: int = Field(ge=0, le=100)

    created_at: datetime
    updated_at: datetime


class ReadingProgress(BaseModel):
    """User progress on educational content."""
    user_id: str
    content_id: str
    progress_percent: int = Field(ge=0, le=100)
    last_position: Optional[str] = Field(None, description="Last chunk/page read")
    time_spent_minutes: int = 0
    completed: bool = False
    completed_at: Optional[datetime] = None
    notes: Optional[List[str]] = None


# Content Recommendations
# =======================

class ContentRecommendation(BaseModel):
    """Recommended educational content."""
    content: EducationalContentBrief
    recommendation_score: float = Field(ge=0.0, le=1.0)
    recommendation_reason: str
    match_score: Optional[float] = Field(None, description="How well it matches user interests")


class RecommendationRequest(BaseModel):
    """Request for content recommendations."""
    user_id: str
    count: int = Field(default=5, ge=1, le=20)
    based_on_reading_history: bool = True
    based_on_topics: Optional[List[str]] = None
    difficulty_level: Optional[str] = None
    content_type: Optional[ContentType] = None


# Analytics
# ==========

class EducationAnalytics(BaseModel):
    """Analytics for educational content usage."""
    user_id: str

    # Reading stats
    total_content_viewed: int
    total_content_completed: int
    total_time_spent_minutes: int
    average_session_length_minutes: float

    # Progress
    current_reading_streak_days: int
    longest_reading_streak_days: int

    # Preferences
    favorite_topics: List[str]
    favorite_authors: Optional[List[str]] = None
    preferred_difficulty: str

    # Engagement
    total_chats: int
    total_questions_asked: int
    most_chatted_content: Optional[List[Dict[str, Any]]] = None
