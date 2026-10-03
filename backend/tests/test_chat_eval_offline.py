"""Offline, deterministic eval harness for the chat pipeline — the Phase-0 regression gate.

Pins the CURRENT grounding + prompt-assembly contract of `ChatService.prepare_stream_generation`
(the SSE path) and the prompt builders, with NO network: the context resolver, RAG search, history,
stock enrichment, embeddings, and the deterministic widget are all stubbed. Any later phase — in
particular the google-genai SDK migration (Phase 1a, which must be behavior-preserving) — must keep
these green. Phase 1b intentionally UPDATES the reasoning-scaffolding assertions when the
`===ANSWER===` prompt hack is replaced by real streamed thinking tokens.

No live Gemini/FMP/Supabase (testing.md rule): the service is built via `object.__new__` and every
I/O method it calls is overridden with a canned stub.
"""

from __future__ import annotations

import pytest

from app.services.chat_service import ChatService
from _price_fakes import PriceFromFMPFake


class _FakeGemini:
    def __init__(self, embedding=None, raises=False):
        self._embedding = embedding if embedding is not None else [0.0] * 1536
        self._raises = raises

    async def generate_embedding(self, text, model_name=None):
        if self._raises:
            raise RuntimeError("embedding backend down")
        return self._embedding


def _make_service(*, chunks=None, profit=None, snapshot=None, profile=None,
                  widget=None, history=None, embed_raises=False):
    """A ChatService with NO real clients; every I/O method stubbed to canned values."""
    svc = object.__new__(ChatService)
    svc.supabase = None
    svc.fmp = None
    svc.price = PriceFromFMPFake(svc.fmp)
    svc.gemini = _FakeGemini(raises=embed_raises)

    svc._get_recent_messages = lambda session_id, limit=10: list(history or [])

    # Phase 4: RAG goes through a single _retrieve_context entry point (rewrite → embed → search →
    # rerank). Stub it here to return the canned chunks + citations (or nothing on the failure case),
    # mirroring its never-raise contract — no need to stub the internal steps.
    async def _retrieve(_user_message, _stock_id, _hist):
        if embed_raises:
            return [], []
        cs = list(chunks or [])
        cits = [{"index": i + 1, "source": c.get("section_title", "Document"),
                 "text": (c.get("chunk_text") or "")[:200]} for i, c in enumerate(cs)]
        return cs, cits

    svc._retrieve_context = _retrieve

    async def _profit(_t):
        return profit

    async def _snap(_t):
        return snapshot

    async def _prof(_t):
        return profile

    async def _widget(_asset_type, _stock_id, _reference_id):
        return widget

    svc._get_profit_summary = _profit
    svc._get_snapshot_summary = _snap
    svc._get_company_profile_summary = _prof
    svc._deterministic_widget = _widget
    return svc


def _patch_resolver(monkeypatch, block, seen=None, persona=None):
    """Make the lazily-imported resolver return `block` (else the client context).

    `user_id` is accepted (and recorded into `seen` when given) because the
    TICKER_REPORT branch grounds on the CALLER'S OWN frozen `research_reports` row
    and therefore needs an identity — see `_resolve_ticker_report`. A fake that did
    not take it would let chat_service stop forwarding it without a test noticing.

    `meta` is the resolver's out-param; `persona` stands in for the grounded report's own
    persona (its stored agent tag), which the report chat's mode voice follows.
    """
    import app.services.chat_context_resolver as ccr

    class _FakeResolver:
        async def resolve(self, context_type, reference_id, client_context=None,
                          user_id=None, meta=None):
            if seen is not None:
                seen["user_id"] = user_id
                seen["meta_is_dict"] = isinstance(meta, dict)
            if persona is not None and isinstance(meta, dict):
                meta["report_persona_key"] = persona
            return block if block is not None else client_context

    monkeypatch.setattr(ccr, "get_chat_context_resolver", lambda: _FakeResolver())


@pytest.mark.asyncio
async def test_caller_identity_is_forwarded_to_the_context_resolver(monkeypatch):
    """The report chat can only read the user's OWN stored report if chat_service
    passes the identity down. Without it the resolver silently falls back to the
    close-aligned shared cache, which holds nothing after the next 18:00 ET close —
    so a saved report answers from live market data instead of from itself."""
    seen = {}
    _patch_resolver(monkeypatch, None, seen=seen)
    svc = _make_service()
    await svc.prepare_stream_generation(
        session_id="s1", user_message="what is the bear case?",
        stock_id="AAPL", context_type="TICKER_REPORT",
        reference_id="AAPL|warren_buffett|report-abc",
        user_id="user-42",
    )
    assert seen["user_id"] == "user-42"


