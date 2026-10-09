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

from pydantic import ValidationError

from app.config import settings
from app.schemas.chat import (
    ChatHistoryResponse,
    ChatMessageResponse,
    CreateChatSessionRequest,
    ChatSessionListResponse,
    ChatSessionResponse,
    HistoricalDataPoint,
    MarketOverviewMacroItem,
    MarketOverviewSector,
    MarketOverviewWidget,
    SendChatMessageRequest,
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
    # `credit` — what the turn cost (rich_content-backed, migration-free like the rest).
    # Present ONLY on a free or refunded turn; absent on every legacy row and every
    # normally-charged one, so iOS decodes it Optional and renders no chip by default.
    "credit",
    # `truncated` — True ONLY on an answer the model cut (MAX_TOKENS / SAFETY /
    # RECITATION after real text) that no continuation completed; rich_content-backed,
    # None on every legacy row and every complete turn. iOS decodes `Bool?`.
    "truncated",
    # `context_grounded` — the server's verdict on whether the screen's grounding reached
    # the turn (TICKER_REPORT today). True/False/None, rich_content-backed; iOS decodes
    # `Bool?` and keeps its "Grounded on …" chip on None.
    "context_grounded",
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
    """iOS ChatSessionListDTO = {sessions, total, has_more?}. `has_more` (E6 paging) is
    Optional on both sides: an old build ignores it, a new build treats nil as "unknown"
    and falls back to the page-length heuristic."""
    rows = [{"id": f"s{i}", "created_at": "2026-06-28T00:00:00.000000+00:00"} for i in range(3)]
    resp = ChatSessionListResponse(sessions=[_row_to_session(r) for r in rows], total=3)
    dumped = resp.model_dump()
    assert set(dumped.keys()) == {"sessions", "total", "has_more"}
    assert dumped["total"] == 3 and len(dumped["sessions"]) == 3
    assert dumped["has_more"] is None
    paged = ChatSessionListResponse(sessions=[], total=0, has_more=True).model_dump()
    assert paged["has_more"] is True


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
    assert dumped["truncated"] is None
    assert dumped["context_grounded"] is None


@pytest.mark.parametrize("stored,expected", [
    ({"context_grounded": True}, True),
    ({"context_grounded": False}, False),   # a REAL answer here, unlike `truncated`
    ({"context_grounded": None}, None),
    ({"context_grounded": "false"}, None),  # a non-bool is no verdict, never a coerced one
    ({"context_grounded": "true"}, None),
    ({"context_grounded": 1}, None),
    ({"context_grounded": 0}, None),
    ({"context_grounded": []}, None),
    ({}, None),
])
def test_context_grounded_passes_only_a_real_bool(stored, expected):
    """iOS decodes `context_grounded` as `Bool?` and softens the chip on `false`. A
    hand-edited 0 / "false" must not read as a verdict either way — and because the
    schema field is `Optional[bool]`, Pydantic's lax mode would COERCE such a value if
    `_row_to_message` passed it through, so the filter has to happen before it."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "answer",
        "created_at": "2026-10-01T00:00:00.000000+00:00",
        "rich_content": {"thinking": {"stages": [], "elapsed_ms": 1}, **stored},
    }
    assert _row_to_message(row).model_dump()["context_grounded"] is expected


def test_context_grounded_survives_a_non_dict_rich_content():
    for rc in (None, "garbage", ["x"], 3):
        row = {"id": "m", "session_id": "s", "role": "assistant", "content": "a",
               "created_at": "2026-10-01T00:00:00.000000+00:00", "rich_content": rc}
        assert _row_to_message(row).model_dump()["context_grounded"] is None


@pytest.mark.parametrize("verdict,present", [
    (True, True), (False, True), (None, False), (1, False), ("true", False),
])
def test_the_turn_blob_writes_the_verdict_only_as_a_bool(verdict, present):
    """The ONE builder both doors persist through. No verdict → the blob is the one it
    always was (byte-identical for every non-report chat); a bool → written as-is."""
    from app.api.v1.endpoints.chat import _rich_content_for_turn
    rich = _rich_content_for_turn({"stages": []}, None, None, context_grounded=verdict)
    assert ("context_grounded" in rich) is present
    if present:
        assert rich["context_grounded"] is verdict
    assert _rich_content_for_turn({"stages": []}, None, None) == {"thinking": {"stages": []}}


@pytest.mark.parametrize("stored,expected", [
    ({"truncated": True, "finish_reason": "MAX_TOKENS"}, True),
    ({"truncated": False}, None),      # never False on the wire
    ({"truncated": "yes"}, None),      # a non-bool never reads as cut
    ({"truncated": 1}, None),
    ({}, None),
])
def test_truncated_is_true_or_absent_never_false(stored, expected):
    """iOS decodes `truncated` as `Bool?` and shows the "cut short" notice on `true`
    only; anything that is not literally True must read as absent."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "half",
        "created_at": "2026-07-09T00:00:00.000000+00:00",
        "rich_content": {"thinking": {"stages": [], "elapsed_ms": 1}, **stored},
    }
    assert _row_to_message(row).model_dump()["truncated"] is expected


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


