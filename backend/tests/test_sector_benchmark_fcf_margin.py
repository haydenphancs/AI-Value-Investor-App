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
