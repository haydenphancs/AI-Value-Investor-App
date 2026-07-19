"""
Chat Endpoints — with RAG pipeline
Frontend: POST /chat/sessions, GET /chat/sessions,
          POST /chat/sessions/{id}/messages, GET /chat/sessions/{id},
          DELETE /chat/sessions/{id},
          PATCH /chat/sessions/{id} (update title)
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from supabase import Client
import logging

from app.config import settings
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


def _effective_context(req_context: Optional[str], session_row: dict) -> Optional[str]:
    """The on-screen grounding snapshot to feed the LLM this turn.

    Prefer the per-message value iOS sends from a LIVE detail screen; on a
    history reopen iOS sends none, so fall back to the snapshot persisted from
    when the chat was first opened (migration 087). Returns None when neither
    exists — pre-migration rows read no column, so behavior is identical to today.
    """
    if req_context:
        return req_context
    stored = session_row.get("context_snapshot")
    return stored or None


def _persist_context_snapshot(
    supabase: Client, session_id: str, req_context: Optional[str], session_row: dict
) -> None:
    """Best-effort: persist the live on-screen snapshot so a later history reopen
    can re-ground on the exact data the user saw (migration 087).

    Deliberately ISOLATED + guarded: a missing column (a code deploy that raced
    ahead of the migration) or any transient DB error must NEVER break the chat
    turn — worst case the reopen simply isn't grounded on the snapshot, which is
    today's behavior. Skips the write when there's nothing new to store (reopen
    turns send no context; live turns resend the same frozen snapshot every
    message — so this writes once, then no-ops for the rest of the session).
    """
    if not req_context or req_context == session_row.get("context_snapshot"):
        return
    try:
        supabase.table("chat_sessions").update(
            {"context_snapshot": req_context}
        ).eq("id", session_id).execute()
    except Exception as e:
        logger.warning(
            "Chat context_snapshot persist failed (%s: %s) — history reopen won't re-ground on it",
            type(e).__name__, e,
        )


def _sse(event: str, data: dict) -> str:
    """Format a single Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _row_to_message(row: dict) -> ChatMessageResponse:
    """Map a Supabase chat_messages row to the response schema."""
    rc = row.get("rich_content") if isinstance(row.get("rich_content"), dict) else None
    # `widgets` (list) is the Phase-2 multi-widget field; `widget` (single) stays for back-compat
    # with old iOS builds. Fall the list back to the single widget for legacy rows.
    stored_widgets = rc.get("widgets") if rc else None
    stored_widget = rc.get("widget") if rc else None
    if not stored_widgets and stored_widget:
        stored_widgets = [stored_widget]
    # Futuristic-chat fields live in rich_content (no schema migration). Absent → None,
    # so legacy rows and old iOS builds decode unchanged.
    sources = rc.get("sources") if rc else None
    suggestions = rc.get("suggestions") if rc else None
    thinking = rc.get("thinking") if rc else None

    return ChatMessageResponse(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        widget=stored_widget,
        widgets=stored_widgets,
        rich_content=row.get("rich_content"),
        citations=row.get("citations"),
        tokens_used=row.get("tokens_used"),
        sources=sources,
        suggestions=suggestions,
        thinking=thinking,
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

    # Generate the AI response FIRST, then persist the user + assistant rows TOGETHER in one insert.
    # A generation failure therefore leaves NOTHING persisted (no orphaned user row for the client's
    # stream-failure reconcile to later duplicate), and the two rows commit atomically — matching the
    # streaming endpoint's persist contract.
    try:
        from app.services.chat_service import ChatService

        chat_service = ChatService()

        # Prefer the session-persisted context (so a history reload re-grounds),
        # but let a per-message request value override (e.g. the seed message).
        ctx_type = request.context_type or session.data.get("context_type")
        ref_id = request.reference_id or session.data.get("reference_id")
        # On a live turn iOS ships the on-screen snapshot; on a history reopen it
        # sends none → replay the snapshot persisted at open time (migration 087).
        effective_context = _effective_context(request.context, session.data)
        # True only when a stored snapshot is being replayed (reopen) — so the
        # prompt labels it as a point-in-time copy, not live data.
        context_is_replayed = not request.context and bool(effective_context)

        ai_result = await chat_service.generate_response(
            session_id=session_id,
            user_message=request.message,
            session_type=session.data.get("session_type", "NORMAL"),
            stock_id=session.data.get("stock_id"),
            context=effective_context,
            context_type=ctx_type,
            reference_id=ref_id,
            context_is_replayed=context_is_replayed,
        )

        # Build the widget payload (if Gemini triggered the stock tool)
        widget_payload = ai_result.get("widget")

        # Explicit created_at keeps user-before-assistant ordering: a single multi-row insert would
        # otherwise stamp both rows with the same now() default, and get_chat_history orders by
        # created_at asc — the assistant could sort ahead of the question.
        now = datetime.now(timezone.utc)
        user_msg: dict = {
            "session_id": session_id,
            "role": "user",
            "content": request.message,
            "created_at": now.isoformat(),
        }
        ai_msg: dict = {
            "session_id": session_id,
            "role": "assistant",
            "content": ai_result["content"],
            "citations": ai_result.get("citations"),
            "tokens_used": ai_result.get("tokens_used"),
            "created_at": (now + timedelta(milliseconds=1)).isoformat(),
        }
        # Persist widget in rich_content column so history reloads work
        if widget_payload:
            ai_msg["rich_content"] = {"widget": widget_payload}

        result = supabase.table("chat_messages").insert([user_msg, ai_msg]).execute()
        assistant_row = next(
            (r for r in (result.data or []) if r.get("role") == "assistant"), None
        )
        if assistant_row is None:
            raise RuntimeError("assistant row missing from chat_messages insert result")

        # Update session metadata. message_count + last_message_at are maintained atomically by the
        # trg_chat_message_count AFTER-INSERT trigger (one +1 per inserted row), so we do NOT set them
        # here — an absolute `current_count + 2` from a request-start snapshot both double-counts the
        # trigger and races/undercounts on concurrent same-session sends.
        preview = ai_result["content"][:100]
        current_count = session.data.get("message_count", 0)

        # Auto-title from the user's first question so history search-by-name matches the topic.
        # This upgrades the auto-generated defaults ONLY — "New Chat"/None (general chats) AND the
        # "Chat about <TICKER>" default given to asset/report chats — and only on the first exchange
        # (message_count == 0), so a later message or a user rename is never clobbered. Guard against
        # an empty/whitespace first message so we never blank a useful title.
        update_payload: dict = {
            "preview_message": preview,
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

        # Persist the on-screen snapshot (best-effort, guarded) so a later reopen re-grounds.
        _persist_context_snapshot(supabase, session_id, request.context, session.data)

        return _row_to_message(assistant_row)

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
    # Live turn → the iOS on-screen snapshot; history reopen (context=None) →
    # the snapshot persisted at open time (migration 087).
    effective_context = _effective_context(request.context, sdata)
    # True only when a stored snapshot is being replayed (reopen) — labels it as
    # a point-in-time copy in the prompt so stale figures aren't answered as live.
    context_is_replayed = not request.context and bool(effective_context)
    session_type = sdata.get("session_type", "NORMAL")
    stock_id = sdata.get("stock_id")
    user_message = request.message

    async def event_gen():
        import time as _time
        from app.services.chat_service import ChatService
        from app.integrations.gemini import _is_quota_error

        chat_service = ChatService()
        started = _time.monotonic()

        grounded = (
            f"{ctx_type}:{ref_id}"
            if ctx_type and ctx_type.strip().upper() != "NONE"
            else ""
        )
        yield _sse("meta", {"session_id": session_id, "grounded_on": grounded})

        content: Optional[str] = None
        citations = None
        widgets: list = []
        tokens_used = None
        sources = None
        suggestions = None
        streamed_any = False

        # The model streams REAL reasoning: stream_text tags each chunk as ("thought"|"answer", text).
        # Thoughts → the thinking card (`reasoning` frames), answer → the bubble (`token` frames).
        # Reasoning is model text → it rides the same identity-guarded system instruction which
        # forbids "AI/model" mentions.
        reasoning_text = ""
        answer_parts: list = []
        reasoning_parts: list = []

        try:
            # Multi-agent (Phase 3): a cheap router picks the specialist lens(es). Run it in PARALLEL
            # with prep so the router's ~400ms hides behind the RAG/widget work. Never raises → general.
            from app.services.agents.chat_router import route_question
            from app.services.agents.chat_specialists import apply_specialist
            prep_coro = chat_service.prepare_stream_generation(
                session_id=session_id,
                user_message=user_message,
                session_type=session_type,
                stock_id=stock_id,
                context=effective_context,
                context_type=ctx_type,
                reference_id=ref_id,
                context_is_replayed=context_is_replayed,
            )
            if settings.CHAT_MULTI_AGENT_ENABLED:
                prep, route = await asyncio.gather(
                    prep_coro, route_question(chat_service.gemini, user_message),
                )
            else:
                prep = await prep_coro
                route = {"specialists": ["general"], "mode": "single", "labels": ["General"]}

            # Capture sources up-front so they survive even if streaming later fails and we
            # fall back to full generation below.
            sources = prep.get("sources")
            if sources:
                yield _sse("sources", {"sources": sources})
            # Surface the routing decision (a real specialist / a synthesis) for the thinking card.
            if route["specialists"] != ["general"]:
                yield _sse("routing", {
                    "specialists": route["specialists"],
                    "labels": route["labels"],
                    "mode": route["mode"],
                })

            # Agentic streaming: the model may call tools (analyst / sentiment / chart / …)
            # mid-stream. thought → reasoning card, answer → bubble, tool → progress + widget.
            from app.services.agents.chat_tools import (
                build_chat_tool_declarations, build_chat_tool_handlers,
                widget_from_tool_result, widget_key,
            )
            asset_type = prep.get("asset_type") or "NORMAL"
            tools = build_chat_tool_declarations(include_market_overview=(asset_type == "INDEX"))
            handlers = build_chat_tool_handlers(chat_service)

            # Start with the deterministic base widget (so an asset-detail chat always shows its
            # chart); agentic tool calls add more, deduped by (widget_type, ticker).
            seen_widgets: set = set()
            base_widget = prep.get("widget")
            if base_widget:
                widgets.append(base_widget)
                seen_widgets.add(widget_key(base_widget))

            # Single mode: one specialist streams its focused agentic answer. Synthesize mode: several
            # specialists run in parallel + a merged answer streams (their widgets arrive as
            # ("widget", …) events since the specialist runs aren't streamed to the client directly).
            if route["mode"] == "synthesize":
                answer_stream = chat_service.stream_synthesis(prep, user_message, route, tools, handlers)
            else:
                system_instruction = apply_specialist(prep["system_instruction"], route["specialists"][0])
                answer_stream = chat_service.gemini.stream_agentic(
                    prep["prompt"], tools=tools, tool_handlers=handlers,
                    system_instruction=system_instruction,
                )

            async for kind, payload in answer_stream:
                streamed_any = True
                if kind == "thought":
                    reasoning_parts.append(payload)
                    yield _sse("reasoning", {"delta": payload})
                elif kind == "answer":
                    answer_parts.append(payload)
                    yield _sse("token", {"delta": payload})
                elif kind == "tool":
                    # Real progress into the thinking card + collect any renderable widget.
                    yield _sse("tool_step", {"name": payload.get("name"), "args": payload.get("args")})
                    w = widget_from_tool_result(payload.get("result"))
                    if w is not None and widget_key(w) not in seen_widgets:
                        seen_widgets.add(widget_key(w))
                        widgets.append(w)
                elif kind == "widget":
                    # Synthesis path: a specialist's widget (already the full payload).
                    if payload is not None and widget_key(payload) not in seen_widgets:
                        seen_widgets.add(widget_key(payload))
                        widgets.append(payload)

            content = "".join(answer_parts)
            reasoning_text = "".join(reasoning_parts)
            if not content.strip():
                raise RuntimeError("empty stream result")
            citations = prep.get("citations")

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
                    context=effective_context,
                    context_type=ctx_type,
                    reference_id=ref_id,
                    context_is_replayed=context_is_replayed,
                )
                content = ai_result.get("content")
                citations = ai_result.get("citations")
                fb_widget = ai_result.get("widget")
                widgets = [fb_widget] if fb_widget else []  # discard streamed widgets; fallback replaces
                tokens_used = ai_result.get("tokens_used")
                # The aborted stream's thoughts don't correspond to this fallback answer — drop them
                # so the persisted thinking card matches (the `reset` frame clears the live display).
                reasoning_text = ""
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

        # Guardrail monitoring (non-blocking): surface advice-boundary / identity-leak drift for
        # review. We log, not block — a false positive dropping a good answer is worse than a flag.
        from app.services.agents.chat_guardrails import scan_answer
        _issues = scan_answer(content)
        if _issues:
            logger.warning("Chat guardrail flags %s (session=%s): %r", _issues, session_id, content[:200])

        elapsed_ms = int((_time.monotonic() - started) * 1000)
        thinking_payload = {
            "stages": [],                    # canned steps replaced by the streamed reasoning below
            "reasoning": reasoning_text,
            "source_count": len(sources) if sources else 0,
            "elapsed_ms": elapsed_ms,
        }

        # Persist the turn FIRST — BEFORE the best-effort follow-up-suggestions call below. That
        # call can park for minutes on a throttled Gemini (retry × 90s timeout); the user has
        # already read the streamed answer, so a disconnect in that window CANCELS this generator
        # (CancelledError is a BaseException — uncaught by the except-Exception guards). Writing the
        # durable turn up-front guarantees the answered exchange is never lost from history.
        try:
            # rich_content carries the widget + futuristic-chat fields (thinking / sources /
            # suggestions) in one JSONB column — no schema migration. Suggestions are added AFTER
            # this durable write (below), so they can never block or drop it.
            rich_content: dict = {"thinking": thinking_payload}
            if widgets:
                rich_content["widgets"] = widgets
                rich_content["widget"] = widgets[0]   # back-compat: old iOS builds read `widget`
            if sources:
                rich_content["sources"] = sources

            # Persist the user + assistant rows TOGETHER in ONE insert so the turn is atomic: a
            # failing assistant write can never leave an orphaned user row for the client's
            # stream-failure reconcile to later duplicate. Explicit created_at preserves
            # user-before-assistant ordering (a single multi-row insert would otherwise stamp both
            # rows with the same now() default, and get_chat_history orders by created_at asc).
            now = datetime.now(timezone.utc)
            user_row: dict = {
                "session_id": session_id, "role": "user", "content": user_message,
                "created_at": now.isoformat(),
            }
            ai_msg: dict = {
                "session_id": session_id,
                "role": "assistant",
                "content": content,
                "citations": citations,
                "tokens_used": tokens_used,
                "rich_content": rich_content,
                "created_at": (now + timedelta(milliseconds=1)).isoformat(),
            }
            inserted = supabase.table("chat_messages").insert([user_row, ai_msg]).execute()
            assistant_row = next(
                (r for r in (inserted.data or []) if r.get("role") == "assistant"), None
            )
            if assistant_row is None:
                raise RuntimeError("assistant row missing from chat_messages insert result")

            # Session metadata + first-question auto-title (mirrors send_chat_message). message_count
            # and last_message_at are maintained by the trg_chat_message_count AFTER-INSERT trigger, so
            # we don't set them here (avoids double-counting the trigger + the concurrent-send race).
            current_count = sdata.get("message_count", 0)
            update_payload: dict = {
                "preview_message": content[:100],
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

            # Persist the on-screen snapshot (best-effort, guarded) so a later reopen re-grounds.
            _persist_context_snapshot(supabase, session_id, request.context, sdata)
        except Exception as e:
            logger.error("Chat stream persist failed: %s", e, exc_info=True)
            yield _sse("error", {
                "error_code": "INTERNAL_ERROR",
                "user_message": "Your answer was generated but couldn't be saved. Please try again.",
            })
            return

        # Follow-up suggestions — best-effort, AFTER the durable write. Being slow or cancelled here
        # can no longer drop the saved turn (worst case: no chips, which degrade gracefully).
        try:
            suggestions = await chat_service.generate_followup_suggestions(
                user_message=user_message,
                answer=content,
                context_type=ctx_type,
                reference_id=ref_id,
            )
            if suggestions:
                yield _sse("suggestions", {"questions": suggestions})
                # Reflect them in the terminal `done` message + persist so a reload shows the chips.
                rich_content["suggestions"] = suggestions
                assistant_row["rich_content"] = rich_content
                try:
                    supabase.table("chat_messages").update(
                        {"rich_content": rich_content}
                    ).eq("id", assistant_row["id"]).execute()
                except Exception as e:
                    logger.warning(
                        "Chat suggestions persist failed (%s: %s) — chips shown live only",
                        type(e).__name__, e,
                    )
        except Exception as e:
            logger.warning("Chat suggestions step failed (%s: %s) — skipping", type(e).__name__, e)
            suggestions = None

        yield _sse("done", {"message": _row_to_message(assistant_row).model_dump()})

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
