"""Math tests for the new `fcf_margin` sector-benchmark computed metric.

fcf_margin = freeCashFlow / revenue, a JOIN across cashflow + income. Two things
make it different from the existing computed multiples (P/FCF, EV/EBITDA) and must
be pinned:
  1. It is stored as a DECIMAL (no ×100) so the consumer's ×100 matches the direct
     margins (gross/operating/net).
  2. NEGATIVE fcf margins (cash-burning companies) are KEPT in the sector median —
     a margin's sign is real, unlike the profitable-only multiples.
Pure / offline.
"""

import pytest

from app.services.sector_benchmark_service import _compute_ratio_values


def test_fcf_margin_is_decimal_and_keeps_negatives():
    # Two companies, FY2024: one healthy (12/100 = 0.12), one burning (-30/100 = -0.30).
    companies = [
        {
            "income_annual": [{"calendarYear": "2024", "revenue": 100.0}],
            "cashflow_annual": [{"calendarYear": "2024", "freeCashFlow": 12.0}],
        },
        {
            "income_annual": [{"calendarYear": "2024", "revenue": 100.0}],
            "cashflow_annual": [{"calendarYear": "2024", "freeCashFlow": -30.0}],
        },
    ]
    out = _compute_ratio_values(companies, "fcf_margin", "annual")
    assert "2024" in out
    vals = sorted(out["2024"])
    # Stored as a DECIMAL (0.12 / -0.30), NOT a percent (12 / -30).
    assert vals[0] == pytest.approx(-0.30)
    assert vals[1] == pytest.approx(0.12)
    # The cash-burning company's NEGATIVE margin is included (no >0 gate).
    assert any(v < 0 for v in vals)


def test_price_multiples_drop_negatives_and_zero():
    """Price multiples (P/E·P/B·P/S) + interest coverage are positive-only: a
    loss-maker's negative ratio and a 0 are excluded from the median — matching the
    company-side 'no negative P/E/P/B' convention + external providers."""
    from app.services.sector_benchmark_service import (
        SectorBenchmarkService, METRIC_CONFIGS,
    )

    for name, field, cap in [
        ("pe_ratio", "priceToEarningsRatio", 200.0),
        ("pb_ratio", "priceToBookRatio", 200.0),
        ("ps_ratio", "priceToSalesRatio", 200.0),
        ("interest_coverage", "interestCoverageRatio", 100.0),
    ]:
        cfg = next(mc for mc in METRIC_CONFIGS if mc["name"] == name)
        assert cfg.get("positive_only") is True, name
        assert cfg.get("cap") == cap, name

    pe_cfg = next(mc for mc in METRIC_CONFIGS if mc["name"] == "pe_ratio")
    companies = [
        {"ratios_annual": [{"calendarYear": "2024", "priceToEarningsRatio": 25.0}]},
        {"ratios_annual": [{"calendarYear": "2024", "priceToEarningsRatio": -10.0}]},  # loss-maker → drop
        {"ratios_annual": [{"calendarYear": "2024", "priceToEarningsRatio": 0.0}]},     # undefined → drop
        {"ratios_annual": [{"calendarYear": "2024", "priceToEarningsRatio": 35.0}]},
    ]
    svc = SectorBenchmarkService()
    vals = svc._collect_metric_values(companies, pe_cfg, "annual")
    assert sorted(vals.get("2024", [])) == [25.0, 35.0]


def test_winsorize_for_caps_positive_only_multiples():
    """The per-metric cap clamps the tail (near-zero-denominator artifacts) for
    positive-only multiples, while non-capped direct metrics keep their sign."""
    from app.services.industry_benchmark_service import _winsorize_for

    assert _winsorize_for("pe_ratio", "direct", [15.0, 30.0, 5000.0]) == [15.0, 30.0, 200.0]
    assert _winsorize_for("interest_coverage", "direct", [12.0, 800.0]) == [12.0, 100.0]
    # net_margin is NOT capped/positive-only — negatives are real and kept.
    assert _winsorize_for("net_margin", "direct", [-0.10, 0.20]) == [-0.10, 0.20]


def test_fcf_margin_skips_nonpositive_revenue_and_missing_fcf():
    companies = [
        {  # revenue 0 → undefined margin, skipped
            "income_annual": [{"calendarYear": "2024", "revenue": 0.0}],
            "cashflow_annual": [{"calendarYear": "2024", "freeCashFlow": 12.0}],
        },
        {  # freeCashFlow missing → skipped
            "income_annual": [{"calendarYear": "2023", "revenue": 100.0}],
            "cashflow_annual": [{"calendarYear": "2023", "freeCashFlow": None}],
        },
        {  # income year without a matching cashflow year → no join, skipped
            "income_annual": [{"calendarYear": "2022", "revenue": 100.0}],
            "cashflow_annual": [{"calendarYear": "2021", "freeCashFlow": 10.0}],
        },
    ]
    out = _compute_ratio_values(companies, "fcf_margin", "annual")
    assert out == {}
