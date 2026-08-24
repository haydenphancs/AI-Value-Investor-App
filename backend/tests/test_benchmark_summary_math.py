"""Math for the Performance card's "Average Annual Return vs benchmark" block.

WHY THIS EXISTS. A TestFlight tester reported the block as "hard to read or compare".
Chasing that found the reason was not only layout: on two of the four screens that render
it, the two numbers a reader is invited to compare were not comparable.

Every figure below was checked against the live FMP API on 2026-08-23 before being
written down; the docstrings carry the measurement so a future reader can tell a real
regression from a market that moved.

  * FMP caps a daily series at 5,000 rows, which puts EVERY still-trading symbol's
    full-history fetch at 2006-10-05 — SPY, QQQ and AAPL alike. So a stock card reading
    "S&P 500 9.1% · Since Dec 31, 1981" was showing SPY's 2006→2026 CAGR (9.0966%) under
    a label twenty-five years older.
  * ETF: ARKK read 13.0% vs 9.1% (ARKK from 2014-10-31, the S&P from 2006-10-05). Over
    ARKK's own window the S&P returned 12.0%, so the card tripled ARKK's apparent edge.
  * Commodity: gold read 31.8%/yr — a 612% total return divided by 19.2 years, an
    ARITHMETIC mean published as an annual return — against a hardcoded `sp_benchmark`
    of 10.5. The compound rate is 10.7%/yr and the S&P returned 8.8% over that window.

No network and no Supabase: every test builds its own rows and runs the production
builders on `__init__`-bypassed instances.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest
from fastapi.encoders import jsonable_encoder

from app.services.benchmark_math import (
    MIN_MEASURABLE_YEARS,
    cagr_between,
    format_since,
    overlapping_cagrs,
)
from app.services.commodity_service import CommodityService
from app.services.etf_service import ETFService
from app.services.stock_overview_service import StockOverviewService


# ── helpers ──────────────────────────────────────────────────────────────────

def _series(start_date: date, end_date: date, n: int, first: float, last: float):
    """`n` rows spread evenly from `start_date` to `end_date`, prices `first` -> `last`.

    ⚠️ TAKE THE END DATE, DO NOT DERIVE IT FROM `n`. FMP rows are TRADING days, so a
    5,000-row series spans ~19.9 years, not 5,000 calendar days (~13.7). An earlier draft
    of this helper stepped one calendar day per row and every figure below missed its
    measured target by a third — gold came out at 16.1%/yr instead of 10.7%.

    Prices are geometric, so the CAGR of the whole series equals the CAGR of its
    endpoints. That also means every sub-window has the SAME CAGR, which is exactly what
    a shared-window test must not rely on — use `_two_regime` for a benchmark.
    """
    if n < 2:
        return [{"date": start_date.isoformat(), "close": first}]
    span = (end_date - start_date).days
    ratio = (last / first) ** (1 / (n - 1))
    return [
        {"date": (start_date + timedelta(days=round(span * i / (n - 1)))).isoformat(),
         "close": first * (ratio ** i)}
        for i in range(n)
    ]


def _two_regime(start_date: date, end_date: date, n: int,
                first: float, mid: float, last: float):
    """A series whose growth rate CHANGES halfway.

    A benchmark built with `_series` returns the same CAGR over every sub-window, which
    silently makes "was this scored over the shared window?" untestable — the wrong
    answer and the right one are the same number. Real markets have regimes; so does this.
    """
    half = n // 2
    mid_date = start_date + timedelta(days=(end_date - start_date).days // 2)
    head = _series(start_date, mid_date, half, first, mid)
    tail = _series(mid_date, end_date, n - half + 1, mid, last)[1:]
    return head + tail


def _renders(model) -> bool:
    """True iff the model survives FastAPI's `allow_nan=False` renderer."""
    json.dumps(jsonable_encoder(model), allow_nan=False)
    return True


# ── cagr_between ─────────────────────────────────────────────────────────────

def test_cagr_between_reproduces_the_measured_sp500_figure():
    # SPY's real closes, first and last row of the capped full-history fetch.
    assert cagr_between(135.18, 765.72, "2006-10-05", "2026-08-21") == 9.1


def test_cagr_between_is_compound_not_arithmetic():
    # Doubling over exactly 10 years is 7.2%/yr compound. The arithmetic mean of the
    # same move is 10.0%/yr — the number the commodity screen was publishing.
    out = cagr_between(100.0, 200.0, "2010-01-01", "2019-12-31")
    assert out == pytest.approx(7.2, abs=0.1)
    assert out != pytest.approx(10.0, abs=0.5)


