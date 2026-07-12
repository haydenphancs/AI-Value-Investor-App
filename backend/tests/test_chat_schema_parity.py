"""
Schema-parity tests for the chat pipeline (backend ↔ iOS Codable).

The unified AIChatScreen on iOS decodes EVERY chat response through `ChatSessionDTO`,
`ChatMessageDTO`, `ChatSessionListDTO`, `ChatHistoryDTO` and the polymorphic
`ChatWidgetData` (Models/ChatConversationModels.swift). A single shape drift — a
renamed key, a field iOS treats as non-optional that the backend can null, or a widget
whose `widget_type` the iOS switch can't map — crashes decode. `GET /chat/sessions/{id}`
returns ALL messages in one array, so ONE bad message kills the whole history load.

These run the REAL endpoint serializers (`_row_to_session` / `_row_to_message`) over
worst-case Supabase rows and assert the output keeps the exact contract the iOS decoder
needs. No network / Supabase — data shape only.

iOS contract pinned here (Models/ChatConversationModels.swift):
  ChatSessionDTO  — required (non-optional): id, message_count, is_saved, created_at.
                    optional: title, session_type, stock_id, preview_message, last_message_at.
  ChatMessageDTO  — required: id, session_id, role, content, created_at.
                    optional: widget, citations, tokens_used.  (backend also emits
                    rich_content, which the iOS decoder ignores — extra keys are fine.)
  ChatWidgetData  — discriminated by `widget_type`; iOS maps "market_overview" → market
                    overview, EVERYTHING ELSE → stock_chart. So the backend must only ever
                    emit a widget whose widget_type is one of those two AND which carries the
                    full set of fields that variant requires (else iOS throws on decode).
"""

from __future__ import annotations

import pytest

from app.schemas.chat import (
    ChatHistoryResponse,
    ChatMessageResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
    HistoricalDataPoint,
    MarketOverviewMacroItem,
    MarketOverviewSector,
    MarketOverviewWidget,
    StockChartWidget,
    UpdateChatSessionRequest,
)
from app.api.v1.endpoints.chat import _row_to_message, _row_to_session

# ── iOS-required (non-optional DTO property) keys ───────────────────────────
_SESSION_REQUIRED = {"id", "message_count", "is_saved", "created_at"}
_SESSION_ALL_KEYS = _SESSION_REQUIRED | {
    "title", "session_type", "stock_id", "preview_message", "last_message_at",
    # Context-aware chat (migration 085): optional on both sides. iOS decodes
    # them as Optional so absent/null is fine; when present they re-ground a
    # reloaded session on the same cached data.
    "context_type", "reference_id",
}
_MESSAGE_REQUIRED = {"id", "session_id", "role", "content", "created_at"}
_MESSAGE_ALL_KEYS = _MESSAGE_REQUIRED | {
    "widget", "widgets", "citations", "tokens_used",
    # Futuristic-chat additions (rich_content-backed; all Optional on iOS so absent/null is
    # fine and old builds ignore them): the thinking card + sources pills + follow-up chips.
    # `widgets` (Phase 2) is the multi-widget list; `widget` stays for back-compat.
    "sources", "suggestions", "thinking",
}

# iOS StockChartWidgetData / MarketOverviewWidgetData non-optional properties.
_STOCK_WIDGET_REQUIRED = {
    "widget_type", "ticker", "company_name", "current_price", "change",
    "change_percent", "day_high", "day_low", "volume", "avg_volume", "historical_data",
}
_HISTPOINT_REQUIRED = {"date", "open", "high", "low", "close", "volume"}
_MARKET_WIDGET_REQUIRED = {
    "widget_type", "pe_ratio", "forward_pe", "valuation_level", "earnings_yield",
    "historical_avg_pe", "sectors", "advancing", "declining", "macro_indicators",
}

# The ONLY two widget_type values the iOS polymorphic decoder can map to a complete shape.
_IOS_WIDGET_TYPES = {"stock_chart", "market_overview"}


def _assert_required_non_null(dumped: dict, required: set[str], where: str) -> None:
    for key in required:
        assert key in dumped, f"{where}: missing iOS-required key {key!r} (have {sorted(dumped)})"
        assert dumped[key] is not None, f"{where}: iOS-required key {key!r} is null (iOS decodes non-optional)"


