"""Math/transform tests for ChatService's pure chat-widget + deep-dive helpers.

No network, no Supabase, no Gemini — these are the outlier guards for the bugs an adversarial
review confirmed in the chat data flow:

  * _is_deep_dive_request  — Python and/or precedence let 'deep analysis' / 'market deep dive'
                             route a STOCK chat through the (message-agnostic) Market Deep Dive
                             cache, serving a stale, off-topic report.
  * _normalize_historical  — FMP EOD rows with present-but-null / non-dict fields would raise
                             int(None) / TypeError and abort the ENTIRE stock-chart widget.
  * _build_stock_widget    — a null price/change/volume from /stable/quote fed into the
                             non-Optional StockChartWidget floats raised a Pydantic ValidationError,
                             silently dropping the chart card.
"""

from __future__ import annotations

from app.services.chat_service import ChatService


# ── _is_deep_dive_request (operator-precedence fix) ─────────────────────────

def test_stock_chat_never_triggers_deep_dive_even_with_trigger_words():
    """The bug: on a STOCK chat, 'deep analysis' / 'market deep dive' used to set is_deep_dive=True
    (bypassing the not-is_stock guard), serving a stale cached report for a different question."""
    for phrase in ("give me a deep analysis of margins",
                   "do a market deep dive",
                   "deep dive on the moat"):
        assert ChatService._is_deep_dive_request(True, "AAPL", phrase) is False


def test_non_stock_triggers_on_each_phrase():
    for phrase in ("deep dive please", "a deep analysis", "market deep dive now"):
        assert ChatService._is_deep_dive_request(False, "^GSPC", phrase) is True


def test_non_stock_without_trigger_phrase_is_false():
    assert ChatService._is_deep_dive_request(False, "^GSPC", "how is the market today") is False


def test_missing_stock_id_is_false():
    # No symbol → nothing to key the deep-dive cache on.
    assert ChatService._is_deep_dive_request(False, None, "market deep dive") is False
    assert ChatService._is_deep_dive_request(False, "", "deep analysis") is False


def test_case_insensitive():
    assert ChatService._is_deep_dive_request(False, "GCUSD", "MARKET DEEP DIVE") is True


# ── _normalize_historical (null / malformed FMP rows) ───────────────────────

