"""Math/transform tests for the hardened Earnings Shockers pipeline.

Covers the two pure functions that decide what the App-Exclusive Signals
"Earnings Shockers" card shows:
  * ``_aggregate_earnings`` — penny-estimate floor, foreign/threshold skips,
    freshest-per-symbol dedup, and FRESHEST-FIRST ranking.
  * ``_earnings_quote_ok`` — the exchange + $250M quality gate (kills OTC).

No network / Supabase — the functions take plain dicts. Run:
    cd backend && ./venv/bin/pytest tests/test_signals_earnings.py -x
"""

import math

from app.services.signals_service import (
    _aggregate_earnings,
    _earnings_quote_ok,
    _EARNINGS_MIN_ABS_ESTIMATE,
)


def _row(symbol, date, actual, estimate):
    return {
        "symbol": symbol,
        "date": date,
        "epsActual": actual,
        "epsEstimated": estimate,
    }


# ── _aggregate_earnings ──────────────────────────────────────────────────

def test_penny_estimate_artifact_skipped_but_low_bar_kept():
    """est below the floor explodes the % → skipped; NKE's real $0.11 bar survives."""
    cal = [
        _row("PENNY", "2026-07-02", 0.10, 0.02),   # est 0.02 < 0.05 → +400% artifact, DROP
        _row("NKE", "2026-07-02", 0.72, 0.11),     # est 0.11 ≥ 0.05 → +554% real beat, KEEP
    ]
    assert _EARNINGS_MIN_ABS_ESTIMATE == 0.05
    res = _aggregate_earnings(cal)
    syms = [e.symbol for e in res.entries]
    assert "PENNY" not in syms
    assert "NKE" in syms


def test_recency_first_fresh_small_beats_stale_big():
    """A fresh +15% must outrank a 5-day-old +90% (freshest-first)."""
    cal = [
        _row("STALEBIG", "2026-06-28", 1.90, 1.00),  # +90%, older
        _row("FRESH", "2026-07-03", 1.15, 1.00),     # +15%, newer
    ]
    res = _aggregate_earnings(cal)
    assert res.entries[0].symbol == "FRESH"
    assert res.entries[1].symbol == "STALEBIG"


def test_magnitude_orders_within_same_day():
    cal = [
        _row("SMALL", "2026-07-03", 1.40, 1.00),  # +40%
        _row("BIG", "2026-07-03", 1.80, 1.00),    # +80%
    ]
    res = _aggregate_earnings(cal)
    assert [e.symbol for e in res.entries] == ["BIG", "SMALL"]


def test_keeps_freshest_report_per_symbol():
    """A symbol reporting twice keeps its MOST-RECENT report, not the biggest."""
    cal = [
        _row("DUP", "2026-06-28", 1.90, 1.00),  # +90%, older
        _row("DUP", "2026-07-01", 1.12, 1.00),  # +12%, newer → this one wins
    ]
    res = _aggregate_earnings(cal)
    assert len(res.entries) == 1
    assert res.entries[0].value == 12.0
    assert res.as_of_date == "2026-07-01"


def test_foreign_dotted_symbols_dropped():
    cal = [
        _row("ZOO.L", "2026-07-02", 2.00, 1.00),   # foreign → dropped
        _row("REAL", "2026-07-02", 1.50, 1.00),
    ]
    res = _aggregate_earnings(cal)
    assert [e.symbol for e in res.entries] == ["REAL"]


def test_below_threshold_and_missing_fields_skipped():
    cal = [
        _row("TINY", "2026-07-02", 1.05, 1.00),          # +5% < 10% floor → skip
        _row("NOACT", "2026-07-02", None, 1.00),         # missing actual → skip
        _row("NOEST", "2026-07-02", 1.50, None),         # missing estimate → skip
        _row("GOOD", "2026-07-02", 1.50, 1.00),          # +50% → keep
    ]
    res = _aggregate_earnings(cal)
    assert [e.symbol for e in res.entries] == ["GOOD"]


def test_nan_values_do_not_crash_and_are_skipped():
    cal = [
        _row("NANACT", "2026-07-02", float("nan"), 1.00),
        _row("NANEST", "2026-07-02", 1.50, float("nan")),
        _row("GOOD", "2026-07-02", 1.50, 1.00),
    ]
    res = _aggregate_earnings(cal)
    assert [e.symbol for e in res.entries] == ["GOOD"]


def test_empty_or_nonlist_returns_none():
    assert _aggregate_earnings([]) is None
    assert _aggregate_earnings(None) is None
    assert _aggregate_earnings("not a list") is None


def test_as_of_is_the_freshest_shown_date():
    cal = [
        _row("A", "2026-07-01", 1.50, 1.00),
        _row("B", "2026-07-03", 1.20, 1.00),
        _row("C", "2026-06-29", 1.90, 1.00),
    ]
    res = _aggregate_earnings(cal)
    assert res.as_of_date == "2026-07-03"


# ── _earnings_quote_ok (exchange + $250M gate) ───────────────────────────

def test_quote_gate_accepts_major_exchange_large_cap():
    assert _earnings_quote_ok({"exchange": "NYSE", "marketCap": 1_000_000_000}) is True
    assert _earnings_quote_ok({"exchange": "nasdaq", "marketCap": 300_000_000}) is True  # case-insensitive
    assert _earnings_quote_ok({"exchange": "AMEX", "marketCap": 300_000_000}) is True


def test_quote_gate_drops_otc():
    # TCYSF / BKRRF are OTC with caps above $250M — the exchange check is what stops them.
    assert _earnings_quote_ok({"exchange": "OTC", "marketCap": 1_000_000_000}) is False


def test_quote_gate_drops_sub_floor_and_bad_cap():
    assert _earnings_quote_ok({"exchange": "NYSE", "marketCap": 100_000_000}) is False   # < $250M
    assert _earnings_quote_ok({"exchange": "NYSE", "marketCap": float("nan")}) is False  # NaN cap
    assert _earnings_quote_ok({"exchange": "NYSE"}) is False                             # missing cap
    assert _earnings_quote_ok({"marketCap": 1_000_000_000}) is False                     # missing exchange
    assert _earnings_quote_ok({}) is False
    assert _earnings_quote_ok("not a dict") is False