def _assert_keys_subset(expected: set[str], dumped: dict, where: str) -> None:
    """Every iOS-mapped key must exist in the payload (snake_case parity); extras allowed."""
    missing = expected - dumped.keys()
    assert not missing, f"{where}: payload missing iOS-mapped keys {missing}"


# ── Sessions ────────────────────────────────────────────────────────────────

def test_worst_case_session_row_keeps_ios_required_fields():
    """A brand-new session row with every optional absent still decodes on iOS."""
    # NOT NULL DEFAULTs in the DB (message_count=0, is_saved=false) — but a row read
    # right after insert may omit them entirely; _row_to_session must still fill them.
    row = {"id": "sess-1", "created_at": "2026-06-28T00:00:00.000000+00:00"}
    dumped = _row_to_session(row).model_dump()

    _assert_keys_subset(_SESSION_ALL_KEYS, dumped, "session")
    _assert_required_non_null(dumped, _SESSION_REQUIRED, "session")
    assert isinstance(dumped["message_count"], int), "iOS messageCount is Int (non-optional)"
    assert isinstance(dumped["is_saved"], bool), "iOS isSaved is Bool (non-optional)"
    # Optionals may be null — iOS decodes them as Optional.
    assert dumped["title"] is None and dumped["last_message_at"] is None


def test_session_row_with_explicit_null_optionals():
    """Outlier: optionals present-but-null (Supabase returns the column as JSON null)."""
    row = {
        "id": "sess-2",
        "title": None, "session_type": None, "stock_id": None,
        "preview_message": None, "last_message_at": None,
        "message_count": 4, "is_saved": True,
        "created_at": "2026-06-28T12:34:56.789012+00:00",
    }
    dumped = _row_to_session(row).model_dump()
    _assert_required_non_null(dumped, _SESSION_REQUIRED, "session-nulls")
    assert dumped["message_count"] == 4 and dumped["is_saved"] is True


def test_session_row_carries_context_fields_for_regrounding():
    """A context-aware session (migration 085) round-trips context_type +
    reference_id so a history reload re-grounds on the same cached data."""
    row = {
        "id": "sess-ctx", "created_at": "2026-07-06T00:00:00.000000+00:00",
        "session_type": "REPORT",
        "context_type": "TICKER_REPORT", "reference_id": "AAPL|warren_buffett",
    }
    dumped = _row_to_session(row).model_dump()
    _assert_keys_subset(_SESSION_ALL_KEYS, dumped, "session-ctx")
    _assert_required_non_null(dumped, _SESSION_REQUIRED, "session-ctx")
    assert dumped["context_type"] == "TICKER_REPORT"
    assert dumped["reference_id"] == "AAPL|warren_buffett"


def test_legacy_session_row_has_null_context_fields():
    """A pre-085 row (no context columns) still decodes; iOS reads them as nil."""
    row = {"id": "sess-legacy", "created_at": "2026-07-06T00:00:00.000000+00:00"}
    dumped = _row_to_session(row).model_dump()
    # Keys present (so iOS's optional decode finds them) but null.
    assert "context_type" in dumped and dumped["context_type"] is None
    assert "reference_id" in dumped and dumped["reference_id"] is None


def test_session_list_shape():
    rows = [{"id": f"s{i}", "created_at": "2026-06-28T00:00:00.000000+00:00"} for i in range(3)]
    resp = ChatSessionListResponse(sessions=[_row_to_session(r) for r in rows], total=3)
    dumped = resp.model_dump()
    assert set(dumped.keys()) == {"sessions", "total"}, "iOS ChatSessionListDTO = {sessions, total}"
    assert dumped["total"] == 3 and len(dumped["sessions"]) == 3


# ── Messages ──────────────────────────────────────────────────────────────────

def test_worst_case_message_row_keeps_ios_required_fields():
    """A plain text AI message with no widget/citations/tokens decodes on iOS."""
    row = {
        "id": "msg-1", "session_id": "sess-1", "role": "assistant",
        "content": "Here is the answer.",
        "created_at": "2026-06-28T00:00:00.123456+00:00",
        # rich_content / citations / tokens_used absent
    }
    dumped = _row_to_message(row).model_dump()
    _assert_keys_subset(_MESSAGE_ALL_KEYS, dumped, "message")
    _assert_required_non_null(dumped, _MESSAGE_REQUIRED, "message")
    assert dumped["widget"] is None and dumped["citations"] is None and dumped["tokens_used"] is None