def test_cagr_between_handles_a_loss():
    assert cagr_between(200.0, 100.0, "2016-01-01", "2026-01-01") == pytest.approx(-6.7, abs=0.1)


@pytest.mark.parametrize("start,end", [
    (float("nan"), 100.0),
    (100.0, float("nan")),
    (float("inf"), 100.0),
    (100.0, float("-inf")),
])
def test_cagr_between_rejects_non_finite_prices(start, end):
    """A bare NaN/Infinity FMP token is TRUTHY and slips past `not x` / `x <= 0`. Landing
    one in the REQUIRED `avg_annual_return` float 500s the whole detail response under
    Starlette's `allow_nan=False`."""
    assert cagr_between(start, end, "2010-01-01", "2020-01-01") is None


@pytest.mark.parametrize("start,end", [(0.0, 100.0), (-5.0, 100.0), (100.0, 0.0), (100.0, -5.0)])
def test_cagr_between_rejects_non_positive_prices(start, end):
    assert cagr_between(start, end, "2010-01-01", "2020-01-01") is None


@pytest.mark.parametrize("sd,ed", [("", "2020-01-01"), ("2010-01-01", None), ("nope", "2020-01-01")])
def test_cagr_between_rejects_unparseable_dates(sd, ed):
    assert cagr_between(100.0, 200.0, sd, ed) is None


def test_cagr_between_refuses_a_span_too_short_to_annualise():
    """Annualising a three-day move raises noise to the ~120th power. The old crypto
    helper answered a TOTAL return here, into a field named `avg_annual_return`."""
    assert cagr_between(100.0, 200.0, "2026-01-01", "2026-01-04") is None
    # ...and the floor is the documented constant, not a magic number in the branch.
    just_under = date(2026, 1, 1) + timedelta(days=int(MIN_MEASURABLE_YEARS * 365.25) - 5)
    just_over = date(2026, 1, 1) + timedelta(days=int(MIN_MEASURABLE_YEARS * 365.25) + 5)
    assert cagr_between(100.0, 110.0, "2026-01-01", just_under.isoformat()) is None
    assert cagr_between(100.0, 110.0, "2026-01-01", just_over.isoformat()) is not None


def test_cagr_between_never_returns_zero_as_a_failure_signal():
    """`0.0` used to be the failure value in three services, which is indistinguishable
    from a flat market — and rendered as "S&P 500 Benchmark 0.0%" with a verdict badge."""
    assert cagr_between(None, None, None, None) is None


# ── overlapping_cagrs: the shared-window rule ────────────────────────────────

def test_both_sides_are_measured_over_the_window_they_share():
    """The ARKK case. Asset starts 2014, benchmark 2006; the benchmark must be scored
    over the ASSET's window, not its own."""
    asset = _series(date(2014, 10, 31), date(2026, 8, 21), 2968, 20.38, 86.21)
    # Two regimes, so the benchmark's CAGR over the asset's window genuinely differs
    # from its own full-history CAGR — otherwise this test cannot fail.
    bench_full = _two_regime(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 200.0, 765.72)

    a, b, since = overlapping_cagrs(asset, bench_full)

    assert since == "2014-10-31"
    # The benchmark's own full-history CAGR is a DIFFERENT number; using it here is the bug.
    own_window = cagr_between(135.18, 765.72, "2006-10-05", bench_full[-1]["date"])
    assert b != own_window
    # Cross-check by hand: the benchmark's close on the asset's start date, to its last.
    start_row = next(r for r in bench_full if r["date"] >= "2014-10-31")
    assert b == cagr_between(start_row["close"], bench_full[-1]["close"],
                             start_row["date"], bench_full[-1]["date"])
    assert a == pytest.approx(cagr_between(20.38, 86.21, "2014-10-31", asset[-1]["date"]), abs=0.1)


def test_a_benchmark_identical_to_the_asset_scores_identically():
    """SPY's own screen: the ETF service passes the same series for both sides. If these
    two ever diverge, the alignment is wrong somewhere."""
    rows = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 765.72)
    a, b, since = overlapping_cagrs(rows, rows)
    assert a == b
    assert since == "2006-10-05"


def test_window_start_is_the_later_of_the_two_starts():
    asset = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 100.0, 900.0)
    bench = _series(date(2014, 1, 2), date(2026, 8, 21), 3000, 50.0, 200.0)
    _, _, since = overlapping_cagrs(asset, bench)
    assert since == "2014-01-02"


