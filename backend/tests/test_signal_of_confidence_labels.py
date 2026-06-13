"""Fiscal-vs-calendar quarter labels for the Signal of Confidence / Capital
Allocation chart.

Off-calendar-fiscal companies (Oracle FY ends May, Apple Sep, Microsoft Jun)
get an "FY" marker so their fiscal quarters aren't confused with the
calendar-based 13F / Institutions chart (which counts quarters differently and
lags ~45 days). Pure functions — no network.
"""

from __future__ import annotations

from app.services.signal_of_confidence_service import (
    _is_off_calendar_fiscal,
    _quarterly_period_label,
)


def _rec(period: str, date: str, fiscal_year=None, calendar_year=None) -> dict:
    r: dict = {"period": period, "date": date}
    if fiscal_year is not None:
        r["fiscalYear"] = fiscal_year
    if calendar_year is not None:
        r["calendarYear"] = calendar_year
    return r


def test_calendar_fiscal_year_uses_apostrophe_label():
    """December fiscal-year end → calendar-aligned → "Q4 '26" (unchanged)."""
    rec = _rec("Q4", "2026-12-31", fiscal_year=2026, calendar_year=2026)
    assert _is_off_calendar_fiscal(rec) is False
    assert _quarterly_period_label(rec, use_fiscal_year=True) == "Q4 '26"
    # A calendar Q2 (ends June) is also aligned.
    q2 = _rec("Q2", "2026-06-30", fiscal_year=2026, calendar_year=2026)
    assert _is_off_calendar_fiscal(q2) is False
    assert _quarterly_period_label(q2, use_fiscal_year=True) == "Q2 '26"


def test_oracle_off_calendar_uses_fy_label():
    """Oracle FY ends May 31. Fiscal Q4 FY26 (ends 2026-05-31) and fiscal Q1
    FY26 (ends 2025-08-31) both read "FY26" — the user-reported case."""
    q4 = _rec("Q4", "2026-05-31", fiscal_year=2026, calendar_year=2026)
    assert _is_off_calendar_fiscal(q4) is True
    assert _quarterly_period_label(q4, use_fiscal_year=True) == "Q4 FY26"
    q1 = _rec("Q1", "2025-08-31", fiscal_year=2026, calendar_year=2025)
    assert _is_off_calendar_fiscal(q1) is True
    assert _quarterly_period_label(q1, use_fiscal_year=True) == "Q1 FY26"


def test_other_off_calendar_companies_flagged():
    """Apple (FY ends ~Sep) and Microsoft (FY ends Jun) are off-calendar too."""
    aapl_q4 = _rec("Q4", "2026-09-26", fiscal_year=2026)
    msft_q4 = _rec("Q4", "2026-06-30", fiscal_year=2026)
    assert _is_off_calendar_fiscal(aapl_q4) is True
    assert _is_off_calendar_fiscal(msft_q4) is True
    assert _quarterly_period_label(aapl_q4, use_fiscal_year=True) == "Q4 FY26"
    assert _quarterly_period_label(msft_q4, use_fiscal_year=True) == "Q4 FY26"


def test_non_fiscal_path_keeps_calendar_form():
    """use_fiscal_year=False never emits an FY marker — calendar-year form."""
    rec = _rec("Q4", "2026-05-31", fiscal_year=2026, calendar_year=2026)
    assert _quarterly_period_label(rec, use_fiscal_year=False) == "Q4 '26"


def test_malformed_record_does_not_crash_or_flag():
    """Missing/short date or non-numeric period → treated as calendar (no FY)."""
    assert _is_off_calendar_fiscal({"period": "Q4", "date": ""}) is False
    assert _is_off_calendar_fiscal({"period": "", "date": "2026-05-31"}) is False
    assert _is_off_calendar_fiscal({"period": "FY", "date": "2026-05-31"}) is False
