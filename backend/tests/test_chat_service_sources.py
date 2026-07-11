"""Math/transform tests for ChatService._build_sources.

Pure function — no network, no Supabase, no Gemini. Builds the small "sources" list
shown in the chat thinking card from the grounding we already resolved (screen context
+ RAG filing citations). Called by prepare_stream_generation and persisted for reload.
"""

from __future__ import annotations

from app.services.chat_service import ChatService


def test_ticker_context_pill_uses_ticker_before_pipe():
    """Report/asset contexts show a named pill whose detail is the ticker (ref_id before '|')."""
    sources = ChatService._build_sources("TICKER_REPORT", "AAPL|warren_buffett", None)
    assert sources == [{"label": "Cay research report", "detail": "AAPL"}]

    sources = ChatService._build_sources("STOCK", "MSFT", None)
    assert sources == [{"label": "Company financials", "detail": "MSFT"}]


def test_slug_context_has_no_ticker_detail():
    """Money Moves / Journey / Book use a slug/order ref that ISN'T a readable ticker →
    label pill only, detail stays None (never leak a raw slug into the UI)."""
    sources = ChatService._build_sources("MONEY_MOVES_ARTICLE", "why-diversify-101", None)
    assert sources == [{"label": "Money Moves article", "detail": None}]


def test_filing_citations_dedup_and_skip_generic():
    """RAG citations → distinct SEC-filing sections; duplicate sections collapse and the
    generic 'Document' fallback is skipped."""
    citations = [
        {"index": 1, "source": "Risk Factors", "text": "..."},
        {"index": 2, "source": "risk factors", "text": "dupe (case-insensitive)"},
        {"index": 3, "source": "Document", "text": "generic"},
        {"index": 4, "source": "MD&A", "text": "..."},
        {"index": 5, "source": "", "text": "no section"},
    ]
    sources = ChatService._build_sources("STOCK", "AAPL", citations)
    details = [s["detail"] for s in sources]
    assert details == ["AAPL", "Risk Factors", "MD&A"]
    assert all(s["label"] == "SEC filing" for s in sources[1:])


def test_no_context_no_citations_is_empty():
    assert ChatService._build_sources(None, None, None) == []
    assert ChatService._build_sources("NONE", "", []) == []
    assert ChatService._build_sources("GENERAL", None, None) == []


def test_malformed_citations_do_not_crash():
    """Outlier: non-dict / missing-source citation rows are skipped, not fatal."""
    citations = [None, 42, {"index": 1}, {"source": None}, {"source": "10-K", "text": "x"}]
    sources = ChatService._build_sources("STOCK", "TSLA", citations)
    # context pill + the one valid filing section
    assert {"label": "Company financials", "detail": "TSLA"} in sources
    assert {"label": "SEC filing", "detail": "10-K"} in sources


def test_sources_capped_for_compact_card():
    """Many distinct sections cap at 6 total pills (1 context + 5 filings) to keep the card small."""
    citations = [{"index": i, "source": f"Section {i}", "text": "x"} for i in range(20)]
    sources = ChatService._build_sources("STOCK", "NVDA", citations)
    assert len(sources) == 6