def test_bare_list_shape_sorted_ascending():
    raw = [
        {"date": "2025-01-03", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 30},
        {"date": "2025-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 10},
        {"date": "2025-01-02", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 20},
    ]
    rows = ChatService._normalize_historical(raw)
    assert [r["date"] for r in rows] == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert rows[0]["volume"] == 10


def test_legacy_dict_shape_unwrapped():
    raw = {"historical": [{"date": "2025-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5}]}
    rows = ChatService._normalize_historical(raw)
    assert len(rows) == 1 and rows[0]["close"] == 1


def test_none_and_unknown_shape_yield_empty():
    assert ChatService._normalize_historical(None) == []
    assert ChatService._normalize_historical("oops") == []
    assert ChatService._normalize_historical(42) == []


def test_present_but_null_fields_coerce_to_zero_no_crash():
    # The crux: int(None) / a null-in-sort-key would abort the whole widget.
    raw = [{"date": "2025-01-02", "open": None, "high": None, "low": None, "close": None, "volume": None}]
    rows = ChatService._normalize_historical(raw)
    assert rows == [{"date": "2025-01-02", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}]


def test_null_date_does_not_crash_sort():
    raw = [
        {"date": None, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"date": "2025-01-01", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2},
    ]
    rows = ChatService._normalize_historical(raw)
    # Null date coerces to "" which sorts first; no TypeError comparing None vs str.
    assert [r["date"] for r in rows] == ["", "2025-01-01"]


def test_non_dict_rows_skipped():
    raw = [None, 7, "bad", {"date": "2025-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    rows = ChatService._normalize_historical(raw)
    assert len(rows) == 1 and rows[0]["date"] == "2025-01-01"


def test_missing_key_falls_back_to_zero():
    raw = [{"date": "2025-01-01"}]  # only a date, rest absent
    rows = ChatService._normalize_historical(raw)
    assert rows[0] == {"date": "2025-01-01", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}


# ── _build_stock_widget (null quote fields → no ValidationError) ─────────────

def _good_hist():
    return ChatService._normalize_historical(
        [{"date": "2025-01-01", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000}]
    )


def test_build_widget_happy_path():
    quote = {
        "name": "Apple Inc.", "price": 150.0, "change": 1.5, "changesPercentage": 1.0,
        "dayHigh": 151.0, "dayLow": 148.0, "volume": 5_000_000, "marketCap": 2.5e12,
        "pe": 28.0, "yearHigh": 200.0, "yearLow": 120.0,
    }
    w = ChatService._build_stock_widget("AAPL", quote, _good_hist(), 4_800_000, True)
    assert w["widget_type"] == "stock_chart"
    assert w["ticker"] == "AAPL"
    assert w["current_price"] == 150.0
    assert w["is_market_open"] is True
    assert w["avg_volume"] == 4_800_000
    assert len(w["historical_data"]) == 1


def test_build_widget_null_required_fields_coerce_to_zero():
    """The confirmed bug: a halted/thin ticker returns null price/change/volume; those feed the
    NON-Optional StockChartWidget floats and used to raise ValidationError → the card was dropped."""
    quote = {
        "name": None, "price": None, "change": None, "changesPercentage": None,
        "dayHigh": None, "dayLow": None, "volume": None,
        "marketCap": None, "pe": None, "yearHigh": None, "yearLow": None,
    }
    w = ChatService._build_stock_widget("XYZ", quote, _good_hist(), 0, False)
    assert w["widget_type"] == "stock_chart"
    assert w["company_name"] == "XYZ"          # name None → ticker fallback
    assert w["current_price"] == 0
    assert w["change"] == 0
    assert w["change_percent"] == 0
    assert w["day_high"] == 0 and w["day_low"] == 0
    assert w["volume"] == 0
    # Genuinely-optional fields stay None (not coerced).
    assert w["market_cap"] is None
    assert w["pe_ratio"] is None
    assert w["year_high"] is None and w["year_low"] is None
    assert w["is_market_open"] is False


def test_build_widget_empty_quote_still_builds():
    w = ChatService._build_stock_widget("ZZZ", {}, [], 0, None)
    assert w["widget_type"] == "stock_chart"
    assert w["ticker"] == "ZZZ" and w["company_name"] == "ZZZ"
    assert w["historical_data"] == []
    assert w["is_market_open"] is None


def test_end_to_end_null_row_does_not_drop_widget():
    """Compose the two helpers on the worst case: null quote fields + a null-volume history row."""
    hist = ChatService._normalize_historical(
        [{"date": "2025-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": None}]
    )
    w = ChatService._build_stock_widget("HALT", {"price": None, "volume": None}, hist, 0, None)
    assert w["widget_type"] == "stock_chart"
    assert w["historical_data"][0]["volume"] == 0


# ── _detect_asset_type (commodity USD codes must beat the crypto endswith heuristic) ──

def test_detect_commodity_usd_symbols_beat_crypto_heuristic():
    """The confirmed bug: `endswith('USD')` (crypto) ran BEFORE the commodity set, so every
    USD-suffixed FMP commodity code (gold GCUSD, oil CLUSD, …) mis-classified as CRYPTO and got the
    crypto analyst voice — the entire commodity-USD branch was dead code."""
    for sym in ("GCUSD", "CLUSD", "SIUSD", "NGUSD", "PLUSD", "HGUSD", "ZCUSD", "KCUSD"):
        assert ChatService._detect_asset_type(sym) == "COMMODITY"


def test_detect_commodity_plain_aliases():
    for sym in ("GOLD", "SILVER", "OIL", "NATGAS", "PLATINUM", "COPPER"):
        assert ChatService._detect_asset_type(sym) == "COMMODITY"


def test_detect_crypto_still_wins_for_non_commodity_usd():
    # USD-suffixed symbols NOT in the commodity set stay CRYPTO (no regression).
    for sym in ("BTCUSD", "ETHUSD", "SOLUSD", "ADAUSDT"):
        assert ChatService._detect_asset_type(sym) == "CRYPTO"
    for sym in ("BTC", "ETH", "DOGE"):
        assert ChatService._detect_asset_type(sym) == "CRYPTO"


def test_detect_index_stock_and_empty():
    assert ChatService._detect_asset_type("^GSPC") == "INDEX"
    assert ChatService._detect_asset_type("AAPL") == "STOCK"
    assert ChatService._detect_asset_type("") == "NORMAL"
    assert ChatService._detect_asset_type(None) == "NORMAL"


def test_detect_is_case_insensitive():
    assert ChatService._detect_asset_type("gcusd") == "COMMODITY"
    assert ChatService._detect_asset_type("btcusd") == "CRYPTO"


# ── _get_valuation_level (missing / non-positive P/E must not read as "Bargain") ──

def test_valuation_level_zero_or_negative_is_unknown():
    """The bug: the index sector-benchmark fallback yields pe=0 on a thin/failed recompute; without
    a guard, 0 < 18 rendered as 'Bargain' — a no-data market looked attractively cheap."""
    assert ChatService._get_valuation_level(0) == "Unknown"
    assert ChatService._get_valuation_level(0.0) == "Unknown"
    assert ChatService._get_valuation_level(-5) == "Unknown"
    assert ChatService._get_valuation_level(None) == "Unknown"


def test_valuation_level_nan_is_unknown():
    """The bug: NaN slips past `nan <= 0` AND every `nan < band` (all False), so it fell through to
    the most-expensive 'Overheated' band — the exact inverse of the 0/negative guard's intent. NaN is
    reachable: index_service does `round(pe,1) if pe else 0`, and `round(float('nan'),1) == nan`."""
    assert ChatService._get_valuation_level(float("nan")) == "Unknown"


def test_valuation_level_bands():
    assert ChatService._get_valuation_level(15) == "Bargain"
    assert ChatService._get_valuation_level(17.9) == "Bargain"
    assert ChatService._get_valuation_level(18) == "Fair Value"
    assert ChatService._get_valuation_level(23.9) == "Fair Value"
    assert ChatService._get_valuation_level(24) == "Expensive"
    assert ChatService._get_valuation_level(29.9) == "Expensive"
    assert ChatService._get_valuation_level(30) == "Overheated"
    assert ChatService._get_valuation_level(45) == "Overheated"


# ── _build_sources (RAG source_type → correct pill label, not always "SEC filing") ──

def test_build_sources_labels_book_and_article_by_type():
    """The latent bug: every RAG citation was labeled 'SEC filing'; once the corpus is ingested a
    book/article chunk would be mis-attributed to a filing. source_type now drives the label."""
    citations = [
        {"index": 1, "source": "Chapter 20", "source_type": "book",
         "source_label": "The Intelligent Investor by Benjamin Graham", "text": "..."},
        {"index": 2, "source": "Section 3", "source_type": "article",
         "source_label": "Understanding Moats", "text": "..."},
        {"index": 3, "source": "Risk Factors", "source_type": "filing",
         "source_label": "AAPL 10-K 2024", "text": "..."},
    ]
    labels = {(s["label"], s["detail"]) for s in ChatService._build_sources(None, None, citations)}
    assert ("Book", "The Intelligent Investor by Benjamin Graham") in labels
    assert ("Article", "Understanding Moats") in labels
    assert ("SEC filing", "AAPL 10-K 2024") in labels


def test_build_sources_absent_source_type_defaults_to_sec_filing():
    # The current filing-only stock path: chunks carry no source_type / source_label → detail is the
    # section title and the label stays 'SEC filing' (unchanged behavior).
    citations = [{"index": 1, "source": "Management Discussion", "text": "..."}]
    sources = ChatService._build_sources(None, None, citations)
    assert {"label": "SEC filing", "detail": "Management Discussion"} in sources


def test_build_sources_context_pill_and_dedup():
    citations = [
        {"index": 1, "source": "Risk Factors", "source_type": "filing", "source_label": "MD&A", "text": ""},
        {"index": 2, "source": "Risk Factors", "source_type": "filing", "source_label": "MD&A", "text": ""},
    ]
    sources = ChatService._build_sources("STOCK", "AAPL", citations)
    # Screen-context pill first, then ONE deduped filing pill (the duplicate MD&A collapses).
    assert sources[0] == {"label": "Company financials", "detail": "AAPL"}
    assert [s for s in sources if s["label"] == "SEC filing"] == [{"label": "SEC filing", "detail": "MD&A"}]


def test_build_sources_skips_document_fallback():
    citations = [{"index": 1, "source": "Document", "text": ""}]
    assert ChatService._build_sources(None, None, citations) == []


# ── _build_prompt (a present-but-null chunk_text must not TypeError the join) ──
#
# The latent bug: `"\n---\n".join(c.get("chunk_text", "") …)` — `.get(k, "")` returns None on a
# present-but-NULL key (only ABSENT keys get the default), and `str.join` on a None raises
# TypeError. `_build_prompt` runs OUTSIDE any try/except (generate_response /
# prepare_stream_generation), so a single malformed chunk row (reachable once the RAG corpus is
# ingested — chunk_text is NOT NULL today but the ingest path is new) would abort the whole prompt
# build → the user gets an error frame instead of an answer. The fix uses `(x or "")`.

def test_build_prompt_null_chunk_text_does_not_crash():
    chunks = [
        {"chunk_text": None, "section_title": None},   # the crash trigger pre-fix
        {"chunk_text": "real evidence text"},
    ]
    prompt = ChatService._build_prompt("What is the moat?", "", chunks)
    # Did not raise; the real chunk survives and the null chunk contributes an empty string
    # (never the literal "None") — no poisoned context reaches the model.
    assert "real evidence text" in prompt
    assert "None" not in prompt
    assert "RELEVANT CONTEXT" in prompt
    assert "What is the moat?" in prompt


def test_build_prompt_all_null_chunks_still_builds():
    prompt = ChatService._build_prompt("hi", "", [{"chunk_text": None}, {"chunk_text": None}])
    assert "None" not in prompt
    assert "hi" in prompt


def test_build_prompt_no_chunks_no_context_section():
    prompt = ChatService._build_prompt("just a question", "CONVERSATION HISTORY:\nUser: hey", [])
    assert "RELEVANT CONTEXT" not in prompt          # no chunks → no context block, no citation note
    assert "CONVERSATION HISTORY" in prompt
    assert "just a question" in prompt


def test_build_prompt_missing_chunk_text_key_absent_is_empty():
    # Absent key (not null) already worked via the default; assert the fix didn't regress it.
    prompt = ChatService._build_prompt("q", "", [{"section_title": "S"}])
    assert "None" not in prompt
    assert "RELEVANT CONTEXT" in prompt
