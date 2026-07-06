"""
Chat Endpoints — with RAG pipeline
Frontend: POST /chat/sessions, GET /chat/sessions,
          POST /chat/sessions/{id}/messages, GET /chat/sessions/{id},
          DELETE /chat/sessions/{id},
          PATCH /chat/sessions/{id} (update title)
"""

import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
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
        context_type=row.get("context_type"),
        reference_id=row.get("reference_id"),
        preview_message=row.get("preview_message"),
        message_count=row.get("message_count", 0),
        is_saved=row.get("is_saved", False),
        created_at=row["created_at"],
        last_message_at=row.get("last_message_at"),
    )


# Map the screen's context type → the session_type the iOS history badge knows
# (ChatConversationModels.historyItemType: STOCK/BOOK/CONCEPT/JOURNEY/REPORT/NORMAL).
_CONTEXT_TO_SESSION_TYPE = {
    "TICKER_REPORT": "REPORT",
    "STOCK": "STOCK",
    "ETF": "STOCK",
    "CRYPTO": "STOCK",
    "INDEX": "STOCK",
    "COMMODITY": "STOCK",
    "MONEY_MOVES_ARTICLE": "CONCEPT",
    "JOURNEY_LESSON": "JOURNEY",
    "BOOK": "BOOK",
}


def _session_type_for(context_type: Optional[str], stock_id: Optional[str]) -> str:
    """Derive the persisted session_type from context_type (falls back to the
    legacy stock_id → STOCK / NORMAL rule when no context type is sent)."""
    if context_type:
        mapped = _CONTEXT_TO_SESSION_TYPE.get(context_type.strip().upper())
        if mapped:
            return mapped
    return "STOCK" if stock_id else "NORMAL"


