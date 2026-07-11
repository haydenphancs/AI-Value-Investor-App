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
