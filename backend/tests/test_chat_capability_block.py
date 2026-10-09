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
              "change": 1.2, "change_percent": 0.52, "is_market_open": True, "currency": "USD"}

    async def _widget(*a, **k):
        return widget

    async def _grounding(*a, **k):
        # (context, server_grounded, replayed, cache_safe, report_persona_key)
        return None, False, False, True, None

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


# ── report chat's web_search: declared ONLY on a turn whose gate opened ──────

_ALL_CLASSES = ["STOCK", "NORMAL", "ETF", "CRYPTO", "INDEX", "COMMODITY"]


@pytest.mark.parametrize("asset_type", _ALL_CLASSES)
def test_web_search_is_declared_only_when_granted(asset_type, licensed_analyst_data):
    base = set(chat_tools.tools_for_asset_type(asset_type))
    assert chat_tools.WEB_SEARCH_TOOL not in base, "never part of a class table"
    granted = set(chat_tools.tools_for_asset_type(asset_type, web_search=True))
    assert granted == base | {chat_tools.WEB_SEARCH_TOOL}
    declared = {fd.name for t in chat_tools.build_chat_tool_declarations(asset_type, web_search=True)
                for fd in (t.function_declarations or [])}
    assert declared == granted
    default_declared = {fd.name for t in chat_tools.build_chat_tool_declarations(asset_type)
                        for fd in (t.function_declarations or [])}
    assert chat_tools.WEB_SEARCH_TOOL not in default_declared
    block = chat_tools.capability_block(frozenset(granted))
    assert _named_tools(block) == granted
    assert "web_search" not in chat_tools.capability_block(frozenset(base))


@pytest.mark.parametrize("asset_type,symbol", [("STOCK", "AAPL"), ("INDEX", "^GSPC")])
def test_the_full_instruction_names_web_search_only_on_a_granted_turn(asset_type, symbol, licensed_analyst_data):
    svc = _svc()
    granted = svc._build_system_instruction("REPORT", symbol, asset_type=asset_type,
                                            web_search_granted=True)
    allowed = set(chat_tools.tools_for_asset_type(asset_type, web_search=True))
    assert _named_tools(granted) == allowed
    plain = svc._build_system_instruction("REPORT", symbol, asset_type=asset_type)
    assert "web_search" not in plain
    # A tool-less build claims nothing, even on a web turn (the fallback / the synthesis merge).
    tool_less = svc._build_system_instruction("REPORT", symbol, asset_type=asset_type,
                                              tools_granted=False, web_search_granted=True)
    assert _named_tools(tool_less) == set()


def test_the_web_search_capability_says_explicit_ask_and_no_market_data():
    cap = chat_tools.TOOL_CAPABILITIES[chat_tools.WEB_SEARCH_TOOL].lower()
    desc = chat_tools.TOOL_DESCRIPTIONS[chat_tools.WEB_SEARCH_TOOL].lower()
    assert "explicitly" in cap and "market data" in cap and "once per question" in cap
    assert "explicitly asked" in desc and "at most once" in desc and "never a figure" in desc
    for vendor in ("brave", "google", "gemini", "bing"):
        assert vendor not in cap and vendor not in desc
    # It names no OTHER tool identifier: the news tool may be absent on the class.
    assert _named_tools(desc) == {chat_tools.WEB_SEARCH_TOOL} or _named_tools(desc) == set()


def test_web_search_is_never_a_chip_scope():
    assert chat_tools.WEB_SEARCH_TOOL not in chat_tools._CHIP_SCOPE_BY_TOOL
    for asset_type in _ALL_CLASSES:
        assert "web" not in chat_tools.chip_scope_block(asset_type, "TICKER_REPORT").lower().split()


# ── The financials tool (2026-10-08): its routing line and the analyst/estimates clause ──

_FIN = chat_tools.FINANCIALS_TOOL


@pytest.mark.parametrize("asset_type", _ALL_CLASSES)
def test_the_financial_figures_line_rides_with_the_tool(asset_type, licensed_analyst_data):
    allowed = chat_tools.tools_for_asset_type(asset_type)
    block = chat_tools.capability_block(allowed)
    has = _FIN in allowed
    assert ("FINANCIAL FIGURES" in block) == has
    assert ("call check_company_financials" in block) == has
    if has:
        line = block[block.index("FINANCIAL FIGURES"):]
        line = line[:line.index("memory.") + len("memory.")]
        for word in ("revenue", "EPS", "margins", "cash flow", "debt and cash", "valuation multiples",
                     "dividends", "splits", "earnings dates and results"):
            assert word in line, word
        assert "period, basis and currency" in line and "never answer them from memory" in line


