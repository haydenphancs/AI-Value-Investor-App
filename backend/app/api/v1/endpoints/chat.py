"""
Chat Endpoints
Handles AI chat sessions for education and stock analysis.
Requirements: Section 4.4 - Educational Articles and Books Chat
Requirements: Section 4.4 - Company's Fundamental Information Chat
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
from pydantic import BaseModel
from typing import Optional, List
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, StandardRateLimit

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models
# =======================

class ChatSessionCreate(BaseModel):
    session_type: str  # 'education', 'stock_analysis', 'general'
    content_id: Optional[str] = None  # For education chat
    stock_id: Optional[str] = None  # For stock analysis chat
    title: Optional[str] = None


class ChatMessage(BaseModel):
    content: str


class ChatResponse(BaseModel):
    message_id: str
    session_id: str
    role: str
    content: str
    citations: Optional[List[dict]] = None
    created_at: str


# Endpoints
# =========

@router.post("/sessions")
async def create_chat_session(
    request: ChatSessionCreate,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Create a new chat session.

    Args:
        request: Session creation request
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Created session data
    """
    session_data = {
        "user_id": user["id"],
        "session_type": request.session_type,
        "content_id": request.content_id,
        "stock_id": request.stock_id,
        "title": request.title or f"Chat {request.session_type}"
    }

    result = supabase.table("chat_sessions").insert(session_data).execute()

    return result.data[0] if result.data else {}


@router.get("/sessions")
async def get_my_chat_sessions(
    session_type: Optional[str] = None,
    limit: int = 20,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get user's chat sessions.

    Args:
        session_type: Filter by session type
        limit: Number of sessions to return
        user: Current user data
        supabase: Supabase client

    Returns:
        list: Chat sessions
    """
    query = supabase.table("chat_sessions").select(
        """
        id, session_type, title, message_count, is_active,
        created_at, last_message_at,
        content:educational_content(id, title, type),
        stock:stocks(id, ticker, company_name, logo_url)
        """
    ).eq("user_id", user["id"]).order("last_message_at", desc=True)

    if session_type:
        query = query.eq("session_type", session_type)

    result = query.limit(limit).execute()

    return result.data


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get chat session details with message history.

    Args:
        session_id: Session ID
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Session with messages
    """
    # Get session
    session = supabase.table("chat_sessions").select(
        """
        *,
        content:educational_content(id, title, type, author),
        stock:stocks(id, ticker, company_name, sector)
        """
    ).eq("id", session_id).eq("user_id", user["id"]).single().execute()

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Get messages
    messages = supabase.table("chat_messages").select("*").eq(
        "session_id", session_id
    ).order("created_at", desc=False).execute()

    return {
        **session.data,
        "messages": messages.data
    }


@router.post("/sessions/{session_id}/messages", response_model=ChatResponse)
async def send_chat_message(
    session_id: str,
    message: ChatMessage,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    _rate_limit=StandardRateLimit
):
    """
    Send a message in a chat session and get AI response.
    Section 4.4.3 - REQ-8, REQ-9: Retrieve from vector database
    Section 4.4.3 - REQ-10: Generate answer with citations

    Args:
        session_id: Session ID
        message: User message
        user: Current user data
        supabase: Supabase client

    Returns:
        ChatResponse: AI response with citations
    """
    # Verify session belongs to user
    session = supabase.table("chat_sessions").select("*").eq(
        "id", session_id
    ).eq("user_id", user["id"]).single().execute()

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Save user message
    user_message_data = {
        "session_id": session_id,
        "role": "user",
        "content": message.content
    }

    supabase.table("chat_messages").insert(user_message_data).execute()

    # Generate AI response (this should call the chat service)
    try:
        from app.services.chat_service import ChatService
        from app.integrations.gemini import GeminiClient

        gemini_client = GeminiClient()
        chat_service = ChatService(gemini_client)

        ai_response = await chat_service.generate_response(
            session_id=session_id,
            user_message=message.content,
            session_type=session.data["session_type"],
            content_id=session.data.get("content_id"),
            stock_id=session.data.get("stock_id")
        )

        # Save AI message
        ai_message_data = {
            "session_id": session_id,
            "role": "assistant",
            "content": ai_response["content"],
            "citations": ai_response.get("citations"),
            "retrieved_chunks": ai_response.get("retrieved_chunks"),
            "tokens_used": ai_response.get("tokens_used"),
            "model_version": ai_response.get("model_version")
        }

        result = supabase.table("chat_messages").insert(ai_message_data).execute()

        return ChatResponse(**result.data[0])

    except Exception as e:
        logger.error(f"Chat response generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate response")


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Delete a chat session and all its messages.

    Args:
        session_id: Session ID
        user: Current user data
        supabase: Supabase client

    Returns:
        dict: Success message
    """
    # Verify ownership
    session = supabase.table("chat_sessions").select("id").eq(
        "id", session_id
    ).eq("user_id", user["id"]).single().execute()

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Delete session (cascade will delete messages)
    supabase.table("chat_sessions").delete().eq("id", session_id).execute()

    return {"message": "Chat session deleted successfully"}
