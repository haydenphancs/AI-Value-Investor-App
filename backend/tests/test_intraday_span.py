"""`chart_helper.intraday_span` — the sparkline's only time axis.

WHY THIS EXISTS

A sparkline ships as a bare `List[float]`. iOS used to spread those N points
across the FULL tile width, so at 10:15 ET a 34-bar morning drew edge-to-edge
and looked exactly like a completed 78-bar session — the card said "the day is
done" beside a live, moving price, and it contradicted the asset-detail 1D
chart, which has always left the untraded remainder of the day empty.

`intraday_span` is the missing axis: `(from, to)` fractions of the asset's own
session that iOS multiplies by the card width.

THE LOAD-BEARING PROPERTY IS THE FALLBACK
-----------------------------------------
Every input this function cannot place returns `FULL_SPAN` — the exact
pre-span behaviour. A wrong-but-narrow span would SHRINK a chart that used to
render fine, which is a worse regression than the bug being fixed. Most of the
cases below assert that fallback, not the happy path.

Pure math: no network, no Supabase, no clock (every instant is passed in).
"""

from __future__ import annotations

import pytest

from app.services.chart_helper import FULL_SPAN, intraday_span
from app.utils.market_hours import US_MARKET_EARLY_CLOSES

# A regular Thursday. Deliberately NOT a holiday or half-day.
DAY = "2026-08-13"
# Day after Thanksgiving 2026 — a 13:00 ET close, from the shared table.
HALF_DAY = "2026-11-27"

REGULAR_LENGTH = 390.0     # 09:30 → 16:00
HALF_DAY_LENGTH = 210.0    # 09:30 → 13:00
FULL_DAY_LENGTH = 1440.0   # 00:00 → 24:00


def bars(*times: str, day: str = DAY) -> list[dict]:
    """FMP intraday rows: `"YYYY-MM-DD HH:MM:SS"`, ET, oldest-first."""
    return [{"date": f"{day} {t}:00", "close": 100.0} for t in times]


# ── The reported bug: a partial equity session must not fill the tile ──


def test_mid_morning_equity_covers_only_the_elapsed_fraction():
    """The exact case the user reported, measured against live FMP data.

    At 12:15 ET on 2026-08-14, ORCL's 5-min series ran 09:30 → 12:15 (34 of a
    full session's 78 bars). The last bar COVERS 12:15–12:20, so the line should
    reach (12:20 − 09:30) / 390 = 0.4359 — a bit over two fifths of the card.
    """
    lo, hi = intraday_span(bars("09:30", "12:15"), extended_hours=False)
    assert lo == 0.0
    assert hi == pytest.approx(170 / REGULAR_LENGTH, abs=1e-4)
    assert 0.40 < hi < 0.45


def test_completed_equity_session_fills_the_whole_width():
    """After the close the card must look exactly as it always did.

    The final regular bar is stamped 15:55 and covers 15:55–16:00, so `+ interval`
    is what makes this land on 1.0 rather than 0.987 — a full session that stopped
    a hair short would read as a permanently unfinished day.
    """
    assert intraday_span(bars("09:30", "15:55"), extended_hours=False) == (0.0, 1.0)


def test_crypto_measures_against_the_whole_calendar_day():
    """A 24/7 asset's session is 00:00–24:00, not the equity bell.

    Verified against live FMP: BTCUSD's bars start at 00:00 ET while ^GSPC's start
    at 09:30. Measuring Bitcoin on the equity window would have it "finish the
    day" by lunchtime.
    """
    lo, hi = intraday_span(bars("00:00", "12:15"), extended_hours=True)
    assert lo == 0.0
    assert hi == pytest.approx(740 / FULL_DAY_LENGTH, abs=1e-4)
    # Same instant, different fraction than the equity above — that asymmetry is
    # the point of passing extended_hours through.
    equity_hi = intraday_span(bars("09:30", "12:15"), extended_hours=False)[1]
    assert hi > equity_hi


def test_the_same_bars_read_differently_under_each_window():
    """`extended_hours` MUST match the flag the bars were fetched with."""
    same = bars("09:30", "12:15")
    assert intraday_span(same, extended_hours=False) != intraday_span(
        same, extended_hours=True
    )


# ── Session-boundary edge cases ───────────────────────────────────────


def test_half_day_close_counts_as_a_complete_session():
    """A 12:55 bar on a 13:00-close day is the LAST one, not 60% of a day."""
    assert HALF_DAY in {
        f"{y:04d}-{m:02d}-{d:02d}" for (y, m, d) in US_MARKET_EARLY_CLOSES
    }, "test fixture drifted from the shared early-close table"
    assert intraday_span(bars("09:30", "12:55", day=HALF_DAY), extended_hours=False) == (
        0.0,
        1.0,
    )


def test_half_day_midpoint_scales_to_the_shorter_session():
    lo, hi = intraday_span(bars("09:30", "11:10", day=HALF_DAY), extended_hours=False)
    assert lo == 0.0
    assert hi == pytest.approx(105 / HALF_DAY_LENGTH, abs=1e-4)
    # The SAME bars on a normal day cover a much smaller slice of a longer session.
    assert hi > intraday_span(bars("09:30", "11:10"), extended_hours=False)[1]


