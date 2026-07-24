"""Shared fiscal-statement quarter display labels.

Single source of truth for the ``"Q4 '26"`` labels shown on report charts that
are built from FMP financial statements (Capital Allocation, EPS Track Record,
Growth, Profit Power, the Fundamentals drill-down). Uses the company's FISCAL
year so off-calendar fiscal companies (Oracle FY ends May, Apple Sep, Microsoft
Jun) stay chronologically monotonic — a calendar-year label would sort fiscal
``"Q1 '26"`` (ends Aug 2025) *before* the prior fiscal ``"Q4 '26"`` (ends May
2025), which is the EPS-track-record scrambling bug this replaces.

NOT used by the Institutions / 13F chart (``holders_service``) — that
intentionally counts CALENDAR quarters and lags ~45 days (see
``latest_filed_13f_quarter`` at the bottom of this module, the single source of
truth for that selection) — nor by
``sector_benchmark_service``, whose calendar labels are storage keys (and the
``_match_period`` calendar join keys in growth/profit-power services), not
display strings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple


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


# ── 13F filing-lag-aware calendar quarter ─────────────────────────────────────

# SEC Rule 13f-1: an institution's 13F-HR for a calendar quarter is due 45 days
# after that quarter ENDS. Picking the quarter that merely ended most recently
# therefore reads a PARTIALLY-FILED aggregate from FMP's
# `institutional-ownership/*` endpoints.
#
# That is not a rounding error. On 2026-07-23 (Q2'26 deadline ~Aug 14) AAPL's
# Q2'26 positions-summary held 1,760 of 6,347 filers, so the 4,587 funds that had
# simply not filed yet were counted as `closedPositions` and produced
# `numberOf13FsharesChange = -9,108,611,538`. The Recent Activities card rendered
# "$434.0B in / $3058.5B out" — a fabricated ~$3 trillion institutional exodus.
_13F_FILING_LAG_DAYS = 45

_QUARTER_END_DAY = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def latest_filed_13f_quarter(
    now: Optional[datetime] = None,
    lag_days: int = _13F_FILING_LAG_DAYS,
) -> Tuple[int, int]:
    """Return ``(year, quarter)`` of the most recent calendar quarter whose 13F
    filing deadline has already passed — i.e. the newest quarter FMP can report
    a COMPLETE institutional aggregate for.

    Examples (default 45-day lag):
      2026-01-10 → (2025, 3)   Q4'25 ended Dec 31, due ~Feb 14 — not yet filed
      2026-02-20 → (2025, 4)   Q4'25 deadline has passed
      2026-07-23 → (2026, 1)   Q2'26 ends Jun 30, due ~Aug 14 — not yet filed
      2026-08-20 → (2026, 2)   Q2'26 deadline has passed
    """
    now = now or datetime.now(timezone.utc)
    ref = now - timedelta(days=lag_days)

    quarter = (ref.month - 1) // 3 + 1
    year = ref.year

    # `ref` normally sits INSIDE a quarter that has not ended yet, so the newest
    # settled quarter is the previous one. Only when `ref` lands on/after the
    # quarter's own end date does that quarter itself qualify.
    if (ref.month, ref.day) < _QUARTER_END_DAY[quarter]:
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return year, quarter