# ── prepare_stream_generation: grounding + prompt assembly (the SSE path) ────

@pytest.mark.asyncio
async def test_grounding_block_injected_into_system_instruction(monkeypatch):
    _patch_resolver(monkeypatch, "GROUNDING: AAPL trades at 30x forward earnings.")
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="why so expensive?",
        stock_id="AAPL", context_type="STOCK", reference_id="AAPL",
    )
    assert "GROUNDING: AAPL trades at 30x forward earnings." in out["system_instruction"]
    assert "CLIENT CONTEXT" in out["system_instruction"]


@pytest.mark.asyncio
async def test_rag_chunks_injected_into_prompt_and_citations(monkeypatch):
    _patch_resolver(monkeypatch, None)
    chunks = [{"chunk_text": "Apple 10-K risk: supply-chain concentration in Asia.",
               "section_title": "Risk Factors"}]
    svc = _make_service(chunks=chunks)
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="what are the risks?",
        stock_id="AAPL", context_type="STOCK", reference_id="AAPL",
    )
    assert "RELEVANT CONTEXT" in out["prompt"]
    assert "supply-chain concentration" in out["prompt"]
    assert out["citations"] and out["citations"][0]["source"] == "Risk Factors"


@pytest.mark.asyncio
async def test_sources_pills_for_ticker_report(monkeypatch):
    _patch_resolver(monkeypatch, "grounded report block")
    chunks = [{"chunk_text": "x", "section_title": "MD&A"}]
    svc = _make_service(chunks=chunks)
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="bull and bear case?",
        stock_id="AAPL", context_type="TICKER_REPORT", reference_id="AAPL|warren_buffett",
    )
    pills = {(s["label"], s["detail"]) for s in out["sources"]}
    assert ("Cay research report", "AAPL") in pills
    assert ("SEC filing", "MD&A") in pills


@pytest.mark.asyncio
async def test_identity_and_brevity_always_in_system_instruction(monkeypatch):
    _patch_resolver(monkeypatch, None)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q",
        stock_id="AAPL", context_type="STOCK", reference_id="AAPL",
    )
    si = out["system_instruction"]
    assert "Cay AI" in si and "never" in si.lower()          # identity rule
    assert "SHORT" in si or "concise" in si.lower()           # brevity directive


@pytest.mark.asyncio
async def test_no_reasoning_scaffolding_after_thinking_migration(monkeypatch):
    """Phase 1b: real thinking tokens replaced the `===ANSWER===` prompt hack, so the scaffolding
    must be GONE from both the system instruction and the prompt. Reasoning now comes from the
    model's native thought parts (requested via ThinkingConfig in stream_text)."""
    _patch_resolver(monkeypatch, None)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q",
        stock_id="AAPL", context_type="STOCK", reference_id="AAPL",
    )
    assert "===ANSWER===" not in out["system_instruction"]
    assert "===ANSWER===" not in out["prompt"]
    # The identity + brevity directives remain (not part of the reasoning hack).
    assert "Cay AI" in out["system_instruction"]


@pytest.mark.asyncio
async def test_deterministic_widget_attached_for_stock(monkeypatch):
    _patch_resolver(monkeypatch, None)
    widget = {"widget_type": "stock_chart", "ticker": "AAPL"}
    svc = _make_service(widget=widget)
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="chart?",
        stock_id="AAPL", context_type="STOCK", reference_id="AAPL",
    )
    assert out["widget"] == widget


@pytest.mark.asyncio
async def test_no_widget_for_general_chat(monkeypatch):
    _patch_resolver(monkeypatch, None)
    svc = _make_service(widget=None)
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="what is compound interest?",
    )
    assert out["widget"] is None
    # Ungrounded general chat: no sources pill either.
    assert not out["sources"]


@pytest.mark.asyncio
async def test_rag_failure_degrades_without_crash(monkeypatch):
    """Embedding backend down → no citations, but the prompt + system instruction still build."""
    _patch_resolver(monkeypatch, None)
    svc = _make_service(embed_raises=True)
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q",
        stock_id="AAPL", context_type="STOCK", reference_id="AAPL",
    )
    assert out["citations"] is None
    assert out["prompt"] and out["system_instruction"]


# ── Pure prompt-builder contract (no service construction needed) ────────────

