"""
Earnings service — fetches EPS/Revenue estimates & actuals from FMP,
builds the response payload that matches the iOS EarningsData struct.

Data sources (priority order):
- Earnings calendar (bulk, filtered by symbol): actual EPS/Revenue + consensus estimates (adjusted/non-GAAP)
- Income statement (quarterly): fiscal period labels (Q1-Q4), fiscal dates, revenue fallback
- Analyst estimates (quarterly): future quarter EPS/Revenue estimates
- Historical prices: close price on each fiscal quarter end date
"""

import asyncio
import math
import logging
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import FMPClient, FMPException, get_fmp_client
from app.utils.period_labels import quarterly_period_label
from app.schemas.earnings import (
    EarningsDailyPriceSchema,
    EarningsQuarterSchema,
    EarningsPricePointSchema,
    EarningsResponse,
    NextEarningsDateSchema,
)
from app.services._earnings_common import (
    STALE_RESCHEDULE_DAYS,
    eps_digit_shift_suspect,
    has_reported_actual,
    next_pending_earnings,
    parse_fmp_timing,
    timing_display,
    within_reschedule_window,
    UNSPECIFIED,
)

logger = logging.getLogger(__name__)

# ── In-memory cache ────────────────────────────────────────────────
# (written_at, value, ttl_seconds) — the TTL is per entry so a DEGRADED build can be
# held for a minute (absorbing a retry storm) without living the full 5 minutes.
_cache: Dict[str, Tuple[float, Any, float]] = {}
_CACHE_TTL = 300  # 5 minutes
_DEGRADED_CACHE_TTL = 60

# Stored INSIDE earnings_cache.response_json (stripped on read; not a response field).
# Bump it whenever a stored value's computation changes, so rows written before a deploy
# are rebuilt on their next read instead of being served for up to 24h. That is the only
# way to evict an already-poisoned row: AVGO's Q2 '26 dropped-digit revenue (-89.97%)
# sat in this tier with no version to invalidate it.
#   1 (2026-09-30): revenue reconciliation against the filed income statement,
#     has_estimate, GAAP fallbacks without a surprise, one-to-one announcement
#     assignment, just-reported quarter synthesis, anchored forecast labels.
#   2 (2026-09-30, round 2): an ambiguous day-98-110 release is no longer synthesized as
#     the next (unreported) quarter; a dropped-digit feed revenue with no consensus loses
#     to the filing; a just-reported revenue >50% off consensus is omitted; the next date
#     keeps a 30-45-day-later real report (21-day reschedule window). A v1 row written by
#     any pre-deploy build would otherwise serve those wrong values for up to 24h.
#   3 (2026-09-30, round 3): an EPS actual with the digit-shift signature
#     (`eps_digit_shift_suspect`, now a sign-aware filing tie-break) is OMITTED on every
#     path — the matched announcement (it used to show the filed GAAP EPS) and the
#     just-reported quarter (it used to ship a "-90% miss"); a late release with an
#     earlier still-unreported estimate quarter goes to a quarter only on evidence.
#   4 (2026-10-01, P16): a proven reschedule leftover no longer becomes
#     next_earnings_date; the projection (or None) replaces it
#     (`_reschedule_leftovers`). A v3 row keyed on the leftover served "Expected
#     <leftover>" until that date passed.
_EARNINGS_PAYLOAD_VERSION = 4


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value, ttl = entry
    if time.time() - ts > ttl:
        del _cache[key]
        return None
    return value


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any, ttl: Optional[float] = None) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value, _CACHE_TTL if ttl is None else ttl)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── In-flight deduplication ────────────────────────────────────────
# A miss downloads 6 years of daily closes; concurrent misses for the same
# ticker must share one fetch.
_inflight: Dict[str, asyncio.Future] = {}


# ── Helpers ────────────────────────────────────────────────────────

def _fiscal_year_for_quarter(cal_year: int, end_month: int, quarter: int) -> int:
    """Fiscal year for a quarter ending in (cal_year, end_month), i.e. the
    calendar year of that fiscal cycle's Q4 (the FY-end). For a December FYE this
    equals the calendar year; for Oracle (May FYE) fiscal Q1 (ends Aug 2025) →
    FY2026. Used to keep FORECAST labels (estimates, which lack fiscalYear)
    fiscal-consistent with the historical labels, so the Earnings Timeline reads
    monotonically across the actual→forecast boundary."""
    months_to_q4 = (4 - quarter) * 3
    return cal_year + ((end_month - 1) + months_to_q4) // 12