@pytest.mark.parametrize("licensed", [True, False])
def test_the_financial_figures_line_names_estimates_only_while_licensed(licensed, monkeypatch, licensed_analyst_data):
    monkeypatch.setattr(chat_tools, "analyst_estimates_available", lambda: licensed)
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type("STOCK"))
    line = block[block.index("FINANCIAL FIGURES"):]
    line = line[:line.index("memory.")]
    assert ("analysts' revenue and EPS estimates" in line) == licensed


def test_several_tools_are_requested_together_up_to_the_round_cap(licensed_analyst_data, monkeypatch):
    from app.config import settings
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type("STOCK"))
    assert "request them together in one step (at most 8)" in block
    monkeypatch.setattr(settings, "CHAT_TOOL_ROUND_MAX_JOBS", 5)
    assert "(at most 5)" in chat_tools.capability_block(chat_tools.tools_for_asset_type("STOCK"))
    for bad in (0, 17, True, "8", None):
        monkeypatch.setattr(settings, "CHAT_TOOL_ROUND_MAX_JOBS", bad)
        assert "(at most 8)" in chat_tools.capability_block(chat_tools.tools_for_asset_type("STOCK"))
    # A single tool has nothing to request together.
    assert "together in one step" not in chat_tools.capability_block(frozenset({"get_ticker_news"}))


def _estimates_patch(monkeypatch, ratings: bool, estimates: bool):
    import app.services.chat_service as cs
    monkeypatch.setattr(chat_tools, "analyst_section_available", lambda: ratings)
    monkeypatch.setattr(cs, "analyst_section_available", lambda: ratings)
    monkeypatch.setattr(chat_tools, "analyst_estimates_available", lambda: estimates)
    monkeypatch.setattr(cs, "analyst_estimates_available", lambda: estimates)


_EST = "Analysts' forward revenue and EPS ESTIMATES are a separate dataset"


@pytest.mark.parametrize("estimates", [True, False])
@pytest.mark.parametrize("asset_type,symbol", [("STOCK", "AAPL"), ("NORMAL", None), ("ETF", "SPY"),
                                               ("CRYPTO", "BTCUSD"), ("INDEX", "^GSPC")])
def test_the_estimates_clause_needs_the_licence_and_the_tool(asset_type, symbol, estimates, monkeypatch):
    """The ratings clause is narrowed to ratings and targets; the estimates sentence appears only
    where the financials tool is granted AND the estimates dataset is licensed — and it never
    licenses a rating, a consensus recommendation or a price target."""
    _estimates_patch(monkeypatch, ratings=False, estimates=estimates)
    instr = _svc()._build_system_instruction("NORMAL", symbol, asset_type=asset_type)
    granted = _FIN in chat_tools.tools_for_asset_type(asset_type)
    assert (_EST in instr) == (granted and estimates)
    assert "NO analyst ratings or price-target data" in instr
    assert "get_analyst_analysis" not in instr
    if _EST in instr:
        clause = instr[instr.index(_EST):]
        clause = clause[:clause.index("price target.") + len("price target.")]
        assert "never present them as a rating, a consensus recommendation or a price target" in clause


def test_the_estimates_clause_is_absent_from_a_tool_less_build(monkeypatch):
    _estimates_patch(monkeypatch, ratings=False, estimates=True)
    instr = _svc()._build_system_instruction("NORMAL", "AAPL", asset_type="STOCK", tools_granted=False)
    assert _EST not in instr and _named_tools(instr) == set()


def test_the_estimates_clause_is_absent_when_the_kill_switch_is_off(monkeypatch):
    from app.config import settings
    _estimates_patch(monkeypatch, ratings=False, estimates=True)
    monkeypatch.setattr(settings, "CHAT_DATA_TOOLS_ENABLED", False)
    instr = _svc()._build_system_instruction("NORMAL", "AAPL", asset_type="STOCK")
    assert _EST not in instr and _FIN not in instr


def test_the_narrowed_ratings_clause_still_refuses_ratings_and_targets(monkeypatch):
    _estimates_patch(monkeypatch, ratings=False, estimates=True)
    instr = _svc()._build_system_instruction("NORMAL", "AAPL", asset_type="STOCK")
    for phrase in ("If asked about analyst ratings, a rating consensus, price targets, or "
                   "upgrades/downgrades", "rather than estimating or recalling it"):
        assert phrase in instr, phrase