def test_asset_persona_injected_per_type():
    svc = object.__new__(ChatService)
    idx = svc._build_system_instruction("NORMAL", "^GSPC", asset_type="INDEX")
    assert "market strategist" in idx.lower()
    cry = svc._build_system_instruction("NORMAL", "BTCUSD", asset_type="CRYPTO")
    assert "crypto analyst" in cry.lower()
    etf = svc._build_system_instruction("NORMAL", "SPY", asset_type="ETF")
    assert "etf analyst" in etf.lower()


def test_index_persona_never_names_specific_indices():
    svc = object.__new__(ChatService)
    idx = svc._build_system_instruction("NORMAL", "^GSPC", asset_type="INDEX")
    # The persona must instruct the model to say "the market", not name real indices.
    assert "the market" in idx.lower()


def test_client_context_wrapped_with_framing():
    svc = object.__new__(ChatService)
    si = svc._build_system_instruction(
        "NORMAL", "AAPL", asset_type="STOCK", client_context="AAPL grounding facts here",
    )
    assert "CLIENT CONTEXT" in si
    assert "AAPL grounding facts here" in si


# ── The trusted report rule through BOTH doors (TestFlight #57) ───────────────
#
# `report_grounded` is a SERVER verdict: the context type is TICKER_REPORT and the resolver BUILT
# the block (`server_grounded`). A pass-through / timed-out resolve hands back the client's own
# string — `grounded` is true for that too, which is exactly why the rule never keys on it.

_RULE = ChatService._REPORT_GROUNDING_RULE
_SENTINEL = "THE REPORT ON SCREEN:"
_BUILT = "The user is viewing the in-depth Cay research report for Broadcom Inc. (AVGO)."
_STOCK_CHART = {"widget_type": "stock_chart", "ticker": "AVGO", "current_price": 364.54,
                "change": -1.25, "change_percent": -0.34}


def _assert_rule_once_before_fence(instr: str):
    assert instr.count(_SENTINEL) == 1 and instr.count(_RULE) == 1
    assert instr.index(_SENTINEL) < instr.index("<<<CLIENT_CONTEXT>>>")


@pytest.mark.asyncio
async def test_stream_door_adds_the_rule_to_both_instructions_and_keeps_the_live_quote(monkeypatch):
    _patch_resolver(monkeypatch, _BUILT)
    svc = _make_service(widget=_STOCK_CHART)
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="is NVIDIA the main competitor?",
        stock_id="AVGO", context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman",
    )
    assert out["server_grounded"] is True
    for key in ("system_instruction", "system_instruction_no_tools"):
        instr = out[key]
        _assert_rule_once_before_fence(instr)
        # Coexists with the LIVE QUOTE line, which stays AFTER the fence.
        assert instr.count("LIVE QUOTE shown on") == 1
        assert instr.index("<<<END_CLIENT_CONTEXT>>>") < instr.index("LIVE QUOTE shown on")


@pytest.mark.asyncio
@pytest.mark.parametrize("ctype, client_ctx, block", [
    ("TICKER_REPORT", "client typed this", None),   # resolver fell back / timed out → pass-through
    ("TICKER_REPORT", None, None),                  # nothing resolved, nothing sent
    ("STOCK", None, _BUILT),                        # a server block, but not a report screen
    ("COMMODITY", "client ctx", "client ctx\n\nCommodity profile: gold"),
    ("ETF", None, "The user is viewing the ETF detail screen for X (X)."),
])
async def test_stream_door_omits_the_rule_without_a_server_built_report(monkeypatch, ctype, client_ctx, block):
    _patch_resolver(monkeypatch, block)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", stock_id="AVGO", context=client_ctx,
        context_type=ctype, reference_id="AVGO|bill_ackman",
    )
    assert _SENTINEL not in out["system_instruction"]
    assert _SENTINEL not in out["system_instruction_no_tools"]


def _generate_service(monkeypatch, block, *, tools_raise: bool):
    from unittest.mock import AsyncMock

    seen = {}

    class _Gem:
        async def generate_with_tools(self, **kw):
            seen["tools"] = kw["system_instruction"]
            if tools_raise:
                raise RuntimeError("function calling exploded")
            return {"text": "answer", "tokens_used": 3, "tool_results": [], "finish_reason": "STOP"}

        async def generate_text(self, **kw):
            seen["fallback"] = kw["system_instruction"]
            return {"text": "plain answer", "tokens_used": 2, "finish_reason": "STOP"}

    svc = ChatService.__new__(ChatService)
    svc.supabase = object()
    svc.fmp = object()
    svc.gemini = _Gem()
    _patch_resolver(monkeypatch, block)
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._deterministic_widget = AsyncMock(return_value=None)
    return svc, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_raise", [False, True])