def test_message_empty_content_still_valid():
    """Outlier: a widget-only message can have empty content; iOS handles empty text."""
    row = {
        "id": "msg-e", "session_id": "s", "role": "assistant", "content": "",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
    }
    dumped = _row_to_message(row).model_dump()
    _assert_required_non_null(dumped, _MESSAGE_REQUIRED, "message-empty")
    assert dumped["content"] == ""


def test_worst_case_message_row_has_null_futuristic_fields():
    """A pre-feature row (no rich_content) → sources/suggestions/thinking all None. Old iOS
    builds ignore the keys; new builds decode them as Optional. No crash either way."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "hi",
        "created_at": "2026-07-09T00:00:00.000000+00:00",
    }
    dumped = _row_to_message(row).model_dump()
    _assert_keys_subset(_MESSAGE_ALL_KEYS, dumped, "worst-case futuristic")
    assert dumped["sources"] is None
    assert dumped["suggestions"] is None
    assert dumped["thinking"] is None


def test_message_surfaces_thinking_sources_suggestions_from_rich_content():
    """A full futuristic-chat row round-trips the thinking card + sources + follow-ups (all
    stored under rich_content, no migration) so a HISTORY RELOAD re-shows them."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "answer",
        "created_at": "2026-07-09T00:00:00.000000+00:00",
        "rich_content": {
            "widget": _stock_widget_payload(),
            "sources": [
                {"label": "Cay research report", "detail": "AAPL"},
                {"label": "SEC filing", "detail": "Risk Factors"},
            ],
            "suggestions": ["What's the valuation?", "How wide is the moat?"],
            "thinking": {
                "stages": [],
                "reasoning": "ROE can exceed margins when equity is small; check the equity base.",
                "source_count": 2, "elapsed_ms": 4200,
            },
        },
    }
    dumped = _row_to_message(row).model_dump()
    _assert_keys_subset(_MESSAGE_ALL_KEYS, dumped, "futuristic message")
    _assert_required_non_null(dumped, _MESSAGE_REQUIRED, "futuristic message")
    # widget still extracted alongside the new fields
    assert dumped["widget"]["widget_type"] == "stock_chart"
    assert isinstance(dumped["sources"], list)
    assert dumped["sources"][0]["label"] == "Cay research report"
    assert dumped["suggestions"] == ["What's the valuation?", "How wide is the moat?"]
    assert dumped["thinking"]["source_count"] == 2
    assert dumped["thinking"]["elapsed_ms"] == 4200
    assert dumped["thinking"]["reasoning"].startswith("ROE can exceed")