def test_the_financials_capability_names_no_vendor_and_no_rating():
    for text in (chat_tools.TOOL_DESCRIPTIONS[_FIN], chat_tools.TOOL_CAPABILITIES[_FIN],
                 chat_tools._CHIP_SCOPE_BY_TOOL[_FIN]):
        low = text.lower()
        for vendor in ("fmp", "financial modeling prep", "gemini", "google", "brave"):
            assert not re.search(rf"\b{re.escape(vendor)}\b", low), (vendor, text[:60])
    desc = chat_tools.TOOL_DESCRIPTIONS[_FIN]
    assert "no analyst ratings and no price targets" in desc
    for section in chat_tools.FINANCIAL_SECTIONS:
        assert section in desc
    # Final review 2026-10-09: the capability line is a short noun list — the declaration
    # carries the sections (the line used to echo them all, a third copy in every prompt).
    cap = chat_tools.TOOL_CAPABILITIES[_FIN]
    assert "in sections (" not in cap and len(cap) < 200, cap
    assert cap.startswith("check_company_financials for a company's reported figures")


# ── The asset-profile tool, press releases and macro readings (2026-10-08) ────────────────

_PROF = chat_tools.PROFILE_TOOL


def _line(block: str, start: str, end: str) -> str:
    """The sentence(s) from `start` up to and including the first `end` after it."""
    i = block.index(start)
    j = block.index(end, i) + len(end)
    return block[i:j]


@pytest.mark.parametrize("asset_type", _ALL_CLASSES)
def test_the_profile_facts_line_rides_with_the_tool(asset_type, licensed_analyst_data):
    allowed = chat_tools.tools_for_asset_type(asset_type)
    block = chat_tools.capability_block(allowed)
    has = _PROF in allowed
    assert has == (asset_type in ("STOCK", "NORMAL", "ETF", "CRYPTO"))
    assert ("COMPANY, FUND AND COIN FACTS" in block) == has
    assert ("call check_asset_profile" in block) == has
    if has:
        line = _line(block, "COMPANY, FUND AND COIN FACTS", "the facts describe. ")
        for word in ("who runs a company", "executives", "employees", "headquarters",
                     "listing date", "peers", "expense ratio", "holdings", "sector weights",
                     "a coin's supply", "before answering"):
            assert word in line, word
        # An unlisted CURRENT officer or profile figure is "not in Caydex's data" — founders and
        # past leaders stay background (next test). "Its facts take precedence over anything you
        # remember" is gone: CAYDEX DATA FIRST says it once in every prompt (final review
        # 2026-10-09, the prompt had grown ~50% on repeats).
        assert "take precedence over anything you remember" not in line
        assert "not in Caydex's data" in line and "from memory" in line
        assert "a current officer (the ceo, the cfo or another executive)" in line.lower()
        assert "resolved_as" in line


def _sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


_BACKGROUND_ROLES = ("founder", "founded", "creator", "created", "maintainer", "maintains",
                     "past leader", "former")


@pytest.mark.parametrize("asset_type,symbol", [("STOCK", "AAPL"), ("NORMAL", None),
                                               ("ETF", "SPY"), ("CRYPTO", "DOGEUSD")])
def test_the_profile_line_leaves_founders_and_maintainers_to_background_knowledge(
        asset_type, symbol, licensed_analyst_data):
    """TestFlight E5 ("Who maintains DOGE?" → "Caydex has no information") must not come back
    through the profile line: written as "a role it does not list is not in Caydex's data", it
    reached founders, creators and maintainers — roles no profile lists — against WHAT YOU
    KNOW's background tier. The data-only clause names CURRENT officers and profile figures;
    founders, creators, maintainers and past leaders are background, in the same prompt as the
    tier that licenses them."""
    instr = _svc()._build_system_instruction("NORMAL", symbol, asset_type=asset_type,
                                             tools_granted=True)
    line = _line(instr, "COMPANY, FUND AND COIN FACTS", "the facts describe. ")
    data_only = [s for s in _sentences(line) if "not in Caydex's data" in s]
    assert len(data_only) == 1, data_only
    assert "a current officer" in data_only[0].lower()
    for role in _BACKGROUND_ROLES:
        assert role not in data_only[0].lower(), (role, data_only[0])
    assert ("Founders, creators, maintainers and past leaders are background, not profile "
            "data: answer them from general knowledge") in line
    # The background tier that answers them is in the same prompt, after the line's rule.
    assert "who founded or maintains a project" in instr
    assert "Never answer a background question with 'Caydex has no information about X'" in instr
    # No sentence anywhere in the prompt puts a background role on data-only.
    for sentence in _sentences(instr):
        if "not in Caydex's data" in sentence or "come ONLY from" in sentence:
            low = sentence.lower()
            assert not any(r in low for r in _BACKGROUND_ROLES), sentence[:160]