async def test_non_stream_door_adds_the_rule_to_the_tool_round_and_the_fallback(monkeypatch, tools_raise):
    svc, seen = _generate_service(monkeypatch, _BUILT, tools_raise=tools_raise)
    out = await svc.generate_response(
        "sess", "is NVIDIA the main competitor?", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman",
    )
    _assert_rule_once_before_fence(seen["tools"])
    if tools_raise:
        assert out.get("degraded") == "no_tools"
        _assert_rule_once_before_fence(seen["fallback"])
        # The fallback is the TOOL-LESS build: no tool is named in it.
        from app.services.agents.chat_tools import TOOL_DESCRIPTIONS
        assert not any(name in seen["fallback"] for name in TOOL_DESCRIPTIONS)
    else:
        assert "fallback" not in seen


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_raise", [False, True])
async def test_non_stream_door_omits_the_rule_on_a_pass_through(monkeypatch, tools_raise):
    svc, seen = _generate_service(monkeypatch, None, tools_raise=tools_raise)
    await svc.generate_response(
        "sess", "q", stock_id="AVGO", context="client typed this",
        context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman",
    )
    for instr in seen.values():
        assert _SENTINEL not in instr
        assert "client typed this" in instr        # the context is still there, just not promoted


# ── The chip's grounding verdict (`context_grounded`) through BOTH doors ──────
#
# iOS's "Grounded on Research Report · AAPL" chip used to key on the context TYPE alone, so a
# report chat whose report could not be found (the direct door with no report id, after the
# close-aligned `ticker_report_cache` rolled over) still claimed it. The verdict is the
# SERVER's: True only when the resolver BUILT the block. A client pass-through satisfies
# `grounded` and must still read False; a context type with no verdict reads None.

@pytest.mark.parametrize("ctype, server_grounded, expected", [
    ("TICKER_REPORT", True, True),
    ("TICKER_REPORT", False, False),
    (" ticker_report ", True, True),      # normalised like the resolver's own dispatch
    ("ticker_report", False, False),
    ("TICKER_REPORT", 1, False),          # only a real True vouches
    ("STOCK", True, None),                # grounded by enrichment, not the resolver
    ("STOCK", False, None),
    ("ETF", True, None),
    ("COMMODITY", True, None),            # appends the caller's string
    ("BOOK", False, None),                # pass-through by design
    ("NONE", False, None),
    ("", False, None),
    (None, False, None),
])
def test_context_grounding_verdict_table(ctype, server_grounded, expected):
    from app.services.chat_service import context_grounding_verdict
    assert context_grounding_verdict(ctype, server_grounded) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("client_ctx, block, expected", [
    (None, _BUILT, True),                    # the resolver built the report block
    (None, None, False),                     # no report found, nothing sent: ungrounded
    ("client typed this", None, False),      # pass-through: `grounded` is True, the verdict is not
])
async def test_stream_door_reports_the_report_verdict(monkeypatch, client_ctx, block, expected):
    _patch_resolver(monkeypatch, block)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="what is the bear case?", stock_id="AVGO",
        context=client_ctx, context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman",
    )
    assert out["context_grounded"] is expected
    assert out["context_grounded"] is out["server_grounded"]
    if client_ctx:
        assert out["grounded"] is True, "the pass-through still earns the sources pill"