def test_message_citations_are_objects_not_scalars():
    """iOS decodes citations as [ChatCitationDTO] (objects). A list of scalars would crash."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "x",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "citations": [
            {"index": 1, "source": "10-K", "text": "risk factors..."},
            {"index": 2, "source": "Document", "text": ""},
        ],
    }
    dumped = _row_to_message(row).model_dump()
    assert isinstance(dumped["citations"], list)
    for c in dumped["citations"]:
        assert isinstance(c, dict), "each citation must be a JSON object (iOS ChatCitationDTO)"
        # iOS keys are all-optional, but only index/source/text are mapped.
        assert c.keys() <= {"index", "source", "text"}, f"unexpected citation keys: {c.keys()}"


# ── Widgets (polymorphic decode) ──────────────────────────────────────────────

def _stock_widget_payload() -> dict:
    return StockChartWidget(
        ticker="AAPL", company_name="Apple Inc.", current_price=200.0, change=1.5,
        change_percent=0.75, day_high=201.0, day_low=198.0, volume=50_000_000,
        avg_volume=60_000_000, is_market_open=True,
        historical_data=[HistoricalDataPoint(date="2026-06-27", open=199, high=201,
                                             low=198, close=200, volume=50_000_000)],
    ).model_dump()


def _market_widget_payload() -> dict:
    return MarketOverviewWidget(
        pe_ratio=22.0, forward_pe=20.0, valuation_level="Fair Value", earnings_yield=4.5,
        historical_avg_pe=18.0,
        sectors=[MarketOverviewSector(sector="Tech", change_percent=1.2)],
        advancing=300, declining=200,
        macro_indicators=[MarketOverviewMacroItem(title="Fed", signal="neutral")],
    ).model_dump()


def test_stock_chart_widget_matches_ios_required_keys():
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "chart",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "rich_content": {"widget": _stock_widget_payload()},
    }
    widget = _row_to_message(row).model_dump()["widget"]
    assert widget is not None, "widget must survive the rich_content extraction"
    assert widget["widget_type"] == "stock_chart"
    assert widget["widget_type"] in _IOS_WIDGET_TYPES
    _assert_keys_subset(_STOCK_WIDGET_REQUIRED, widget, "stock_chart widget")
    # Market-open flag (Optional; iOS drives the "Live"/"Closed" dot from it).
    assert widget["is_market_open"] is True
    assert isinstance(widget["historical_data"], list)
    for point in widget["historical_data"]:
        _assert_keys_subset(_HISTPOINT_REQUIRED, point, "historical_data point")


def test_market_overview_widget_matches_ios_required_keys():
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "market",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "rich_content": {"widget": _market_widget_payload()},
    }
    widget = _row_to_message(row).model_dump()["widget"]
    assert widget["widget_type"] == "market_overview"
    assert widget["widget_type"] in _IOS_WIDGET_TYPES
    _assert_keys_subset(_MARKET_WIDGET_REQUIRED, widget, "market_overview widget")
    for sec in widget["sectors"]:
        _assert_keys_subset({"sector", "change_percent"}, sec, "sector entry")
    for macro in widget["macro_indicators"]:
        _assert_keys_subset({"title", "signal"}, macro, "macro item")


def test_multi_widget_list_and_legacy_fallback():
    """Phase 2 multi-widget contract. `rich_content.widgets` is a LIST of widget payloads; the
    single `widget` stays for back-compat. A Phase-2 row exposes the full list AND mirrors the
    first into `widget` (old iOS renders one); a LEGACY row (only `widget`) exposes
    widgets == [widget] (new iOS renders the list); no widget → both None."""
    w1, w2 = _stock_widget_payload(), _market_widget_payload()
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "compare",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "rich_content": {"widgets": [w1, w2], "widget": w1},
    }
    dumped = _row_to_message(row).model_dump()
    assert isinstance(dumped["widgets"], list) and len(dumped["widgets"]) == 2
    assert dumped["widgets"][0]["widget_type"] == "stock_chart"
    assert dumped["widgets"][1]["widget_type"] == "market_overview"
    assert dumped["widget"]["widget_type"] == "stock_chart"      # back-compat single
    for w in dumped["widgets"]:
        assert w["widget_type"] in _IOS_WIDGET_TYPES

    # Legacy row (single widget only) → widgets falls back to [widget] for new iOS builds.
    legacy = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "chart",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "rich_content": {"widget": _stock_widget_payload()},
    }
    d2 = _row_to_message(legacy).model_dump()
    assert d2["widget"] is not None
    assert isinstance(d2["widgets"], list) and len(d2["widgets"]) == 1
    assert d2["widgets"][0]["widget_type"] == "stock_chart"

    # No widget at all → both None (plain text message).
    plain = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "hi",
        "created_at": "2026-06-28T00:00:00.000000+00:00", "rich_content": {},
    }
    d3 = _row_to_message(plain).model_dump()
    assert d3["widget"] is None and d3["widgets"] is None


def test_canonical_widgets_only_emit_ios_decodable_types():
    """
    Guard the polymorphic contract: iOS maps widget_type "market_overview" → market overview
    and EVERYTHING ELSE → stock_chart. So the backend must never emit a widget whose type the
    iOS switch can't satisfy with a complete shape. Both canonical widgets carry a recognized
    discriminator; if a third widget type is ever added here, iOS must learn it FIRST.
    """
    assert StockChartWidget.model_fields["widget_type"].default == "stock_chart"
    assert MarketOverviewWidget.model_fields["widget_type"].default == "market_overview"
    assert {"stock_chart", "market_overview"} == _IOS_WIDGET_TYPES


def test_rich_content_without_widget_yields_no_widget():
    """Outlier: rich_content present but no 'widget' key → iOS widget stays nil (no crash)."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "x",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "rich_content": {"something_else": 1},
    }
    assert _row_to_message(row).model_dump()["widget"] is None


# ── History bundle ────────────────────────────────────────────────────────────

def test_history_response_shape_and_empty_messages():
    session = _row_to_session({"id": "s", "created_at": "2026-06-28T00:00:00.000000+00:00"})
    resp = ChatHistoryResponse(session=session, messages=[])
    dumped = resp.model_dump()
    assert set(dumped.keys()) == {"session", "messages"}, "iOS ChatHistoryDTO = {session, messages}"
    assert dumped["messages"] == []  # empty history must validate (new session)