def test_the_profile_description_says_it_lists_current_officers_only():
    desc = chat_tools.TOOL_DESCRIPTIONS[_PROF]
    assert "It lists a company's current officers, not its founders or past leaders." in desc
    # The `kind` override is explained where the model reads the tool, with the LTC example.
    assert "the optional `kind` (company, fund or coin)" in desc
    assert "LTC Properties versus Litecoin" in desc


def test_the_profile_capability_leads_with_its_name_and_says_what_it_covers():
    cap = chat_tools.TOOL_CAPABILITIES[_PROF]
    assert cap == (
        "check_asset_profile for a company's executives, size, headquarters, listing date and "
        "peers, a fund's fees and holdings, or a coin's supply"
    )
    desc = chat_tools.TOOL_DESCRIPTIONS[_PROF]
    for phrase in ("CEO and key executives", "employee count", "headquarters", "listing (IPO) date",
                   "peers", "expense ratio", "top holdings", "sector weights",
                   "maximum supply", "Crypto Fear & Greed", "never answer those from memory",
                   "resolved_as", "No prices"):
        assert phrase in desc, phrase


@pytest.mark.parametrize("name", [chat_tools.PROFILE_TOOL, "get_ticker_news", "get_market_snapshot",
                                  "get_market_overview"])
def test_the_new_registry_text_names_no_vendor_and_no_other_tool(name):
    """No vendor in model-facing text (IDENTITY_RULE) — the one credit, Alternative.me, belongs
    in the tool RESULT beside the reading, not in the declaration. And no description names
    another tool: the classes differ in what they grant."""
    texts = [chat_tools.TOOL_DESCRIPTIONS[name], chat_tools.TOOL_CAPABILITIES[name]]
    if name in chat_tools._CHIP_SCOPE_BY_TOOL:
        texts.append(chat_tools._CHIP_SCOPE_BY_TOOL[name])
    for text in texts:
        low = text.lower()
        for vendor in ("fmp", "financial modeling prep", "gemini", "google", "brave", "fred",
                       "coingecko", "alternative.me", "cboe"):
            assert not re.search(rf"\b{re.escape(vendor)}\b", low), (vendor, name, text[:60])
        assert _named_tools(text) <= {name}, (name, _named_tools(text))


@pytest.mark.parametrize("asset_type", _ALL_CLASSES)
def test_the_macro_readings_line_rides_with_the_snapshot(asset_type, licensed_analyst_data):
    """Every class has the snapshot, so every chat is told rates, yields, inflation,
    unemployment and FX come from its DATED macro block — and that the VIX (and the DXY) are
    not in Caydex data, never estimated."""
    allowed = chat_tools.tools_for_asset_type(asset_type)
    block = chat_tools.capability_block(allowed)
    assert "get_market_snapshot" in allowed, "fixture: every class has the snapshot"
    line = _line(block, "MACRO READINGS", "rather than estimating them. ")
    for word in ("interest rate", "Treasury yield", "inflation", "unemployment",
                 "exchange rate", "call get_market_snapshot", "dated macro reading",
                 "'as of <date>'", "never a figure from memory"):
        assert word in line, word
    assert "The VIX and the DXY are not in Caydex data" in line
    has_overview = "get_market_overview" in allowed
    assert ("macro indicators are written outlook labels, not measured readings" in block) \
        == has_overview
    if has_overview:
        assert asset_type == "INDEX"


def test_no_macro_line_without_the_snapshot():
    block = chat_tools.capability_block(frozenset({"get_ticker_news", "get_market_overview"}))
    assert "MACRO READINGS" not in block and "not measured readings" not in block