@pytest.mark.asyncio
@pytest.mark.parametrize("ctype, block", [
    ("STOCK", None), ("STOCK", _BUILT),
    ("ETF", "The user is viewing the ETF detail screen for X (X)."),
    ("NONE", None),
])
async def test_stream_door_gives_no_verdict_for_other_context_types(monkeypatch, ctype, block):
    _patch_resolver(monkeypatch, block)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", stock_id="AVGO", context_type=ctype, reference_id="AVGO",
    )
    assert out["context_grounded"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_raise", [False, True])
@pytest.mark.parametrize("client_ctx, block, expected", [
    (None, _BUILT, True),
    (None, None, False),
    ("client typed this", None, False),
])
async def test_non_stream_door_reports_the_same_verdict(monkeypatch, tools_raise, client_ctx, block, expected):
    """The stream door's FALLBACK persists this door's verdict, so it must be computed here
    too — on the tool round AND the tool-less plain-text fallback."""
    svc, _ = _generate_service(monkeypatch, block, tools_raise=tools_raise)
    out = await svc.generate_response(
        "sess", "what is the bear case?", stock_id="AVGO", context=client_ctx,
        context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman",
    )
    assert out["context_grounded"] is expected


@pytest.mark.asyncio
async def test_non_stream_door_gives_no_verdict_for_a_stock_chat(monkeypatch):
    svc, _ = _generate_service(monkeypatch, None, tools_raise=False)
    out = await svc.generate_response(
        "sess", "q", stock_id="AVGO", context_type="STOCK", reference_id="AVGO",
    )
    assert out["context_grounded"] is None


# ── The report chat's MODE VOICE through BOTH doors (2026-10-02) ─────────────
#
# "Cay AI · Growth Hunter Agent": a REPORT session renders the report persona's mode voice,
# chosen from the GROUNDED report's own persona (the resolver's `meta` out-param), else the
# validated `reference_id` segment. Both doors must carry it into every instruction they build
# (the tool round, the tool-less fallback, the stream's tool-less merge variant) and hand the
# endpoint the key for its guardrail log lines. The chips stay neutral.

_VOICE_MARK = "REPORT CHAT MODE — "
_GROWTH_VOICE = "REPORT CHAT MODE — Growth Hunter Agent."


@pytest.fixture
def _voice_on(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "CHAT_REPORT_VOICE_ENABLED", True)
    return settings


@pytest.mark.asyncio
async def test_stream_door_carries_the_voice_in_both_instructions(monkeypatch, _voice_on):
    seen = {}
    _patch_resolver(monkeypatch, _BUILT, seen=seen)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="what is the bear case?", session_type="REPORT",
        stock_id="AVGO", context_type="TICKER_REPORT", reference_id="AVGO|lynch",
    )
    assert seen["meta_is_dict"] is True, "chat_service must hand the resolver its out-param"
    for key in ("system_instruction", "system_instruction_no_tools"):
        instr = out[key]
        assert instr.count(_GROWTH_VOICE) == 1
        assert instr.index(_GROWTH_VOICE) < instr.index(_SENTINEL) < instr.index("<<<CLIENT_CONTEXT>>>")
    assert out["report_voice_key"] == "peter_lynch"


@pytest.mark.asyncio
async def test_stream_door_voice_follows_the_grounded_reports_persona(monkeypatch, _voice_on):
    """An old build's notification route sends `warren_buffett` for a Growth Hunter report."""
    _patch_resolver(monkeypatch, _BUILT, persona="peter_lynch")
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|warren_buffett|rid-1",
    )
    assert _GROWTH_VOICE in out["system_instruction"]
    assert "Quality Compounder Agent" not in out["system_instruction"]
    assert out["report_voice_key"] == "peter_lynch"


@pytest.mark.asyncio
@pytest.mark.parametrize("session_type", ["STOCK", "NORMAL"])
async def test_stream_door_never_voices_a_non_report_session(monkeypatch, _voice_on, session_type):
    """A per-message TICKER_REPORT override on a STOCK session is grounded, never voiced."""
    _patch_resolver(monkeypatch, _BUILT, persona="peter_lynch")
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", session_type=session_type, stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|lynch",
    )
    assert _VOICE_MARK not in out["system_instruction"]
    assert _VOICE_MARK not in out["system_instruction_no_tools"]
    assert out["report_voice_key"] is None


@pytest.mark.asyncio
async def test_stream_door_rollback_switch(monkeypatch, _voice_on):
    _voice_on.CHAT_REPORT_VOICE_ENABLED = False   # monkeypatch restores it
    _patch_resolver(monkeypatch, _BUILT, persona="peter_lynch")
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|lynch",
    )
    assert _VOICE_MARK not in out["system_instruction"]
    assert "You specialize in value investing education." in out["system_instruction"]
    assert out["report_voice_key"] is None


@pytest.mark.asyncio
async def test_unresolved_report_keeps_the_voice_but_not_the_report_rule(monkeypatch, _voice_on):
    _patch_resolver(monkeypatch, None)            # the report did not resolve
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|burry",
    )
    instr = out["system_instruction"]
    assert instr.count("REPORT CHAT MODE — Deep Value Skeptic Agent.") == 1
    assert _SENTINEL not in instr
    assert out["context_grounded"] is False


