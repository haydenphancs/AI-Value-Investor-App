"""Math/transform tests for the hardened Earnings Shockers pipeline.

Covers the two pure functions that decide what the App-Exclusive Signals
"Earnings Shockers" card shows:
  * ``_aggregate_earnings`` — penny-estimate floor, foreign/threshold skips,
    freshest-per-symbol dedup, and FRESHEST-FIRST ranking.
  * ``_earnings_quote_ok`` — the exchange + $250M quality gate (kills OTC).
  * the 2026-10-09 hardening: the dropped/added-digit skip and the non-common
    listing rules (``_non_common_symbol_shape`` / ``_is_non_common_listing``), each
    with the honest twins that must survive them.

No network / Supabase — the functions take plain dicts. Run:
    cd backend && ./venv/bin/pytest tests/test_signals_earnings.py -x
"""

import math

import logging
import time

import pytest

from app.services.signals_service import (
    _aggregate_earnings,
    _earnings_quote_ok,
    _is_non_common_listing,
    _non_common_symbol_shape,
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


# ── Dropped / added digit in the feed's EPS actual (2026-10-09) ──────────
#
# FMP occasionally ships an actual with a digit dropped or added (0.169 for a real 1.69).
# Ranked as-is it leads the card as a "-90% miss" / "+900% beat". The shared signature
# (`_earnings_common.eps_digit_shift_suspect`, no GAAP tie-break: the calendar has none)
# SKIPS the row — the correct degraded behaviour is no row, never a "fixed" number.


def test_digit_shift_actuals_are_skipped_not_ranked(caplog):
    cal = [
        _row("DROPPED", "2026-07-03", 0.169, 1.70),   # ratio ~0.1  → "-90%"  → skip
        _row("ADDED", "2026-07-03", 17.0, 1.70),      # ratio 10    → "+900%" → skip
        _row("NEGDROP", "2026-07-03", -0.169, -1.70), # same sign, ratio ~0.1 → skip
        _row("REAL", "2026-07-03", 1.95, 1.50),       # +30% → kept
    ]
    with caplog.at_level(logging.WARNING, logger="app.services.signals_service"):
        res = _aggregate_earnings(cal)
    assert [e.symbol for e in res.entries] == ["REAL"]
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "dropped/added-digit" in msg and "DROPPED 0.169 vs 1.7" in msg and "ADDED" in msg


def test_real_large_misses_and_band_edges_survive_the_digit_guard():
    cal = [
        _row("REAL85", "2026-07-03", 0.255, 1.70),   # ratio 0.15: log10 -0.82, NOT a power of ten → -85% kept
        _row("EDGE20", "2026-07-03", 0.34, 1.70),    # ratio 0.2: inside the normal band → -80% kept
        _row("FLIP", "2026-07-03", -0.17, 1.70),     # opposite signs can never be a digit shift → -110% kept
    ]
    res = _aggregate_earnings(cal)
    got = {e.symbol: e.value for e in res.entries}
    assert got == {"REAL85": -85.0, "EDGE20": -80.0, "FLIP": -110.0}


def test_a_suspect_newest_row_does_not_hide_an_honest_older_report():
    # Same skip semantics as the other filters: the suspect row is dropped, it does not
    # poison the symbol — an honest report earlier in the week still ranks.
    cal = [
        _row("TWICE", "2026-07-03", 0.169, 1.70),   # newest, suspect → skipped
        _row("TWICE", "2026-06-30", 1.30, 1.00),    # older, honest +30%
    ]
    res = _aggregate_earnings(cal)
    assert [(e.symbol, e.value) for e in res.entries] == [("TWICE", 30.0)]
    assert res.as_of_date == "2026-06-30"


# ── Non-common listings (2026-10-09: RZLVW "… Warrants" ranked #7) ───────


@pytest.mark.parametrize("sym", ["ABCD-WT", "ABCD-WTS", "ABCD-WS", "ABCD-W", "XYZ-U", "XYZ-UN",
                                 "ABC-R", "ABC-RT", "ABC-WI", "BAC-P", "BAC-PB", "BAC-PL"])
def test_dash_suffixed_non_common_lines_are_skipped_before_quoting(sym):
    assert _non_common_symbol_shape(sym) is True
    res = _aggregate_earnings([_row(sym, "2026-07-03", 2.0, 1.0), _row("COMMON", "2026-07-03", 2.0, 1.0)])
    assert [e.symbol for e in res.entries] == ["COMMON"]


@pytest.mark.parametrize("sym", ["BRK-B", "BF-B", "MOG-A", "HEI-A", "LEN-B", "GEF-B", "NVR", "SNOW"])
def test_class_shares_and_short_symbols_keep_their_rows(sym):
    # A class share carries ONE class letter; a 3-4 letter symbol ending in R/W is a
    # company (NVR, SNOW), not a fifth-letter identifier.
    assert _non_common_symbol_shape(sym) is False
    assert _non_common_symbol_shape(sym, "NYSE") is False
    res = _aggregate_earnings([_row(sym, "2026-07-03", 2.0, 1.0)])
    assert [e.symbol for e in res.entries] == [sym]


def test_nasdaq_fifth_letter_rule_needs_the_exchange():
    assert _non_common_symbol_shape("RZLVW") is False            # pre-quote: exchange unknown
    assert _non_common_symbol_shape("RZLVW", "NASDAQ") is True   # warrant
    assert _non_common_symbol_shape("ABCDR", "nasdaq") is True   # rights (case-insensitive)
    assert _non_common_symbol_shape("SPCXU", "NASDAQ") is True   # unit
    assert _non_common_symbol_shape("ABCDW", "NYSE") is False    # the convention is NASDAQ's
    for share_class in ("GOOGL", "FWONK", "CMCSA", "LBRDK", "FWONA"):
        assert _non_common_symbol_shape(share_class, "NASDAQ") is False


@pytest.mark.parametrize("sym,name,exchange", [
    ("RZLVW", "Rezolve AI Limited Warrants", "NASDAQ"),            # the live 2026-10-09 row
    ("RZLVW", None, "NASDAQ"),                                       # no name: the fifth letter alone
    ("SPCXU", "Space Acquisition Corp Units", "NASDAQ"),
    ("ABCDR", "ABCD Acquisition Corp Rights", "NASDAQ"),
    ("ABCX", "ABCX Acquisition Corp - Rights", "NYSE"),
    ("DUKR", "Duke Robotics Corp. C/wts Exp 06/05/2031", "NASDAQ"),
    ("ABCD-WT", "ABCD Corp", "NYSE"),
    ("BAC-PB", "Bank of America Corporation", "NYSE"),
    ("XYZP", "XYZ Corp 6.5% Series A Cumulative Preferred", "NYSE"),
    ("XYZQ", "XYZ Corp Depositary Shares Preferred Stock", "NYSE"),
    ("XYZN", "XYZ Corp 5.25% Senior Notes due 2031", "NYSE"),
    ("XYZD", "XYZ Corp Convertible Debentures", "NYSE"),
    ("XYZV", "XYZ Corp When Issued", "NYSE"),
    ("XYZF", "XYZ Corp Pfd Series B", "NYSE"),
])
def test_non_common_listings_are_recognised(sym, name, exchange):
    assert _is_non_common_listing(sym, name, exchange) is True


@pytest.mark.parametrize("sym,name,exchange", [
    ("PFBC", "Preferred Bank", "NASDAQ"),                                     # "Preferred" is its NAME
    ("UNT", "Unit Corporation", "NYSE"),
    ("URI", "United Rentals, Inc.", "NYSE"),
    ("BFAM", "Bright Horizons Family Solutions Inc.", "NYSE"),               # "right" inside a word
    ("WBD", "Warner Bros. Discovery, Inc.", "NASDAQ"),                      # Warner ≠ warrant
    ("EPD", "Enterprise Products Partners L.P. Common Units", "NYSE"),      # an MLP's units ARE its equity
    ("FWONA", "Liberty Media Corporation Series A Liberty Formula One", "NASDAQ"),  # tracking stock
    ("GOOGL", "Alphabet Inc.", "NASDAQ"),
    ("BRK-B", "Berkshire Hathaway Inc.", "NYSE"),
    ("NVR", "NVR, Inc.", "NYSE"),
    ("SNOW", "Snowflake Inc.", "NYSE"),
    ("CMCSA", "Comcast Corporation", "NASDAQ"),
    ("MSFT", None, "NASDAQ"),                                                # no name → symbol rules only
    ("META", "", "NASDAQ"),
    ("AAPL", 12345, "NASDAQ"),                                               # wrong type never raises
])
def test_honest_common_listings_survive(sym, name, exchange):
    assert _is_non_common_listing(sym, name, exchange) is False


def test_listing_name_check_is_capped_and_linear():
    # A pathological name is cut to `_NON_COMMON_NAME_MAX` before any regex runs.
    t0 = time.perf_counter()
    assert _is_non_common_listing("ABCD", "Acme " * 50_000, "NYSE") is False
    assert time.perf_counter() - t0 < 0.5
    # Names are short: a descriptor past the cap is not read (pins that the cap applies).
    assert _is_non_common_listing("ABCD", "x" * 10_000 + " Warrants", "NYSE") is False
    assert _is_non_common_listing("ABCD", "Acme Warrants", "NYSE") is True