def test_the_snapshot_and_overview_registry_text_tell_readings_from_labels():
    desc = chat_tools.TOOL_DESCRIPTIONS["get_market_snapshot"]
    for phrase in ("official macro readings, each dated", "fed funds rate", "10-year Treasury yield",
                   "10-year minus 2-year spread", "unemployment", "CPI and core PCE",
                   "euro, yen and pound", "broad dollar index (not the DXY)",
                   "The VIX and the DXY are not in Caydex data"):
        assert phrase in desc, phrase
    cap = chat_tools.TOOL_CAPABILITIES["get_market_snapshot"]
    assert "dated official macro readings" in cap and "exchange rates" in cap
    for text in (chat_tools.TOOL_DESCRIPTIONS["get_market_overview"],
                 chat_tools.TOOL_CAPABILITIES["get_market_overview"]):
        assert "macro outlook labels" in text and "macro indicators" not in text


@pytest.mark.parametrize("asset_type", _ALL_CLASSES)
def test_the_company_announcements_line_rides_with_the_news_tool(asset_type, licensed_analyst_data):
    allowed = chat_tools.tools_for_asset_type(asset_type)
    block = chat_tools.capability_block(allowed)
    has = "get_ticker_news" in allowed
    assert ("COMPANY ANNOUNCEMENTS" in block) == has
    if has:
        line = _line(block, "COMPANY ANNOUNCEMENTS", "Caydex's view. ")
        for word in ("guided to", "call get_ticker_news", "press_releases",
                     "attribute each to the company with its date",
                     "the company's own statement", "never as independent reporting"):
            assert word in line, word


def test_the_news_registry_text_names_press_releases_as_the_companys_statements():
    desc = chat_tools.TOOL_DESCRIPTIONS["get_ticker_news"]
    assert "the company's own latest press releases" in desc
    assert "the company's own statements, not independent reporting" in desc
    assert "what guidance it gave" in desc
    assert "press releases" in chat_tools.TOOL_CAPABILITIES["get_ticker_news"]


def test_the_new_lines_are_absent_from_a_tool_less_build(licensed_analyst_data):
    instr = _svc()._build_system_instruction("NORMAL", "AAPL", asset_type="STOCK",
                                             tools_granted=False)
    for marker in ("COMPANY, FUND AND COIN FACTS", "MACRO READINGS", "COMPANY ANNOUNCEMENTS"):
        assert marker not in instr, marker
    assert _named_tools(instr) == set()


# ── the web tool's tier variants (2026-10-08) ─────────────────────────────────
#
# "explicit" is the registry's own text; "news" and "auto" live OUT of the registry (so the tool
# count stays the count of tools). Each still leads with the tool name, names no other tool and no
# vendor, and keeps both never-for-market-data phrases the Privacy Policy states as an instruction.


@pytest.mark.parametrize("mode", chat_tools.WEB_SEARCH_MODES)
def test_every_web_variant_keeps_the_market_data_instruction_and_names_only_itself(mode):
    desc = chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS[mode]
    cap = chat_tools.WEB_SEARCH_TIER_CAPABILITIES[mode]
    flat_desc = " ".join(desc.lower().split())
    assert "never use this for prices, quotes, price changes or other market data" in flat_desc
    assert "exchange rates, the vix and the dxy included" in flat_desc
    assert cap.startswith("web_search ") and "never for prices, quotes or market data" in cap
    assert "the VIX and the DXY" in cap
    for text in (desc, cap):
        assert _named_tools(text) <= {"web_search"}, (mode, _named_tools(text))
        low = text.lower()
        for vendor in ("brave", "google", "gemini", "bing", "fmp", "openai"):
            assert not re.search(rf"\b{vendor}\b", low), (mode, vendor)


def test_the_variants_stay_out_of_the_registry():
    assert chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["explicit"] is chat_tools.TOOL_DESCRIPTIONS["web_search"]
    assert chat_tools.WEB_SEARCH_TIER_CAPABILITIES["explicit"] is chat_tools.TOOL_CAPABILITIES["web_search"]
    for registry in (chat_tools.TOOL_DESCRIPTIONS, chat_tools.TOOL_CAPABILITIES):
        assert chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["auto"] not in registry.values()
        assert set(registry) == set(chat_tools.TOOL_DESCRIPTIONS)


def test_the_auto_and_news_texts_never_say_the_tool_must_be_called():
    for mode in ("auto", "news"):
        text = chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS[mode]
        assert "call it for that request" not in text and "explicitly asked" not in text
        assert "at most once" in text
    assert "only after Caydex's data and tools could not answer" in chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["auto"]
    assert "Never call it to restate or check a figure" in chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["auto"]
    assert "licensed headlines come first" in chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["news"]
    # with no company in view round 1 reads the market-wide news (review 2026-10-09)
    assert "the market-wide news when no company is in view" in chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS["news"]


