"""Fiscal-year quarter labels in earnings_service.

Historical (actuals) labels come from the shared helper (covered in
test_period_labels). Here we pin the FORECAST path (`_infer_fiscal_label`, which
runs on analyst estimates that lack a `fiscalYear` field) and its fiscal-year
helper, so forecast quarters stay monotonic with actuals for off-calendar-FY
companies (Oracle). Without this, the Earnings Timeline would scramble across the
actual→forecast boundary (e.g. "Q4 '26" then "Q1 '26"). Pure functions — no network.
"""

from __future__ import annotations

from app.services.earnings_service import (
    _build_fiscal_quarter_map,
    _fiscal_year_for_quarter,
    _infer_fiscal_label,
)

# Oracle FY ends May: Q1 ends Aug, Q2 Nov, Q3 Feb, Q4 May.
ORCL_MAP = {8: "Q1", 11: "Q2", 2: "Q3", 5: "Q4"}


def test_fiscal_year_for_quarter_oracle():
    assert _fiscal_year_for_quarter(2025, 8, 1) == 2026   # Q1 ends Aug 2025 → FY26
    assert _fiscal_year_for_quarter(2025, 11, 2) == 2026  # Q2 ends Nov 2025 → FY26
    assert _fiscal_year_for_quarter(2026, 2, 3) == 2026   # Q3 ends Feb 2026 → FY26
    assert _fiscal_year_for_quarter(2026, 5, 4) == 2026   # Q4 ends May 2026 → FY26


def test_fiscal_year_for_quarter_calendar_company():
    # December FYE → fiscal year == calendar year.
    assert _fiscal_year_for_quarter(2025, 3, 1) == 2025
    assert _fiscal_year_for_quarter(2025, 12, 4) == 2025


def test_infer_fiscal_label_oracle_forecast_is_fiscal():
    # A future Q1 FY27 estimate (ends ~Aug 2026) labels "Q1 '27", not "Q1 '26".
    assert _infer_fiscal_label("2026-08-31", ORCL_MAP) == "Q1 '27"
    # Future Q3 FY27 (ends ~Feb 2027) → "Q3 '27".
    assert _infer_fiscal_label("2027-02-28", ORCL_MAP) == "Q3 '27"


def test_infer_fiscal_label_no_map_falls_back_to_calendar():
    # No inferred pattern → plain calendar quarter (best effort, no crash).
    assert _infer_fiscal_label("2026-06-30", {}) == "Q2 '26"


def test_build_fiscal_quarter_map_oracle():
    income = [
        {"period": "Q1", "date": "2025-08-31"},
        {"period": "Q2", "date": "2025-11-30"},
        {"period": "Q3", "date": "2026-02-28"},
        {"period": "Q4", "date": "2026-05-31"},
    ]
    assert _build_fiscal_quarter_map(income) == {8: "Q1", 11: "Q2", 2: "Q3", 5: "Q4"}
