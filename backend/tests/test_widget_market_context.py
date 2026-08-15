"""The market band — how the tape itself did, above the single name.

Pure module, so every branch is testable with plain values, per `.claude/rules/testing.md`.

WHAT THIS GUARDS
----------------
The band is the answer to "is the whole market red, or just this stock", which the widget
previously could not say: the S&P's move existed in the payload but only buried inside the
headline mover's own `context`, where nothing rendered it.

The failure to avoid is the same one the rest of this feature is built around — **a claim
made out of a failed upstream call**. A band of zeroes ("the market was flat, no sector
moved") is a statement about the world, and manufacturing it from two 429s is
indistinguishable, on a Home Screen, from a real reading. So the builder returns None when
nothing was readable, and omits each half independently.
"""

from __future__ import annotations

import json
import math

import pytest

from app.services.widget_movers_service import (
    _INDEX_SYMBOLS,
    build_market_context,
)

_SECTORS = [
    ("Energy", 0.81), ("Utilities", 0.44), ("Health Care", 0.12),
    ("Financials", -0.20), ("Industrials", -0.35), ("Materials", -0.51),
    ("Real Estate", -0.62), ("Consumer Staples", -0.70),
    ("Communication Services", -0.95), ("Consumer Discretionary", -1.10),
    ("Technology", -1.44),
]


def _rows(**overrides):
    base = {
        "^GSPC": {"symbol": "^GSPC", "changePercentage": -0.62, "price": 6412.1},
        "^IXIC": {"symbol": "^IXIC", "changePercentage": -0.91, "price": 21340.5},
        "^DJI": {"symbol": "^DJI", "changePercentage": -0.30, "price": 44812.0},
    }
    base.update(overrides)
    return base


# ── the happy path ────────────────────────────────────────────────────


def test_the_band_carries_every_index_in_declared_order():
    mc = build_market_context(_rows(), _SECTORS, sector_available=True)
    assert [i.symbol for i in mc.indices] == [s for s, _ in _INDEX_SYMBOLS]
    assert [i.label for i in mc.indices] == ["S&P 500", "Nasdaq", "Dow"]


def test_the_label_comes_from_the_server_not_the_symbol():
    """An installed widget cannot learn a new index's display name on its own."""
    mc = build_market_context(_rows(), _SECTORS, sector_available=True)
    assert mc.indices[0].label == "S&P 500" and mc.indices[0].symbol == "^GSPC"


def test_breadth_counts_only_sectors_that_are_up():
    mc = build_market_context(_rows(), _SECTORS, sector_available=True)
    assert mc.breadth_total == 11
    assert mc.breadth_up == 3          # Energy, Utilities, Health Care
    assert mc.leading_sector == "Energy"
    assert mc.lagging_sector == "Technology"


def test_the_sentence_never_contradicts_the_numbers():
    mc = build_market_context(_rows(), _SECTORS, sector_available=True)
    assert "S&P 500 fell 0.6%" in mc.text
    assert "3 of 11 sectors up" in mc.text


# ── degradation: each leg fails on its own ────────────────────────────


def test_a_failed_sector_snapshot_omits_breadth_and_keeps_the_indices():
    mc = build_market_context(_rows(), [], sector_available=False)
    assert len(mc.indices) == 3
    assert mc.breadth_up is None and mc.breadth_total is None
    assert mc.leading_sector is None


def test_a_failed_sector_snapshot_never_reports_zero_sectors_up():
    """"0 of 11 sectors up" is a claim about the market, not an absence of data."""
    mc = build_market_context(_rows(), [], sector_available=False)
    assert mc.breadth_up != 0
    assert "sectors up" not in (mc.text or "")


def test_failed_index_quotes_keep_the_breadth_line():
    mc = build_market_context({}, _SECTORS, sector_available=True)
    assert mc.indices == []
    assert mc.breadth_up == 3
    assert "sectors up" in mc.text


def test_everything_failing_yields_no_band_at_all():
    """The tile then leads with the mover, exactly as it did before this existed."""
    assert build_market_context({}, [], sector_available=False) is None
    assert build_market_context({}, [], sector_available=True) is None


# ── the numbers themselves ────────────────────────────────────────────


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "n/a", {}])
def test_an_unreadable_change_is_dropped_not_zeroed(bad):
    mc = build_market_context(
        _rows(**{"^GSPC": {"symbol": "^GSPC", "changePercentage": bad, "price": None}}),
        _SECTORS, sector_available=True,
    )
    syms = [i.symbol for i in mc.indices]
    assert "^GSPC" not in syms, "an index with no readable number must not be rendered as 0"
    assert "^IXIC" in syms, "the other indices must survive"


def test_a_flat_index_is_described_as_flat_not_fallen():
    """`_dir_word` answers rose/fell; neither is true of a flat tape, and
    "fell 0.0%" contradicts itself in a single phrase."""
    mc = build_market_context(
        _rows(**{"^GSPC": {"symbol": "^GSPC", "changePercentage": 0.0, "price": 6400.0}}),
        _SECTORS, sector_available=True,
    )
    assert "S&P 500 flat" in mc.text
    assert "fell 0.0%" not in mc.text


def test_a_single_readable_sector_is_not_both_leader_and_laggard():
    mc = build_market_context(_rows(), [("Energy", 0.81)], sector_available=True)
    assert mc.leading_sector == "Energy"
    assert mc.lagging_sector is None


def test_an_all_red_tape_names_no_leader_in_the_sentence():
    """"Technology leads -1.4%" is not leadership."""
    mc = build_market_context(
        _rows(), [(n, -abs(c)) for n, c in _SECTORS], sector_available=True
    )
    assert mc.breadth_up == 0
    assert "leads" not in mc.text


def test_the_band_serialises_without_nan_or_infinity():
    """`NaN`/`Infinity` are not valid JSON and fail Swift's decoder outright."""
    mc = build_market_context(_rows(), _SECTORS, sector_available=True)
    blob = json.dumps(mc.model_dump(mode="json"))
    assert "NaN" not in blob and "Infinity" not in blob
    for i in mc.indices:
        assert i.change_percent is None or math.isfinite(i.change_percent)


def test_percentages_are_rounded_for_the_wire():
    mc = build_market_context(
        _rows(**{"^GSPC": {"symbol": "^GSPC", "changePercentage": -0.6234567, "price": 1.23456}}),
        _SECTORS, sector_available=True,
    )
    assert mc.indices[0].change_percent == -0.62
    assert mc.indices[0].price == 1.23


def test_a_missing_index_row_does_not_shift_the_others():
    mc = build_market_context(
        {"^DJI": {"symbol": "^DJI", "changePercentage": -0.30, "price": 44812.0}},
        _SECTORS, sector_available=True,
    )
    assert [i.symbol for i in mc.indices] == ["^DJI"]
    assert mc.indices[0].label == "Dow"
