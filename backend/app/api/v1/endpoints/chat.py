"""
Chat Endpoints — with RAG pipeline
Frontend: POST /chat/sessions, GET /chat/sessions,
          POST /chat/sessions/{id}/messages, GET /chat/sessions/{id},
          DELETE /chat/sessions/{id},
          PATCH /chat/sessions/{id} (update title)
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
import logging

from app.database import get_supabase
from app.dependencies import get_current_user_or_guest
from app.schemas.chat import (
    CreateChatSessionRequest,
    SendChatMessageRequest,
    UpdateChatSessionRequest,
    ChatSessionResponse,
    ChatMessageResponse,
    ChatSessionListResponse,
    ChatHistoryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Helpers ─────────────────────────────────────────────────────────

def _row_to_session(row: dict) -> ChatSessionResponse:
    """Map a Supabase chat_sessions row to the response schema."""
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


def _row_to_message(row: dict) -> ChatMessageResponse:
    """Map a Supabase chat_messages row to the response schema."""
    stored_widget = None
    if row.get("rich_content") and isinstance(row["rich_content"], dict):
        stored_widget = row["rich_content"].get("widget")

    return ChatMessageResponse(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        widget=stored_widget,
        rich_content=row.get("rich_content"),
        citations=row.get("citations"),
        tokens_used=row.get("tokens_used"),
        created_at=row["created_at"],
    )


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """List all chat sessions for the current user, newest first."""
    result = (
        supabase.table("chat_sessions")
        .select("*")
        .eq("user_id", user["id"])
        .order("last_message_at", desc=True, nullsfirst=False)
        .range(offset, offset + limit - 1)
        .execute()
    )

    sessions = [_row_to_session(r) for r in (result.data or [])]
    return ChatSessionListResponse(sessions=sessions, total=len(sessions))


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_chat_session(
    request: CreateChatSessionRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Create a new chat session."""
    now_iso = datetime.now(timezone.utc).isoformat()
    session_data = {
        "user_id": user["id"],
        "session_type": "STOCK" if request.stock_id else "NORMAL",
        "stock_id": request.stock_id,
        "title": f"Chat about {request.stock_id}" if request.stock_id else "New Chat",
        "last_message_at": now_iso,
    }

    result = supabase.table("chat_sessions").insert(session_data).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create session")

    return _row_to_session(result.data[0])


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def send_chat_message(
    session_id: str,
    request: SendChatMessageRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Send a message and get AI response with RAG."""
    # Verify session ownership
    session = (
        supabase.table("chat_sessions")
        .select("*")
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )

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

        # Build the widget payload (if Gemini triggered the stock tool)
        widget_payload = ai_result.get("widget")

        # Save AI message
        ai_msg: dict = {
            "session_id": session_id,
            "role": "assistant",
            "content": ai_result["content"],
            "citations": ai_result.get("citations"),
            "tokens_used": ai_result.get("tokens_used"),
        }
        # Persist widget in rich_content column so history reloads work
        if widget_payload:
            ai_msg["rich_content"] = {"widget": widget_payload}

        result = supabase.table("chat_messages").insert(ai_msg).execute()

        # Update session metadata with proper timestamp
        now_iso = datetime.now(timezone.utc).isoformat()
        preview = ai_result["content"][:100]
        current_count = session.data.get("message_count", 0)

        # Auto-title: use first user message as title if still "New Chat"
        update_payload: dict = {
            "message_count": current_count + 2,
            "preview_message": preview,
            "last_message_at": now_iso,
        }
        if session.data.get("title") in ("New Chat", None):
            update_payload["title"] = request.message[:80]

        supabase.table("chat_sessions").update(update_payload).eq(
            "id", session_id
        ).execute()

        row = result.data[0]
        return _row_to_message(row)

    except Exception as e:
        logger.error(f"Chat response failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate response")


@router.get("/sessions/{session_id}", response_model=ChatHistoryResponse)
async def get_chat_history(
    session_id: str,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Get chat session with full message history."""
    session = (
        supabase.table("chat_sessions")
        .select("*")
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )

    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = (
        supabase.table("chat_messages")
        .select("*")
        .eq("session_id", session_id)
        .order("created_at", desc=False)
        .execute()
    )

    return ChatHistoryResponse(
        session=_row_to_session(session.data),
        messages=[_row_to_message(m) for m in (messages.data or [])],
    )


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_chat_session(
    session_id: str,
    request: UpdateChatSessionRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Update a chat session (title, is_saved)."""
    # Verify ownership
    session = (
        supabase.table("chat_sessions")
        .select("id")
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    update_data = {}
    if request.title is not None:
        update_data["title"] = request.title
    if request.is_saved is not None:
        update_data["is_saved"] = request.is_saved

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = (
        supabase.table("chat_sessions")
        .update(update_data)
        .eq("id", session_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to update session")

    return _row_to_session(result.data[0])


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Delete a chat session and all its messages."""
    # Verify ownership
    session = (
        supabase.table("chat_sessions")
        .select("id")
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    # Delete messages first (child records)
    supabase.table("chat_messages").delete().eq(
        "session_id", session_id
    ).execute()

    # Delete session
    supabase.table("chat_sessions").delete().eq("id", session_id).execute()

    return {"status": "deleted", "session_id": session_id}