def test_history_with_mixed_messages_all_decodable():
    session = _row_to_session({"id": "s", "created_at": "2026-06-28T00:00:00.000000+00:00"})
    rows = [
        {"id": "u", "session_id": "s", "role": "user", "content": "hi",
         "created_at": "2026-06-28T00:00:01.000000+00:00"},
        {"id": "a", "session_id": "s", "role": "assistant", "content": "chart",
         "created_at": "2026-06-28T00:00:02.000000+00:00",
         "rich_content": {"widget": _stock_widget_payload()}},
    ]
    resp = ChatHistoryResponse(session=session, messages=[_row_to_message(r) for r in rows])
    dumped = resp.model_dump()
    for msg in dumped["messages"]:
        _assert_required_non_null(msg, _MESSAGE_REQUIRED, "history message")


# ── History search: the session title is the NAME the iOS search matches ───────
#
# The iOS history search (AIChatScreen.filteredHistoryGroups) filters on
# ChatHistoryItem.title, which is ChatSessionDTO.title (falling back to "Chat").
# So `GET /chat/sessions` MUST carry the session's real title through verbatim, or
# the user can't search by name. These pin that the title survives serialization
# for ordinary, outlier, and unicode names.

@pytest.mark.parametrize("title", [
    "Drawing the Battle Lines",          # a real book-core name the user would search
    "AAPL — should I buy?",              # punctuation / em dash
    "  leading and trailing spaces  ",   # not trimmed server-side; iOS lowercases+contains
    "Δívïdéñd stratégy 📈",              # unicode + emoji
    "x" * 200,                            # very long
])
def test_session_title_is_preserved_verbatim_for_search(title):
    row = {"id": "s", "title": title, "preview_message": "some preview",
           "created_at": "2026-06-28T00:00:00.000000+00:00"}
    dumped = _row_to_session(row).model_dump()
    assert dumped["title"] == title, "title must pass through so iOS can search by name"
    assert dumped["preview_message"] == "some preview"


def test_session_null_title_becomes_ios_chat_fallback():
    """A session with no title serializes title=None; iOS shows/searches 'Chat' (title ?? 'Chat')."""
    row = {"id": "s", "created_at": "2026-06-28T00:00:00.000000+00:00"}  # no title key
    assert _row_to_session(row).model_dump()["title"] is None


# ── Pin / Rename update path (partial update must not clobber the other field) ──

def test_pin_request_omits_title_so_title_is_not_wiped():
    """
    Pinning sends only is_saved (iOS omits nil title via encodeIfPresent). The request model must
    read title as None so `update_chat_session` skips it (`if request.title is not None`) and the
    existing title is preserved. A wiped title would blank the searchable name.
    """
    req = UpdateChatSessionRequest(**{"is_saved": True})  # body carries NO 'title' key
    assert req.title is None, "omitted title must be None so the endpoint skips it"
    assert req.is_saved is True
    # Endpoint logic (mirrors chat.py): only provided fields get written.
    update_data = {}
    if req.title is not None:
        update_data["title"] = req.title
    if req.is_saved is not None:
        update_data["is_saved"] = req.is_saved
    assert update_data == {"is_saved": True}, "pin must not touch title"


def test_rename_request_omits_is_saved_so_pin_state_is_kept():
    req = UpdateChatSessionRequest(**{"title": "Renamed chat"})  # body carries NO 'is_saved' key
    assert req.is_saved is None
    assert req.title == "Renamed chat"
    update_data = {}
    if req.title is not None:
        update_data["title"] = req.title
    if req.is_saved is not None:
        update_data["is_saved"] = req.is_saved
    assert update_data == {"title": "Renamed chat"}, "rename must not touch is_saved"


def test_update_response_row_decodes_like_a_list_row():
    """
    `update_chat_session` returns _row_to_session(result.data[0]) — the SAME shape as list/create,
    so the iOS ChatSessionDTO decode of the update response has identical required-field guarantees.
    Worst case: the updated row omits the optionals; required fields must still be present + non-null.
    """
    updated_row = {"id": "s", "is_saved": True, "message_count": 6,
                   "created_at": "2026-06-28T00:00:00.000000+00:00"}
    dumped = _row_to_session(updated_row).model_dump()
    _assert_keys_subset(_SESSION_ALL_KEYS, dumped, "update response")
    _assert_required_non_null(dumped, _SESSION_REQUIRED, "update response")
    assert dumped["is_saved"] is True and dumped["message_count"] == 6
