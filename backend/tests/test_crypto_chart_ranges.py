"""Crypto chart ranges under a two-year, close-only price source.

Every range the crypto screen offers must return REAL bars for the window its pill
names. Four distinct ways this went wrong, all found by probing the live service:

  1. 1D / 1W called FMP unconditionally → `FMPNotEntitledException`, which failed the
     WHOLE detail build. A hard error, not a degrade.
  2. 5Y / ALL did too.
  3. The first repair branched on `resolve_interval(...) != "daily"` to mean "intraday".
     `DEFAULT_INTERVALS` maps 5Y→"weekly" and ALL→"monthly", so both fell into that
     branch and served **seven days of hourly bars under a 5-year label**. Plausible,
     confidently wrong, and strictly worse than the exception it replaced.
  4. `daily_range_days` / `DEFAULT_INTERVALS` / `ALLOWED_INTERVALS` / `compute_date_range`
     each fall through to a 90-DAY default for an unknown code, so a "2Y" added to only
     three of the four silently serves a 3-month chart.

These are the pure range-map guards; the service-level behaviour is verified live.
"""

from __future__ import annotations

import pytest

from app.services.chart_helper import (
    ALLOWED_INTERVALS,
    DEFAULT_INTERVALS,
    compute_date_range,
    daily_range_days,
    resolve_interval,
)
from app.services.crypto_service import _INTRADAY_RANGES, _OVER_CAP_RANGES

CRYPTO_RANGES = ["1D", "1W", "3M", "6M", "1Y", "2Y"]


# ── 4. "2Y" must be known to ALL FOUR maps ───────────────────────────────────

def test_two_year_is_registered_in_every_range_map():
    """A code missing from any one of these silently degrades to 90 days."""
    assert "2Y" in DEFAULT_INTERVALS, "resolve_interval would default it"
    assert "2Y" in ALLOWED_INTERVALS, "an explicit interval could not be honoured"
    # These two have no membership test — they fall through a `.get(..., default)`, so
    # compare against the default an UNKNOWN code receives.
    unknown_days = daily_range_days("__nope__")
    unknown_from, _ = compute_date_range("__nope__")
    assert daily_range_days("2Y") != unknown_days, "daily_range_days falls through"
    assert compute_date_range("2Y")[0] != unknown_from, "compute_date_range falls through"


def test_two_year_resolves_to_daily_bars():
    assert resolve_interval("2Y", None) == "daily"


def test_two_year_window_is_the_full_seven_hundred_and_thirty_days():
    """The source caps at 730, so the window is spent on the VISIBLE range.

    No MA(200) warm-up is added — 730 + 320 is unreachable, and taking the warm-up out
    of the visible window would render ~14 months under a "2Y" label instead.
    """
    assert daily_range_days("2Y") == 730
    # 1Y by contrast DOES carry the warm-up, which is what makes it larger than its name.
    assert daily_range_days("1Y") > 365


# ── 3. Intraday routing is by RANGE, never by resolved interval ──────────────

def test_only_one_day_and_one_week_are_intraday():
    """The exact bug: an interval test sweeps 5Y and ALL in with them."""
    assert set(_INTRADAY_RANGES) == {"1D", "1W"}
    for code in ("5Y", "ALL", "2Y", "1Y", "3M", "6M"):
        assert code not in _INTRADAY_RANGES, f"{code} must not be served intraday"


def test_the_ranges_an_interval_test_would_have_misrouted_are_not_intraday():
    """Pins the trap directly: these two resolve NON-daily but are long-horizon.

    If this ever fails, `DEFAULT_INTERVALS` changed and the comment explaining why
    `_INTRADAY_RANGES` exists needs rereading before anything is 'simplified'.
    """
    for code in ("5Y", "ALL"):
        assert resolve_interval(code, None) != "daily", (
            f"{code} no longer resolves non-daily — the misrouting trap has changed shape"
        )
        assert code not in _INTRADAY_RANGES


def test_over_cap_ranges_are_exactly_the_ones_beyond_two_years():
    assert _OVER_CAP_RANGES == {"5Y", "ALL"}
    for code in CRYPTO_RANGES:
        assert code not in _OVER_CAP_RANGES, f"{code} is inside the cap"


@pytest.mark.parametrize("code", CRYPTO_RANGES)
def test_every_offered_crypto_range_has_a_real_window(code):
    """Anti-vacuity: each pill the crypto screen shows maps to a genuine window."""
    assert daily_range_days(code) > 0
    assert compute_date_range(code)[0] is not None
    assert resolve_interval(code, None) in ALLOWED_INTERVALS.get(code, {"daily"})