@pytest.mark.asyncio
async def test_voice_follows_the_per_message_reference(monkeypatch, _voice_on):
    _patch_resolver(monkeypatch, None)
    svc = _make_service()
    a = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|wood",
    )
    b = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|ackman",
    )
    assert "Disruption Seeker Agent" in a["system_instruction"]
    assert "Activist Concentrator Agent" in b["system_instruction"]


@pytest.mark.asyncio
async def test_an_unresolvable_report_persona_is_logged_once_per_turn(monkeypatch, _voice_on, caplog):
    import logging
    _patch_resolver(monkeypatch, None)
    svc = _make_service()
    with caplog.at_level(logging.WARNING, logger="app.services.chat_service"):
        out = await svc.prepare_stream_generation(
            session_id="s-77", user_message="q", session_type="REPORT", stock_id="AVGO",
            context_type="TICKER_REPORT", reference_id="AVGO|soros\nERROR forged line",
        )
    assert _VOICE_MARK not in out["system_instruction"]
    records = [r.getMessage() for r in caplog.records if "no mode voice" in r.getMessage()]
    assert len(records) == 1, "logged by the door once, never by the builder (2 builds/turn)"
    assert "s-77" in records[0] and "\n" not in records[0]


@pytest.mark.asyncio
async def test_a_resolved_voice_logs_nothing(monkeypatch, _voice_on, caplog):
    import logging
    _patch_resolver(monkeypatch, None)
    svc = _make_service()
    with caplog.at_level(logging.WARNING, logger="app.services.chat_service"):
        await svc.prepare_stream_generation(
            session_id="s1", user_message="q", session_type="REPORT", stock_id="AVGO",
            context_type="TICKER_REPORT", reference_id="AVGO|lynch",
        )
    assert not [r for r in caplog.records if "no mode voice" in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_raise", [False, True])
async def test_non_stream_door_carries_the_voice_to_the_tool_round_and_the_fallback(
    monkeypatch, _voice_on, tools_raise,
):
    svc, seen = _generate_service(monkeypatch, _BUILT, tools_raise=tools_raise)
    out = await svc.generate_response(
        "sess", "what is the bear case?", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|lynch",
    )
    assert seen["tools"].count(_GROWTH_VOICE) == 1
    if tools_raise:
        assert seen["fallback"].count(_GROWTH_VOICE) == 1
    else:
        assert "fallback" not in seen
    assert out["report_voice_key"] == "peter_lynch"


@pytest.mark.asyncio
async def test_non_stream_door_voice_follows_the_grounded_reports_persona(monkeypatch, _voice_on):
    svc, seen = _generate_service(monkeypatch, _BUILT, tools_raise=False)
    _patch_resolver(monkeypatch, _BUILT, persona="michael_burry")
    out = await svc.generate_response(
        "sess", "q", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|warren_buffett|rid-1",
    )
    assert "REPORT CHAT MODE — Deep Value Skeptic Agent." in seen["tools"]
    assert out["report_voice_key"] == "michael_burry"


@pytest.mark.asyncio
async def test_non_stream_door_gives_no_voice_to_a_stock_chat(monkeypatch, _voice_on):
    svc, seen = _generate_service(monkeypatch, _BUILT, tools_raise=False)
    out = await svc.generate_response(
        "sess", "q", session_type="STOCK", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|lynch",
    )
    assert _VOICE_MARK not in seen["tools"]
    assert out["report_voice_key"] is None


@pytest.mark.asyncio
async def test_specialist_lens_and_merge_inherit_the_voice(monkeypatch, _voice_on):
    """Every specialist and the merge build on prep's instructions: the voice rides in both,
    and a specialist's lens is appended AFTER it (the lens narrows emphasis, never identity)."""
    from app.services.agents.chat_specialists import SPECIALIST_KEYS, apply_specialist

    _patch_resolver(monkeypatch, _BUILT)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q", session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|lynch",
    )
    lensed = [k for k in SPECIALIST_KEYS if k != "general"]
    assert lensed, "anti-vacuity: there are specialists to apply"
    for key in lensed:
        instr = apply_specialist(out["system_instruction"], key)
        assert instr.count(_GROWTH_VOICE) == 1
        focus = instr[len(out["system_instruction"]):]
        assert focus.strip(), key
        assert instr.index(_GROWTH_VOICE) < len(out["system_instruction"]) <= instr.index(focus)
    assert out["system_instruction_no_tools"].count(_GROWTH_VOICE) == 1