def test_an_anchor_with_a_gap_falls_through_to_the_dense_ranges():
    """THE REGRESSION THIS SCAN EXISTS FOR. An anchored series is one old point, a gap of
    decades, then the capped daily range. Taking `max(asset_start, benchmark_start)` asks
    for a date NEITHER side can serve: AAPL resolved to 2006 while a 1993-anchored SPY
    resolved to 1993, thirteen years apart, and the pair was published as one window."""
    asset = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 2.67, 309.35)
    bench = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 765.72)
    ipo = {"price": 0.12835, "date": "1980-12-12"}
    spy_1993 = {"price": 43.94, "date": "1993-01-29"}

    a, b, since = overlapping_cagrs(asset, bench, asset_anchor=ipo, benchmark_anchor=spy_1993)

    assert since == "2006-10-05", "the shared window must fall through to the dense ranges"
    assert b is not None, "the benchmark must not be dropped just because an anchor exists"
    # And it lands on exactly the answer you get with no anchors at all.
    assert (a, b, since) == overlapping_cagrs(asset, bench)


def test_two_anchors_that_do_line_up_are_used():
    """The anchor path is not dead code — when both sides can be extended to the same
    date, the longer window is what gets measured."""
    asset = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 100.0, 900.0)
    bench = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 100.0, 400.0)
    a_anchor = {"price": 10.0, "date": "1990-01-02"}
    b_anchor = {"price": 20.0, "date": "1990-01-03"}

    _, _, since = overlapping_cagrs(asset, bench,
                                    asset_anchor=a_anchor, benchmark_anchor=b_anchor)
    assert since == "1990-01-02"


def test_no_benchmark_series_reports_the_asset_alone():
    asset = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 400.0)
    a, b, since = overlapping_cagrs(asset, [])
    assert a is not None and b is None
    assert since == "2010-01-01"


def test_a_benchmark_that_never_overlaps_is_reported_unavailable_not_mislabelled():
    """A delisted asset whose series ends before the benchmark's begins. The wrong answer
    is to publish the benchmark's own window under the asset's date."""
    asset = _series(date(1995, 1, 2), date(2000, 1, 2), 1200, 10.0, 30.0)
    bench = _series(date(2020, 1, 2), date(2025, 1, 2), 1200, 100.0, 200.0)
    a, b, since = overlapping_cagrs(asset, bench)
    assert b is None
    assert since == "1995-01-02"


def test_non_finite_bars_are_skipped_not_fatal():
    asset = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 400.0)
    asset[0]["close"] = float("nan")
    asset[-1]["close"] = float("inf")
    bench = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 300.0)
    a, b, since = overlapping_cagrs(asset, bench)
    assert a is not None and math.isfinite(a)
    assert since == asset[1]["date"], "the window starts at the first USABLE bar"


def test_unsorted_rows_are_handled():
    """`overlapping_cagrs` must not assume oldest-first: several callers sort, one does
    not, and a wrong endpoint silently inverts the sign of the return."""
    rows = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 400.0)
    bench = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 200.0)
    shuffled = list(reversed(rows))
    assert overlapping_cagrs(shuffled, bench) == overlapping_cagrs(rows, bench)


def test_a_duplicated_final_date_prices_off_the_last_row():
    """Ties on a date resolve by POSITION, reproducing `rows[-1]` on an oldest-first
    list. With the earlier of a tied pair winning, the window silently ends a bar short."""
    rows = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 400.0)
    rows.append({"date": rows[-1]["date"], "close": 800.0})
    bench = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 200.0)
    a, _, _ = overlapping_cagrs(rows, bench)
    assert a == cagr_between(100.0, 800.0, rows[0]["date"], rows[-1]["date"])


def test_asset_end_price_overrides_the_last_close():
    """Commodity ends its own side on the LIVE quote."""
    rows = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 400.0)
    bench = _series(date(2010, 1, 1), date(2022, 1, 1), 3000, 100.0, 200.0)
    base, _, _ = overlapping_cagrs(rows, bench)
    lifted, _, _ = overlapping_cagrs(rows, bench, asset_end_price=800.0)
    assert lifted > base


def test_format_since_styles():
    assert format_since("2021-08-23") == "Aug 2021"
    assert format_since("1981-12-31", style="day") == "Dec 31, 1981"
    assert format_since(None) is None
    assert format_since("not-a-date") is None


# ── ETF builder ──────────────────────────────────────────────────────────────

def _etf():
    return ETFService.__new__(ETFService)


