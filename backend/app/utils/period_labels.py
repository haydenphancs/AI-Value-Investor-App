"""Shared fiscal-statement quarter display labels.

Single source of truth for the ``"Q4 '26"`` labels shown on report charts that
are built from FMP financial statements (Capital Allocation, EPS Track Record,
Growth, Profit Power, the Fundamentals drill-down). Uses the company's FISCAL
year so off-calendar fiscal companies (Oracle FY ends May, Apple Sep, Microsoft
Jun) stay chronologically monotonic — a calendar-year label would sort fiscal
``"Q1 '26"`` (ends Aug 2025) *before* the prior fiscal ``"Q4 '26"`` (ends May
2025), which is the EPS-track-record scrambling bug this replaces.

NOT used by the Institutions / 13F chart (``holders_service``) — that
intentionally counts CALENDAR quarters and lags ~45 days — nor by
``sector_benchmark_service``, whose calendar labels are storage keys (and the
``_match_period`` calendar join keys in growth/profit-power services), not
display strings.
"""

from __future__ import annotations

from typing import Any, Dict


def extract_year(record: Dict[str, Any]) -> str:
    """Calendar year as a string, preferring FMP's ``calendarYear`` then the
    period-end ``date``. Returns ``""`` when neither is usable."""
    cal_year = record.get("calendarYear")
    if cal_year:
        return str(cal_year)
    date_str = record.get("date") or ""
    return date_str[:4] if len(date_str) >= 4 else ""


def quarterly_period_label(
    record: Dict[str, Any], use_fiscal_year: bool = True
) -> str:
    """Display label for a fiscal-statement quarter, e.g. ``"Q4 '26"``.

    With ``use_fiscal_year`` (the default) and FMP's ``fiscalYear`` present, pairs
    the fiscal quarter with the FISCAL year — keeping off-calendar companies
    chronologically monotonic. Falls back to the calendar year of the period-end
    date when ``fiscalYear`` is absent. No ``"FY"`` marker — the apostrophe form
    is used app-wide.
    """
    period = record.get("period", "") or ""
    if use_fiscal_year and record.get("fiscalYear"):
        year = str(record.get("fiscalYear"))
    else:
        year = extract_year(record)
    yy = year[-2:] if len(year) >= 4 else year
    return f"{period} '{yy}"
