"""
Whale-service pure-transform hardening — annualized return (CAGR) outlier
filtering and sector-allocation clamping.

Two defects this pins:

1. The CAGR compounds year-end 1-year returns. A yearly return <= -100% is
   impossible for a long-only 13F portfolio. Admitting sub-(-100%) values let an
   EVEN count of them multiply to a spuriously POSITIVE product that slipped
   past the `product <= 0` guard and surfaced a fabricated CAGR (e.g. two -150%
   years -> a bogus -50%) on the whale profile.

   These now target `_whale_common.compute_13f_cagr` DIRECTLY rather than
   `WhaleService._compute_avg_annual_return`. That method is a one-line delegate
   now, and the formula it used to own had a second, DIVERGENT copy in
   `scripts/hydrate_whales.py` with a -200 floor — so tests aimed only at the
   service were green while the code that actually runs in production used the
   broken floor. Testing the shared helper is what makes them cover both.

   The single-1-year fallback these used to exercise is GONE: a lone 1-year
   figure is now `insufficient_history`, never a number captioned "CAGR".

2. _build_sectors_from_industry sums raw FMP industry weights per GICS sector.
   whale_sector_allocations.allocation has a DB CHECK (0..100); an aggregate
   above 100 (rounding/overlap on a concentrated fund) would raise a CHECK
   violation mid-sync and wipe the sector rows. Values must be clamped to 100.

Pure math — no network, no Supabase. Run via `python -m pytest` from backend/.
"""

from app.services._whale_common import (
    RETURN_INSUFFICIENT,
    RETURN_OK,
    RETURN_UNAVAILABLE,
    compute_13f_cagr,
)
from app.services.whale_service import WhaleService


def _ye(date: str, pct: float) -> dict:
    return {"date": date, "performancePercentage1year": pct}


# ── CAGR outlier filter ──────────────────────────────────────────────


def test_even_count_of_sub_negative_100_returns_is_rejected():
    # Two -150% year-ends would multiply to (+0.25) and yield a bogus -50% CAGR
    # that slips past the product<=0 guard. Both must be filtered out, leaving
    # too few years to annualize.
    perf = [_ye("2023-12-31", -150.0), _ye("2024-12-31", -150.0)]
    result = compute_13f_cagr(perf)
    assert result.value is None
    assert result.status == RETURN_INSUFFICIENT


def test_a_lone_one_year_figure_is_never_reported_as_a_cagr():
    """The defect this replaces: with no year-end rows the old code returned the
    latest 1-year return AND still captioned it "13F Portfolio CAGR" — a false
    statement about the data. It must now decline to produce a number."""
    perf = [{"date": "2024-06-30", "performancePercentage1year": 42.0}]
    result = compute_13f_cagr(perf)
    assert result.value is None, "a 1-year return must not surface as a CAGR"
    assert result.status == RETURN_INSUFFICIENT


def test_one_year_end_row_is_insufficient():
    result = compute_13f_cagr([_ye("2024-12-31", 12.0)])
    assert result.value is None
    assert result.status == RETURN_INSUFFICIENT


def test_valid_year_end_returns_compound_to_cagr():
    perf = [_ye("2023-12-31", 21.0), _ye("2024-12-31", 21.0)]
    result = compute_13f_cagr(perf)
    assert result.status == RETURN_OK
    assert abs(result.value - 21.0) < 0.1


def test_the_window_travels_with_the_number():
    """A 2-year and a 10-year figure used to render identically because the
    window was computed and then discarded."""
    perf = [_ye(f"20{y}-12-31", 10.0) for y in range(20, 25)]
    result = compute_13f_cagr(perf)
    assert result.status == RETURN_OK
    assert result.window_years == 5


def test_duplicate_year_end_rows_do_not_inflate_the_exponent():
    """FMP can return two rows for one year-end. Counting both would compound it
    twice AND change the denominator, silently altering the answer."""
    perf = [_ye("2024-12-31", 10.0), _ye("2024-12-31", 10.0), _ye("2025-12-31", 20.0)]
    result = compute_13f_cagr(perf)
    assert result.window_years == 2


def test_near_total_loss_above_floor_is_allowed():
    # -99% is catastrophic but POSSIBLE — must NOT be over-filtered by the floor.
    perf = [_ye("2023-12-31", -99.0), _ye("2024-12-31", -99.0)]
    result = compute_13f_cagr(perf)
    assert result.status == RETURN_OK
    assert result.value < -90.0


def test_extreme_positive_outlier_is_rejected():
    # >= 500% for a year is treated as corrupt data.
    perf = [_ye("2023-12-31", 900.0), _ye("2024-12-31", 10.0)]
    result = compute_13f_cagr(perf)
    assert result.status == RETURN_INSUFFICIENT


def test_no_data_is_unavailable_not_insufficient():
    """Load-bearing distinction: only `insufficient_history` is a judgement about
    the whale and may CLEAR a stored value. `unavailable` means we could not read
    FMP, and an outage must never blank good data."""
    assert compute_13f_cagr([]).status == RETURN_UNAVAILABLE
    assert compute_13f_cagr(None).status == RETURN_UNAVAILABLE
    assert compute_13f_cagr([_ye("2024-12-31", 5.0)]).status == RETURN_INSUFFICIENT


def test_the_service_delegate_returns_the_same_answer():
    """`WhaleService._compute_avg_annual_return` must stay a delegate — owning a
    second copy of the formula is how it drifted from hydrate_whales."""
    perf = [_ye("2023-12-31", 21.0), _ye("2024-12-31", 21.0)]
    assert WhaleService._compute_avg_annual_return(perf) == compute_13f_cagr(perf)


# ── Sector allocation clamp ──────────────────────────────────────────


def test_sector_allocation_clamped_to_100():
    svc = WhaleService.__new__(WhaleService)
    # Two Technology industries whose weights aggregate to 101.5 (> 100).
    industry = [
        {"industryTitle": "ELECTRONIC COMPUTERS", "weight": 60.3},
        {"industryTitle": "SEMICONDUCTORS & RELATED DEVICES", "weight": 41.2},
    ]
    sectors = svc._build_sectors_from_industry(industry)
    tech = next(s for s in sectors if s["name"] == "Technology")
    assert tech["allocation"] == 100.0  # 101.5 clamped to the DB CHECK ceiling
    assert all(0.0 <= s["allocation"] <= 100.0 for s in sectors)


def test_sector_build_empty_input_returns_empty():
    svc = WhaleService.__new__(WhaleService)
    assert svc._build_sectors_from_industry([]) == []
