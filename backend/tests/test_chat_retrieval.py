"""Offline tests for the Phase-4 chat RAG pipeline: query-rewrite, LLM-rerank, and the
_retrieve_context orchestration. No network — gemini + the search RPCs are stubbed. Every
LLM-touching step must degrade gracefully (never raise)."""

import json

import pytest

from app.services.chat_service import ChatService


class _FakeGemini:
    def __init__(self, rewrite=None, rerank_indices=None, embedding=None, raises=False):
        self._rewrite = rewrite
        self._rerank = rerank_indices
        self._embedding = embedding if embedding is not None else [0.1] * 1536
        self._raises = raises
        self.embed_task_type = None

    async def generate_text(self, prompt, model_name=None, system_instruction=None):
        if self._raises:
            raise RuntimeError("router backend down")
        return {"text": self._rewrite}

    async def generate_json(self, prompt, model_name=None, system_instruction=None):
        if self._raises:
            raise RuntimeError("rerank backend down")
        return {"text": json.dumps({"indices": self._rerank})}

    async def generate_embedding(self, text, model_name=None, task_type=None):
        self.embed_task_type = task_type
        self.embed_text = text
        return self._embedding


def _svc(gemini):
    s = object.__new__(ChatService)
    s.gemini = gemini
    s.supabase = None
    return s


def _chunks(n):
    return [{"chunk_text": f"passage {i}", "section_title": f"S{i}"} for i in range(n)]


# ── _needs_rewrite ──────────────────────────────────────────────────────────

def test_needs_rewrite_heuristic():
    assert ChatService._needs_rewrite("why?") is True                     # short fragment
    assert ChatService._needs_rewrite("why is it down today?") is True    # pronoun "it"
    assert ChatService._needs_rewrite("what about their margins") is True # pronoun "their"
    assert ChatService._needs_rewrite("What is Apple's current P/E ratio?") is False  # standalone
    assert ChatService._needs_rewrite("Explain dollar-cost averaging") is False


# ── _rewrite_query ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rewrite_skips_without_history():
    s = _svc(_FakeGemini(rewrite="SHOULD NOT BE USED"))
    assert await s._rewrite_query("why is it down?", []) == "why is it down?"


@pytest.mark.asyncio
async def test_rewrite_skips_standalone_question():
    g = _FakeGemini(rewrite="SHOULD NOT BE USED")
    s = _svc(g)
    hist = [{"role": "user", "content": "prior"}]
    # A standalone question doesn't trigger the rewrite call at all.
    assert await s._rewrite_query("What is Apple's current P/E ratio?", hist) == "What is Apple's current P/E ratio?"


@pytest.mark.asyncio
async def test_rewrite_resolves_followup():
    g = _FakeGemini(rewrite="Apple AAPL stock price decline today reason")
    s = _svc(g)
    hist = [{"role": "user", "content": "Tell me about Apple"}, {"role": "assistant", "content": "Apple is..."}]
    out = await s._rewrite_query("why is it down?", hist)
    assert out == "Apple AAPL stock price decline today reason"


@pytest.mark.asyncio
async def test_rewrite_failure_returns_original():
    s = _svc(_FakeGemini(raises=True))
    hist = [{"role": "user", "content": "x"}]
    assert await s._rewrite_query("why is it down?", hist) == "why is it down?"


@pytest.mark.asyncio
async def test_rewrite_empty_result_returns_original():
    s = _svc(_FakeGemini(rewrite="   "))
    hist = [{"role": "user", "content": "x"}]
    assert await s._rewrite_query("why is it down?", hist) == "why is it down?"


# ── _rerank_chunks ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rerank_passthrough_when_at_or_below_top_k():
    g = _FakeGemini(rerank_indices=[9, 9, 9])  # would be nonsense; must not be called
    s = _svc(g)
    ch = _chunks(3)
    assert await s._rerank_chunks("q", ch, top_k=5) == ch


@pytest.mark.asyncio
async def test_rerank_reorders_to_top_k():
    s = _svc(_FakeGemini(rerank_indices=[3, 0]))
    ch = _chunks(5)
    out = await s._rerank_chunks("q", ch, top_k=2)
    assert out == [ch[3], ch[0]]


