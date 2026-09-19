"""The system prompt may name ONLY the tools the asset class was actually given.

`_build_system_instruction` used to carry a hardcoded "WHAT YOU CAN ANSWER" paragraph
that named get_ticker_news / explain_price_move / get_market_snapshot unconditionally and
ORDERED the model to call explain_price_move — on an INDEX chat (neither news tool) and a
COMMODITY chat (no explain_price_move). Telling a model to call a tool it cannot see is
how it ends up explaining that it cannot do sectors, or supplying the tool's output from
memory. The paragraph now renders from `tools_for_asset_type` through ONE registry
(`chat_tools.TOOL_CAPABILITIES`) shared with the FunctionDeclarations.

No network.
"""

from __future__ import annotations

import re

import pytest

from app.services.agents import chat_tools
from app.services.chat_service import ChatService

_TOOL_NAMES = set(chat_tools.TOOL_DESCRIPTIONS)


@pytest.fixture
def licensed_analyst_data(monkeypatch):
    # BOTH bindings: `chat_tools` (the class table) and `chat_service` (the analyst
    # clause) each import the function by name. Patching one used to leave the full
    # instruction self-contradictory under the real licence — a capability block that
    # names get_analyst_analysis beside a clause saying there are NO analyst ratings —
    # and the `<=` assertion below passed on it (F12-11).
    monkeypatch.setattr(chat_tools, "analyst_section_available", lambda: True)
    import app.services.chat_service as cs
    monkeypatch.setattr(cs, "analyst_section_available", lambda: True)


def _svc() -> ChatService:
    return ChatService.__new__(ChatService)


def _named_tools(text: str) -> set[str]:
    return {n for n in _TOOL_NAMES if re.search(rf"\b{re.escape(n)}\b", text)}


@pytest.mark.parametrize("asset_type", ["STOCK", "NORMAL", "ETF", "CRYPTO", "INDEX", "COMMODITY"])
def test_prompt_names_only_granted_tools(asset_type, licensed_analyst_data):
    allowed = set(chat_tools.tools_for_asset_type(asset_type))
    declared = {
        fd.name
        for t in chat_tools.build_chat_tool_declarations(asset_type)
        for fd in (t.function_declarations or [])
    }
    assert declared == allowed
    block = chat_tools.capability_block(frozenset(allowed))
    named = _named_tools(block)
    assert named <= declared, f"{asset_type}: prompt names undeclared tool(s) {named - declared}"
    assert named == declared, f"{asset_type}: prompt omits granted tool(s) {declared - named}"


def test_index_is_not_ordered_to_call_a_tool_it_does_not_have(licensed_analyst_data):
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type("INDEX"))
    assert "explain_price_move" not in block and "get_ticker_news" not in block
    assert "get_market_snapshot" in block and "get_market_overview" in block
    # The shape rule survives without the tool that returns `bottom_line`.
    assert "NEVER END A 'WHY' QUESTION" in block and "bottom_line" not in block


def test_commodity_why_routes_to_news_not_the_missing_ladder(licensed_analyst_data):
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type("COMMODITY"))
    assert "explain_price_move" not in block
    assert "call get_ticker_news first" in block


def test_stock_keeps_the_full_why_ladder(licensed_analyst_data):
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type("STOCK"))
    assert "call explain_price_move" in block and "bottom_line" in block
    assert "call get_market_snapshot" in block


def test_no_tools_means_no_claims():
    assert chat_tools.capability_block(frozenset()) == ""


@pytest.mark.parametrize("asset_type", ["STOCK", "INDEX", "COMMODITY", "CRYPTO", "ETF"])
def test_the_session_word_rule_rides_with_the_snapshot(asset_type, licensed_analyst_data):
    """Every class that gets `get_market_snapshot` (all of them today) is told to use the
    tool's own session word. Pre-market Monday the snapshot's numbers are Friday's; the
    tool now stamps `as_of_session.word` = "on Fri", and a prompt that never mentions it
    leaves the model saying "today" about a session that ended three days earlier."""
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type(asset_type))
    assert "get_market_snapshot" in block, "fixture: the snapshot must be granted"
    assert "as_of_session.word" in block and "'on Fri', not 'today'" in block


