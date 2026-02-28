"""
Chat Endpoints — with RAG pipeline
Frontend: POST /chat/sessions, POST /chat/sessions/{id}/messages,
          GET /chat/sessions/{id}
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import get_current_user, StandardRateLimit
from app.schemas.chat import (
    CreateChatSessionRequest, SendChatMessageRequest,
    ChatSessionResponse, ChatMessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_chat_session(
    request: CreateChatSessionRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Create a new chat session."""
    session_data = {
        "user_id": user["id"],
        "session_type": "STOCK" if request.stock_id else "NORMAL",
        "stock_id": request.stock_id,
        "title": f"Chat about {request.stock_id}" if request.stock_id else "New Chat",
    }

    result = supabase.table("chat_sessions").insert(session_data).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create session")

    row = result.data[0]
    return ChatSessionResponse(
        id=row["id"],
        title=row.get("title"),
        session_type=row.get("session_type", "NORMAL"),
        stock_id=row.get("stock_id"),
        preview_message=row.get("preview_message"),
        message_count=row.get("message_count", 0),
        is_saved=row.get("is_saved", False),
        created_at=row["created_at"],
        last_message_at=row.get("last_message_at"),
    )


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def send_chat_message(
    session_id: str,
    request: SendChatMessageRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    _rate_limit=StandardRateLimit,
):
    """Send a message and get AI response with RAG."""
    # Verify session ownership
    session = supabase.table("chat_sessions").select("*").eq(
        "id", session_id
    ).eq("user_id", user["id"]).single().execute()

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Save user message
    user_msg = {
        "session_id": session_id,
        "role": "user",
        "content": request.message,
    }
    supabase.table("chat_messages").insert(user_msg).execute()

    # Generate AI response via chat service
    try:
        from app.services.chat_service import ChatService
        chat_service = ChatService()

        ai_result = await chat_service.generate_response(
            session_id=session_id,
            user_message=request.message,
            session_type=session.data.get("session_type", "NORMAL"),
            stock_id=session.data.get("stock_id"),
        )

        # Save AI message
        ai_msg = {
            "session_id": session_id,
            "role": "assistant",
            "content": ai_result["content"],
            "citations": ai_result.get("citations"),
            "rich_content": ai_result.get("rich_content"),
            "tokens_used": ai_result.get("tokens_used"),
        }
        result = supabase.table("chat_messages").insert(ai_msg).execute()

        # Update session metadata
        supabase.table("chat_sessions").update({
            "message_count": session.data.get("message_count", 0) + 2,
            "preview_message": ai_result["content"][:100],
            "last_message_at": "now()",
        }).eq("id", session_id).execute()

        row = result.data[0]
        return ChatMessageResponse(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            rich_content=row.get("rich_content"),
            citations=row.get("citations"),
            tokens_used=row.get("tokens_used"),
            created_at=row["created_at"],
        )

    except Exception as e:
        logger.error(f"Chat response failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate response")


@router.get("/sessions/{session_id}")
async def get_chat_history(
    session_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
):
    """Get chat session with full message history."""
    session = supabase.table("chat_sessions").select("*").eq(
        "id", session_id
    ).eq("user_id", user["id"]).single().execute()

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = supabase.table("chat_messages").select("*").eq(
        "session_id", session_id
    ).order("created_at", desc=False).execute()

    return {
        "session": session.data,
        "messages": messages.data,
    }