def _sse(event: str, data: dict) -> str:
    """Format a single Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


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
        "session_type": _session_type_for(request.context_type, request.stock_id),
        "stock_id": request.stock_id,
        "context_type": request.context_type,
        "reference_id": request.reference_id,
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
    try:
        session = (
            supabase.table("chat_sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Chat session not found")

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

        # Prefer the session-persisted context (so a history reload re-grounds),
        # but let a per-message request value override (e.g. the seed message).
        ctx_type = request.context_type or session.data.get("context_type")
        ref_id = request.reference_id or session.data.get("reference_id")

        ai_result = await chat_service.generate_response(
            session_id=session_id,
            user_message=request.message,
            session_type=session.data.get("session_type", "NORMAL"),
            stock_id=session.data.get("stock_id"),
            context=request.context,
            context_type=ctx_type,
            reference_id=ref_id,
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

        # Auto-title from the user's first question so history search-by-name matches the topic.
        # This upgrades the auto-generated defaults ONLY — "New Chat"/None (general chats) AND the
        # "Chat about <TICKER>" default given to asset/report chats — and only on the first exchange
        # (message_count == 0), so a later message or a user rename is never clobbered. Guard against
        # an empty/whitespace first message so we never blank a useful title.
        update_payload: dict = {
            "message_count": current_count + 2,
            "preview_message": preview,
            "last_message_at": now_iso,
        }
        existing_title = session.data.get("title")
        is_generic_title = (
            existing_title in ("New Chat", None)
            or (isinstance(existing_title, str) and existing_title.startswith("Chat about "))
        )
        first_question = request.message.strip()
        if current_count == 0 and is_generic_title and first_question:
            update_payload["title"] = first_question[:80]

        supabase.table("chat_sessions").update(update_payload).eq(
            "id", session_id
        ).execute()

        row = result.data[0]
        return _row_to_message(row)

    except Exception as e:
        logger.error(f"Chat response failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate response")


@router.post("/sessions/{session_id}/messages/stream")
async def stream_chat_message(
    session_id: str,
    request: SendChatMessageRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Stream an AI response over SSE (``text/event-stream``).

    Frames: ``meta`` → ``token``* → ``done``, or ``reset`` (discard partial
    tokens) before a fallback ``done``, or ``error``. Nothing is persisted until
    a COMPLETE answer exists (streamed, or via the server-side full-generation
    fallback), so a dropped stream leaves no half-message and the iOS client can
    safely retry via the non-streaming endpoint without duplicating the turn.
    """
    # Verify ownership up front so a bad session is a real 404 (not an SSE frame).
    try:
        session = (
            supabase.table("chat_sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if not session.data:
        raise HTTPException(status_code=404, detail="Chat session not found")

    sdata = session.data
    ctx_type = request.context_type or sdata.get("context_type")
    ref_id = request.reference_id or sdata.get("reference_id")
    session_type = sdata.get("session_type", "NORMAL")
    stock_id = sdata.get("stock_id")
    user_message = request.message

    async def event_gen():
        from app.services.chat_service import ChatService
        from app.integrations.gemini import _is_quota_error

        chat_service = ChatService()

        grounded = (
            f"{ctx_type}:{ref_id}"
            if ctx_type and ctx_type.strip().upper() != "NONE"
            else ""
        )
        yield _sse("meta", {"session_id": session_id, "grounded_on": grounded})

        content: Optional[str] = None
        citations = None
        widget = None
        tokens_used = None
        streamed_any = False

        try:
            prep = await chat_service.prepare_stream_generation(
                session_id=session_id,
                user_message=user_message,
                session_type=session_type,
                stock_id=stock_id,
                context=request.context,
                context_type=ctx_type,
                reference_id=ref_id,
            )
            acc: List[str] = []
            async for delta in chat_service.gemini.stream_text(
                prep["prompt"], system_instruction=prep["system_instruction"]
            ):
                acc.append(delta)
                streamed_any = True
                yield _sse("token", {"delta": delta})

            joined = "".join(acc).strip()
            if not joined:
                raise RuntimeError("empty stream result")
            content = "".join(acc)
            citations = prep.get("citations")
            widget = prep.get("widget")

        except Exception as e:
            # Stream failed (quota / timeout / empty / disconnect). Fall back to
            # the full non-streaming generation so the user still gets an answer.
            logger.warning(
                "Chat stream failed (%s: %s) — falling back to full generation",
                type(e).__name__, e,
            )
            try:
                ai_result = await chat_service.generate_response(
                    session_id=session_id,
                    user_message=user_message,
                    session_type=session_type,
                    stock_id=stock_id,
                    context=request.context,
                    context_type=ctx_type,
                    reference_id=ref_id,
                )
                content = ai_result.get("content")
                citations = ai_result.get("citations")
                widget = ai_result.get("widget")
                tokens_used = ai_result.get("tokens_used")
                if streamed_any:
                    # Discard any partial tokens before the full answer replaces them.
                    yield _sse("reset", {})
            except Exception as e2:
                logger.error("Chat stream fallback failed: %s", e2, exc_info=True)
                code = "GEMINI_QUOTA_EXCEEDED" if _is_quota_error(e2) else "INTERNAL_ERROR"
                yield _sse("error", {
                    "error_code": code,
                    "user_message": "Cay AI couldn't respond right now. Please try again.",
                })
                return

        if not content:
            yield _sse("error", {
                "error_code": "INTERNAL_ERROR",
                "user_message": "Cay AI couldn't respond right now. Please try again.",
            })
            return

        # Persist the turn (user + assistant) only now that the answer is complete.
        try:
            supabase.table("chat_messages").insert({
                "session_id": session_id, "role": "user", "content": user_message,
            }).execute()

            ai_msg: dict = {
                "session_id": session_id,
                "role": "assistant",
                "content": content,
                "citations": citations,
                "tokens_used": tokens_used,
            }
            if widget:
                ai_msg["rich_content"] = {"widget": widget}
            inserted = supabase.table("chat_messages").insert(ai_msg).execute()

            # Session metadata + first-question auto-title (mirrors send_chat_message).
            now_iso = datetime.now(timezone.utc).isoformat()
            current_count = sdata.get("message_count", 0)
            update_payload: dict = {
                "message_count": current_count + 2,
                "preview_message": content[:100],
                "last_message_at": now_iso,
            }
            existing_title = sdata.get("title")
            is_generic_title = (
                existing_title in ("New Chat", None)
                or (isinstance(existing_title, str) and existing_title.startswith("Chat about "))
            )
            first_question = user_message.strip()
            if current_count == 0 and is_generic_title and first_question:
                update_payload["title"] = first_question[:80]
            supabase.table("chat_sessions").update(update_payload).eq(
                "id", session_id
            ).execute()

            yield _sse("done", {"message": _row_to_message(inserted.data[0]).model_dump()})
        except Exception as e:
            logger.error("Chat stream persist failed: %s", e, exc_info=True)
            yield _sse("error", {
                "error_code": "INTERNAL_ERROR",
                "user_message": "Your answer was generated but couldn't be saved. Please try again.",
            })

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # defeat proxy buffering (Railway/nginx)
            "Connection": "keep-alive",
        },
    )


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