@pytest.mark.parametrize("licensed", [True, False])
@pytest.mark.parametrize("asset_type,symbol", [
    ("INDEX", "^GSPC"), ("COMMODITY", "GCUSD"), ("STOCK", "AAPL"), ("CRYPTO", "BTCUSD"),
])
def test_the_full_system_instruction_names_exactly_the_granted_tools(asset_type, symbol, licensed, monkeypatch):
    """End to end through `_build_system_instruction`, under BOTH licence states and with
    both bindings patched. Exactly the granted set — `<=` let a self-contradictory prompt
    (a capability block naming get_analyst_analysis beside "NO analyst ratings") pass — and
    the analyst clause must agree with the class table: it names the tool iff the licence
    AND the class grant it, and says NO otherwise (F12-11)."""
    import app.services.chat_service as cs
    monkeypatch.setattr(chat_tools, "analyst_section_available", lambda: licensed)
    monkeypatch.setattr(cs, "analyst_section_available", lambda: licensed)
    instruction = _svc()._build_system_instruction("NORMAL", symbol, asset_type=asset_type)
    allowed = set(chat_tools.tools_for_asset_type(asset_type))
    named = _named_tools(instruction)
    assert named == allowed, f"{asset_type}/licensed={licensed}: named {named} vs granted {allowed}"
    has_analyst = "get_analyst_analysis" in allowed
    assert ("When you have access to analyst data from the get_analyst_analysis tool" in instruction) == has_analyst
    assert ("NO analyst ratings" in instruction) == (not has_analyst)
    if asset_type == "STOCK":
        assert has_analyst == licensed, "STOCK is granted the analyst tool iff the licence has it"


def test_the_unlicensed_analyst_clause_names_no_tool(monkeypatch):
    """When grades are unlicensed the prompt must say Caydex has no analyst data — and must
    not name get_analyst_analysis anywhere, or the model supplies it from memory."""
    monkeypatch.setattr(chat_tools, "analyst_section_available", lambda: False)
    import app.services.chat_service as cs
    monkeypatch.setattr(cs, "analyst_section_available", lambda: False)
    instruction = _svc()._build_system_instruction("NORMAL", "AAPL", asset_type="STOCK")
    assert "NO analyst ratings" in instruction
    assert "get_analyst_analysis" not in instruction


def test_declarations_and_capabilities_share_one_registry():
    """A tool added to one table and not the other is a prompt/declaration drift."""
    assert set(chat_tools.TOOL_DESCRIPTIONS) == set(chat_tools.TOOL_CAPABILITIES)
    assert set(chat_tools._TOOL_ORDER) == set(chat_tools.TOOL_DESCRIPTIONS)
    for name, cap in chat_tools.TOOL_CAPABILITIES.items():
        assert cap.startswith(name), f"{name}: the capability sentence must lead with the tool name"
    assert "buy/sell" not in " ".join(chat_tools.TOOL_DESCRIPTIONS.values()), (
        "the retired non-streaming registry told the model to fetch a chart 'or whether they "
        "should buy/sell a stock' — that contradicts ADVICE_BOUNDARY"
    )


def test_the_non_streaming_path_has_no_private_registry():
    """The second copy of the declarations in chat_service.py is what drifted."""
    import inspect
    import app.services.chat_service as cs
    src = inspect.getsource(cs)
    assert "_ALL_TOOLS" not in src and "_STOCK_CHART_TOOL" not in src
    assert "build_chat_tool_declarations(" in src and "build_chat_tool_handlers(" in src