def test_non_object_citations_are_dropped_before_they_reach_ios():
    """iOS decodes `[ChatCitationDTO]` all-or-nothing: one scalar element in one message's
    citations blanked the entire history. The backend now keeps dict elements only (and the
    DTO is a total decoder besides)."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "x",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "citations": ["foo", None, 42, {"index": 1, "source": "10-K", "text": "ok"}, []],
    }
    dumped = _row_to_message(row).model_dump()
    assert dumped["citations"] == [{"index": 1, "source": "10-K", "text": "ok"}]
    row["citations"] = ["only", "junk"]
    assert _row_to_message(row).model_dump()["citations"] is None
    row["citations"] = "not a list"
    assert _row_to_message(row).model_dump()["citations"] is None


def test_the_ios_citation_dto_is_a_total_decoder():
    """Brace-bound scan of the Swift struct: it must declare `init(from decoder:` and take
    its container with `try?`, like `ChatSource`."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Models"
           / "ChatConversationModels.swift").read_text(encoding="utf-8")
    src = re.sub(r"//[^\n]*", "", src)
    i = src.index("struct ChatCitationDTO")
    depth, j = 0, src.index("{", i)
    k = j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    block = src[j:k]
    assert "init(from decoder: Decoder) throws" in block
    assert "try? decoder.container(keyedBy: CodingKeys.self)" in block
    assert "try? c.decode(Int.self, forKey: .index)" in block


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


def test_market_overview_widget_additive_fields_keep_the_ios_contract():
    """2026-10-08: `forward_pe_known` (False unless a real forward multiple exists) and
    `macro_indicators_basis` ride the card additively. The iOS-required keys are unchanged,
    `forward_pe` stays a non-null float (iOS decodes a non-optional Double), and a LEGACY stored
    card without either key still decodes."""
    payload = _market_widget_payload()
    assert payload["forward_pe_known"] is False and payload["macro_indicators_basis"] is None
    assert isinstance(payload["forward_pe"], float)
    _assert_required_non_null(payload, _MARKET_WIDGET_REQUIRED, "market_overview widget (new)")

    legacy = {k: v for k, v in payload.items()
              if k not in ("forward_pe_known", "macro_indicators_basis")}
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "market",
        "created_at": "2026-06-28T00:00:00.000000+00:00",
        "rich_content": {"widget": legacy},
    }
    widget = _row_to_message(row).model_dump()["widget"]
    _assert_keys_subset(_MARKET_WIDGET_REQUIRED, widget, "legacy market_overview widget")
    # The new keys are not iOS-required (shipped builds never read them).
    assert not ({"forward_pe_known", "macro_indicators_basis"} & _MARKET_WIDGET_REQUIRED)


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


# ── Request-side input caps (denial-of-wallet structural ceiling) ─────────────
#
# SendChatMessageRequest carries a Pydantic hard-max validator (a cheap 422 for absurd
# payloads before any work). The friendly CHAT_MESSAGE_TOO_LONG 400 is enforced later in
# the endpoint via chat_security.validate_message. These pin that a normal message still
# round-trips and an over-hard-max body is rejected at parse time.

def test_send_request_roundtrips_normal_message():
    req = SendChatMessageRequest(message="What is Apple's P/E ratio?")
    assert req.message == "What is Apple's P/E ratio?"
    assert req.context is None and req.context_type is None and req.reference_id is None


def test_send_request_accepts_message_at_hard_max():
    req = SendChatMessageRequest(message="a" * settings.CHAT_MESSAGE_HARD_MAX)
    assert len(req.message) == settings.CHAT_MESSAGE_HARD_MAX


def test_send_request_rejects_message_over_hard_max():
    with pytest.raises(ValidationError):
        SendChatMessageRequest(message="a" * (settings.CHAT_MESSAGE_HARD_MAX + 1))