@pytest.mark.parametrize("mode,expected", [
    ("explicit", "explicit"), ("news", "news"), ("auto", "auto"),
    (None, "explicit"), ("", "explicit"), ("AUTO", "explicit"), (7, "explicit"),
])
def test_the_declaration_and_the_capability_follow_the_mode(mode, expected):
    tools = chat_tools.build_chat_tool_declarations("STOCK", web_search=True, web_search_mode=mode)
    decls = {fd.name: fd.description for t in tools for fd in t.function_declarations}
    assert decls["web_search"] == chat_tools.WEB_SEARCH_TIER_DESCRIPTIONS[expected]
    allowed = chat_tools.tools_for_asset_type("STOCK", web_search=True)
    block = chat_tools.capability_block(allowed, web_search_mode=mode)
    assert chat_tools.WEB_SEARCH_TIER_CAPABILITIES[expected] in block
    # Every other tool's declaration is untouched by the mode.
    plain = {fd.name: fd.description
             for t in chat_tools.build_chat_tool_declarations("STOCK", web_search=True)
             for fd in t.function_declarations}
    assert {k: v for k, v in decls.items() if k != "web_search"} == \
        {k: v for k, v in plain.items() if k != "web_search"}


def test_the_mode_never_adds_the_tool():
    tools = chat_tools.build_chat_tool_declarations("STOCK", web_search=False, web_search_mode="auto")
    assert "web_search" not in {fd.name for t in tools for fd in t.function_declarations}
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type("STOCK"), web_search_mode="auto")
    assert "web_search" not in block


def test_the_vix_and_dxy_line_lives_once_in_the_macro_readings_paragraph():
    """Final review 2026-10-09: "DXY" appeared three times in one STOCK prompt (five on an
    automatic-web turn). The snapshot's capability is a noun list; the "not in Caydex data" rule
    is said once, in MACRO READINGS (and its declaration keeps the broad-dollar-index detail)."""
    cap = chat_tools.TOOL_CAPABILITIES["get_market_snapshot"]
    assert "DXY" not in cap and "VIX" not in cap
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type("STOCK"))
    snapshot_part = block.replace(chat_tools.TOOL_CAPABILITIES["web_search"], "")
    assert snapshot_part.count("The VIX and the DXY are not in Caydex data") == 1
    assert "broad dollar index (not the DXY)" in chat_tools.TOOL_DESCRIPTIONS["get_market_snapshot"]


# ── a size ceiling per asset type (final review 2026-10-09) ───────────────────
#
# The system prompt grew ~50% in one change, mostly from the same rule said three times. A
# ceiling makes the next regrowth a decision: raise it on purpose, with a reason.
_PROMPT_CEILINGS = {
    # asset_type: (system instruction chars, declaration description chars)
    "STOCK": (15_200, 6_400), "NORMAL": (15_200, 6_400), "ETF": (13_100, 4_000),
    "CRYPTO": (13_200, 4_000), "INDEX": (11_000, 1_200), "COMMODITY": (11_100, 1_500),
}


@pytest.mark.parametrize("asset_type", sorted(_PROMPT_CEILINGS))
def test_the_prompt_stays_under_its_ceiling(asset_type, licensed_analyst_data, monkeypatch):
    monkeypatch.setattr(chat_tools, "analyst_estimates_available", lambda: True)
    symbol = {"NORMAL": None, "INDEX": "^GSPC", "COMMODITY": "GCUSD", "CRYPTO": "BTCUSD",
              "ETF": "SPY"}.get(asset_type, "AAPL")
    instr = _svc()._build_system_instruction("NORMAL", symbol, asset_type=asset_type,
                                             tools_granted=True)
    decls = chat_tools.build_chat_tool_declarations(asset_type)
    desc_chars = sum(len(fd.description or "") for t in decls for fd in t.function_declarations)
    instr_cap, desc_cap = _PROMPT_CEILINGS[asset_type]
    assert len(instr) <= instr_cap, (asset_type, len(instr))
    assert desc_chars <= desc_cap, (asset_type, desc_chars)


def test_the_four_data_tool_capabilities_are_short_noun_lists():
    for name in (chat_tools.FINANCIALS_TOOL, chat_tools.PROFILE_TOOL, chat_tools.OWNERSHIP_TOOL,
                 "get_market_snapshot"):
        assert len(chat_tools.TOOL_CAPABILITIES[name]) <= 250, name
