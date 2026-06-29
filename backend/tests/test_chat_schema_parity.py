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
)
from app.api.v1.endpoints.chat import _row_to_message, _row_to_session

# ── iOS-required (non-optional DTO property) keys ───────────────────────────
_SESSION_REQUIRED = {"id", "message_count", "is_saved", "created_at"}
_SESSION_ALL_KEYS = _SESSION_REQUIRED | {
    "title", "session_type", "stock_id", "preview_message", "last_message_at",
}
_MESSAGE_REQUIRED = {"id", "session_id", "role", "content", "created_at"}
_MESSAGE_ALL_KEYS = _MESSAGE_REQUIRED | {"widget", "citations", "tokens_used"}

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
        avg_volume=60_000_000,
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