def test_send_request_rejects_context_over_hard_max():
    with pytest.raises(ValidationError):
        SendChatMessageRequest(message="hi", context="x" * (settings.CHAT_MESSAGE_HARD_MAX + 1))


def test_send_request_optional_fields_still_accepted():
    req = SendChatMessageRequest(
        message="explain this",
        context="Some grounding text",
        context_type="STOCK",
        reference_id="AAPL",
    )
    assert req.context == "Some grounding text"
    assert req.context_type == "STOCK" and req.reference_id == "AAPL"


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


# ── Turn-cost chip (`rich_content["credit"]`) ───────────────────────
#
# Chat is a flat 1 credit and stays that way; the only thing the user is ever told is when
# a turn cost them LESS. That makes this field's ABSENCE the common case and its presence
# the exception — the opposite of most fields here, and the reason both directions are pinned.

_CREDIT_KEYS = {"outcome", "credits", "reason", "label"}


def test_message_surfaces_the_turn_cost_chip_from_rich_content():
    """A refunded turn re-shows its chip on a HISTORY RELOAD, not just live on the stream."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "answer",
        "created_at": "2026-08-22T00:00:00.000000+00:00",
        "rich_content": {
            "credit": {
                "outcome": "refunded",
                "credits": 0,
                "reason": "chat_degraded_unmerged",
                "label": "1 credit refunded — this answer came back incomplete",
            },
        },
    }
    dumped = _row_to_message(row).model_dump()
    _assert_keys_subset(_MESSAGE_ALL_KEYS, dumped, "credit message")
    _assert_required_non_null(dumped, _MESSAGE_REQUIRED, "credit message")
    assert dumped["credit"]["outcome"] == "refunded"
    assert dumped["credit"]["credits"] == 0
    assert dumped["credit"]["label"].endswith("incomplete")


def test_message_credit_payload_carries_only_ios_mapped_keys():
    """Pins the chip's shape. A backend rename here does not crash iOS — `credit` is a
    dictionary on the wire — it silently BLANKS the chip, which is the failure mode a
    decode test would never catch."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "a",
        "created_at": "2026-08-22T00:00:00.000000+00:00",
        "rich_content": {"credit": {
            "outcome": "free_followup", "credits": 0, "reason": None, "label": "Free follow-up",
        }},
    }
    got = set(_row_to_message(row).model_dump()["credit"].keys())
    assert got == _CREDIT_KEYS, f"credit payload keys drifted: {got ^ _CREDIT_KEYS}"


