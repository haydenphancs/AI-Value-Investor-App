"""
Chat Pydantic Schemas
Request and response models for AI chat sessions.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.schemas.common import SessionType, BaseResponse, TimestampMixin, AIMetadata


# Chat Session Models
# ===================

class ChatSessionCreate(BaseModel):
    """Create chat session request."""
    session_type: SessionType
    content_id: Optional[str] = Field(None, description="For education chat - book/article ID")
    stock_id: Optional[str] = Field(None, description="For stock analysis chat")
    title: Optional[str] = Field(None, max_length=500)
    initial_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional initial context for the session"
    )


class ChatSessionUpdate(BaseModel):
    """Update chat session."""
    title: Optional[str] = None
    is_active: Optional[bool] = None


class ChatSessionBrief(BaseResponse, TimestampMixin):
    """Brief chat session (list view)."""
    id: str
    user_id: str
    session_type: SessionType

    # Related entities
    content_id: Optional[str]
    stock_id: Optional[str]

    # Session info
    title: str
    is_active: bool
    message_count: int

    # Timestamps
    last_message_at: datetime

    # Extra fields
    preview_message: Optional[str] = Field(None, description="Last message preview")
    unread_count: Optional[int] = Field(0, description="Unread messages count")
    session_emoji: str = Field(description="Emoji for session type")


class ChatSessionDetail(ChatSessionBrief):
    """Full chat session with embedded data."""
    # Embedded related data
    content: Optional[Dict[str, Any]] = Field(None, description="Educational content details if applicable")
    stock: Optional[Dict[str, Any]] = Field(None, description="Stock details if applicable")

    # Session metadata
    total_tokens_used: Optional[int] = 0
    total_cost_usd: Optional[float] = 0.0


# Chat Message Models
# ===================

class ChatMessageCreate(BaseModel):
    """Send chat message."""
    content: str = Field(..., min_length=1, max_length=10000)
    attachments: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Optional attachments (charts, screenshots, etc.)"
    )


class ChatMessage(BaseResponse):
    """Chat message."""
    id: str
    session_id: str
    role: str = Field(description="user/assistant/system")
    content: str

    # AI-specific fields
    citations: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Source citations for RAG responses"
    )
    retrieved_chunks: Optional[List[str]] = Field(
        None,
        description="IDs of retrieved chunks for RAG"
    )

    # Metadata
    tokens_used: Optional[int]
    model_version: Optional[str]

    created_at: datetime

    # Extra fields
    is_edited: bool = False
    edited_at: Optional[datetime] = None
    has_feedback: Optional[bool] = Field(False, description="User gave thumbs up/down")
    feedback_score: Optional[int] = Field(None, description="1 (bad) to 5 (good)")


class ChatMessageWithContext(ChatMessage):
    """Chat message with full context."""
    # Previous messages for context
    previous_messages: List[ChatMessage] = Field(max_items=10)

    # Retrieved context (for RAG)
    context_chunks: Optional[List[Dict[str, Any]]] = None

    # AI generation details
    ai_metadata: Optional[AIMetadata] = None


# Chat Response Models
# ====================

class ChatResponse(BaseModel):
    """AI chat response."""
    message: ChatMessage
    suggested_questions: Optional[List[str]] = Field(
        None,
        max_items=3,
        description="Suggested follow-up questions"
    )
    related_topics: Optional[List[str]] = None
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0)


# RAG-Specific Models
# ===================

class RetrievedChunk(BaseModel):
    """Retrieved context chunk for RAG."""
    chunk_id: str
    chunk_text: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    source_title: str
    source_author: Optional[str]
    page_number: Optional[int]
    chunk_index: int


class RAGContext(BaseModel):
    """Retrieved context for RAG response."""
    query: str
    chunks: List[RetrievedChunk]
    total_chunks_retrieved: int
    retrieval_time_ms: int
    embedding_model: str


# Chat Analytics
# ==============

class ChatSessionAnalytics(BaseModel):
    """Analytics for chat sessions."""
    user_id: str
    total_sessions: int
    sessions_by_type: Dict[str, int]
    total_messages: int
    average_messages_per_session: float
    total_tokens_used: int
    total_cost_usd: float
    favorite_topics: Optional[List[str]] = None
    most_active_time: Optional[str] = Field(None, description="Hour of day user is most active")


# Message Feedback
# ================

class MessageFeedback(BaseModel):
    """User feedback on AI message."""
    message_id: str
    feedback_type: str = Field(description="thumbs_up/thumbs_down/report")
    feedback_score: Optional[int] = Field(None, ge=1, le=5)
    feedback_text: Optional[str] = Field(None, max_length=1000)
    issue_category: Optional[str] = Field(
        None,
        description="incorrect/unhelpful/inappropriate/other"
    )


# Conversation Export
# ===================

class ConversationExport(BaseModel):
    """Export conversation for download."""
    session_id: str
    format: str = Field("markdown", description="markdown/json/pdf")
    include_citations: bool = True
    include_metadata: bool = False


class ExportedConversation(BaseModel):
    """Exported conversation data."""
    session: ChatSessionDetail
    messages: List[ChatMessage]
    export_format: str
    exported_at: datetime
    file_url: Optional[str] = Field(None, description="Download URL if file generated")
