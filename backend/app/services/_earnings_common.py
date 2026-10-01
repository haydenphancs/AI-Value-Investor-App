"""
Shared helpers for earnings timing.

Both ``earnings_service`` (TickerDetailView → Financials tab → Earnings
section's ``next_earnings_date``) and ``tracking_service`` (watchlist
Earnings Alert card) read FMP's ``earnings-calendar`` endpoint. Without
these helpers each call-site parsed FMP's free-form ``time`` field
differently, causing the same NVDA Feb-22-after-close event to show as
"after market close" in the alert but "Time Not Specified" in the
Financials tab.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ── Canonical timing tokens ────────────────────────────────────────

BEFORE_OPEN = "before_open"
AFTER_CLOSE = "after_close"
DURING_HOURS = "during_hours"
UNSPECIFIED = "unspecified"


def parse_fmp_timing(fmp_time: Optional[str]) -> str:
    """Normalize FMP's ``time`` field to one of the canonical tokens.

    FMP returns a free-form string like ``"bmo"``, ``"BMO"``,
    ``"before market"``, ``"amc"``, ``"AMC"``, ``"after market close"``,
    ``"dmh"``, or blank. We collapse every variant to one of four tokens
    so the alert and the Financials Earnings section always agree.
    """
    raw = (fmp_time or "").strip().lower()
    if not raw:
        return UNSPECIFIED
    if "bmo" in raw or "before" in raw:
        return BEFORE_OPEN
    if "amc" in raw or "after" in raw:
        return AFTER_CLOSE
    if "dmh" in raw or "during" in raw:
        return DURING_HOURS
    return UNSPECIFIED


# ── Display strings ────────────────────────────────────────────────

# iOS ``EarningsReportTiming`` enum rawValues — emitted by
# earnings_service.next_earnings_date.timing so iOS decodes cleanly.
_DISPLAY = {
    BEFORE_OPEN:  "Before Market Open",
    AFTER_CLOSE:  "After Market Close",
    DURING_HOURS: "During Market Hours",
    UNSPECIFIED:  "Time Not Specified",
}

# Human sentence fragment for alert description lines ("reports earnings
# {SENTENCE}"). ``None`` when unspecified so callers can omit the clause
# entirely rather than guess "after market close".
_SENTENCE = {
    BEFORE_OPEN:  "before market open",
    AFTER_CLOSE:  "after market close",
    DURING_HOURS: "during market hours",
}


def timing_display(token: str) -> str:
    """Return the iOS-compatible display string for a canonical token."""
    return _DISPLAY.get(token, _DISPLAY[UNSPECIFIED])


def timing_sentence(token: str) -> Optional[str]:
    """Human phrase for alert descriptions. ``None`` when unspecified so
    callers drop the timing clause instead of hallucinating a default.
    """
    return _SENTENCE.get(token)


# ── Alert DTO contract ─────────────────────────────────────────────
# tracking_service currently emits the narrower two-value token set
# ``"before_open"`` / ``"after_close"`` / ``None`` via the AlertResponse
# ``report_time`` field — iOS maps it through ``EarningsReportTime`` enum.
# Keep this function the single place that converts.

def alert_report_time(token: str) -> Optional[str]:
    """Return the token value expected by the iOS ``EarningsReportTime``
    enum (``"before_open"`` / ``"after_close"``) or ``None`` when the
    timing is unknown or intraday.
    """
    if token in (BEFORE_OPEN, AFTER_CLOSE):
        return token
    return None


# ── Next pending announcement (shared by the five Financials services) ──
#
# Each service used to carry its own copy of "first row dated AFTER today with no
# actual", and every copy had the same two defects:
#   * ``ec_date <= today_str`` skipped TODAY's pending report. On report day the
#     card read next quarter's date (~3 months out) as "Confirmed", and because that
#     far date is also the Supabase cache-invalidation key, a build made that morning
#     stayed live for its full 24h — the just-reported quarter never appeared.
#   * A reschedule leaves the ORIGINAL date behind as a pending row. When a company
#     reports EARLIER than first announced, that stale row (a few days ahead) was
#     returned as the next "Confirmed" report for a quarter already reported.
#
# 21 days, not more: a reschedule moves a date by days to two weeks, while two REAL
# consecutive releases can sit only ~30-45 days apart — a non-accelerated filer's 10-K
# deadline (Mar 31) and 10-Q deadline (May 15) are exactly 45 days apart, and an NT 10-K
# filer's ~30. A 45-day window dropped those filers' real next report: the card showed the
# quarter AFTER it (~3 months out) as "Confirmed", and that far date became the
# cache-invalidation key of all five Financials services, so their rows were not rebuilt
# on the real release day.
STALE_RESCHEDULE_DAYS = 21


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def has_reported_actual(rec: Dict[str, Any]) -> bool:
    """True when an earnings-calendar row carries a reported EPS or revenue actual."""
    return any(_finite(rec.get(k)) for k in ("epsActual", "eps", "revenueActual"))


def _row_date(rec: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.strptime(str(rec.get("date") or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _sorted_dated_rows(ec_records: Any) -> List[Dict[str, Any]]:
    rows = [
        r for r in (ec_records if isinstance(ec_records, list) else [])
        if isinstance(r, dict) and _row_date(r) is not None
    ]
    rows.sort(key=lambda r: str(r.get("date"))[:10])
    return rows


def _in_window_after_reported(when: datetime, reported: List[datetime]) -> bool:
    return any(0 <= (when - rd).days <= STALE_RESCHEDULE_DAYS for rd in reported)


def within_reschedule_window(rec: Dict[str, Any], ec_records: Any) -> bool:
    """True when pending row ``rec`` is dated within ``STALE_RESCHEDULE_DAYS`` after a row
    that already reported — i.e. it may be a reschedule's leftover. ``next_pending_earnings``
    still returns such a row when no later pending row exists (it is never dropped to
    None); a caller that labels the date "Confirmed" should not, for this one."""
    when = _row_date(rec) if isinstance(rec, dict) else None
    if when is None:
        return False
    reported = [_row_date(r) for r in _sorted_dated_rows(ec_records) if has_reported_actual(r)]
    return _in_window_after_reported(when, reported)


def next_pending_earnings(
    ec_records: Any, today_str: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """The next announcement still to come: the earliest row dated TODAY or later with no
    reported actual. A row dated within ``STALE_RESCHEDULE_DAYS`` after a row that already
    reported is skipped as a stale reschedule ONLY when a later pending row exists to take
    its place; a lone pending row is never dropped (``within_reschedule_window`` tells a
    caller it is suspect). None if no pending row at all."""
    if today_str is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = _sorted_dated_rows(ec_records)
    reported = [_row_date(r) for r in rows if has_reported_actual(r)]
    pending = [
        r for r in rows
        if str(r.get("date"))[:10] >= today_str and not has_reported_actual(r)
    ]
    for i, rec in enumerate(pending):
        ec_date = str(rec.get("date"))[:10]
        if _in_window_after_reported(_row_date(rec), reported):
            if i + 1 < len(pending):
                logger.warning(
                    "earnings calendar: skipping stale pending row %s (symbol=%s) — the "
                    "quarter already reported within %sd before it; next pending row %s",
                    ec_date, rec.get("symbol"), STALE_RESCHEDULE_DAYS,
                    str(pending[i + 1].get("date"))[:10],
                )
                continue
            logger.warning(
                "earnings calendar: keeping lone pending row %s (symbol=%s) although a row "
                "reported within %sd before it — no later pending row to prefer",
                ec_date, rec.get("symbol"), STALE_RESCHEDULE_DAYS,
            )
        return rec
    return None


def next_pending_earnings_date(
    ec_records: Any, today_str: Optional[str] = None
) -> Optional[str]:
    """``next_pending_earnings`` as a ``yyyy-MM-dd`` string (or None)."""
    rec = next_pending_earnings(ec_records, today_str)
    return str(rec.get("date"))[:10] if rec else None


# ── The cache row's report-day stamp, when the calendar itself failed ──
#
# Health Check, Profit Power and Signal of Confidence fetch the per-symbol earnings
# calendar ONLY to stamp their 24h Supabase row with ``next_earnings_date`` — the reader
# drops the row once ``today >= next_earnings_date``, which is what rebuilds the card on
# report day. They used to call it with the swallowing default, so a 429 / 5xx / non-list
# body came back as ``[]`` ("no announcements"), the stamp read None and a clean 24h row
# was written with NO report-day bound. The calendar feeds no served value, so a failed
# fetch is not `degraded` (the response is complete); it is CALENDAR_UNKNOWN, and the
# getter keeps that build in memory but never writes it to the 24h tier.

class _CalendarUnknown:
    """Sentinel type for "the earnings calendar could not be read". Deliberately NOT a
    ``str`` subclass: a string sentinel would be stored in ``next_earnings_date`` and
    ``today >= '<sentinel>'`` compares the wrong way, so that row would never go stale."""

    __slots__ = ()
    _instance: Optional["_CalendarUnknown"] = None

    def __new__(cls) -> "_CalendarUnknown":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "CALENDAR_UNKNOWN"

    # Identity survives copy / deepcopy / pickle, so `is CALENDAR_UNKNOWN` never misses.
    def __copy__(self) -> "_CalendarUnknown":
        return self

    def __deepcopy__(self, _memo: Any) -> "_CalendarUnknown":
        return self

    def __reduce__(self) -> str:
        return "CALENDAR_UNKNOWN"


CALENDAR_UNKNOWN = _CalendarUnknown()

#: What a Financials build hands its getter for the cache row's ``next_earnings_date``:
#: a ``yyyy-MM-dd`` string, None ("no pending announcement" — cacheable), or
#: CALENDAR_UNKNOWN (the calendar failed — never persisted).
EarningsStamp = Union[str, None, _CalendarUnknown]


def stamp_is_persistable(next_earnings: Any) -> bool:
    """True when ``next_earnings`` may be written to a ``next_earnings_date`` column:
    a date string or None. CALENDAR_UNKNOWN (or anything else) may not."""
    return next_earnings is None or isinstance(next_earnings, str)


def next_earnings_stamp(ec_raw: Any, *, ticker: str, service: str) -> EarningsStamp:
    """The cache row's ``next_earnings_date`` from a calendar gather slot fetched with
    ``get_earning_calendar_full(ticker, raise_errors=True)``.

    * a list → ``next_pending_earnings_date`` (``[]``, history-only and malformed rows
      all give a real, cacheable answer: None or the next pending date);
    * ``FMPNotEntitledException`` → None: permanent, so the build without the stamp IS
      the answer (the row keeps its plain 24h TTL rather than never being cached);
    * any other exception — including a ``CancelledError`` INSTANCE that
      ``gather(return_exceptions=True)`` put in the slot — or a non-list body →
      CALENDAR_UNKNOWN, which the getter refuses to persist.
    """
    # Imported here: this module is shared by light callers (tracking, the report
    # collector) that should not pull the FMP client in at import time.
    from app.integrations.fmp import FMPException, FMPNotEntitledException

    if isinstance(ec_raw, list):
        return next_pending_earnings_date(ec_raw)
    if isinstance(ec_raw, FMPNotEntitledException):
        logger.warning(
            "[%s-calendar-not-entitled] ticker=%s step=earnings_calendar: %s: %s — no "
            "next-earnings bound; the build is persisted on the plain 24h TTL",
            service, ticker, type(ec_raw).__name__, ec_raw,
        )
        return None
    if isinstance(ec_raw, BaseException):
        logger.warning(
            "[%s-calendar-unavailable] ticker=%s step=earnings_calendar: %s: %s — values "
            "unaffected; served from memory, NOT persisted",
            service, ticker, type(ec_raw).__name__, ec_raw,
            # A typed FMP failure is expected and self-describing; anything else (a bug,
            # a cancellation) gets its stack.
            exc_info=None if isinstance(ec_raw, FMPException) else ec_raw,
        )
        return CALENDAR_UNKNOWN
    logger.warning(
        "[%s-calendar-unavailable] ticker=%s step=earnings_calendar: expected a list, got "
        "%s — values unaffected; served from memory, NOT persisted",
        service, ticker, type(ec_raw).__name__,
    )
    return CALENDAR_UNKNOWN


# ── Dropped / added digit in a feed EPS actual ─────────────────────
#
# A dropped-digit EPS (feed 0.169 for a real 1.69) answers 200 with no `degraded` reason:
# without this it shipped as a "-90% miss" on the Financials tab AND was frozen into the
# report's EPS Track Record. Its signature is a same-sign ratio to the estimate that sits on
# a power of ten (log10 within the tolerance of a whole number) outside the normal miss
# band. When the filed GAAP EPS is known (non-zero) it is a POSITIVE tie-break, not a
# veto: the feed's actual is suspect only if the filing shares the estimate's sign and
# sits strictly nearer (in log10) the estimate's magnitude than the feed's. A ratio-band
# veto ([0.67, 1.5] of the feed) read a real ~90% small-cap miss near break-even as a
# dropped digit whenever GAAP differed from adjusted — often with the OPPOSITE sign, which
# can never confirm the estimate's magnitude. Shared by earnings_service (the tab) and the
# report collector so the two never disagree about the same quarter.
EPS_GLITCH_MIN_ESTIMATE = 0.05
EPS_GLITCH_NORMAL_BAND = (0.2, 5.0)
EPS_GLITCH_LOG10_TOLERANCE = 0.06
# Float slack on the tie-break: an exact tie (GAAP 3x from both) is not evidence.
_EPS_GLITCH_TIE_EPSILON = 1e-9


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def eps_digit_shift_suspect(actual: Any, estimate: Any, gaap: Any = None) -> bool:
    """True when `actual` vs `estimate` has the dropped/added-digit signature and the
    filed GAAP EPS (when known and non-zero) sides with the estimate: same sign as the
    estimate, and strictly nearer its magnitude than the feed's (log10 distance)."""
    a = _num(actual)
    e = _num(estimate)
    if a is None or e is None or a == 0 or e == 0:
        return False
    if (a > 0) != (e > 0) or abs(e) < EPS_GLITCH_MIN_ESTIMATE:
        return False
    ratio = abs(a / e)
    lo, hi = EPS_GLITCH_NORMAL_BAND
    if lo <= ratio <= hi:
        return False
    lg = math.log10(ratio)
    if abs(lg - round(lg)) > EPS_GLITCH_LOG10_TOLERANCE:
        return False
    g = _num(gaap)
    if g is not None and g != 0:
        if (g > 0) != (e > 0):
            return False
        to_estimate = abs(math.log10(abs(g / e)))
        to_feed = abs(math.log10(abs(g / a)))
        return to_estimate + _EPS_GLITCH_TIE_EPSILON < to_feed
    return True