def test_a_late_first_bar_shifts_the_series_right():
    """An illiquid name that doesn't print until 10:05 starts 9% in, not at 0.

    Anchoring it at x=0 would claim a trade at the opening bell that never
    happened — and it would disagree with the detail chart, which positions each
    point by its own timestamp.
    """
    lo, hi = intraday_span(bars("10:05", "12:15"), extended_hours=False)
    assert lo == pytest.approx(35 / REGULAR_LENGTH, abs=1e-4)
    assert lo > 0.0
    assert hi == pytest.approx(170 / REGULAR_LENGTH, abs=1e-4)


def test_first_and_last_bar_five_minutes_apart_is_a_thin_but_valid_span():
    lo, hi = intraday_span(bars("09:30", "09:35"), extended_hours=False)
    assert lo == 0.0
    assert 0.0 < hi < 0.05


def test_bars_past_the_close_are_clamped_not_overflowed():
    """Bad upstream data must never produce a fraction > 1 (drawn off the card)."""
    lo, hi = intraday_span(bars("09:30", "23:55"), extended_hours=False)
    assert (lo, hi) == (0.0, 1.0)


def test_bars_entirely_outside_the_session_fall_back_to_full_width():
    """Both ends clamp to 0 → `to <= from` → no honest position → FULL_SPAN.

    Reachable in pre-market: `_filter_regular_hours` is a time-of-day test, so a
    symbol whose real venue this window doesn't model can arrive all-outside.
    A zero-width line would be a blank card; full width is what it drew before.
    """
    assert intraday_span(bars("04:00", "05:00"), extended_hours=False) == FULL_SPAN


# ── Degenerate inputs — every one must fall back, never raise ─────────


@pytest.mark.parametrize(
    "value",
    [
        None,
        "garbage",
        123,
        [],
        [{"date": f"{DAY} 09:30:00"}],                    # single bar
        ["not-a-dict", "also-not"],
        [{"no_date": 1}, {"no_date": 2}],                 # missing key
        [{"date": None}, {"date": None}],                 # null dates
        [{"date": ""}, {"date": ""}],                     # blank
        [{"date": "nonsense"}, {"date": "also nonsense"}],
        [{"date": 20260813}, {"date": 20260814}],         # non-string
        [{"date": DAY}, {"date": DAY}],                   # daily bars: no time-of-day
        [{"date": f"{DAY} 25:99:00"}, {"date": f"{DAY} 26:00:00"}],  # impossible clock
        [{"date": "9999-99-99 10:00:00"}, {"date": "9999-99-99 11:00:00"}],
    ],
)
def test_every_degenerate_input_returns_full_span(value):
    assert intraday_span(value, extended_hours=False) == FULL_SPAN
    assert intraday_span(value, extended_hours=True) == FULL_SPAN


def test_a_non_dict_row_mixed_into_real_bars_does_not_raise():
    out = intraday_span(
        ["junk", {"date": f"{DAY} 09:30:00"}, {"date": f"{DAY} 12:15:00"}],
        extended_hours=False,
    )
    # The junk row is skipped and the two real bars still place the series.
    assert out[1] == pytest.approx(170 / REGULAR_LENGTH, abs=1e-4)


def test_zero_interval_still_produces_a_usable_span():
    """`interval_minutes=0` must not collapse a real series to zero width."""
    lo, hi = intraday_span(bars("09:30", "12:15"), extended_hours=False, interval_minutes=0)
    assert lo == 0.0
    assert hi == pytest.approx(165 / REGULAR_LENGTH, abs=1e-4)


def test_negative_interval_is_ignored_rather_than_shrinking_the_span():
    lo, hi = intraday_span(bars("09:30", "12:15"), extended_hours=False, interval_minutes=-30)
    assert hi == pytest.approx(165 / REGULAR_LENGTH, abs=1e-4)


def test_reversed_bars_fall_back_instead_of_drawing_backwards():
    """Callers pass oldest-first. Newest-first would invert the span, and iOS
    would compute a negative step and draw the line right-to-left."""
    assert intraday_span(bars("12:15", "09:30"), extended_hours=False) == FULL_SPAN


# ── Output contract ───────────────────────────────────────────────────


@pytest.mark.parametrize("ext", [False, True])
@pytest.mark.parametrize(
    "pair",
    [("09:30", "09:35"), ("09:30", "12:15"), ("00:00", "23:55"), ("10:05", "15:55")],
)
def test_output_is_always_an_ordered_pair_of_fractions(pair, ext):
    lo, hi = intraday_span(bars(*pair), extended_hours=ext)
    assert isinstance(lo, float) and isinstance(hi, float)
    assert 0.0 <= lo < hi <= 1.0
    # 4dp: sub-pixel on a ~68pt tile, and it keeps the JSON payload small.
    assert lo == round(lo, 4) and hi == round(hi, 4)