def test_etf_benchmark_is_scored_over_the_etf_window():
    """ARKK, to the numbers measured live: 13.0 vs 9.1 was the shipped answer, 13.0 vs
    12.0 is the true one. The S&P figure must NOT be its own full-history CAGR."""
    arkk = _series(date(2014, 10, 31), date(2026, 8, 21), 2968, 20.38, 86.21)
    spy = _two_regime(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 200.0, 765.72)

    out = _etf()._build_benchmark_summary(arkk, spy, symbol="ARKK")

    assert out is not None
    spy_own_window = cagr_between(135.18, 765.72, "2006-10-05", spy[-1]["date"])
    assert out.sp_benchmark != spy_own_window
    assert out.benchmark_available is True
    assert out.window_label == "All-time"
    assert out.since_date is not None and "2014" in out.since_date
    assert _renders(out)


def test_etf_benchmark_against_itself_is_a_dead_heat():
    """SPY's own ETF screen. The service passes one series for both sides."""
    spy = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 765.72)
    out = _etf()._build_benchmark_summary(spy, spy, symbol="SPY")
    assert out.avg_annual_return == out.sp_benchmark


def test_etf_benchmark_needs_a_year_of_data():
    short = _series(date(2025, 1, 1), date(2025, 6, 1), 100, 10, 12)
    assert _etf()._build_benchmark_summary(short, []) is None


def test_etf_missing_spy_history_is_declared_not_faked():
    etf = _series(date(2014, 10, 31), date(2026, 8, 21), 2968, 20.38, 86.21)
    out = _etf()._build_benchmark_summary(etf, [], symbol="ARKK")
    assert out is not None
    assert out.benchmark_available is False
    assert out.sp_benchmark == 0.0, "the wire field stays a float; the FLAG carries the truth"
    assert _renders(out)


def test_etf_summary_no_longer_carries_a_duplicate_since_date():
    """`benchmark_since_date` was either equal to `since_date` or None at every call
    site, and printing it produced the two identical "Since Aug 2021" columns the tester
    was looking at."""
    spy = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 765.72)
    out = _etf()._build_benchmark_summary(spy, spy, symbol="SPY")
    assert not hasattr(out, "benchmark_since_date")


# ── Stock builder ────────────────────────────────────────────────────────────

def _stock():
    return StockOverviewService.__new__(StockOverviewService)


def _recent(years: float, n: int, first: float, last: float):
    """`n` rows spanning `years` back from today, so the 5-year cutoff is exercised for real."""
    today = date.today()
    return _series(today - timedelta(days=round(years * 365.25)), today, n, first, last)


def test_stock_primary_row_is_the_five_year_window_and_says_so():
    stock = _recent(12, 3000, 50.0, 300.0)
    spy = _recent(12, 3000, 300.0, 500.0)
    out = _stock()._build_benchmark_summary(stock, spy, ticker="TEST")
    assert out.window_label == "5-year"
    assert out.benchmark_available is True
    assert out.since_date is not None
    assert _renders(out)


def test_stock_with_under_five_years_labels_the_row_all_time():
    # 290 rows over 14 months: ENOUGH ROWS to take the >=252 five-year branch while
    # covering nothing like five years. This is the case that used to print "5-year"
    # directly above a since-date fourteen months old.
    stock = _recent(1.17, 290, 50.0, 90.0)
    spy = _recent(1.17, 290, 300.0, 340.0)
    out = _stock()._build_benchmark_summary(stock, spy, ticker="YOUNG")
    assert out.window_label == "All-time"
    # ...and does not then repeat itself in the secondary row.
    assert out.alltime_annual_return is None
    assert out.alltime_since_date is None


def test_stock_all_time_row_only_appears_when_it_covers_a_different_window():
    stock = _recent(16, 4000, 10.0, 300.0)
    spy = _recent(16, 4000, 200.0, 700.0)
    out = _stock()._build_benchmark_summary(stock, spy, ticker="OLD")
    assert out.window_label == "5-year"
    assert out.alltime_annual_return is not None
    assert out.alltime_since_date is not None
    assert out.alltime_since_date != out.since_date


def test_stock_missing_spy_history_is_declared_not_zero():
    """The old code coalesced an uncomputable S&P CAGR to 0.0, so an upstream failure
    rendered "S&P 500 Benchmark 0.0%" plus an "Outperforming" badge."""
    out = _stock()._build_benchmark_summary(_recent(12, 3000, 50.0, 300.0), [], ticker="TEST")
    assert out.benchmark_available is False
    assert out.sp_benchmark == 0.0
    assert _renders(out)


