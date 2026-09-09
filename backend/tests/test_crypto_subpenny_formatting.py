"""Sub-penny prices must stay DISTINGUISHABLE — the 24h High/Low row depends on it.

`_fmt`'s sub-dollar branch was a flat 6 decimals. On the coins that branch exists for that
is wrong twice:

  • SHIB's 24h High (5.4123e-06) and 24h Low (5.3891e-06) both render "$0.000005", so a
    real intraday range is displayed as a dead flat line — on the row whose only job is to
    show the range.
  • Anything under 1e-6 renders "$0.000000" — a fabricated zero, the same defect class as
    the "$0.00 Market Cap" the `_usd_opt` rewrite removed from this screen.

The fix mirrors `_round_close`'s magnitude ladder, so the Key Statistics column and the
chart agree on how much precision a price has.
"""
from __future__ import annotations

import pytest

from app.services.crypto_service import _fmt, _round_close


# Measured live against CoinGecko on 2026-09-09, not invented: SHIB's current price, 24h
# High and 24h Low all rendered as the IDENTICAL string "$0.000005" under the old flat-6dp
# branch — the High is 5% above the Low, and the Low differs from the current price.
_SHIB_LIVE = {"current": 5.24e-06, "high_24h": 5.5e-06, "low_24h": 5.26e-06}


def test_the_three_shib_rows_measured_live_are_all_distinguishable():
    shown = {k: _fmt(v) for k, v in _SHIB_LIVE.items()}
    assert len(set(shown.values())) == 3, shown


def test_the_shib_high_is_not_rounded_5_percent_away_from_its_real_value():
    """`f"{5.5e-06:.6f}"` is "0.000006" — a 9% overstatement of the day's high, on the row
    whose only job is to bound the day."""
    assert float(_fmt(_SHIB_LIVE["high_24h"]).lstrip("$")) == pytest.approx(5.5e-06)


def test_shib_high_and_low_do_not_collapse_to_the_same_string():
    high, low = 5.4123e-06, 5.3891e-06
    assert _fmt(high) != _fmt(low), "a real 0.4% intraday range rendered as flat"


def test_nothing_real_renders_as_a_fabricated_zero():
    for v in (1.2e-08, 5.41e-06, 9.9e-11, 1e-10):
        assert _fmt(v) not in ("$0.000000", "$0.00"), v


def test_an_exact_zero_still_renders_as_a_clean_zero():
    """A MEASURED zero is a fact and should look like one, not like $0.0000000000."""
    assert _fmt(0.0) == "$0.00"


def test_none_is_still_an_em_dash():
    assert _fmt(None) == "—"


@pytest.mark.parametrize("value,expected", [
    (0.5, "$0.50"),          # never fewer than 2 dp — "$0.5" reads as a truncation
    (0.9999, "$0.9999"),
    (5e-05, "$0.00005"),     # trailing padding trimmed
    (1.0, "$1.00"),
    (1234.5, "$1,234.50"),
    (1.5e9, "$1.50B"),
    (2.5e12, "$2.50T"),
])
def test_the_rest_of_the_ladder_is_unchanged(value, expected):
    """The magnitude branches above $1 are shipped formatting — the fix must not move them."""
    assert _fmt(value) == expected


@pytest.mark.parametrize("value", [
    5.4123e-06, 5.3891e-06, 0.00004999, 0.5, 12.34, 1.2e-08,
])
def test_fmt_never_shows_less_precision_than_round_close_keeps(value):
    """The two ladders must agree: a price the chart rounds to 10 dp cannot be printed at
    6 in the statistics column, or the same number reads differently on one screen."""
    rounded = _round_close(value)
    shown = _fmt(value).lstrip("$").replace(",", "")
    assert float(shown) == pytest.approx(rounded, rel=1e-9, abs=1e-12), (shown, rounded)


def test_negative_sub_penny_keeps_its_sign():
    assert _fmt(-5.4123e-06).startswith("$-")
