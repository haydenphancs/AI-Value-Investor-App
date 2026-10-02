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


def _patch_resolver(monkeypatch, block, seen=None):
    """Make the lazily-imported resolver return `block` (else the client context).

    `user_id` is accepted (and recorded into `seen` when given) because the
    TICKER_REPORT branch grounds on the CALLER'S OWN frozen `research_reports` row
    and therefore needs an identity — see `_resolve_ticker_report`. A fake that did
    not take it would let chat_service stop forwarding it without a test noticing.
    """
    import app.services.chat_context_resolver as ccr

    class _FakeResolver:
        async def resolve(self, context_type, reference_id, client_context=None,
                          user_id=None):
            if seen is not None:
                seen["user_id"] = user_id
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