def test_stock_summary_is_omitted_when_the_stock_itself_is_unmeasurable():
    """Nothing to compare — better no card than a flat 0.0%."""
    assert _stock()._build_benchmark_summary([], _recent(12, 3000, 300.0, 500.0)) is None
    assert _stock()._build_benchmark_summary(
        _recent(0.4, 100, 1, 2), _recent(12, 3000, 300, 500)
    ) is None


# ── Commodity builder ────────────────────────────────────────────────────────

def _commodity():
    return CommodityService.__new__(CommodityService)


def test_commodity_baseline_is_a_shared_window_and_a_measured_benchmark():
    """Gold, to the live numbers: 657.20 on 2007-05-29 -> 4680.60 on 2026-08-21."""
    gold = _series(date(2007, 5, 29), date(2026, 8, 21), 4800, 657.2, 4680.6)
    spy = _two_regime(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 200.0, 765.72)

    derived = _commodity()._derive_from_history(gold, spy)

    assert derived["bench_since"] == "2007-05-29", "gold starts after SPY, so gold's start wins"
    assert derived["bench_base"]["date"] == "2007-05-29"
    assert derived["bench_sp_cagr"] is not None
    # Measured over GOLD's window, so NOT the S&P's own full-history figure...
    assert derived["bench_sp_cagr"] != cagr_between(135.18, 765.72, "2006-10-05", spy[-1]["date"])
    # ...and emphatically not the hardcoded literal it replaced.
    assert derived["bench_sp_cagr"] != 10.5


def test_commodity_return_is_compound_not_an_arithmetic_mean():
    """The shipped screen read 31.8%/yr for gold: a 612% total return over 19.2 years,
    divided. The compound rate is 10.7%/yr — a three-fold overstatement."""
    gold = _series(date(2007, 5, 29), date(2026, 8, 21), 4800, 657.2, 4680.6)
    spy = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 765.72)
    derived = _commodity()._derive_from_history(gold, spy)

    base = derived["bench_base"]
    end_date = gold[-1]["date"]
    compound = cagr_between(base["close"], 4680.6, base["date"], end_date)

    years = (date.fromisoformat(end_date) - date.fromisoformat(base["date"])).days / 365.25
    arithmetic = ((4680.6 - base["close"]) / base["close"] * 100) / years

    assert compound == pytest.approx(10.7, abs=0.3)
    assert arithmetic == pytest.approx(31.8, abs=1.0)
    assert compound < arithmetic / 2


def test_commodity_year_clamp_no_longer_understates_a_young_series():
    """`years = max(1, ...)` divided a sub-year move by a full year. The >=252-row gate
    is the real guard, and `cagr_between`'s floor backs it up."""
    short = _series(date.today() - timedelta(days=200), date.today(), 200, 100.0, 150.0)
    derived = _commodity()._derive_from_history(short, [])
    assert derived["bench_base"] is None, "under a year of bars yields no baseline at all"


def test_commodity_nan_live_price_yields_no_benchmark_rather_than_a_500():
    """`if _bench_base and price` let a NaN through — NaN is truthy — and it survived
    `round()` into the REQUIRED float, where `allow_nan=False` 500s the whole screen.
    The surrounding `except Exception: pass` could not catch it: nothing raised."""
    gold = _series(date(2007, 5, 29), date(2026, 8, 21), 4800, 657.2, 4680.6)
    derived = _commodity()._derive_from_history(
        gold, _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.0, 765.0)
    )
    base = derived["bench_base"]
    for bad in (float("nan"), float("inf"), 0.0, -1.0, None):
        assert cagr_between(base["close"], bad, base["date"], "2026-08-21") is None


def test_commodity_without_spy_history_declares_the_benchmark_missing():
    gold = _series(date(2007, 5, 29), date(2026, 8, 21), 4800, 657.2, 4680.6)
    derived = _commodity()._derive_from_history(gold, [])
    assert derived["bench_sp_cagr"] is None
    # The commodity's own baseline still stands — only the comparison is withheld.
    assert derived["bench_base"] is not None
    assert derived["bench_since"] == "2007-05-29"


def test_commodity_derived_persists_no_live_price():
    """Tier 2 holds this for 12h. A price in here would serve a stale header."""
    gold = _series(date(2007, 5, 29), date(2026, 8, 21), 4800, 657.2, 4680.6)
    derived = _commodity()._derive_from_history(
        gold, _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.0, 765.0)
    )
    assert set(derived["bench_base"]) == {"close", "date"}
    assert derived["bench_base"]["date"] == derived["bench_since"]
    assert isinstance(derived["bench_sp_cagr"], float)
