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

import re
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


# ── Fiscal-year keys for ANNUAL rows and off-calendar detection ─────────────────

# A period END on Jan 1-7 belongs to the PRIOR fiscal year: 52/53-week filers whose
# year ends on the Saturday nearest Dec 31 (Cadence, Snap-on, Kellanova) close FY2025
# on 2026-01-03. Keying such a row on ``date[:4]`` labelled FY2025 "2026", left 2025
# empty and duplicated a year elsewhere (FY2021 ends 2022-01-01, FY2022 2022-12-31).
_YEAR_END_SPILL_DAYS = 7


def _parse_iso_date(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def annual_fiscal_year(record: Dict[str, Any]) -> str:
    """Fiscal-year key for an ANNUAL statement row, e.g. ``"2025"``.

    Prefers FMP's ``fiscalYear`` (``/stable`` no longer ships ``calendarYear``);
    otherwise the year of ``date - 7 days`` so a Jan 1-7 year end counts as the
    prior year. The same spill rule also corrects a ``fiscalYear`` that merely
    repeats the end-date year of a Jan 1-7 close. Returns ``""`` when unusable.
    """
    end = _parse_iso_date(record.get("date"))
    fy_raw = str(record.get("fiscalYear") or "").strip()
    if len(fy_raw) == 4 and fy_raw.isdigit():
        if (
            end is not None
            and end.month == 1
            and end.day <= _YEAR_END_SPILL_DAYS
            and int(fy_raw) == end.year
        ):
            return str(end.year - 1)
        return fy_raw
    if end is None:
        return _valid_year(extract_year(record))
    return str((end - timedelta(days=_YEAR_END_SPILL_DAYS)).year)


def _valid_year(value: str) -> str:
    """``value`` if it is a 4-digit year, else ``""`` — ``extract_year`` slices
    ``date[:4]`` unvalidated, so a malformed date would otherwise label a bar "not-"."""
    return value if len(value) == 4 and value.isdigit() else ""


def annual_benchmark_key(record: Dict[str, Any]) -> str:
    """JOIN key for an ANNUAL row against the stored sector/industry benchmarks.

    The producer keys every peer's annual value on the calendar year of its period
    end. Joining on ``fiscalYear`` instead puts a start-year-named fiscal year (Home
    Depot's FY2025 ends 2026-02-01) one year BEHIND its same-calendar retail peers;
    joining on the raw ``date[:4]`` puts a Jan 1-7 year end (Cadence FY2025 ends
    2026-01-03) one year AHEAD of its December peers. The year of ``date - 7 days``
    is right for both. Display labels use ``annual_fiscal_year``; only the join uses
    this.
    """
    end = _parse_iso_date(record.get("date"))
    if end is None:
        return _valid_year(extract_year(record))
    return str((end - timedelta(days=_YEAR_END_SPILL_DAYS)).year)


def calendar_quarter_of(date_value: Any) -> Optional[int]:
    """Calendar quarter (1-4) a period END falls in, treating an end on day 1-7 of a
    month as the previous month (52/53-week quarter ends spill a few days over)."""
    end = _parse_iso_date(date_value)
    if end is None:
        return None
    if end.day <= _YEAR_END_SPILL_DAYS:
        end = end - timedelta(days=_YEAR_END_SPILL_DAYS)
    return (end.month - 1) // 3 + 1


# ── Calendar-quarter JOIN keys for the quarterly sector/industry benchmarks ─────
#
# The stored quarterly benchmarks (period_type ``calendar_quarter`` in
# `sector_benchmarks`) pool every company by the CALENDAR quarter its period ENDS in,
# and every consumer joins a company's quarter to that key. The fiscal ``period``
# ("Q1".."Q4") is deliberately ignored: Microsoft's fiscal Q1 is Jul-Sep, Apple's is
# Oct-Dec, and Nvidia's Q4 ends in late January, so "fiscal quarter + calendar year"
# (the old key) put their quarters next to peers' quarters 3-10 months away.

def calendar_quarter_key(date_value: Any) -> Optional[Tuple[int, int]]:
    """``(calendar year, calendar quarter)`` of a period END, or None when the date is
    unusable. Same 1-7-day spill rule as :func:`calendar_quarter_of`, applied to the YEAR
    too: a 52/53-week Q4 that closes on 2026-01-03 is calendar Q4 2025."""
    end = _parse_iso_date(date_value)
    if end is None:
        return None
    if end.day <= _YEAR_END_SPILL_DAYS:
        end = end - timedelta(days=_YEAR_END_SPILL_DAYS)
    return end.year, (end.month - 1) // 3 + 1


def previous_calendar_quarter(key: Tuple[int, int]) -> Tuple[int, int]:
    """The calendar quarter immediately before ``key`` (Q1 2026 → Q4 2025)."""
    year, quarter = key
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def format_calendar_quarter(key: Tuple[int, int]) -> str:
    """``(2025, 3)`` → ``"Q3'25"`` — the stored ``period_label`` of a calendar-quarter
    benchmark row (same spelling as the legacy fiscal-keyed rows, so the label parsers
    and chronological sort keys keep working; the period_type tells the two apart)."""
    year, quarter = key
    return f"Q{quarter}'{year % 100:02d}"


def calendar_quarter_label(record: Any) -> str:
    """Calendar-quarter benchmark join key for one QUARTERLY statement row, from its
    period-end ``date`` only (``"Q3'25"``). Returns ``""`` when the row has no usable
    date — the caller must skip it rather than invent a key."""
    if not isinstance(record, dict):
        return ""
    key = calendar_quarter_key(record.get("date"))
    return format_calendar_quarter(key) if key is not None else ""


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


_FILING_PERIOD_RE = re.compile(r"^(\d{4})-Q([1-4])$")


def filing_period_display(period: str) -> str:
    """``"2026-Q2"`` → ``"Q2 2026"``. Anything else → ``""``.

    Used to date-stamp the Whale Profile's 13F portfolio tile, so the reader can
    see that a figure captioned "13F Equity Portfolio" is a quarter-end snapshot
    up to ~4.5 months old rather than a live balance.

    ⚠️ The strict regex is load-bearing, not defensive habit. Congressional
    snapshots write their period as ``YYYY-MM`` (``now.strftime("%Y-%m")``), and
    a looser parse would render one of those as a 13F quarter — inventing a
    filing that does not exist for a politician who never files a 13F. Returning
    "" makes the caller omit the stamp, which is the honest outcome.
    """
    match = _FILING_PERIOD_RE.match(str(period or "").strip())
    if not match:
        return ""
    return f"Q{match.group(2)} {match.group(1)}"
