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
    svc.gemini = _FakeGemini(raises=embed_raises)

    svc._get_recent_messages = lambda session_id, limit=10: list(history or [])
    svc._search_filing_chunks = lambda emb, ticker: list(chunks or [])
    svc._search_all_chunks = lambda emb: list(chunks or [])

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


def _patch_resolver(monkeypatch, block):
    """Make the lazily-imported resolver return `block` (else the client context)."""
    import app.services.chat_context_resolver as ccr

    class _FakeResolver:
        async def resolve(self, context_type, reference_id, client_context=None):
            return block if block is not None else client_context

    monkeypatch.setattr(ccr, "get_chat_context_resolver", lambda: _FakeResolver())


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
async def test_reasoning_scaffolding_present_when_streaming(monkeypatch):
    """CURRENT CONTRACT (the `===ANSWER===` prompt hack). Phase 1a must preserve it; Phase 1b
    replaces it with real streamed thinking tokens and UPDATES this assertion."""
    _patch_resolver(monkeypatch, None)
    svc = _make_service()
    out = await svc.prepare_stream_generation(
        session_id="s1", user_message="q",
        stock_id="AAPL", context_type="STOCK", reference_id="AAPL",
    )
    assert "===ANSWER===" in out["system_instruction"]
    assert "===ANSWER===" in out["prompt"]


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
