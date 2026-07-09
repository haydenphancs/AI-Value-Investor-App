"""
Whale-service pure-transform hardening — annualized return (CAGR) outlier
filtering and sector-allocation clamping.

Two defects this pins:

1. _compute_avg_annual_return compounds year-end 1-year returns into a CAGR. A
   yearly return <= -100% is impossible for a long-only 13F portfolio. Admitting
   sub-(-100%) values let an EVEN count of them multiply to a spuriously POSITIVE
   product that slipped past the `product <= 0` guard and surfaced a fabricated
   CAGR (e.g. two -150% years -> a bogus -50%) on the whale profile.

2. _build_sectors_from_industry sums raw FMP industry weights per GICS sector.
   whale_sector_allocations.allocation has a DB CHECK (0..100); an aggregate
   above 100 (rounding/overlap on a concentrated fund) would raise a CHECK
   violation mid-sync and wipe the sector rows. Values must be clamped to 100.

Pure math — no network, no Supabase. Run via `python -m pytest` from backend/.
"""

from app.services.whale_service import WhaleService


def _ye(date: str, pct: float) -> dict:
    return {"date": date, "performancePercentage1year": pct}


# ── CAGR outlier filter ──────────────────────────────────────────────


def test_even_count_of_sub_negative_100_returns_is_rejected():
    # Two -150% year-ends would multiply to (+0.25) and yield a bogus -50% CAGR
    # that slips past the product<=0 guard. Must be rejected -> None.
    perf = [_ye("2023-12-31", -150.0), _ye("2024-12-31", -150.0)]
    assert WhaleService._compute_avg_annual_return(perf) is None


def test_single_sub_negative_100_fallback_is_rejected():
    # No -12-31 record -> fallback path; it must also reject an impossible value.
    perf = [{"date": "2024-06-30", "performancePercentage1year": -150.0}]
    assert WhaleService._compute_avg_annual_return(perf) is None


def test_valid_year_end_returns_compound_to_cagr():
    perf = [_ye("2023-12-31", 21.0), _ye("2024-12-31", 21.0)]
    result = WhaleService._compute_avg_annual_return(perf)
    assert result is not None
    assert abs(result - 21.0) < 0.1


def test_near_total_loss_above_floor_is_allowed():
    # -99% is catastrophic but POSSIBLE — must NOT be over-filtered by the floor.
    perf = [_ye("2023-12-31", -99.0), _ye("2024-12-31", -99.0)]
    result = WhaleService._compute_avg_annual_return(perf)
    assert result is not None
    assert result < -90.0


def test_extreme_positive_outlier_is_rejected():
    # >= 500% for a year is treated as corrupt data.
    perf = [_ye("2023-12-31", 900.0)]
    assert WhaleService._compute_avg_annual_return(perf) is None


def test_empty_perf_list_returns_none():
    assert WhaleService._compute_avg_annual_return([]) is None


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