def _safe_float(d: dict, key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _first_not_none(*vals: Optional[float]) -> Optional[float]:
    """First value that is not None — unlike ``a or b``, this preserves 0.0.

    ``_safe_float(rec,"epsDiluted") or _safe_float(rec,"eps")`` silently discarded
    a genuine BREAK-EVEN quarter (EPS 0.00 is falsy → falls through → None → the
    quarter's bar vanished from the chart). 0.0 is a real reported value.
    """
    for v in vals:
        if v is not None:
            return v
    return None


def _as_list(payload: Any) -> List[dict]:
    """Normalize an FMP payload to a list of record dicts.

    ``FMPClient._make_request`` is typed ``-> Any`` ("list or dict"); a bare
    error dict returned with a 200 iterates as string KEYS → ``AttributeError``
    → a bare 502 for the Earnings section. Degrade loudly instead.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "earnings: expected a list from FMP, got %s — degrading to empty series",
            type(payload).__name__,
        )
    return []


def _compute_surprise(actual: float, estimate: float) -> Optional[float]:
    if estimate == 0:
        return None
    return round(((actual - estimate) / abs(estimate)) * 100, 2)


def _find_close_price(
    date_str: str, price_lookup: Dict[str, float]
) -> Optional[float]:
    """Find close price for a date. If exact date missing, scan ±5 days."""
    if date_str in price_lookup:
        return price_lookup[date_str]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None
    for delta in range(1, 6):
        key = (dt - timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in price_lookup:
            return price_lookup[key]
    for delta in range(1, 6):
        key = (dt + timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in price_lookup:
            return price_lookup[key]
    return None


def _date_to_sort_key(date_str: str) -> str:
    """Ensure dates sort chronologically."""
    return date_str[:10] if date_str else "0000-00-00"


def _build_fiscal_quarter_map(income_sorted: List[dict]) -> Dict[int, str]:
    """Analyze historical income records to build month → fiscal period mapping.

    Returns e.g. {1: "Q4", 4: "Q1", 7: "Q2", 10: "Q3"} for Salesforce.
    """
    freq: Dict[Tuple[int, str], int] = {}
    for rec in income_sorted:
        # Null-safe: `.get(k, "")` returns None for a present-but-null key, and
        # None.startswith(...) raises AttributeError -> 502 for the section.
        period = rec.get("period") or ""
        date_str = rec.get("date") or ""
        if not period.startswith("Q") or not date_str:
            continue
        try:
            month = datetime.strptime(date_str[:10], "%Y-%m-%d").month
            freq[(month, period)] = freq.get((month, period), 0) + 1
        except Exception:
            continue

    # For each month that appears, pick the most frequent period label
    month_to_period: Dict[int, str] = {}
    for (month, period), count in sorted(freq.items(), key=lambda x: -x[1]):
        if month not in month_to_period:
            month_to_period[month] = period

    return month_to_period


def _infer_fiscal_label(est_date: str, fiscal_month_map: Dict[int, str]) -> str:
    """Map an estimate date to the correct fiscal quarter label using the inferred pattern."""
    try:
        dt = datetime.strptime(est_date[:10], "%Y-%m-%d")
    except Exception:
        return est_date[:10]

    if not fiscal_month_map:
        q = (dt.month - 1) // 3 + 1
        yr = dt.strftime("%y")
        return f"Q{q} '{yr}"

    # Find closest fiscal month
    best_month = None
    best_diff = 999
    for month in fiscal_month_map:
        # Circular distance between months (1-12)
        diff = min(abs(dt.month - month), 12 - abs(dt.month - month))
        if diff < best_diff:
            best_diff = diff
            best_month = month

    if best_month is None:
        q = (dt.month - 1) // 3 + 1
        yr = dt.strftime("%y")
        return f"Q{q} '{yr}"

    period = fiscal_month_map[best_month]

    # Determine the year: the fiscal quarter end is in best_month.
    # If est_date month is close to best_month, use the same year context.
    # Handle year boundary: e.g., est_date is Dec and fiscal end is Jan → next year
    if dt.month > best_month and (dt.month - best_month) > 6:
        # est is in Dec, fiscal end is in Jan → fiscal date is next year
        year = dt.year + 1
    elif dt.month < best_month and (best_month - dt.month) > 6:
        # est is in Jan, fiscal end is in Dec → fiscal date is previous year
        year = dt.year - 1
    else:
        year = dt.year

    # Pair the inferred fiscal quarter with the FISCAL year (not the calendar
    # year of the estimate date) so off-calendar-FY companies stay monotonic and
    # match the historical labels built from FMP's fiscalYear field.
    try:
        q = int(period[1:])
        fy = _fiscal_year_for_quarter(year, best_month, q)
    except (ValueError, IndexError):
        fy = year
    yr = str(fy % 100).zfill(2)
    return f"{period} '{yr}"


def _parse_day(value: Any) -> Optional[datetime]:
    """``yyyy-MM-dd`` (any suffix) → datetime, or None for a missing/malformed date."""
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _row_key(rec: dict) -> str:
    return str(rec.get("date") or "")[:10]


# ── Announcement ↔ quarter pairing ─────────────────────────────────
# No company releases a quarter within a few days of closing it (Oracle, among the
# fastest, reports 9-10 days after quarter end). An announcement dated within this many
# days AFTER a period end is therefore the PREVIOUS quarter's late release, never this
# quarter's own — a December filer's Q4 released 2026-04-02 (92 days, a non-accelerated
# filer near its 10-K deadline) used to be paired with Q1 (ended 2026-03-31, two days
# earlier), so Q1 showed Q4's numbers and the real Q1 release was never used.
_MIN_ANNOUNCE_LAG_DAYS = 7
_ANNOUNCE_WINDOW_DAYS = 80
# A late release (NT 10-K, non-accelerated filer) can land past the 80-day window; it is
# offered to its quarter up to this lag, but never once it is far enough past the NEXT
# period end to be that quarter's own release.
_LATE_RELEASE_MAX_DAYS = 110
_IMPLIED_QUARTER_DAYS = 91
_MAX_QUARTER_SPAN_DAYS = 120
# An analyst estimate within this many days of a reported period end is that quarter.
_COVERED_TOLERANCE_DAYS = 15
# With no reported period on file at all, estimates older than this are not "upcoming".
_UNREPORTED_GRACE_DAYS = 100
_DAYS_PER_QUARTER = 91.3


def _prefer_reported(rows: List[dict]) -> Optional[dict]:
    """First row carrying a reported actual, else the first row (a pending placeholder)."""
    for rec in rows:
        if has_reported_actual(rec):
            return rec
    return rows[0] if rows else None


def _match_announcement(
    period_end: str,
    ec_sorted: List[dict],
    max_days: int = _ANNOUNCE_WINDOW_DAYS,
    consumed: Optional[set] = None,
) -> Optional[dict]:
    """Pair an income quarter (by fiscal PERIOD-END) with its earnings announcement:
    a record dated ``_MIN_ANNOUNCE_LAG_DAYS``..``max_days`` days after the period end,
    preferring one that carries a reported actual.

    An earnings announcement lands ~10-50 days after the quarter-end, and the NEXT
    quarter's announcement is ~100-130 days out — so "after the period end, within ~80
    days" is unambiguous. Crucially it's robust to WHEN the 10-Q/10-K is filed.
    Matching the filing/accepted date instead broke both ways:
      * a tight window MISSED a fiscal-Q4 whose 10-K lags the release (Oracle FY26 Q4:
        announced 2026-06-10, 10-K accepted 2026-06-22) → the quarter fell to the GAAP
        income-statement EPS compared against a non-GAAP estimate = a bogus "miss";
      * a loose window CROSS-MATCHED a very late 10-K into the NEXT quarter's
        announcement (Disney files its 10-K ~Jan for a Sep FY-end → Q4 grabbed Q1's
        numbers, and both showed the same figure).
    Preferring a REPORTED row: across a reschedule FMP can keep the original date as a
    pending row beside the real one. Taking whichever came first paired the quarter with
    the empty placeholder, so its EPS fell to GAAP-vs-non-GAAP and showed a fake miss.
    ``consumed`` (row dates already paired with another quarter) keeps the pairing
    one-to-one. ``ec_sorted`` must be ascending by ``date``.
    """
    pe = _parse_day(period_end)
    if pe is None:
        return None
    rows: List[dict] = []
    for rec in ec_sorted:
        dd = _parse_day(rec.get("date"))
        if dd is None:
            continue
        lag = (dd - pe).days
        if lag < _MIN_ANNOUNCE_LAG_DAYS:
            continue
        if lag > max_days:
            break
        if consumed and _row_key(rec) in consumed:
            continue
        rows.append(rec)
    return _prefer_reported(rows)


def _match_late_release(
    pe: datetime, next_pe: datetime, ec_sorted: List[dict], consumed: set
) -> Optional[dict]:
    """A release past the normal window, still offered to quarter ``pe`` because it
    lands too soon after ``next_pe`` to be the NEXT quarter's own release."""
    rows: List[dict] = []
    for rec in ec_sorted:
        dd = _parse_day(rec.get("date"))
        if dd is None:
            continue
        lag = (dd - pe).days
        if lag <= _ANNOUNCE_WINDOW_DAYS:
            continue
        if lag > _LATE_RELEASE_MAX_DAYS or (dd - next_pe).days >= _MIN_ANNOUNCE_LAG_DAYS:
            break
        if _row_key(rec) in consumed:
            continue
        rows.append(rec)
    return _prefer_reported(rows)


# A REPORTED release past a quarter's late window but still within _LATE_RELEASE_MAX_DAYS
# of its period end (day ~98-110) is AMBIGUOUS: a late fiscal Q4 (an NT 10-K filer at day
# ~100) or the NEXT quarter's fast release (a bank's day-13 Q1 is day 104). Dates alone
# cannot tell them apart, so it goes to a quarter only on evidence (_late_release_owner),
# and nobody uses it without. Guessing "next" synthesized an unreported Q1 from Q4's numbers
# (Phase A2) and dropped the real pending Q1 date; guessing "this" would hand a bank's Q1 to
# Q4 whenever the feed has a hole at Q4.
_EST_MATCH_REL_TOL = 0.10    # the row's consensus within 10% of a quarter's analyst average
_EST_MATCH_MARGIN = 2.0      # ... and more than 2x nearer it than the other quarter's


def _estimate_near(
    fiscal_date: str,
    est_by_date: Optional[Dict[str, dict]],
    tolerance_days: int = _COVERED_TOLERANCE_DAYS,
) -> Optional[dict]:
    """The analyst-estimate row for a fiscal period end: exact date, else the nearest
    within ``tolerance_days`` (earlier first on a tie)."""
    if not est_by_date:
        return None
    if fiscal_date in est_by_date:
        return est_by_date[fiscal_date]
    dt = _parse_day(fiscal_date)
    if dt is None:
        return None
    for delta in range(1, tolerance_days + 1):
        for d in (dt - timedelta(days=delta), dt + timedelta(days=delta)):
            k = d.strftime("%Y-%m-%d")
            if k in est_by_date:
                return est_by_date[k]
    return None


def _consensus_vote(
    rec: dict, est_this: Optional[dict], est_next: Optional[dict]
) -> Optional[str]:
    """``"this"`` / ``"next"`` when the row's OWN consensus (``epsEstimated``,
    ``revenueEstimated``) clearly matches one quarter's analyst average; None when it is
    missing, equal for both, halfway, far from both — or when EPS and revenue disagree."""
    votes: set = set()
    for row_field, avg_field in (("epsEstimated", "epsAvg"), ("revenueEstimated", "revenueAvg")):
        row_v = _safe_float(rec, row_field)
        a = _safe_float(est_this, avg_field) if est_this else None
        b = _safe_float(est_next, avg_field) if est_next else None
        if row_v is None or a is None or b is None:
            continue
        da, db = abs(row_v - a), abs(row_v - b)
        if da * _EST_MATCH_MARGIN < db and da <= _EST_MATCH_REL_TOL * abs(a):
            votes.add("this")
        elif db * _EST_MATCH_MARGIN < da and db <= _EST_MATCH_REL_TOL * abs(b):
            votes.add("next")
    return votes.pop() if len(votes) == 1 else None


def _late_release_owner(
    rec: dict,
    pe: datetime,
    next_pe: datetime,
    ec_sorted: List[dict],
    est_this: Optional[dict],
    est_next: Optional[dict],
    next_reported: bool,
) -> Optional[str]:
    """Whose release is ``rec``, an ambiguous row 98-110 days after quarter ``pe``?

    * ``"this"`` — quarter ``pe``'s late release: its consensus matches ``pe``'s analyst
      average, or the NEXT quarter's own release is listed in that quarter's window more
      than ``STALE_RESCHEDULE_DAYS`` after it (a nearer row is a reschedule duplicate, not
      evidence). When the next quarter already has an income row it has reported, so only
      a REPORTED later row counts.
    * ``"next"`` — its consensus matches the next quarter's and no later row contradicts it.
    * None — ambiguous: no quarter may use it.
    """
    dd = _parse_day(rec.get("date"))
    if dd is None:
        return None
    vote = _consensus_vote(rec, est_this, est_next)
    later = False
    for other in ec_sorted:
        od = _parse_day(other.get("date"))
        if od is None or other is rec:
            continue
        if next_reported and not has_reported_actual(other):
            continue
        if (od - dd).days > STALE_RESCHEDULE_DAYS and (
            _MIN_ANNOUNCE_LAG_DAYS <= (od - next_pe).days <= _ANNOUNCE_WINDOW_DAYS
        ):
            later = True
            break
    if vote == "next":
        return None if later else "next"
    if vote == "this" or later:
        return "this"
    return None


# ── Reschedule leftovers (the next-date card) ──────────────────────
# A company that reports EARLIER than first announced can leave the original date in the
# feed as a pending row. The shared ``next_pending_earnings`` rule compares dates only, so
# a LONE such row (no later pending row to prefer) came back as the next report: the card
# read "Expected <leftover>" with the leftover's own timing, the AI chat context quoted it,
# and the projection never ran. The pairing above already knows which fiscal period each
# reported release belongs to, and it assumes no quarter releases within
# ``_MIN_ANNOUNCE_LAG_DAYS`` of its own period end. So a pending row dated within
# ``STALE_RESCHEDULE_DAYS`` after a release of period P, and before P's NEXT period end +
# that lag, cannot be any later quarter's release: it is proven to be the leftover.
# Without period evidence for the release (no income row, no synthesized quarter) nothing
# is dropped — the shared rule still shows such a row, unconfirmed.


def _reschedule_leftover_evidence(
    ec_records: Any,
    release_periods: Optional[Dict[str, str]],
    period_end_keys: Any,
    today_str: str,
) -> Dict[str, Tuple[str, str, str]]:
    """Pending rows (dated today or later) proven to be a reschedule's leftover →
    ``(release date, its period end P, the next period end)`` of the reported row that
    proves it. The next period end is the earliest known one (filed or estimated) more
    than ``_COVERED_TOLERANCE_DAYS`` and at most ``_MAX_QUARTER_SPAN_DAYS`` after P, else
    P + ``_IMPLIED_QUARTER_DAYS``. Input order and junk rows do not matter."""
    if not isinstance(release_periods, dict) or not release_periods:
        return {}
    if not isinstance(ec_records, list):
        return {}
    today = _parse_day(today_str)
    if today is None:
        return {}
    keys = period_end_keys if isinstance(period_end_keys, (list, tuple, set)) else []
    period_ends = sorted({d for d in (_parse_day(k) for k in keys) if d is not None})

    dated = [
        (rec, dd) for rec, dd in (
            (r, _parse_day(r.get("date"))) for r in ec_records if isinstance(r, dict)
        )
        if dd is not None
    ]
    # Each reported release whose fiscal period is known, nearest-first for the log.
    releases: List[Tuple[datetime, datetime, datetime]] = []
    for rec, dd in dated:
        if not has_reported_actual(rec):
            continue
        pe = _parse_day(release_periods.get(_row_key(rec)))
        if pe is None:
            continue
        nxt = next(
            (
                e for e in period_ends
                if _COVERED_TOLERANCE_DAYS < (e - pe).days <= _MAX_QUARTER_SPAN_DAYS
            ),
            pe + timedelta(days=_IMPLIED_QUARTER_DAYS),
        )
        releases.append((dd, pe, nxt))
    releases.sort(reverse=True)

    out: Dict[str, Tuple[str, str, str]] = {}
    for rec, when in dated:
        key = _row_key(rec)
        if when < today or key in out or has_reported_actual(rec):
            continue
        for rd, pe, nxt in releases:
            if (
                0 <= (when - rd).days <= STALE_RESCHEDULE_DAYS
                and (when - nxt).days < _MIN_ANNOUNCE_LAG_DAYS
            ):
                out[key] = (
                    rd.strftime("%Y-%m-%d"), pe.strftime("%Y-%m-%d"),
                    nxt.strftime("%Y-%m-%d"),
                )
                break
    return out


def _reschedule_leftovers(
    ec_records: Any,
    release_periods: Optional[Dict[str, str]],
    period_end_keys: Any,
    today_str: str,
) -> set:
    """Dates of the pending rows proven to be a reschedule's leftover (see
    ``_reschedule_leftover_evidence``). Empty without period evidence."""
    return set(_reschedule_leftover_evidence(
        ec_records, release_periods, period_end_keys, today_str,
    ))


def _claim_ambiguous_release(
    key: str,
    pe: datetime,
    next_pe: datetime,
    next_reported: bool,
    ec_sorted: List[dict],
    consumed: set,
    est_by_date: Optional[Dict[str, dict]],
    ticker: str,
) -> Optional[dict]:
    """The first unconsumed REPORTED row past quarter ``pe``'s late window (and within
    ``_LATE_RELEASE_MAX_DAYS``) that the evidence gives to ``pe``, else None."""
    est_this = _estimate_near(key, est_by_date)
    est_next = _estimate_near(next_pe.strftime("%Y-%m-%d"), est_by_date)
    for rec in ec_sorted:
        dd = _parse_day(rec.get("date"))
        if dd is None:
            continue
        lag = (dd - pe).days
        if lag <= _ANNOUNCE_WINDOW_DAYS:
            continue
        if lag > _LATE_RELEASE_MAX_DAYS:
            break
        if (
            (dd - next_pe).days < _MIN_ANNOUNCE_LAG_DAYS
            or _row_key(rec) in consumed
            or not has_reported_actual(rec)
        ):
            continue
        owner = _late_release_owner(
            rec, pe, next_pe, ec_sorted, est_this, est_next, next_reported,
        )
        if owner == "this":
            logger.warning(
                "earnings %s: release %s (%sd after %s) assigned to %s as its late release "
                "— past the late window, but the evidence (consensus / the next quarter's "
                "own later release) says it is not the next quarter's",
                ticker, _row_key(rec), lag, key, key,
            )
            return rec
    return None


def _assign_announcements(
    income_sorted: List[dict],
    ec_sorted: List[dict],
    est_by_date: Optional[Dict[str, dict]] = None,
    *,
    ticker: str = "",
) -> Dict[str, dict]:
    """One-to-one: each income period end (``yyyy-MM-dd``) → its announcement row.

    Walks the quarters oldest first and never hands one row to two quarters, so a late
    Q4 release can no longer be read as the next quarter's numbers (both quarters used
    to show the same figures, or the later quarter showed the earlier one's). A quarter
    with nothing in its window or its late window may still claim an AMBIGUOUS row
    (98-110 days) on evidence — see ``_late_release_owner``; ``est_by_date`` (analyst
    estimates by period end) feeds the consensus half of that evidence.
    """
    pes: List[Tuple[str, datetime]] = []
    seen: set = set()
    for rec in income_sorted:
        key = _row_key(rec)
        dt = _parse_day(key)
        if dt is None or key in seen:
            continue
        seen.add(key)
        pes.append((key, dt))

    assigned: Dict[str, dict] = {}
    consumed: set = set()
    for i, (key, pe) in enumerate(pes):
        match = _match_announcement(key, ec_sorted, consumed=consumed)
        if match is None:
            # A later income row means the quarter after this one has reported (even when
            # a hole in history makes its period end implied).
            next_reported = i + 1 < len(pes)
            next_pe = pes[i + 1][1] if next_reported else None
            if next_pe is None or (next_pe - pe).days > _MAX_QUARTER_SPAN_DAYS:
                next_pe = pe + timedelta(days=_IMPLIED_QUARTER_DAYS)
            match = _match_late_release(pe, next_pe, ec_sorted, consumed)
            if match is None:
                match = _claim_ambiguous_release(
                    key, pe, next_pe, next_reported, ec_sorted, consumed, est_by_date,
                    ticker,
                )
        if match is not None:
            assigned[key] = match
            consumed.add(_row_key(match))
    return assigned


def _pair_unconsumed_announcements(
    ec_sorted: List[dict],
    consumed: set,
    newest_end: datetime,
    estimate_keys: List[str],
    today_str: str,
    *,
    newest_owned: bool = True,
    est_by_date: Optional[Dict[str, dict]] = None,
    ticker: str = "",
) -> List[Tuple[str, dict]]:
    """Reported announcements that no income quarter claimed yet → ``(period_end, row)``.

    A company announces days (a 10-Q) to weeks (a Q4 10-K) before FMP carries the filed
    income row. Only income rows produced history, so in that gap the quarter it had
    ALREADY reported rendered as a gray pending estimate and its announced actual was
    dropped. The period end is the latest analyst-estimate date the release follows by
    ``_MIN_ANNOUNCE_LAG_DAYS``..``_ANNOUNCE_WINDOW_DAYS`` days (one-to-one), else the
    newest period end + one quarter. A row dated on/before the newest filed period end
    is a stale duplicate, never a new quarter.

    ``newest_owned`` False (the newest filed quarter has no REPORTED announcement): a row
    within ``_LATE_RELEASE_MAX_DAYS`` of its period end may be THAT quarter's late release
    (an NT 10-K Q4 at day ~100), so it becomes the next quarter only when the evidence
    says so (``_late_release_owner`` → "next"). Otherwise an unreported Q1 was rendered as
    reported with Q4's numbers, and the real pending Q1 date was dropped as stale. The
    same evidence decides when an EARLIER estimate quarter (no income row, no release
    used) lies between the newest filed quarter and the chosen period end: the row goes
    to that quarter ("this"), stays with the later one ("next"), or to none.
    """
    est_dts = sorted({d for d in (_parse_day(k) for k in estimate_keys) if d is not None})
    used_periods: set = set()
    last_end = newest_end
    implied_next = newest_end + timedelta(days=_IMPLIED_QUARTER_DAYS)
    newest_key = newest_end.strftime("%Y-%m-%d")
    out: List[Tuple[str, dict]] = []
    for rec in ec_sorted:
        key = _row_key(rec)
        dd = _parse_day(key)
        if (
            dd is None
            or key in consumed
            or key > today_str
            or dd <= newest_end
            or not has_reported_actual(rec)
        ):
            continue
        if not newest_owned and (dd - newest_end).days <= _LATE_RELEASE_MAX_DAYS:
            owner = _late_release_owner(
                rec, newest_end, implied_next, ec_sorted,
                _estimate_near(newest_key, est_by_date),
                _estimate_near(implied_next.strftime("%Y-%m-%d"), est_by_date),
                False,
            )
            if owner != "next":
                logger.warning(
                    "earnings %s: release %s is %sd after the newest filed quarter %s, "
                    "which has no reported announcement — it may be that quarter's late "
                    "release, so it is NOT shown as the next quarter (owner=%s)",
                    ticker, key, (dd - newest_end).days, newest_key, owner,
                )
                continue
        candidates = [
            e for e in est_dts
            if e not in used_periods
            and (e - newest_end).days > _COVERED_TOLERANCE_DAYS
            and _MIN_ANNOUNCE_LAG_DAYS <= (dd - e).days <= _ANNOUNCE_WINDOW_DAYS
        ]
        if candidates:
            pe = max(candidates)
        else:
            pe = last_end + timedelta(days=_IMPLIED_QUARTER_DAYS)
            if pe in used_periods or not (
                _MIN_ANNOUNCE_LAG_DAYS <= (dd - pe).days <= _ANNOUNCE_WINDOW_DAYS
            ):
                continue
        # An estimate quarter strictly between the newest filed quarter and `pe` that no
        # release has been used for yet has not reported, so this row may be ITS late
        # release: an NT 10-K Q4 at day ~100 while FMP's newest income row is still Q3
        # (Q3 owns its release, so the gate above never runs) rendered Q1 as REPORTED with
        # Q4's numbers, and Q4 vanished. Only evidence moves it (`_late_release_owner` for
        # the nearest such quarter against `pe`): "this" → that quarter; "next" → `pe`;
        # None → no quarter uses it. Deliberately not capped at _LATE_RELEASE_MAX_DAYS: a
        # day-111+ Q4 would otherwise still be pinned onto Q1.
        earlier = [
            e for e in est_dts
            if e not in used_periods
            and (e - newest_end).days > _COVERED_TOLERANCE_DAYS
            and (pe - e).days > _COVERED_TOLERANCE_DAYS
        ]
        if earlier:
            e_prev = max(earlier)
            e_prev_key, pe_key = e_prev.strftime("%Y-%m-%d"), pe.strftime("%Y-%m-%d")
            owner = _late_release_owner(
                rec, e_prev, pe, ec_sorted,
                _estimate_near(e_prev_key, est_by_date), _estimate_near(pe_key, est_by_date),
                False,
            )
            if owner == "this":
                logger.warning(
                    "earnings %s: release %s (%sd after %s) is the late release of %s, "
                    "which has no income row yet — not %s's result (consensus / %s's own "
                    "later release)",
                    ticker, key, (dd - e_prev).days, e_prev_key, e_prev_key, pe_key, pe_key,
                )
                pe = e_prev
            elif owner != "next":
                logger.warning(
                    "earnings %s: release %s is %sd after %s, an earlier quarter with no "
                    "income row and no release yet — it may be that quarter's late "
                    "release or %s's, so it is used by no quarter",
                    ticker, key, (dd - e_prev).days, e_prev_key, pe_key,
                )
                continue
        used_periods.add(pe)
        last_end = max(last_end, pe)
        out.append((pe.strftime("%Y-%m-%d"), rec))
    return out


# ── Forecast labels ────────────────────────────────────────────────

def _forecast_anchor(income_sorted: List[dict]) -> Optional[Tuple[datetime, int, int]]:
    """``(period_end, quarter, fiscal_year)`` of the newest income row carrying FMP's own
    ``fiscalYear`` and a Q1-Q4 period — the label every forecast steps forward from."""
    for rec in reversed(income_sorted):
        period = str(rec.get("period") or "")
        if len(period) != 2 or period[0] != "Q" or period[1] not in "1234":
            continue
        fy_raw = str(rec.get("fiscalYear") or "").strip()[:4]
        if len(fy_raw) != 4 or not fy_raw.isdigit():
            continue
        dt = _parse_day(rec.get("date"))
        if dt is None:
            continue
        return dt, int(period[1]), int(fy_raw)
    return None


def _forecast_label(
    est_date: str,
    anchor: Optional[Tuple[datetime, int, int]],
    fiscal_month_map: Dict[int, str],
) -> str:
    """Label a quarter that has no income row yet (a forecast or a just-reported one).

    Steps forward from the newest HISTORICAL label — n = round(days / 91.3) quarters,
    rolling the fiscal year after Q4 — instead of re-deriving the fiscal year from a
    formula. The formula assumed a fiscal year is named for the calendar year it ENDS
    in, so a start-year-named filer (fiscalYear 2025 for the year ending 2026-02-01)
    jumped a year at the actual→forecast boundary ("Q2 '26" then "Q3 '27"), and a
    December 52/53-week filer whose quarter ends spill into April/October got its
    forecasts a year ahead (and two "Q1 '28" columns). Anchoring follows whatever
    convention FMP's own fiscalYear uses. Falls back to the month-map inference only
    with no anchor, or for a date not at least one quarter past it.
    """
    if anchor is not None:
        dt = _parse_day(est_date)
        if dt is not None:
            d0, q0, fy0 = anchor
            n = round((dt - d0).days / _DAYS_PER_QUARTER)
            if n >= 1:
                idx = (q0 - 1) + n
                return f"Q{idx % 4 + 1} '{(fy0 + idx // 4) % 100:02d}"
    return _infer_fiscal_label(est_date, fiscal_month_map)


# ── Revenue plausibility ───────────────────────────────────────────
# The earnings feed's revenueActual is a SECOND copy of a figure the filed income
# statement also carries, and FMP edits feed rows after the fact. AVGO Q2 FY26 (period
# end 2026-05-03) was served with revenueActual 2,218,700,000 — a dropped digit of the
# filed 22,187,000,000 — against a 22,130,300,000 consensus: a +0.26% beat rendered as a
# -89.97% miss and frozen in both cache tiers. Banks are the reason the filed figure
# cannot simply win: their feed revenue is NET revenue, a different definition from the
# income statement's, and agrees with its own consensus.
_FEED_FILED_DISAGREE = 0.25           # |feed - filed| / filed above this = a disagreement
_MAX_PLAUSIBLE_SURPRISE_PCT = 50.0    # a revenue surprise beyond this needs corroboration
_ESTIMATE_GLITCH_RATIO = 3.0          # feed consensus vs analyst revenueAvg
# Feed / filed revenue outside this band is a unit or digit glitch, not a definition gap:
# a bank's net-vs-gross ratio sits well inside it (a small bank's gross can exceed 2x its
# net), while a dropped or extra digit (0.1x / 10x) does not.
_FEED_FILED_MIN_RATIO = 0.2
_FEED_FILED_MAX_RATIO = 5.0


class _Reconciled(NamedTuple):
    actual: float
    estimate: float
    surprise: Optional[float]
    has_estimate: bool


def _no_comparable_consensus(actual: float) -> _Reconciled:
    # estimate_value is a required float (shipped iOS decodes a non-optional Double), so
    # it repeats the actual; has_estimate=False is what tells iOS there was no consensus.
    return _Reconciled(actual, actual, None, False)


def _rel_gap(value: float, reference: float) -> float:
    return abs(value - reference) / abs(reference) if reference else math.inf


def _is_usd_statement(rec: dict) -> bool:
    """Income rows carry FMP's ``reportedCurrency``; consensus figures are USD. A
    missing currency is treated as USD (the common case for US filers)."""
    return str(rec.get("reportedCurrency") or "").strip().upper() in ("", "USD")


def _reconcile_revenue(
    feed_actual: Optional[float],
    feed_est: Optional[float],
    filed: Optional[float],
    filed_usd: bool,
    analyst_avg: Optional[float],
    *,
    ticker: str,
    fiscal_key: str,
    omit_uncorroborated_outlier: bool = False,
) -> Optional[_Reconciled]:
    """Revenue actual / estimate / surprise for one quarter, or None with no actual (or
    an omitted outlier, below).

    * ``feed_actual`` present and the filed (USD) revenue disagrees by >25%: take
      whichever is CLOSER to the consensus (the AVGO dropped digit loses to the filing;
      a bank's net-revenue feed figure keeps its own consensus). If neither lies within
      50% of the consensus, keep the feed value with no surprise.
    * ``feed_actual`` present with NO feed consensus: a feed/filed ratio outside
      [0.2, 5] is a digit/unit glitch and the filing wins; a smaller (>25%) disagreement
      is decided by the analyst ``revenueAvg`` (closer wins, if within 50%). When the
      filing wins, the quarter is reconciled exactly like one with no feed revenue.
      Before, the feed value returned before any check and a 10x-off figure was charted.
    * ``omit_uncorroborated_outlier`` (the just-reported quarter, no filing yet): a feed
      actual more than 50% off its consensus is OMITTED (None), never charted — and never
      emitted with a null actual, which iOS reads as an upcoming quarter.
    * A surprise beyond ±50% stands only when corroborated: the filed USD revenue agrees
      with the actual AND the feed consensus is not >3x off the analyst ``revenueAvg``
      for the quarter (a dropped digit in the ESTIMATE otherwise shipped as +900%).
    * ``feed_actual`` missing → cross-source: filed revenue against a consensus from
      another feed. Compared only for a USD statement and only within ±50%; a foreign
      filer's TWD/JPY revenue against a USD consensus, or a bank's gross revenue against
      a net-revenue consensus, is not a surprise.
    Every intervention logs a WARNING naming the ticker, quarter and all the numbers.
    """
    if feed_actual is None:
        if filed is None:
            return None
        if feed_est is None:
            return _no_comparable_consensus(filed)
        if not filed_usd:
            logger.warning(
                "earnings revenue %s %s: no surprise — filed revenue %s is not USD, "
                "consensus %s is", ticker, fiscal_key, filed, feed_est,
            )
            return _no_comparable_consensus(filed)
        surprise = _compute_surprise(filed, feed_est)
        if surprise is not None and abs(surprise) > _MAX_PLAUSIBLE_SURPRISE_PCT:
            logger.warning(
                "earnings revenue %s %s: no surprise — filed revenue %s vs a consensus "
                "%s from another feed is %s%% (definition/currency mismatch, not a result)",
                ticker, fiscal_key, filed, feed_est, surprise,
            )
            return _no_comparable_consensus(filed)
        return _Reconciled(filed, feed_est, surprise, True)

    filed_ok = filed is not None and filed_usd and filed > 0
    if feed_est is None:
        if filed_ok and _rel_gap(feed_actual, filed) > _FEED_FILED_DISAGREE:
            ratio = feed_actual / filed
            if not (_FEED_FILED_MIN_RATIO <= ratio <= _FEED_FILED_MAX_RATIO):
                logger.warning(
                    "earnings revenue %s %s: feed revenueActual %s is %.3gx the filed "
                    "revenue %s and the feed has no consensus — a digit/unit glitch; "
                    "using the filing", ticker, fiscal_key, feed_actual, ratio, filed,
                )
                return _reconcile_revenue(
                    None, analyst_avg, filed, filed_usd, analyst_avg,
                    ticker=ticker, fiscal_key=fiscal_key,
                )
            if analyst_avg is not None and analyst_avg > 0:
                gap_filed = _rel_gap(filed, analyst_avg)
                if (
                    gap_filed < _rel_gap(feed_actual, analyst_avg)
                    and gap_filed <= _MAX_PLAUSIBLE_SURPRISE_PCT / 100
                ):
                    logger.warning(
                        "earnings revenue %s %s: feed revenueActual %s disagrees with "
                        "filed revenue %s and the feed has no consensus; the filing is "
                        "closer to the analyst revenueAvg %s — using it",
                        ticker, fiscal_key, feed_actual, filed, analyst_avg,
                    )
                    return _reconcile_revenue(
                        None, analyst_avg, filed, filed_usd, analyst_avg,
                        ticker=ticker, fiscal_key=fiscal_key,
                    )
        return _no_comparable_consensus(feed_actual)

    if filed_ok and _rel_gap(feed_actual, filed) > _FEED_FILED_DISAGREE:
        gap_feed = _rel_gap(feed_actual, feed_est)
        gap_filed = _rel_gap(filed, feed_est)
        if min(gap_feed, gap_filed) > _MAX_PLAUSIBLE_SURPRISE_PCT / 100:
            logger.warning(
                "earnings revenue %s %s: feed %s and filed %s disagree and NEITHER is "
                "within 50%% of consensus %s — keeping the feed value, no surprise",
                ticker, fiscal_key, feed_actual, filed, feed_est,
            )
            return _no_comparable_consensus(feed_actual)
        if gap_filed < gap_feed:
            logger.warning(
                "earnings revenue %s %s: feed revenueActual %s disagrees with filed "
                "revenue %s; the filing is closer to consensus %s — using it",
                ticker, fiscal_key, feed_actual, filed, feed_est,
            )
            return _Reconciled(filed, feed_est, _compute_surprise(filed, feed_est), True)
        # The feed agrees with its own consensus: a definitional difference (a bank's
        # net revenue), not a glitch. INFO, not WARNING — it fires every quarter for
        # every bank and would drown the real interventions.
        logger.info(
            "earnings revenue %s %s: feed %s differs from filed %s but matches consensus "
            "%s — keeping the feed value", ticker, fiscal_key, feed_actual, filed, feed_est,
        )
        return _Reconciled(
            feed_actual, feed_est, _compute_surprise(feed_actual, feed_est), True
        )

    surprise = _compute_surprise(feed_actual, feed_est)
    if surprise is not None and abs(surprise) > _MAX_PLAUSIBLE_SURPRISE_PCT:
        if not filed_ok:
            # The analyst revenueAvg can still corroborate the ACTUAL (the feed's own
            # consensus being the glitched figure): then it is kept, without a surprise.
            corroborated = (
                analyst_avg is not None
                and analyst_avg != 0
                and _rel_gap(feed_actual, analyst_avg) <= _MAX_PLAUSIBLE_SURPRISE_PCT / 100
            )
            if omit_uncorroborated_outlier and not corroborated:
                logger.warning(
                    "earnings revenue %s %s: OMITTED — feed revenueActual %s is %s%% off "
                    "consensus %s and no filing exists yet to corroborate it; not charted "
                    "until the filed figure lands", ticker, fiscal_key, feed_actual,
                    surprise, feed_est,
                )
                return None
            logger.warning(
                "earnings revenue %s %s: no surprise — %s%% (feed %s vs consensus %s) "
                "has no filed USD revenue to corroborate it (filed=%s usd=%s)",
                ticker, fiscal_key, surprise, feed_actual, feed_est, filed, filed_usd,
            )
            return _no_comparable_consensus(feed_actual)
        if analyst_avg is not None and analyst_avg != 0:
            ratio = feed_est / analyst_avg
            if ratio <= 0 or ratio > _ESTIMATE_GLITCH_RATIO or ratio < 1 / _ESTIMATE_GLITCH_RATIO:
                logger.warning(
                    "earnings revenue %s %s: no surprise — feed consensus %s is %.2fx the "
                    "analyst revenueAvg %s (a glitched estimate, not a %s%% result)",
                    ticker, fiscal_key, feed_est, ratio, analyst_avg, surprise,
                )
                return _no_comparable_consensus(feed_actual)
    return _Reconciled(feed_actual, feed_est, surprise, True)


def _quarter(
    label: str,
    actual: Optional[float],
    estimate: float,
    surprise: Optional[float],
    fiscal_key: str,
    has_estimate: bool,
) -> EarningsQuarterSchema:
    return EarningsQuarterSchema(
        quarter=label,
        actual_value=actual,
        estimate_value=estimate,
        surprise_percent=surprise,
        fiscal_date=fiscal_key,
        has_estimate=has_estimate,
    )


def _checked_leg(ticker: str, leg: str, payload: Any, degraded: List[str]) -> List[dict]:
    """A gather leg as a list of dicts. A raised leg or a non-list body is recorded in
    ``degraded`` (the build is then PARTIAL: never persisted, see ``get_earnings``); a
    genuinely EMPTY list — a new listing — is not a failure."""
    if isinstance(payload, BaseException):
        logger.error(
            "earnings %s: %s leg failed — %s: %s; build is DEGRADED",
            ticker, leg, type(payload).__name__, payload,
        )
        degraded.append(leg)
        return []
    if not isinstance(payload, list):
        logger.warning(
            "earnings %s: %s leg returned %s, not a list; build is DEGRADED",
            ticker, leg, type(payload).__name__,
        )
        degraded.append(leg)
        return []
    return [r for r in payload if isinstance(r, dict)]


# ── Service ────────────────────────────────────────────────────────

class EarningsService:
    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()
        self.supabase = get_supabase()

    async def get_earnings(self, ticker: str) -> EarningsResponse:
        """Two-tier cache-aside with in-flight dedup.

        Tier 1: in-memory (5 min) · Tier 2: Supabase ``earnings_cache`` (24h,
        invalidated early by the next earnings date). This is the heaviest
        payload on the Financials tab — a miss downloads SIX YEARS of daily
        closes — so deduping concurrent misses matters more here than anywhere.
        """
        ticker = ticker.upper()
        cache_key = f"earnings:{ticker}"

        # ── Tier 1: in-memory ──
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # ── Tier 2: Supabase ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Earnings Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight dedup ──
        if cache_key in _inflight:
            logger.info(f"Earnings in-flight JOIN for {ticker}")
            # SHIELDED. Awaiting the shared future directly means a joiner that gives up
            # (client disconnect, request timeout) CANCELS THE FUTURE ITSELF — and the leader's
            # `set_result` then raises InvalidStateError, 500ing a request whose data loaded
            # perfectly, while every other joiner gets a CancelledError. Verified: an
            # unshielded joiner cancellation makes the leader's set_result raise; a shielded
            # one leaves it untouched. Matches profit_power_service.py.
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Earnings cache MISS for {ticker} — fetching from FMP")
            result = await self._build_earnings(ticker)

            # This service already knows the next earnings date — reuse it as
            # the cache-invalidation key instead of an extra lookup.
            next_earnings = (
                result.next_earnings_date.date if result.next_earnings_date else None
            )
            if result.degraded:
                # A PARTIAL build (an upstream leg failed) is never persisted: the 24h tier
                # used to freeze it for every user — an income 429 left only estimates, a
                # swallowed earnings-feed 429 turned every quarter into a GAAP-vs-non-GAAP
                # "miss" — and the report collector then baked it into a paid report. A
                # short in-memory entry still absorbs the retry storm.
                logger.warning(
                    "Earnings NOT persisted for %s (degraded: %s) — in-memory for %ss only",
                    ticker, ", ".join(result.degraded), _DEGRADED_CACHE_TTL,
                )
                _cache_set(cache_key, result, ttl=_DEGRADED_CACHE_TTL)
            else:
                loop.run_in_executor(
                    None, self._upsert_supabase_cache_safe, ticker, result, next_earnings
                )
                _cache_set(cache_key, result)
            if not future.done():
                future.set_result(result)
            return result
        except Exception as e:
            fail_shared_future(future, e)
            raise
        finally:
            # CancelledError is a BaseException, not caught above. Resolve with a NORMAL
            # exception rather than `future.cancel()`: a joiner awaiting a cancelled future
            # gets CancelledError, which propagates as task cancellation instead of failing
            # through its own error path. Matches profit_power_service.py.
            if not future.done():
                future.set_exception(RuntimeError("in-flight earnings fetch was cancelled"))
                future.exception()   # mark retrieved; silences the GC warning when unjoined
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ───────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[EarningsResponse]:
        """Return the cached response if fresh (<24h and before next earnings).
        Synchronous — call via asyncio.to_thread()."""
        try:
            row = (
                self.supabase.table("earnings_cache")
                .select("response_json, cached_at, next_earnings_date")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            if cached_at.tzinfo is None:  # defensive: column is timestamptz
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Earnings Supabase cache STALE (age={age}) for {ticker}")
                return None

            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                # `>=`, deliberately: an entry built on report day carries TODAY as its
                # next date, and must keep rebuilding so the after-close actuals appear.
                if today_str >= next_earnings:
                    logger.info(
                        f"Earnings Supabase cache STALE (past earnings "
                        f"{next_earnings}) for {ticker}"
                    )
                    return None

            json_data = entry.get("response_json") or {}
            # A VERSION, so a computation fix evicts rows cached before the deploy (see
            # _EARNINGS_PAYLOAD_VERSION). A row from before versioning has none.
            version = json_data.get("payload_version")
            if version != _EARNINGS_PAYLOAD_VERSION:
                logger.info(
                    "Earnings Supabase cache STALE for %s (payload_version=%r, want %d) "
                    "— rebuilding", ticker, version, _EARNINGS_PAYLOAD_VERSION,
                )
                return None
            if json_data.get("degraded"):
                # Never written by this code (degraded builds are not persisted); a row
                # that carries reasons anyway must not be served for 24h.
                logger.warning(
                    "Earnings Supabase row for %s is marked degraded %s — ignoring it",
                    ticker, json_data.get("degraded"),
                )
                return None
            # Not a response field; strip it so the model never depends on extras being
            # ignored.
            json_data = {k: v for k, v in json_data.items() if k != "payload_version"}
            return EarningsResponse(**json_data)
        except Exception as e:
            logger.warning(f"Earnings Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: EarningsResponse,
        next_earnings: Optional[str],
    ) -> None:
        """Write-through to the Supabase tier. Best-effort: logged, never fatal."""
        if result.degraded:
            # get_earnings already routes a degraded build away from here; a second
            # guard at the writer, because a frozen partial build is the bug this
            # whole gate exists to stop.
            logger.warning(
                "Earnings upsert REFUSED for %s — degraded build (%s)",
                ticker, ", ".join(result.degraded),
            )
            return
        try:
            self.supabase.table("earnings_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": {
                        **result.model_dump(),
                        "payload_version": _EARNINGS_PAYLOAD_VERSION,
                    },
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Earnings Supabase upsert failed for {ticker}: {e}")

    async def _build_earnings(self, ticker: str) -> EarningsResponse:
        # UTC, matching the cache freshness check in _check_supabase_cache (which
        # compares next_earnings_date against a UTC "today"). Identical on
        # Railway, but keeps the write path and read path on one clock.
        today = datetime.now(timezone.utc).date()
        # 6 years (was 4) so the Earnings Timeline's oldest actual year (annual
        # income reaches back ~5 yrs) gets price coverage. The daily series is
        # still clipped to the oldest income date below, and the TickerDetail
        # earnings chart clips to its displayed quarters, so the extra history
        # is unused there.
        six_years_ago = (today - timedelta(days=6 * 365)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        # Phase 1: Fetch income statements, analyst estimates, and prices in parallel
        income_raw, estimates_raw, prices_raw = await asyncio.gather(
            self.fmp.get_income_statement(ticker, period="quarter", limit=20),
            self.fmp.get_analyst_estimates(ticker, period="quarter", limit=20),
            self.fmp.get_historical_prices(ticker, from_date=six_years_ago, to_date=today_str),
            return_exceptions=True,
        )

        # Which legs failed. A failed leg is replaced by an empty one so the section
        # still renders, but the build is then PARTIAL — `get_earnings` must not freeze it
        # into the 24h tier (or let a report freeze it). A genuinely empty list is not a
        # failure.
        degraded: List[str] = []
        income_raw = _checked_leg(ticker, "income", income_raw, degraded)
        estimates_raw = _checked_leg(ticker, "estimates", estimates_raw, degraded)

        # Sort income statements chronologically (oldest first).
        # `(r.get("period") or "")` — a present-but-null period would otherwise
        # raise AttributeError on .startswith.
        income_sorted = sorted(
            [
                r for r in income_raw
                if r.get("date") and (r.get("period") or "").startswith("Q")
            ],
            key=lambda r: str(r["date"]),
        )

        # Phase 2: Earnings announcements (past + upcoming) for this symbol — ONE call.
        #
        # The per-symbol /stable/earnings?symbol=X endpoint returns ALL of a ticker's
        # announcements, historical AND upcoming, with paired epsActual/epsEstimated and
        # revenueActual/revenueEstimated. It's the authoritative source: no date-window
        # blind spot (a fiscal-Q4 whose 10-K is filed >10 days after the release is still
        # included — Oracle FY26 Q4: announced 06-10, 10-K accepted 06-22) and no
        # 4000-record cap risk, so every reported quarter gets its apples-to-apples
        # non-GAAP actual vs estimate, and the upcoming date feeds next_earnings_date.
        #
        # This REPLACES the old per-quarter global `earnings-calendar` fan-out
        # (_fetch_earnings_calendar_for_symbol = ~2 windows/quarter + 5 forward windows),
        # which downloaded the ENTIRE market's calendar (up to 4000 companies per call)
        # and discarded all but this ticker — that single path was ~99% of the app's FMP
        # bandwidth (4.3 GB/mo, ~10k calls). One per-symbol call (~KB) carries the same
        # data; next_earnings_date still falls back to analyst-estimates if a future
        # announcement isn't listed yet.
        #
        # raise_errors=True: the default swallows a 429/5xx into `[]`, which reads as "no
        # announcements" — every quarter then fell to the GAAP-vs-non-GAAP fallback and
        # that build was cached for 24h.
        try:
            full_ec = await self.fmp.get_earning_calendar_full(ticker, raise_errors=True)
        except Exception as e:
            logger.warning(
                "earnings %s: per-symbol earnings feed failed — %s: %s; build is DEGRADED",
                ticker, type(e).__name__, e,
                exc_info=not isinstance(e, FMPException),
            )
            degraded.append("earnings_feed")
            full_ec = []
        if not isinstance(full_ec, list):
            logger.warning(
                "earnings %s: per-symbol earnings feed returned %s, not a list; build is "
                "DEGRADED", ticker, type(full_ec).__name__,
            )
            degraded.append("earnings_feed")
            full_ec = []
        ec_records = [r for r in full_ec if isinstance(r, dict)]

        # Earnings-calendar rows keyed by report date. On a same-date duplicate keep the
        # row that carries a reported actual — keeping whichever FMP listed LAST let a
        # pending twin replace the real result (a fake GAAP-vs-non-GAAP miss).
        ec_by_date: Dict[str, dict] = {}
        for rec in ec_records:
            d = _row_key(rec)
            if _parse_day(d) is None:
                continue
            prior = ec_by_date.get(d)
            if prior is None or (has_reported_actual(rec) and not has_reported_actual(prior)):
                ec_by_date[d] = rec
        # Announcements ascending by date → pair each income quarter with its
        # announcement (see _assign_announcements / _match_announcement).
        ec_sorted = sorted(ec_by_date.values(), key=_row_key)

        # Build price lookup: date → close
        # FMP returns either a list or a dict with "historical" key
        if isinstance(prices_raw, BaseException):
            logger.error(
                "earnings %s: prices leg failed — %s: %s; build is DEGRADED",
                ticker, type(prices_raw).__name__, prices_raw,
            )
            degraded.append("prices")
            price_list: List[Any] = []
        elif isinstance(prices_raw, dict) and isinstance(prices_raw.get("historical"), list):
            price_list = prices_raw["historical"]
        elif isinstance(prices_raw, list):
            price_list = prices_raw
        else:
            logger.warning(
                "earnings %s: prices leg returned %s; build is DEGRADED",
                ticker, type(prices_raw).__name__,
            )
            degraded.append("prices")
            price_list = []
        # A non-dict row used to reach `p.get(...)` → AttributeError → a bare 502 for the
        # whole Earnings section.
        malformed_prices = sum(1 for p in price_list if not isinstance(p, dict))
        if malformed_prices:
            logger.warning(
                "earnings %s: skipping %d non-dict price rows", ticker, malformed_prices,
            )
        price_list = [p for p in price_list if isinstance(p, dict)]

        price_lookup: Dict[str, float] = {}
        for p in price_list:
            d = p.get("date")
            c = p.get("close")
            if d and c is not None:
                try:
                    fc = float(c)
                    if math.isfinite(fc):  # a NaN/Inf close -> REQUIRED price float -> 500
                        price_lookup[str(d)[:10]] = fc
                except (ValueError, TypeError):
                    pass

        logger.info(f"Price lookup has {len(price_lookup)} entries for {ticker}")

        # Sort estimates chronologically (oldest first)
        estimates_sorted = sorted(
            [e for e in estimates_raw if _parse_day(e.get("date")) is not None],
            key=_row_key,
        )

        # Build estimate lookup by date for matching with income statements
        est_by_date: Dict[str, dict] = {}
        for est in estimates_sorted:
            est_by_date[_row_key(est)] = est

        # ── Build merged quarterly data ──
        # Phase A : historical quarters from income-statement, enriched by earnings-calendar
        # Phase A2: reported quarters FMP has no income row for yet
        # Phase B : future quarters from analyst-estimates

        eps_quarters: List[EarningsQuarterSchema] = []
        revenue_quarters: List[EarningsQuarterSchema] = []
        price_history: List[EarningsPricePointSchema] = []
        used_fiscal_dates: set = set()

        fiscal_month_map = _build_fiscal_quarter_map(income_sorted)
        forecast_anchor = _forecast_anchor(income_sorted)

        # Pair every income quarter with its announcement, one-to-one (see
        # _assign_announcements for the late-Q4 and reschedule failure modes).
        assignments = _assign_announcements(
            income_sorted, ec_sorted, est_by_date, ticker=ticker,
        )
        consumed = {_row_key(r) for r in assignments.values()}
        # Reported release date → the fiscal period end it reported (Phase A2 adds its
        # own below). Only the next-date card reads it: a pending row this evidence
        # proves is a reschedule's leftover is not the next report (_reschedule_leftovers).
        release_periods: Dict[str, str] = {
            _row_key(r): pe for pe, r in assignments.items() if has_reported_actual(r)
        }
        # This ticker's own release lag (period end → announcement), for projecting a
        # next date when the feed lists none.
        announce_lags: List[int] = []
        for pe_key, rec in assignments.items():
            pe_dt, ann_dt = _parse_day(pe_key), _parse_day(rec.get("date"))
            if pe_dt is not None and ann_dt is not None and has_reported_actual(rec):
                announce_lags.append((ann_dt - pe_dt).days)

        # ── Phase A: Historical quarters ──
        for rec in income_sorted:
            fiscal_date = rec["date"]
            # Fiscal-year display label ("Q4 '26") — uses FMP's fiscalYear so
            # off-calendar-FY companies (Oracle FY ends May) read monotonically
            # instead of scrambling fiscal Q1/Q2 to the prior calendar year.
            label = quarterly_period_label(rec, use_fiscal_year=True)
            fiscal_key = str(fiscal_date)[:10]

            # This quarter's announcement (adjusted / non-GAAP actual + estimate), paired
            # by fiscal PERIOD-END — robust to when the 10-Q/10-K is filed; see
            # _match_announcement for the Oracle (lagging Q4 10-K) and Disney (very late
            # 10-K) failure modes that filing-date matching got wrong.
            ec_match = assignments.get(fiscal_key)
            matched_est = self._find_matching_estimate(fiscal_key, est_by_date)
            analyst_rev_avg = _safe_float(matched_est, "revenueAvg") if matched_est else None
            filed_rev = _safe_float(rec, "revenue")
            filed_usd = _is_usd_statement(rec)
            gaap_eps = _first_not_none(_safe_float(rec, "epsDiluted"), _safe_float(rec, "eps"))

            if ec_match:
                # earnings-calendar has properly paired adjusted actual/estimate data
                ec_eps_actual = _safe_float(ec_match, "epsActual")
                ec_eps_estimate = _safe_float(ec_match, "epsEstimated")
                ec_rev_actual = _safe_float(ec_match, "revenueActual")
                ec_rev_estimate = _safe_float(ec_match, "revenueEstimated")

                # EPS: use earnings-calendar values (adjusted, non-GAAP) — unless the
                # actual has the dropped/added-digit signature (`eps_digit_shift_suspect`:
                # ~10x off its estimate, and the filed GAAP EPS — when known — sides with
                # the estimate). Then the feed's actual is not trusted and the EPS point is
                # OMITTED. Never the GAAP figure: it was plotted inside the "Adjusted EPS"
                # series, so AVGO's GAAP 1.03 between adjusted ~1.6s read as a ~35% EPS
                # collapse. The report's Track Record applies the same shared rule, so the
                # two never disagree.
                if (
                    ec_eps_actual is not None and ec_eps_estimate is not None
                    and eps_digit_shift_suspect(ec_eps_actual, ec_eps_estimate, gaap_eps)
                ):
                    logger.warning(
                        "earnings EPS digit-shift suspect for %s %s — feed actual %s vs "
                        "estimate %s (filed GAAP %s): EPS point omitted",
                        ticker, fiscal_key, ec_eps_actual, ec_eps_estimate, gaap_eps,
                    )
                elif ec_eps_actual is not None and ec_eps_estimate is not None:
                    eps_quarters.append(_quarter(
                        label, ec_eps_actual, ec_eps_estimate,
                        _compute_surprise(ec_eps_actual, ec_eps_estimate), fiscal_key, True,
                    ))
                elif ec_eps_actual is not None:
                    # Has actual but no estimate — show actual without surprise
                    eps_quarters.append(_quarter(
                        label, ec_eps_actual, ec_eps_actual, None, fiscal_key, False,
                    ))
                elif gaap_eps is not None:
                    # Matched announcement lacks a usable EPS actual (a not-yet-reported
                    # placeholder, or an FMP gap). DON'T silently drop the quarter's EPS —
                    # show the income-statement GAAP epsDiluted, the SAME degrade path as
                    # the no-match branch. Without this, a matched-but-null-actual record
                    # consumed the quarter and its EPS bar vanished. The only estimate on
                    # hand is the NON-GAAP epsAvg, so there is NO surprise: GAAP 1.03 vs
                    # adjusted 1.57 read as a -34% "miss" for AVGO, counted in the report's
                    # beat/miss record and averaged into its narrative.
                    est_eps = _safe_float(matched_est, "epsAvg") if matched_est else None
                    if est_eps is not None:
                        logger.warning(
                            "earnings EPS DEGRADED to GAAP epsDiluted for %s %s — matched "
                            "announcement had null epsActual; no surprise against the "
                            "non-GAAP epsAvg (actual=%s est=%s)",
                            ticker, fiscal_key, gaap_eps, est_eps,
                        )
                    eps_quarters.append(_quarter(
                        label, gaap_eps, gaap_eps, None, fiscal_key, False,
                    ))

                revenue = _reconcile_revenue(
                    ec_rev_actual, ec_rev_estimate, filed_rev, filed_usd, analyst_rev_avg,
                    ticker=ticker, fiscal_key=fiscal_key,
                )
            else:
                # No earnings-calendar match — fall back to income-statement + analyst-estimates
                if gaap_eps is not None:
                    est_eps = _safe_float(matched_est, "epsAvg") if matched_est else None
                    if est_eps is not None:
                        # DEGRADED path: no announcement in the per-symbol feed for this
                        # quarter, so the only actual is the income statement's GAAP
                        # epsDiluted and the only estimate the NON-GAAP analyst epsAvg —
                        # the apples-to-oranges comparison the announcement matching
                        # avoids. Show the actual, compute NO surprise, and log it loudly
                        # so it's greppable in prod. See _match_announcement.
                        logger.warning(
                            "earnings surprise DEGRADED to GAAP epsDiluted for %s %s — no "
                            "announcement in per-symbol feed; no surprise against the "
                            "non-GAAP epsAvg (actual=%s est=%s)",
                            ticker, fiscal_key, gaap_eps, est_eps,
                        )
                    eps_quarters.append(_quarter(
                        label, gaap_eps, gaap_eps, None, fiscal_key, False,
                    ))

                revenue = _reconcile_revenue(
                    None, analyst_rev_avg, filed_rev, filed_usd, analyst_rev_avg,
                    ticker=ticker, fiscal_key=fiscal_key,
                )

            if revenue is not None:
                revenue_quarters.append(_quarter(
                    label, revenue.actual, revenue.estimate, revenue.surprise,
                    fiscal_key, revenue.has_estimate,
                ))

            used_fiscal_dates.add(fiscal_key)
            self._append_price_point(ticker, label, fiscal_key, price_lookup, price_history)

        # ── Phase A2: reported quarters with no income row yet ──
        if income_sorted:
            newest_key = _row_key(income_sorted[-1])
            newest_end = _parse_day(newest_key)
            # Owned only by a REPORTED row: a quarter paired with a pending placeholder may
            # still have its real (late) release among the unconsumed rows.
            newest_owned = has_reported_actual(assignments.get(newest_key) or {})
            synthesized = (
                _pair_unconsumed_announcements(
                    ec_sorted, consumed, newest_end,
                    [_row_key(e) for e in estimates_sorted], today_str,
                    newest_owned=newest_owned, est_by_date=est_by_date, ticker=ticker,
                )
                if newest_end is not None else []
            )
            for pe_key, rec in synthesized:
                label = _forecast_label(pe_key, forecast_anchor, fiscal_month_map)
                est_row = self._find_matching_estimate(pe_key, est_by_date)
                eps_actual = _safe_float(rec, "epsActual")
                eps_est = _first_not_none(
                    _safe_float(rec, "epsEstimated"),
                    _safe_float(est_row, "epsAvg") if est_row else None,
                )
                # The same digit-shift gate as Phase A (no filing exists yet, so the
                # signature alone decides): a dropped-digit actual (0.169 vs 1.70) shipped
                # as a "-90% miss" on the newest quarter while the report hid it. The EPS
                # point is OMITTED until the filing lands; revenue and price stay, and
                # `used_fiscal_dates` below keeps Phase B from re-adding it as pending.
                if (
                    eps_actual is not None and eps_est is not None
                    and eps_digit_shift_suspect(eps_actual, eps_est, None)
                ):
                    logger.warning(
                        "earnings EPS digit-shift suspect for %s %s (just reported %s, no "
                        "filing yet) — feed actual %s vs estimate %s: EPS point omitted "
                        "until the filing lands",
                        ticker, pe_key, _row_key(rec), eps_actual, eps_est,
                    )
                elif eps_actual is not None:
                    if eps_est is not None:
                        eps_quarters.append(_quarter(
                            label, eps_actual, eps_est,
                            _compute_surprise(eps_actual, eps_est), pe_key, True,
                        ))
                    else:
                        eps_quarters.append(_quarter(
                            label, eps_actual, eps_actual, None, pe_key, False,
                        ))
                # No filed revenue exists yet to corroborate the feed, so a revenue more
                # than 50% off consensus is OMITTED until the 10-Q lands (Phase A then
                # reconciles it against the filing) — a kept value without a surprise
                # still charted the AVGO dropped digit and fed it to the report.
                revenue = _reconcile_revenue(
                    _safe_float(rec, "revenueActual"),
                    _first_not_none(
                        _safe_float(rec, "revenueEstimated"),
                        _safe_float(est_row, "revenueAvg") if est_row else None,
                    ),
                    None, True,
                    _safe_float(est_row, "revenueAvg") if est_row else None,
                    ticker=ticker, fiscal_key=pe_key, omit_uncorroborated_outlier=True,
                )
                if revenue is not None:
                    revenue_quarters.append(_quarter(
                        label, revenue.actual, revenue.estimate, revenue.surprise,
                        pe_key, revenue.has_estimate,
                    ))
                logger.info(
                    "earnings %s: %s (%s) reported %s, before its income row — built from "
                    "the announcement", ticker, label, pe_key, _row_key(rec),
                )
                ann_dt, pe_dt = _parse_day(rec.get("date")), _parse_day(pe_key)
                if ann_dt is not None and pe_dt is not None:
                    announce_lags.append((ann_dt - pe_dt).days)
                consumed.add(_row_key(rec))
                release_periods[_row_key(rec)] = pe_key
                used_fiscal_dates.add(pe_key)
                self._append_price_point(ticker, label, pe_key, price_lookup, price_history)

        # The newest period already reported. Phase B used to emit EVERY estimate not
        # within 15 days of an income date, so when the income call failed (or the
        # history had a hole) quarters ended long ago rendered as gray "upcoming" dots.
        # Cut on the last REPORTED period, never on today: a quarter that has ended but
        # not yet reported (Q3 ends 09-30, reports late October) is genuinely upcoming.
        if used_fiscal_dates:
            reported_through = max(used_fiscal_dates)
        else:
            reported_releases = [
                _row_key(r) for r in ec_sorted
                if has_reported_actual(r) and _row_key(r) <= today_str
            ]
            reported_through = (
                max(reported_releases) if reported_releases
                else (today - timedelta(days=_UNREPORTED_GRACE_DAYS)).strftime("%Y-%m-%d")
            )

        # ── Phase B: Future quarters from analyst-estimates ──
        for est in estimates_sorted:
            est_key = _row_key(est)
            est_eps = _safe_float(est, "epsAvg")
            est_rev = _safe_float(est, "revenueAvg")

            if est_eps is None and est_rev is None:
                continue
            if est_key <= reported_through:
                continue

            # Skip if this estimate matches an already-processed income quarter
            est_dt = _parse_day(est_key)
            if any(
                abs((est_dt - fd).days) <= _COVERED_TOLERANCE_DAYS
                for fd in (_parse_day(d) for d in used_fiscal_dates)
                if fd is not None
            ):
                continue

            # Future quarter — no actuals
            label = _forecast_label(est_key, forecast_anchor, fiscal_month_map)

            if est_eps is not None:
                eps_quarters.append(_quarter(label, None, est_eps, None, est_key, True))
            if est_rev is not None:
                revenue_quarters.append(_quarter(label, None, est_rev, None, est_key, True))

        # Sort everything by actual fiscal date (correct for all fiscal year types)
        eps_quarters.sort(key=lambda q: q.fiscal_date or "9999-99-99")
        revenue_quarters.sort(key=lambda q: q.fiscal_date or "9999-99-99")
        price_history.sort(key=lambda p: p.fiscal_date or "9999-99-99")

        # ── Daily Price History (continuous line data) ──
        daily_price_history: List[EarningsDailyPriceSchema] = []
        # Use ALL income dates to determine the price range
        all_income_dates = {str(r["date"])[:10] for r in income_sorted if r.get("date")}
        if all_income_dates and price_list:
            sorted_dates = sorted(all_income_dates)
            range_start = sorted_dates[0]
            range_end = today_str
            for p in price_list:
                d = str(p.get("date") or "")[:10]
                c = p.get("close")
                if d and c is not None and range_start <= d <= range_end:
                    try:
                        fc = float(c)
                    except (ValueError, TypeError):
                        continue
                    # A NaN/Inf close reaches the REQUIRED `price: float` and
                    # Starlette serializes with allow_nan=False -> ValueError ->
                    # 500 for the whole Earnings section. The price_lookup loop
                    # above already guards this; this loop did not.
                    if not math.isfinite(fc):
                        logger.warning(
                            "earnings %s: non-finite close %r on %s — skipping daily point",
                            ticker, c, d,
                        )
                        continue
                    daily_price_history.append(
                        EarningsDailyPriceSchema(date=d, price=fc)
                    )
            daily_price_history.sort(key=lambda x: x.date)

        # ── Next Earnings Date ──
        next_earnings = self._find_next_earnings_date(
            estimates_sorted, ec_records, used_fiscal_dates, today_str,
            reported_through=reported_through,
            announce_lag_days=(
                int(round(statistics.median(announce_lags))) if announce_lags else None
            ),
            release_periods=release_periods,
            ticker=ticker,
        )

        logger.info(
            f"Earnings for {ticker}: {len(eps_quarters)} EPS quarters, "
            f"{len(revenue_quarters)} rev quarters, {len(price_history)} price points, "
            f"next={'yes' if next_earnings else 'no'}, degraded={degraded or 'no'}"
        )

        return EarningsResponse(
            symbol=ticker,
            eps_quarters=eps_quarters,
            revenue_quarters=revenue_quarters,
            price_history=price_history,
            daily_price_history=daily_price_history,
            next_earnings_date=next_earnings,
            degraded=degraded,
        )

    @staticmethod
    def _append_price_point(
        ticker: str,
        label: str,
        fiscal_key: str,
        price_lookup: Dict[str, float],
        price_history: List[EarningsPricePointSchema],
    ) -> None:
        """Close on the quarter's period end. OMIT the point when no close is within
        ±5 days rather than emitting a fabricated 0: a 0 is a real price on the wire, it
        entered the chart's Y domain and dragged the whole price line to the floor. iOS
        matches price points to quarters by LABEL (not by position), so a missing entry
        is handled; a fake 0 was not."""
        close_price = _find_close_price(fiscal_key, price_lookup)
        if close_price is None:
            logger.warning(
                "earnings %s: no close price within +-5d of %s — omitting price point",
                ticker, fiscal_key,
            )
            return
        price_history.append(EarningsPricePointSchema(
            quarter=label,
            price=close_price,
            fiscal_date=fiscal_key,
        ))

    def _find_matching_estimate(
        self, fiscal_date: str, est_by_date: Dict[str, dict], tolerance_days: int = 15
    ) -> Optional[dict]:
        """Find analyst-estimate record matching a fiscal date."""
        return _estimate_near(fiscal_date, est_by_date, tolerance_days)

    def _find_next_earnings_date(
        self,
        estimates_sorted: List[dict],
        ec_records: List[dict],
        used_fiscal_dates: set,
        today_str: str,
        reported_through: Optional[str] = None,
        announce_lag_days: Optional[int] = None,
        *,
        release_periods: Optional[Dict[str, str]] = None,
        ticker: str = "",
    ) -> Optional[NextEarningsDateSchema]:
        """Find the next earnings date.

        Prefers FMP's earnings-calendar (confirmed date + timing), through the shared
        ``next_pending_earnings`` rule: TODAY's pending report counts (it used to be
        skipped, so on report day the card read next quarter's date as "Confirmed"),
        and a stale reschedule row is skipped when a later pending row exists (a lone one
        is kept but shown unconfirmed). Uses the shared timing parser so the returned
        ``timing`` matches what the alert card shows for the same event.

        Before that rule runs, a pending row PROVEN to be a reschedule's leftover is
        dropped (``_reschedule_leftovers``): ``release_periods`` (reported release date →
        the fiscal period end the pairing gave it) shows it sits within
        ``STALE_RESCHEDULE_DAYS`` after that release and before the next period end +
        ``_MIN_ANNOUNCE_LAG_DAYS``, so it is no later quarter's release. A lone leftover
        used to come back as "Expected <leftover>" — and as the earnings_cache key — and
        the projection below never ran. Only the unreported row is dropped (a reported
        twin on the same date stays). Without ``release_periods`` (the default) nothing
        is dropped: a suspect row with no period evidence keeps the shared rule.

        Fallback (no pending row in the feed): an analyst estimate is dated at the
        fiscal PERIOD END, not the release, so the projection is period end + this
        ticker's median announcement lag, unconfirmed. It used to return the bare period
        end — ~6 weeks early for AVGO — and once that period ended it jumped a whole
        quarter ahead past the pending report. An ended-but-unreported quarter is
        therefore still a candidate, and only a STRICTLY future projection is returned:
        a past/today date would read stale on the card and invalidate the Supabase row
        on every read. With no lag history there is no honest projection → None. After a
        dropped leftover the projection is always later than it (next period end + a lag
        of at least ``_MIN_ANNOUNCE_LAG_DAYS``), or None — never the leftover.
        """
        ec_rows = ec_records
        leftovers = _reschedule_leftover_evidence(
            ec_records,
            release_periods,
            list(used_fiscal_dates or ())
            + [_row_key(e) for e in estimates_sorted if isinstance(e, dict)],
            today_str,
        )
        if leftovers:
            for left, (released, period, next_period) in sorted(leftovers.items()):
                logger.warning(
                    "earnings %s step=next_date: pending row %s is a reschedule leftover — "
                    "period %s already reported on %s (%sd before it), and no later "
                    "quarter (next period end %s) releases before %sd past its end; "
                    "dropped from the next date",
                    ticker, left, period, released,
                    (_parse_day(left) - _parse_day(released)).days,
                    next_period, _MIN_ANNOUNCE_LAG_DAYS,
                )
            ec_rows = [
                r for r in ec_records
                if not (
                    isinstance(r, dict)
                    and not has_reported_actual(r)
                    and _row_key(r) in leftovers
                )
            ]

        pending = next_pending_earnings(ec_rows, today_str)
        if pending is not None:
            # A lone pending row dated just after a reported one that the pairing could
            # NOT prove a leftover (above) is returned rather than dropped, but it may
            # still be one — so it is shown, never as "Confirmed".
            return NextEarningsDateSchema(
                date=_row_key(pending),
                is_confirmed=not within_reschedule_window(pending, ec_rows),
                timing=timing_display(parse_fmp_timing(pending.get("time"))),
            )

        if announce_lag_days is None:
            return None
        lag = max(_MIN_ANNOUNCE_LAG_DAYS, min(_LATE_RELEASE_MAX_DAYS, announce_lag_days))
        used = [d for d in (_parse_day(k) for k in used_fiscal_dates) if d is not None]
        for est in estimates_sorted:
            est_key = _row_key(est)
            est_dt = _parse_day(est_key)
            if est_dt is None:
                continue
            if reported_through and est_key <= reported_through:
                continue
            if any(abs((est_dt - u).days) <= _COVERED_TOLERANCE_DAYS for u in used):
                continue
            projected = (est_dt + timedelta(days=lag)).strftime("%Y-%m-%d")
            if projected > today_str:
                return NextEarningsDateSchema(
                    date=projected,
                    is_confirmed=False,
                    timing=timing_display(UNSPECIFIED),
                )
        return None


# ── Singleton ──────────────────────────────────────────────────────
_earnings_service: Optional[EarningsService] = None


def get_earnings_service() -> EarningsService:
    global _earnings_service
    if _earnings_service is None:
        _earnings_service = EarningsService()
    return _earnings_service
