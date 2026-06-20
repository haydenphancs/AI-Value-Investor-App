"""Shared fiscal-statement quarter labels (app.utils.period_labels).

The display form is "Q4 '26" app-wide — paired with the company's FISCAL year so
off-calendar-fiscal companies (Oracle FY ends May) stay chronologically
monotonic. No "FY" marker. The Institutions / 13F chart intentionally counts
calendar quarters and is NOT built from this helper. Pure functions — no network.
"""

from __future__ import annotations

from app.utils.period_labels import extract_year, quarterly_period_label


def _rec(period: str, date: str, fiscal_year=None, calendar_year=None) -> dict:
    r: dict = {"period": period, "date": date}
    if fiscal_year is not None:
        r["fiscalYear"] = fiscal_year
    if calendar_year is not None:
        r["calendarYear"] = calendar_year
    return r


def test_calendar_aligned_uses_fiscal_year_apostrophe():
    rec = _rec("Q4", "2026-12-31", fiscal_year=2026, calendar_year=2026)
    assert quarterly_period_label(rec, use_fiscal_year=True) == "Q4 '26"


def test_oracle_off_calendar_is_monotonic_no_fy_marker():
    # ORCL FY26: Q1 ends Aug 2025 (calendar 2025) but fiscalYear 2026; Q4 ends
    # May 2026. With the fiscal year all four read '26 — monotonic. The old EPS
    # bug labeled Q1/Q2 as '25 (calendar of the period-end date) → scrambled.
    q1 = _rec("Q1", "2025-08-31", fiscal_year=2026, calendar_year=2025)
    q2 = _rec("Q2", "2025-11-30", fiscal_year=2026, calendar_year=2025)
    q3 = _rec("Q3", "2026-02-28", fiscal_year=2026, calendar_year=2026)
    q4 = _rec("Q4", "2026-05-31", fiscal_year=2026, calendar_year=2026)
    labels = [quarterly_period_label(q, use_fiscal_year=True) for q in (q1, q2, q3, q4)]
    assert labels == ["Q1 '26", "Q2 '26", "Q3 '26", "Q4 '26"]


def test_no_fy_marker_ever():
    q4 = _rec("Q4", "2026-05-31", fiscal_year=2026)
    assert "FY" not in quarterly_period_label(q4, use_fiscal_year=True)


def test_calendar_fallback_when_no_fiscal_year():
    # use_fiscal_year=False (calendar join form) OR fiscalYear absent → calendar.
    rec = _rec("Q2", "2024-06-30", calendar_year=2024)
    assert quarterly_period_label(rec, use_fiscal_year=False) == "Q2 '24"
    assert quarterly_period_label({"period": "Q2", "date": "2024-06-30"}) == "Q2 '24"


def test_extract_year_prefers_calendar_year_then_date():
    assert extract_year({"calendarYear": 2025, "date": "2025-05-31"}) == "2025"
    assert extract_year({"date": "2023-02-28"}) == "2023"
    assert extract_year({}) == ""


def test_malformed_record_does_not_crash():
    # Missing fields degrade gracefully (no exception) — real records always have
    # period + date, so these never occur in practice.
    assert quarterly_period_label({}, use_fiscal_year=True) == " '"
    assert quarterly_period_label({"period": "Q1"}, use_fiscal_year=True) == "Q1 '"