@pytest.mark.asyncio
async def test_followup_chips_stay_neutral(monkeypatch, _voice_on):
    captured = {}

    class _Gem:
        async def generate_json(self, prompt, system_instruction=None, model_name=None):
            captured["system"] = system_instruction
            return {"text": '{"suggestions": ["What is the PEG?", "What could break it?"]}'}

    svc = object.__new__(ChatService)
    svc.gemini = _Gem()
    chips = await svc.generate_followup_suggestions(
        "what is the bear case?", "an answer", "TICKER_REPORT", "AAPL|lynch",
    )
    assert chips, "anti-vacuity: the chip call ran"
    assert _VOICE_MARK not in captured["system"]
    assert "You specialize in value investing education." in captured["system"]


# ── Report chat's web search: the trusted rule, the unavailable line, the report date ──
#
# One gate decision per turn (`open_web_search_turn`) drives the tool, the capability block AND
# the trusted `_WEB_RESULTS_RULE`; a tool-less build of the same turn, and a web-intent turn the
# gate cannot serve (switch off / no key), get the one-line `_WEB_UNAVAILABLE_RULE` instead. The
# report's as-of date comes from the resolver's meta and only for a server-built report block.
# The key AND the switch are set explicitly in every case (`Settings` reads backend/.env).

_WEB_RULE = ChatService._WEB_RESULTS_RULE
_WEB_NONE = ChatService._WEB_UNAVAILABLE_RULE
_WEB_ASK = "Can you verify the DOJ case against Apple?"


def _web_gate(monkeypatch, *, on: bool):
    from app.config import settings as _s
    import app.services.chat_web_search_service as cws
    monkeypatch.setattr(_s, "BRAVE_SEARCH_API_KEY", "test-key" if on else "")
    monkeypatch.setattr(_s, "CHAT_REPORT_WEB_SEARCH_ENABLED", on)
    monkeypatch.setattr(cws, "_cache", {})
    monkeypatch.setattr(cws, "_inflight", {})


def _patch_resolver_dated(monkeypatch, block, as_of):
    import app.services.chat_context_resolver as ccr

    class _Dated:
        async def resolve(self, context_type, reference_id, client_context=None,
                          user_id=None, meta=None):
            if isinstance(meta, dict) and as_of is not None:
                meta["report_as_of"] = as_of
            return block if block is not None else client_context

    monkeypatch.setattr(ccr, "get_chat_context_resolver", lambda: _Dated())


async def _web_prep(monkeypatch, *, gate_on=True, block=_BUILT, as_of="2026-09-22",
                    message=_WEB_ASK, session_type="REPORT", context=None):
    _web_gate(monkeypatch, on=gate_on)
    _patch_resolver_dated(monkeypatch, block, as_of)
    svc = _make_service(widget=_STOCK_CHART)
    return await svc.prepare_stream_generation(
        session_id="s1", user_message=message, session_type=session_type, stock_id="AVGO",
        context=context, context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman",
        user_id="user-1",
    )


def _assert_once_before_fence(instr: str, rule: str):
    assert instr.count(rule) == 1, rule[:40]
    if "<<<CLIENT_CONTEXT>>>" in instr:
        assert instr.index(rule) < instr.index("<<<CLIENT_CONTEXT>>>")
        fenced = instr[instr.index("<<<CLIENT_CONTEXT>>>"):instr.index("<<<END_CLIENT_CONTEXT>>>")]
        assert "WEB RESULTS:" not in fenced and "WEB SEARCH:" not in fenced


@pytest.mark.asyncio
async def test_a_web_turn_carries_the_rule_the_tool_less_build_the_one_liner_and_the_date(monkeypatch):
    out = await _web_prep(monkeypatch)
    assert out["web_turn"] is not None and out["web_search_granted"] is True
    _assert_once_before_fence(out["system_instruction"], _WEB_RULE)
    assert _WEB_NONE not in out["system_instruction"]
    # After the report rule, so both trusted blocks sit together ahead of the fence.
    assert out["system_instruction"].index(_SENTINEL) < out["system_instruction"].index("WEB RESULTS:")
    # The tool-less build has no results in front of it: the one-liner, never the rule.
    _assert_once_before_fence(out["system_instruction_no_tools"], _WEB_NONE)
    assert _WEB_RULE not in out["system_instruction_no_tools"]
    assert out["report_as_of"] == "Sep 22, 2026"
    assert out["web_turn"].report_date == "Sep 22, 2026"