@pytest.mark.asyncio
async def test_rerank_filters_bad_indices_and_backfills():
    # Out-of-range (99), duplicate (1), non-int ("x") dropped; then backfill from vector order.
    s = _svc(_FakeGemini(rerank_indices=[99, 1, 1, "x", 4]))
    ch = _chunks(6)
    out = await s._rerank_chunks("q", ch, top_k=3)
    assert out[0] is ch[1] and out[1] is ch[4]        # valid picks first, in order
    assert len(out) == 3 and out[2] in (ch[0], ch[2], ch[3], ch[5])  # backfilled


@pytest.mark.asyncio
async def test_rerank_failure_uses_vector_order():
    s = _svc(_FakeGemini(raises=True))
    ch = _chunks(10)
    out = await s._rerank_chunks("q", ch, top_k=4)
    assert out == ch[:4]


# ── _retrieve_context (orchestration) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieve_context_uses_retrieval_query_and_reranks(monkeypatch):
    g = _FakeGemini(rewrite="AAPL risk factors", rerank_indices=[2, 0])
    s = _svc(g)
    candidates = _chunks(20)
    # Wider search returns 20 candidates; rerank narrows to top_k.
    s._search_filing_chunks = lambda emb, ticker, match_count=None: (
        candidates if match_count == 20 else candidates[:5]
    )
    monkeypatch.setattr("app.config.settings.RAG_TOP_K_RESULTS", 2)
    chunks, citations = await s._retrieve_context("what are its risks?",
                                                  stock_id="AAPL",
                                                  history=[{"role": "user", "content": "tell me about AAPL"}])
    # Query embedded as a QUERY (not a document); rewrite resolved the follow-up.
    assert g.embed_task_type == "RETRIEVAL_QUERY"
    assert g.embed_text == "AAPL risk factors"
    # Reranked to top 2 in the model's order, with matching citations.
    assert chunks == [candidates[2], candidates[0]]
    assert [c["source"] for c in citations] == ["S2", "S0"]
    assert citations[0]["index"] == 1 and citations[1]["index"] == 2


@pytest.mark.asyncio
async def test_retrieve_context_no_stock_uses_all_chunks(monkeypatch):
    g = _FakeGemini(rerank_indices=[0])
    s = _svc(g)
    called = {}

    def _all(emb, match_count=None):
        called["match_count"] = match_count
        return _chunks(3)

    s._search_all_chunks = _all
    chunks, citations = await s._retrieve_context("What is a P/E ratio?", stock_id=None, history=[])
    assert called["match_count"] == 20     # wider candidate set requested
    assert len(chunks) == 3 and len(citations) == 3
    assert g.embed_task_type == "RETRIEVAL_QUERY"


@pytest.mark.asyncio
async def test_retrieve_context_never_raises_on_embed_failure():
    s = _svc(_FakeGemini(raises=True))
    chunks, citations = await s._retrieve_context("q", stock_id="AAPL", history=[])
    assert chunks == [] and citations == []


# ── _condense_history (Phase 5 rolling-summary memory) ──────────────────────

@pytest.mark.asyncio
async def test_condense_empty_history():
    assert await _svc(_FakeGemini())._condense_history([]) == ""


@pytest.mark.asyncio
async def test_condense_short_history_is_verbatim_no_summary():
    s = _svc(_FakeGemini(rewrite="SHOULD NOT SUMMARIZE"))
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello there"}]
    block = await s._condense_history(hist)
    assert "CONVERSATION HISTORY" in block and "hi" in block and "hello there" in block
    assert "summary" not in block.lower()          # short → no summary call used


@pytest.mark.asyncio
async def test_condense_long_history_summarizes_older_keeps_recent():
    s = _svc(_FakeGemini(rewrite="- user asked about AAPL\n- discussed valuation"))
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"} for i in range(10)]
    block = await s._condense_history(hist)
    assert "EARLIER CONVERSATION (summary)" in block
    assert "user asked about AAPL" in block         # the rolling summary
    assert "RECENT MESSAGES" in block and "msg9" in block   # last turns verbatim
    assert "msg0" not in block                      # older rolled into the summary, not verbatim


@pytest.mark.asyncio
async def test_condense_summary_failure_falls_back_to_recent():
    s = _svc(_FakeGemini(raises=True))
    hist = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    block = await s._condense_history(hist)
    assert "CONVERSATION HISTORY" in block and "m9" in block   # recent-only, no crash