def test_the_tool_less_fallback_instruction_names_no_tool(licensed_analyst_data):
    """`tools_granted=False` is the plain-text fallback: the same prompt with tool claims
    would tell a model with nothing attached to "call explain_price_move first"."""
    instruction = _svc()._build_system_instruction(
        "NORMAL", "AAPL", asset_type="STOCK", tools_granted=False,
    )
    assert _named_tools(instruction) == set(), _named_tools(instruction)
    # The rest of the prompt is intact — identity, advice boundary, the subject line.
    assert "Cay AI" in instruction and "AAPL" in instruction


def test_the_tool_less_replayed_context_clause_does_not_point_at_tools(licensed_analyst_data):
    """A history reopen replays the client snapshot with a clause steering the model to
    'rely on your live tools' — which the tool-less merge / fallback does not have; a
    model told that supplies a tool's output from memory (F06-9)."""
    kw = dict(asset_type="STOCK", client_context="AAPL P/E 28.1, target $250",
              context_is_replayed=True)
    with_tools = _svc()._build_system_instruction("NORMAL", "AAPL", tools_granted=True, **kw)
    no_tools = _svc()._build_system_instruction("NORMAL", "AAPL", tools_granted=False, **kw)
    assert "live tools" in with_tools
    assert "live tools" not in no_tools, no_tools
    assert "point-in-time snapshot" in no_tools and "LIVE QUOTE line below" in no_tools
    assert "<<<CLIENT_CONTEXT>>>" in no_tools, "the snapshot itself is still there"


@pytest.mark.asyncio
async def test_prep_hands_the_live_quote_to_the_tool_less_instruction_too(monkeypatch, licensed_analyst_data):
    """The synthesis merge narrates the chart card as well; without the LIVE QUOTE line its
    only 'current' numbers were the replayed snapshot's."""
    import app.services.chat_service as cs
    svc = _svc()
    widget = {"widget_type": "stock_chart", "ticker": "AAPL", "current_price": 231.5,
              "change": 1.2, "change_percent": 0.52, "is_market_open": True}

    async def _widget(*a, **k):
        return widget

    async def _grounding(*a, **k):          # (context, server_grounded, replayed, cache_safe)
        return None, False, False, True

    async def _retrieve(*a, **k):
        return [], []

    async def _condense(*a, **k):
        return ""

    async def _none(*a, **k):
        return None
    # Every upstream the prep touches is stubbed at the method boundary — the suite's
    # network guard would block them anyway, but noisily and slowly.
    monkeypatch.setattr(svc, "_deterministic_widget", _widget)
    monkeypatch.setattr(svc, "_resolve_grounding", _grounding)
    monkeypatch.setattr(svc, "_get_recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_retrieve_context", _retrieve)
    monkeypatch.setattr(svc, "_condense_history", _condense)
    monkeypatch.setattr(svc, "_check_deep_dive_cache", lambda *a, **k: None)
    for name in ("_get_profit_summary", "_get_snapshot_summary", "_get_company_profile_summary"):
        monkeypatch.setattr(svc, name, _none)
    prep = await svc.prepare_stream_generation(
        session_id="s1", user_message="how is it doing", session_type="NORMAL",
        stock_id="AAPL", context=None, context_type="TICKER", reference_id="AAPL",
    )
    assert "LIVE QUOTE" in prep["system_instruction"]
    assert "LIVE QUOTE" in prep["system_instruction_no_tools"]
    assert "$231.50" in prep["system_instruction_no_tools"]


def test_prep_builds_a_tool_free_instruction_for_the_merge():
    """`stream_synthesis`'s MERGE is a `stream_text` call with no tools; it must not be
    handed the tool-bearing instruction ("call explain_price_move before answering")."""
    import inspect
    import app.services.chat_service as cs
    src = inspect.getsource(cs.ChatService.prepare_stream_generation)
    assert '"system_instruction_no_tools": system_instruction_no_tools' in src
    assert "tools_granted=False" in src
    merge = inspect.getsource(cs.ChatService.stream_synthesis)
    assert 'prep.get("system_instruction_no_tools")' in merge