def test_a_normally_charged_row_has_no_credit_chip():
    """The common case. A turn that simply cost 1 credit stores nothing, so iOS renders
    nothing — no meter on every answer."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "a",
        "created_at": "2026-08-22T00:00:00.000000+00:00",
        "rich_content": {"thinking": {"stages": [], "source_count": 0, "elapsed_ms": 10}},
    }
    assert _row_to_message(row).model_dump()["credit"] is None


def test_legacy_row_without_rich_content_has_no_credit_chip():
    """Every message written before this feature. Must decode, not explode."""
    row = {
        "id": "m", "session_id": "s", "role": "assistant", "content": "a",
        "created_at": "2026-07-01T00:00:00.000000+00:00",
    }
    dumped = _row_to_message(row).model_dump()
    _assert_keys_subset(_MESSAGE_ALL_KEYS, dumped, "legacy row")
    assert dumped["credit"] is None


# ── S03-7: the two client-chosen grounding fields have a hard ceiling ─────────


@pytest.mark.parametrize("model", [SendChatMessageRequest, CreateChatSessionRequest])
@pytest.mark.parametrize("field, limit", [("context_type", 64), ("reference_id", 256)])
def test_over_cap_grounding_fields_are_a_422_not_a_log_line(model, field, limit):
    base = {"message": "hi"} if model is SendChatMessageRequest else {}
    model(**base, **{field: "x" * limit})               # at the cap: accepted
    with pytest.raises(ValidationError):
        model(**base, **{field: "x" * (limit + 1)})


def test_the_longest_legitimate_reference_fits():
    """"TICKER|persona|<uuid>" is the longest reference iOS sends (~70 chars)."""
    ref = "GOOGL|warren_buffett|" + "0123456789abcdef" * 2 + "-0123"
    assert len(ref) < 256
    assert SendChatMessageRequest(message="hi", reference_id=ref).reference_id == ref


# ── Report chat's web source pills on the wire (2026-10-02) ────────────────────
#
# A web pill is one more element of `sources` (`ChatSourcePill`, kind "web"): base pills first,
# then ≤5 web pills. The response field stays `List[Any]` (a legacy row can never fail it), no
# NEW top-level message key exists, `thinking.web_searched` rides inside `thinking`, and a
# stored web pill whose URL is unsafe never reaches the iOS decoder.

from pathlib import Path as _Path
import re as _re

from app.schemas.chat import ChatSourcePill

_WEB_PILL = {"kind": "web", "label": "Web", "detail": "Reuters", "title": "DOJ case advances",
             "url": "https://www.reuters.com/legal/a", "published_at": "2026-09-30"}
_IOS_MODELS = (_Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Models"
               / "ChatConversationModels.swift")


def _web_row(sources, thinking=None):
    return {"id": "m", "session_id": "s", "role": "assistant", "content": "answer",
            "created_at": "2026-10-02T00:00:00.000000+00:00",
            "rich_content": {"sources": sources,
                             "thinking": thinking or {"stages": [], "source_count": len(sources),
                                                      "elapsed_ms": 1, "web_searched": True}}}


def test_a_stored_web_pill_round_trips_with_its_extra_keys_and_no_new_message_key():
    base = {"label": "Cay research report", "detail": "AAPL"}
    dumped = _row_to_message(_web_row([base, _WEB_PILL])).model_dump()
    _assert_keys_subset(_MESSAGE_ALL_KEYS, dumped, "web message")
    _assert_required_non_null(dumped, _MESSAGE_REQUIRED, "web message")
    assert set(dumped) == set(ChatMessageResponse.model_fields), "no new top-level message key"
    assert dumped["sources"] == [base, _WEB_PILL]
    assert dumped["thinking"]["web_searched"] is True


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://www.reuters.com/a", "data:,x",
                                 "https://user@reuters.com/a", "https://10.0.0.1/a", None, 7])
def test_an_unsafe_stored_web_pill_never_reaches_the_decoder(url):
    base = {"label": "Cay research report", "detail": "AAPL"}
    dumped = _row_to_message(_web_row([base, {**_WEB_PILL, "url": url}])).model_dump()
    assert dumped["sources"] == [base]
    assert dumped["rich_content"]["sources"] == [base]


def test_every_pill_shape_the_backend_writes_validates_against_the_contract():
    for pill in ({"label": "Cay research report", "detail": "AAPL"},
                 {"kind": "screen", "label": "Stock detail", "detail": "AAPL"}, _WEB_PILL,
                 {**_WEB_PILL, "title": None, "published_at": None}):
        ChatSourcePill.model_validate(pill)
    with pytest.raises(ValidationError):
        ChatSourcePill.model_validate({"detail": "no label"})


def _swift_struct_body(src: str, name: str) -> str:
    """The brace-bounded body of `struct <name>`, string- and comment-aware enough for a model
    file: line comments and block comments are removed first."""
    src = _re.sub(r"/\*.*?\*/", "", src, flags=_re.S)
    src = "\n".join(_re.sub(r"(?<!:)//.*$", "", line) for line in src.splitlines())
    m = _re.search(rf"\bstruct\s+{name}\b[^{{]*\{{", src)
    assert m, f"struct {name} not found"
    depth, i = 1, m.end()
    while depth and i < len(src):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end(): i - 1]


def _coding_key_wire_names(body: str) -> set:
    m = _re.search(r"enum\s+CodingKeys\s*:\s*String\s*,\s*CodingKey\s*\{(.*?)\}", body, flags=_re.S)
    assert m, "no CodingKeys enum"
    names = set()
    for case in _re.findall(r"\bcase\s+([^\n]+)", m.group(1)):
        for part in case.split(","):
            part = part.strip()
            if not part:
                continue
            raw = _re.search(r'=\s*"([^"]+)"', part)
            names.add(raw.group(1) if raw else part.split("=")[0].strip())
    return names


def test_the_pill_contract_matches_the_ios_chat_source_coding_keys():
    """`ChatSourcePill` (the backend contract) and `ChatSource` (the iOS decoder) name the same
    wire keys — a renamed key on either side is a pill that silently never becomes tappable."""
    body = _swift_struct_body(_IOS_MODELS.read_text(encoding="utf-8"), "ChatSource")
    assert _coding_key_wire_names(body) == set(ChatSourcePill.model_fields)


def test_the_web_searched_flag_is_a_key_the_ios_thinking_decoder_reads():
    body = _swift_struct_body(_IOS_MODELS.read_text(encoding="utf-8"), "ChatThinking")
    assert {"stages", "reasoning", "source_count", "elapsed_ms", "web_searched"} <= \
        _coding_key_wire_names(body)