@pytest.mark.asyncio
async def test_a_web_intent_the_gate_cannot_serve_gets_only_the_one_liner(monkeypatch):
    out = await _web_prep(monkeypatch, gate_on=False)
    assert out["web_turn"] is None
    for key in ("system_instruction", "system_instruction_no_tools"):
        _assert_once_before_fence(out[key], _WEB_NONE)
        assert _WEB_RULE not in out[key]


@pytest.mark.asyncio
@pytest.mark.parametrize("message,session_type", [
    ("What is the moat?", "REPORT"),          # no web intent
    (_WEB_ASK, "STOCK"),                      # web intent outside a report chat
    (_WEB_ASK, "NORMAL"),
])
async def test_no_web_line_without_a_report_chat_web_intent(monkeypatch, message, session_type):
    for gate_on in (True, False):
        out = await _web_prep(monkeypatch, gate_on=gate_on, message=message, session_type=session_type)
        for key in ("system_instruction", "system_instruction_no_tools"):
            assert "WEB RESULTS:" not in out[key] and "WEB SEARCH:" not in out[key], (key, gate_on)
        assert out["web_turn"] is None


@pytest.mark.asyncio
async def test_the_rule_renders_even_when_the_report_did_not_resolve(monkeypatch):
    out = await _web_prep(monkeypatch, block=None, as_of="2026-09-22")
    assert "<<<CLIENT_CONTEXT>>>" not in out["system_instruction"]
    assert out["system_instruction"].count(_WEB_RULE) == 1
    # No server-built report block → no report date, whatever the meta said.
    assert out["report_as_of"] is None and out["web_turn"].report_date is None


@pytest.mark.asyncio
async def test_a_client_pass_through_never_supplies_the_report_date(monkeypatch):
    out = await _web_prep(monkeypatch, block=None, context="client typed: Report dated 1999-01-01.")
    assert out["report_as_of"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("as_of,expected", [
    (None, None), ("Oct 2, 4:31 PM", None), ("Sep 22, 2026 close", "Sep 22, 2026 close"),
    ("not a date <<<", None),
])
async def test_the_report_date_is_humanized_or_dropped(monkeypatch, as_of, expected):
    out = await _web_prep(monkeypatch, as_of=as_of)
    assert out["report_as_of"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_raise", [False, True])
async def test_non_stream_door_web_rule_fallback_line_and_report_date(monkeypatch, tools_raise):
    _web_gate(monkeypatch, on=True)
    svc, seen = _generate_service(monkeypatch, _BUILT, tools_raise=tools_raise)
    _patch_resolver_dated(monkeypatch, _BUILT, "2026-09-22")
    out = await svc.generate_response(
        "sess", _WEB_ASK, session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman", user_id="user-1",
    )
    _assert_once_before_fence(seen["tools"], _WEB_RULE)
    assert out["report_as_of"] == "Sep 22, 2026"
    if tools_raise:
        # The plain-text fallback never saw web results: the one-liner, no tool names.
        _assert_once_before_fence(seen["fallback"], _WEB_NONE)
        assert _WEB_RULE not in seen["fallback"]
        from app.services.agents.chat_tools import TOOL_DESCRIPTIONS
        assert not any(name in seen["fallback"] for name in TOOL_DESCRIPTIONS)
        assert out["web_search_used"] is False


@pytest.mark.asyncio
async def test_non_stream_door_unserved_web_intent_gets_the_one_liner(monkeypatch):
    _web_gate(monkeypatch, on=False)
    svc, seen = _generate_service(monkeypatch, _BUILT, tools_raise=False)
    await svc.generate_response(
        "sess", _WEB_ASK, session_type="REPORT", stock_id="AVGO",
        context_type="TICKER_REPORT", reference_id="AVGO|bill_ackman", user_id="user-1",
    )
    _assert_once_before_fence(seen["tools"], _WEB_NONE)
    assert _WEB_RULE not in seen["tools"]


def test_history_fed_to_the_model_never_carries_the_code_authored_caveat():
    from app.services.chat_security import web_caveat_line
    caveat = web_caveat_line("2026-09-22")
    turns = ChatService._fmt_turns([
        {"role": "user", "content": "verify the DOJ case"},
        {"role": "assistant", "content": f"Reuters, Sep 30, 2026: it advanced.\n\n{caveat}"},
        # A user quoting the caveat is the user's text — left alone.
        {"role": "user", "content": caveat},
    ])
    assert "Assistant: Reuters, Sep 30, 2026: it advanced." in turns
    assert turns.count("Web results are third-party") == 1
    assert turns.endswith(f"User: {caveat}")
