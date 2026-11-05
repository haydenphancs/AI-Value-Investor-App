"""Live earnings results — the report-day state behind the Tracking card's LIVE label, its
results sheet and the live push (owner request 2026-10-29, on INTC's report day).

WHAT IT ANSWERS. For one (symbol, report date): is the report upcoming, live, reported or
awaiting, and where do EPS and revenue stand against consensus SO FAR — each metric on its
own, because whichever lands first is shown first ("if revenue comes out, show it right away").

TWO SOURCES, ONE RECORD PER (symbol, report date).
  * FMP `/stable/earnings-calendar`. The consensus, and eventually the actuals. Its EPS
    actual is the adjusted figure, the consensus's own basis (`earnings_service.py` header).
    FMP fills the actuals ~1–3 h after a release and an after-close report can wait until
    the next morning (`earnings_window_service._HOT_REFRESH_SECONDS`), so on its own it is
    near-live at best.
  * The company's own 8-K Item 2.02 press release (`earnings_filing_service`): minutes after
    the release, every figure a verbatim quote. PRELIMINARY until FMP has the same metric.
  A record is STICKY: a figure, once verified, never goes back to "not out yet", and FMP's
  figure replaces the filing's the moment FMP has one.
  Between two FMP answers the NEWEST wins. The Tracking memo can be 30 min behind this
  module's own fetch, so a memo row older than the record's last own fetch is skipped whole at
  all three folds (the tick, the card, the sheet): no reader can put an older answer back. A
  memo refreshed after the fetch folds as usual: a report FMP has confirmed is no longer
  fetched, so the memo is how a later FMP correction reaches it.

DUPLICATE ROWS. FMP can list one report twice, a stale pending row beside the reported one,
in either order. One response therefore decides per report, never per row, and a repeat of it
decides the same again (every tick and feed build folds the same rows). Each metric's actual
comes from one row with that row's own estimate, a pending twin supplies neither, and the
record takes one landing decision. An estimate moves only with its own actual, or while that
metric has no FMP actual, so a surprise is never worked out against another row's consensus.
The card and the sheet read the same pick (`one_row_per_report`).
  A row left out still counts as evidence: the consensus every row states is read before
anything folds (`_note_consensus`) and kept GUARD-ONLY. The sanity guards, the zero rules and
"is a revenue still due" read it; a surprise, the card and the sheet never do.

THE MEMO. The live days (`live_days`): every calendar day from the previous trading day
through today, one single-day calendar call each — a multi-day request is cut at 4,000 rows
with the NEWEST dates kept (`fetch_calendar_days`). Calendar days, because a report can carry
a weekend or holiday date. This one window holds the records, the push sender's memory and
every reader, and each refresh prunes to it first: a report the sender has forgotten must
already be gone from the records, or it reads as new on every round.
  * Today's rows ride on the Tracking memo (`tracking_service._cached_earnings_calendar`,
    30 min, the owner's 2026-10-22 cadence), and every reader folds them in. The tick pays to
    keep that memo fresh only from 06:00 ET to midnight on a trading day
    (`tick_refreshes_memo`): before 06:00 and on a closed day it folds the rows already held
    and makes no call, unless none are held (a restart). A feed build still refreshes the memo
    at any hour. The evening's rows for tomorrow are what make an overnight fill a landing at
    the first read of the morning. This module fetches today ITSELF only while it is HOT.
  * The days before today are held only here (after-close actuals land overnight, and the
    card must still be there the next morning).
  * HOT = inside `fmp_window_open` (06:00–22:00 ET on the day, 06:00–12:00 ET on the next
    trading day), and a WATCHED report on that day still wants FMP (`needs_fmp`): it lacks
    results, or a figure on show is still the filing's. So a filing ends the SEC watch
    (`pending_symbols`) but not this: FMP is what confirms it, and on the next morning this
    fetch is the only way FMP's answer arrives. A weekend morning is not hot, and a closed day
    is never hot after its own day. Hot days refresh every `EARNINGS_LIVE_HOT_TTL_SECONDS`
    (5 min). Any other day before today is asked once, then, while a watched report on it
    still wants FMP, every 30 min, or every 2 h while today is a closed day
    (`EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS`). 5 min and not faster: the filing path supplies
    the speed, FMP is the confirmation.
  * Cost with nobody opening the app, tick by tick (pinned in the tests). A busy report day,
    today's and the previous trading day's watched reports wanting FMP all day: 411 calls,
    192 for today, 108 for the previous day and 111 for the memo. A filing that completes a
    report costs what a pending row costs until FMP confirms it. A weekend day: 12, plus a
    first look at each day before it never asked, and no memo call. A watched report dated on
    a closed day adds up to 192 on that day. Feed builds add the memo's rounds the tick no
    longer pays for: ~37 before 06:00 on a trading day and up to ~150 on a closed day.
  * Refreshed by the live loop ONLY (`run_earnings_live_loop`, 60 s). Feed builds and the
    sheet endpoint fold the Tracking memo and read; they never fetch, so the live state adds
    no latency to the feed.

STATUSES (no time of day exists on `/stable` rows, so the window is the report day):
  * `upcoming` — a later date, or today before 06:00 ET with nothing in yet.
  * `live`     — today, 06:00–22:00 ET, results not complete. LIVE is drawn ONLY then, and
                 it is true: SEC is checked every minute in the release windows and FMP
                 every 5 min.
  * `reported` — complete (EPS, plus revenue when a revenue estimate exists, shown or
                 guard-only), or the live window ended with at least one figure in.
  * `awaiting` — the window ended with nothing in. "Results not in yet" — never "late", a
                 status `earnings_window_service` deliberately does not have (FMP lag and
                 reschedule duplicates make it misleading). An awaiting card stays up until
                 12:00 ET on the first trading day after its report, and drops once.
  A symbol draws ONE card from the days before today (`newest_report`): the newest reported
  report, else the newest one still up. A company does not report twice in a few days, so a
  pending row beside a reported one is a leftover and never hides the figures. The sheet
  opened with no date takes the same pick, and still answers a report whose card has dropped.

THE SHEET'S POLL (`sheet_poll_seconds`, `poll_seconds` on the wire). 30 s while `live`. 120 s
while `reported` with a preliminary figure on show, inside `fmp_window_open`: FMP's fill is what
replaces that figure, and the loop asks at the hot cadence then. None otherwise, past those hours
too: the record moves too rarely to poll, and the next open reads it. The two rules ship as one,
or the sheet would poll a record nothing updates.

GUARDS, each a defect found elsewhere in this repo:
  * a dropped-digit EPS (`_earnings_common.eps_digit_shift_suspect`) is held as "checking",
    never shown as a -90% miss;
  * a revenue more than 2x off a material consensus is held unless the other source agrees
    within 2% (FMP's dropped-digit revenue, `earnings_service._reconcile_revenue`);
  * a GAAP-only EPS is shown labelled GAAP with no beat/miss — the consensus is adjusted (the
    owner's 2026-09-27 caution, `news_insight_service._earnings_line`);
  * no surprise percentage against an estimate under $0.01 (`earnings_sender.MIN_ABS_ESTIMATE`);
  * a zero revenue from FMP is stored only against a consensus under $100M, with no figure
    held (a pre-revenue name can report nothing): beside a material consensus, or none, it
    is an unfilled field, never a -100% miss and never a landing;
  * a row with an EPS of exactly 0 beside a zero or negative revenue is unfilled as a whole,
    unless that revenue stands and no EPS is held. Beside a real revenue, or with no revenue
    on the row, 0 is a real breakeven.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Collection,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
)

from app.config import settings
from app.services._earnings_common import eps_digit_shift_suspect
from app.services.earnings_filing_service import FilingFigures
from app.services.earnings_window_service import _row_date, _row_symbol, fetch_calendar_days
from app.services.updates_materiality import finite
from app.utils.market_hours import ET, is_trading_day, previous_trading_day

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────

EARNINGS_LIVE_HOT_TTL_SECONDS = 300
EARNINGS_LIVE_COLD_TTL_SECONDS = 1800
#: The days before today while TODAY is a closed day. A late fill is still worth picking up,
#: but a row FMP never fills must not run the market-wide fetch through a weekend.
EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS = 7200
#: How often the sheet asks again: while live, and while FMP can still confirm what a reported
#: report shows (`sheet_poll_seconds`).
EARNINGS_LIVE_POLL_SECONDS = 30
EARNINGS_LIVE_PRELIMINARY_POLL_SECONDS = 120
#: The live window on the report day, in ET hours: [start, end).
EARNINGS_LIVE_START_HOUR_ET = 6
EARNINGS_LIVE_END_HOUR_ET = 22
#: The previous trading day stays hot until this ET hour, and an `awaiting` card stays up until
#: this ET hour on the first trading day after its report.
EARNINGS_LIVE_PREV_DAY_UNTIL_HOUR_ET = 12

#: Below this |estimate| no surprise percentage is honest (`earnings_sender.MIN_ABS_ESTIMATE`:
#: a $0.001 estimate against a $0.40 actual is a 39,900% "surprise").
MIN_ABS_EPS_ESTIMATE = 0.01
#: A revenue estimate this large is material enough for the plausibility guard. Smaller
#: (pre-revenue) companies routinely land far from a tiny consensus, and holding their
#: figure would hide a real result.
_REVENUE_GUARD_MIN_ESTIMATE = 100e6
_REVENUE_PLAUSIBLE = (0.5, 2.0)
_SOURCES_AGREE = 0.02
#: A filing accepted longer than this before its record was first seen was already out at
#: first sight, like an FMP row first seen reported. Long enough to cover a deploy gap, so a
#: deploy inside the release window keeps the landing it can still announce.
_FILING_LANDING_GRACE = timedelta(minutes=15)

_FETCH_DEADLINE_SECONDS = 20.0
_FAILURE_RETRY_SECONDS = 120.0
#: History extras (last 4 quarters, the same quarter last year) per (symbol, report date).
_EXTRAS_TTL_SECONDS = 6 * 3600
_EXTRAS_FAILURE_TTL_SECONDS = 300
#: After a TIMEOUT (the build is still running, shielded) the next request may try again soon.
_EXTRAS_SLOW_RETRY_SECONDS = 20
_EXTRAS_DEADLINE_SECONDS = 12.0
_EXTRAS_MAX = 256
#: A fiscal quarter that ended this close to the report date is the one being reported.
_CURRENT_QUARTER_MAX_DAYS = 100
_YEAR_AGO_TOLERANCE_DAYS = 25

STATUS_UPCOMING = "upcoming"
STATUS_LIVE = "live"
STATUS_REPORTED = "reported"
STATUS_AWAITING = "awaiting"
#: The sheet asked about a ticker with no report in the window (a reschedule moved it out).
STATUS_NONE = "none"

SOURCE_FMP = "fmp"
SOURCE_FILING = "filing"
BASIS_ADJUSTED = "adjusted"
BASIS_GAAP = "gaap"


# ── Data ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LiveMetric:
    """One metric of one report, as shown. `actual` is None while pending OR held."""

    estimate: Optional[float]
    actual: Optional[float] = None
    surprise_pct: Optional[float] = None
    source: Optional[str] = None
    basis: Optional[str] = None
    # Held by a sanity guard: a figure exists but is not shown until it is confirmed.
    checking: bool = False
    # No figure yet, and a consensus, shown or guard-only, says one is coming.
    due: bool = False


@dataclass
class _Record:
    symbol: str
    day: date
    company_name: Optional[str] = None
    eps_estimate: Optional[float] = None
    revenue_estimate: Optional[float] = None
    # GUARD-ONLY (`_note_consensus`): what the latest response stated, from ANY of its rows.
    eps_guard_estimate: Optional[float] = None
    revenue_guard_estimate: Optional[float] = None
    fmp_eps: Optional[float] = None
    fmp_revenue: Optional[float] = None
    filing: Optional[FilingFigures] = None
    checked_at: Optional[datetime] = None
    # Monotonic time of the last own fetch that held this report. Per record, not per day: a
    # ticker that fetch left out keeps folding the memo. None until a fetch has held it.
    fmp_fetched_mono: Optional[float] = None
    # When this process first held the record: read against a filing's acceptance time.
    first_seen_at: Optional[datetime] = None
    # Observed WITHOUT results in this process — what makes a later landing a landing.
    seen_pending: bool = False
    landed_at: Optional[datetime] = None
    landed_from_pending: bool = False
    # The fold runs on every tick and feed build: a refusal is logged once per record.
    revenue_refusal_logged: bool = False
    eps_refusal_logged: bool = False


@dataclass(frozen=True)
class LiveSnapshot:
    symbol: str
    day: date
    status: str
    company_name: Optional[str]
    eps: LiveMetric
    revenue: LiveMetric
    preliminary: bool
    checked_at: Optional[datetime]
    filing: Optional[FilingFigures]
    # How often the sheet should ask again (`sheet_poll_seconds`); None when it should not.
    poll_seconds: Optional[int] = None


@dataclass(frozen=True)
class QuarterOutcome:
    quarter: str
    result: str                       # beat | missed | matched | no_estimate
    surprise_pct: Optional[float]


@dataclass(frozen=True)
class HistoryExtras:
    last_quarters: Tuple[QuarterOutcome, ...]      # oldest first, at most 4
    year_ago_quarter: Optional[str]
    year_ago_revenue: Optional[float]
    year_ago_eps: Optional[float]
    year_ago_eps_comparable: bool


# ── Pure helpers ────────────────────────────────────────────────────


def et_now(now: datetime) -> datetime:
    """``now`` in ET. A naive value is read as UTC (the same rule as `et_date`)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET)


def live_days(today: date) -> List[date]:
    """The one window of the live state: every calendar day from the previous trading day
    through ``today``, oldest first (THE MEMO in the module docstring). Never just those two
    days: `previous_trading_day` steps over a weekend or holiday, and a report dated on one
    would drop out the day after, while the sheet still accepts its date."""
    first = previous_trading_day(today)
    return [first + timedelta(days=i) for i in range((today - first).days + 1)]


def eps_surprise_pct(actual: Optional[float], estimate: Optional[float]) -> Optional[float]:
    """(a - e) / |e| as a percent (2 dp), or None when there is no honest one."""
    if actual is None or estimate is None or abs(estimate) < MIN_ABS_EPS_ESTIMATE:
        return None
    return round((actual - estimate) / abs(estimate) * 100.0, 2)


def revenue_surprise_pct(actual: Optional[float], estimate: Optional[float]) -> Optional[float]:
    if actual is None or estimate is None or estimate <= 0:
        return None
    return round((actual - estimate) / estimate * 100.0, 2)


def eps_metric(rec: _Record) -> LiveMetric:
    """FMP first (adjusted, the consensus's basis), then the filing's adjusted EPS, then the
    filing's GAAP EPS labelled GAAP and never coloured. With no shown estimate the guard reads
    the guard-only one; the surprise and the metric's `estimate` never do."""
    est = rec.eps_estimate
    guard_est = est if est is not None else rec.eps_guard_estimate
    filing = rec.filing
    gaap = filing.eps_gaap if filing is not None else None
    candidates: List[Tuple[float, str, str]] = []
    if rec.fmp_eps is not None:
        candidates.append((rec.fmp_eps, SOURCE_FMP, BASIS_ADJUSTED))
    if filing is not None and filing.eps_adjusted is not None:
        candidates.append((filing.eps_adjusted, SOURCE_FILING, BASIS_ADJUSTED))
    held = False
    for value, source, basis in candidates:
        if eps_digit_shift_suspect(value, guard_est, gaap):
            held = True
            continue
        return LiveMetric(est, value, eps_surprise_pct(value, est), source, basis)
    if held:
        return LiveMetric(est, checking=True)
    if gaap is not None:
        return LiveMetric(est, gaap, None, SOURCE_FILING, BASIS_GAAP)
    return LiveMetric(est)


def revenue_metric(rec: _Record) -> LiveMetric:
    """FMP first, then the filing. Like `eps_metric`, the plausibility guard falls back to the
    guard-only estimate; the surprise and the metric's `estimate` never do."""
    est = rec.revenue_estimate
    guard_est = est if est is not None else rec.revenue_guard_estimate
    fmp = rec.fmp_revenue
    filed = rec.filing.revenue if rec.filing is not None else None
    if fmp is not None:
        value, source, other = fmp, SOURCE_FMP, filed
    elif filed is not None:
        value, source, other = filed, SOURCE_FILING, None
    else:
        return LiveMetric(est, due=guard_est is not None)
    if guard_est is not None and guard_est >= _REVENUE_GUARD_MIN_ESTIMATE and value > 0:
        lo, hi = _REVENUE_PLAUSIBLE
        if not lo <= value / guard_est <= hi:
            agrees = other is not None and abs(other - value) <= _SOURCES_AGREE * value
            if not agrees:
                return LiveMetric(est, checking=True)
    return LiveMetric(est, value, revenue_surprise_pct(value, est), source)


def has_results(eps: LiveMetric, revenue: LiveMetric) -> bool:
    return eps.actual is not None or revenue.actual is not None


def is_complete(eps: LiveMetric, revenue: LiveMetric) -> bool:
    """EPS is in, and so is revenue unless none is coming: any consensus says one is, shown or
    guard-only, and a held figure is not in yet."""
    if eps.actual is None:
        return False
    if revenue.actual is not None:
        return True
    return revenue.estimate is None and not revenue.due and not revenue.checking


def is_preliminary(eps: LiveMetric, revenue: LiveMetric) -> bool:
    """Is a figure on show the filing's, not yet replaced by FMP's?"""
    return SOURCE_FILING in (eps.source, revenue.source)


def needs_fmp(eps: LiveMetric, revenue: LiveMetric) -> bool:
    """Does the loop still want FMP's answer on this report? Until it is complete, and after
    that while a figure on show is the filing's: only FMP's own figure confirms it, colours a
    GAAP-only EPS and sends the live result push.

    Never "FMP's fields are empty". That would ask forever about a report with no revenue
    consensus, and never ask again about a held figure, whose field is full while its
    correction is what the loop is waiting for.
    """
    return not is_complete(eps, revenue) or is_preliminary(eps, revenue)


def fmp_window_open(day: date, n: datetime) -> bool:
    """PURE. Are these the hours the loop asks FMP about `day`'s reports at the hot cadence, if
    one of them still wants it, at `n` (ET)? 06:00–22:00 ET on the day itself, a closed day
    included (a report can carry one's date), and 06:00–12:00 ET on the next trading day, when
    an after-close report fills. A weekend morning is neither, and a closed day's report is
    never hot again after its own day."""
    if not EARNINGS_LIVE_START_HOUR_ET <= n.hour < EARNINGS_LIVE_END_HOUR_ET:
        return False
    today = n.date()
    if day == today:
        return True
    return (is_trading_day(today) and day == previous_trading_day(today)
            and n.hour < EARNINGS_LIVE_PREV_DAY_UNTIL_HOUR_ET)


def tick_refreshes_memo(n: datetime) -> bool:
    """PURE. Are these the hours the tick pays to keep the Tracking memo fresh, at `n` (ET)?
    06:00 ET to midnight on a trading day. Past 22:00 the hot fetch has stopped and the memo is
    what still reads today's late fills. At any other hour the tick folds what the memo holds
    (THE MEMO in the module docstring)."""
    return is_trading_day(n.date()) and n.hour >= EARNINGS_LIVE_START_HOUR_ET


def live_status(day: date, today: date, now_et: datetime, eps: LiveMetric,
                revenue: LiveMetric) -> str:
    """PURE. The status of a report dated `day`, seen at `now_et` (see the module docstring)."""
    if day > today:
        return STATUS_UPCOMING
    any_in = has_results(eps, revenue)
    if day == today:
        if is_complete(eps, revenue):
            return STATUS_REPORTED
        if now_et.hour < EARNINGS_LIVE_START_HOUR_ET:
            return STATUS_LIVE if any_in else STATUS_UPCOMING
        if now_et.hour >= EARNINGS_LIVE_END_HOUR_ET:
            return STATUS_REPORTED if any_in else STATUS_AWAITING
        return STATUS_LIVE
    return STATUS_REPORTED if any_in else STATUS_AWAITING


def sheet_poll_seconds(status: str, preliminary: bool, day: date, n: datetime) -> Optional[int]:
    """PURE. How often the sheet should ask again, or None when one answer is the whole story
    (THE SHEET'S POLL in the module docstring)."""
    if status == STATUS_LIVE:
        return EARNINGS_LIVE_POLL_SECONDS
    if status == STATUS_REPORTED and preliminary and fmp_window_open(day, n):
        return EARNINGS_LIVE_PRELIMINARY_POLL_SECONDS
    return None


def recent_card_visible(status: str, day: date, now_et: datetime) -> bool:
    """A card from before today, for a report dated `day`. Reported stays. Awaiting stays until
    12:00 ET on the first trading day after `day`, so it drops once: a Friday card is up all
    weekend, and a card from the day before a holiday all through it."""
    if status == STATUS_REPORTED:
        return True
    if status != STATUS_AWAITING:
        return False
    today = now_et.date()
    if previous_trading_day(today) > day:
        return False
    return not is_trading_day(today) or now_et.hour < EARNINGS_LIVE_PREV_DAY_UNTIL_HOUR_ET


def snapshot_of(rec: _Record, today: date, now_et: datetime) -> LiveSnapshot:
    eps = eps_metric(rec)
    revenue = revenue_metric(rec)
    preliminary = is_preliminary(eps, revenue)
    status = live_status(rec.day, today, now_et, eps, revenue)
    return LiveSnapshot(
        symbol=rec.symbol, day=rec.day, status=status,
        company_name=rec.company_name, eps=eps, revenue=revenue,
        preliminary=preliminary, checked_at=rec.checked_at, filing=rec.filing,
        poll_seconds=sheet_poll_seconds(status, preliminary, rec.day, now_et),
    )


def newest_report(snaps: Iterable[LiveSnapshot]) -> Optional[LiveSnapshot]:
    """PURE. Which of one symbol's reports from before today it shows, given in any order: the
    newest reported one, else the newest (see STATUSES in the module docstring)."""
    return max(snaps, key=lambda s: (s.status == STATUS_REPORTED, s.day), default=None)


def iso_utc(when: Optional[datetime]) -> Optional[str]:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def card_fields(snap: Optional[LiveSnapshot], report_day: date) -> Dict[str, Any]:
    """`AlertResponse` keyword fields for one earnings card. A future card carries only its
    status and date; a card with no record yet reads as upcoming (or live on its day)."""
    fields: Dict[str, Any] = {"report_date": report_day.isoformat()}
    if snap is None:
        fields["earnings_status"] = STATUS_UPCOMING
        return fields
    fields.update(
        earnings_status=snap.status,
        eps_actual=snap.eps.actual,
        revenue_actual=snap.revenue.actual,
        eps_surprise_pct=snap.eps.surprise_pct,
        revenue_surprise_pct=snap.revenue.surprise_pct,
        eps_basis=snap.eps.basis,
        results_preliminary=snap.preliminary if has_results(snap.eps, snap.revenue) else None,
        results_checked_at=iso_utc(snap.checked_at),
    )
    return fields


def _parse_day(value: Any) -> Optional[date]:
    raw = str(value or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _quarter_result(actual: Optional[float], estimate: Optional[float],
                    has_estimate: Optional[bool]) -> Optional[str]:
    if actual is None:
        return None
    if has_estimate is False or estimate is None:
        return "no_estimate"
    if actual > estimate:
        return "beat"
    if actual < estimate:
        return "missed"
    return "matched"


def history_extras_from(response: Any, report_day: date) -> HistoryExtras:
    """PURE. Last 4 reported quarters and the same quarter a year earlier, from the Financials
    tab's own `EarningsResponse` (so the sheet and the tab can never disagree about a quarter).

    The quarter being reported is the latest whose fiscal end is within
    `_CURRENT_QUARTER_MAX_DAYS` of the report date (10-Q / 10-K deadlines are 40–90 days);
    it is excluded whether or not FMP has filled it yet.
    """
    eps_q = list(getattr(response, "eps_quarters", None) or [])
    rev_q = list(getattr(response, "revenue_quarters", None) or [])

    def _dated(rows: Iterable[Any]) -> List[Tuple[date, Any]]:
        out = []
        for q in rows:
            d = _parse_day(getattr(q, "fiscal_date", None))
            if d is not None and d <= report_day:
                out.append((d, q))
        out.sort(key=lambda t: t[0])
        return out

    eps_dated = _dated(eps_q)
    current_end: Optional[date] = None
    if eps_dated and (report_day - eps_dated[-1][0]).days <= _CURRENT_QUARTER_MAX_DAYS:
        current_end = eps_dated[-1][0]
    previous = [(d, q) for d, q in eps_dated if current_end is None or d < current_end]
    outcomes: List[QuarterOutcome] = []
    for d, q in previous:
        actual = finite(getattr(q, "actual_value", None))
        result = _quarter_result(actual, finite(getattr(q, "estimate_value", None)),
                                 getattr(q, "has_estimate", None))
        if result is None:
            continue
        pct = finite(getattr(q, "surprise_percent", None)) if result != "no_estimate" else None
        outcomes.append(QuarterOutcome(str(getattr(q, "quarter", "") or d.isoformat()), result, pct))
    last4 = tuple(outcomes[-4:])

    target = (current_end or (report_day - timedelta(days=35))) - timedelta(days=365)

    def _closest(rows: List[Tuple[date, Any]]) -> Optional[Any]:
        best = None
        for d, q in rows:
            gap = abs((d - target).days)
            if gap <= _YEAR_AGO_TOLERANCE_DAYS and (best is None or gap < best[0]):
                best = (gap, q)
        return best[1] if best else None

    ya_eps_row = _closest(previous)
    ya_rev_row = _closest([(d, q) for d, q in _dated(rev_q) if current_end is None or d < current_end])
    ya_eps = finite(getattr(ya_eps_row, "actual_value", None)) if ya_eps_row is not None else None
    ya_rev = finite(getattr(ya_rev_row, "actual_value", None)) if ya_rev_row is not None else None
    label_row = ya_eps_row if ya_eps_row is not None else ya_rev_row
    ya_label = None
    if label_row is not None:
        ya_label = str(getattr(label_row, "quarter", "") or "") or None
    return HistoryExtras(
        last_quarters=last4,
        year_ago_quarter=ya_label,
        year_ago_revenue=ya_rev if ya_rev is not None and ya_rev > 0 else None,
        year_ago_eps=ya_eps,
        # `has_estimate` False on the year-ago row means its EPS fell to GAAP; comparing that
        # to an adjusted actual is the GAAP-vs-non-GAAP fake swing the owner ruled out.
        year_ago_eps_comparable=bool(ya_eps_row is not None
                                     and getattr(ya_eps_row, "has_estimate", True) is not False),
    )


def yoy_growth_pct(actual: Optional[float], year_ago: Optional[float]) -> Optional[float]:
    """Percent change vs the year-ago figure, only when both are positive (a sign change or a
    loss base has no meaningful percentage)."""
    if actual is None or year_ago is None or actual <= 0 or year_ago <= 0:
        return None
    return round((actual - year_ago) / year_ago * 100.0, 2)


def _usable_actuals(row: Dict[str, Any]) -> int:
    """How many results a calendar row carries: a finite EPS (0.0 is a real breakeven) and a
    positive revenue. Not `row_reported`: a zero or negative revenue must not rank a row as
    the reported one, nor an EPS of 0.0 beside it, the unfilled shape. Whether those are
    results is `_storable_figures`'s call; a row with a real figure outranks them either
    way, so the pick needs no consensus."""
    eps = finite(row.get("epsActual"))
    revenue = finite(row.get("revenueActual"))
    if revenue is not None and revenue <= 0:
        return int(eps is not None and eps != 0)
    return int(eps is not None) + int(revenue is not None)


def _row_rank(row: Dict[str, Any], today: date) -> Tuple[int, str]:
    """How strongly a row speaks for its report: results first, then FMP's own `lastUpdated`.
    Actuals on a row dated after today are garbage (`earnings_window_service._absorb`), so
    they rank as nothing, and a re-touched pending twin never beats a reported row."""
    day = _row_date(row)
    usable = _usable_actuals(row) if day is not None and day <= today else 0
    return usable, str(row.get("lastUpdated") or "")


def _ranked_rows(group: List[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
    """One report's rows, weakest first. The best row is last, and a tie goes to the first
    listed, the same pick as `one_row_per_report`."""
    return sorted(reversed(group), key=lambda row: _row_rank(row, today))


def _rows_to_fold(ranked: List[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
    """The ranked rows that speak for the report. When any row carries results the others are
    dropped; all of those are kept, because two reported rows can split EPS and revenue."""
    reported = [row for row in ranked if _row_rank(row, today)[0] > 0]
    return reported or ranked


def one_row_per_report(
    rows: Iterable[Any], today: date, *, symbols: Optional[Collection[str]] = None,
) -> Dict[Tuple[str, Optional[date]], Dict[str, Any]]:
    """PURE. The one row that speaks for each (symbol, date), first-seen order kept, so a
    timestamp date merges with its plain twin and a reschedule's two dates stay two reports.

    For the readers that show ONE row: the card and the sheet. Non-dict and symbol-less rows
    are skipped, and ``symbols`` narrows the answer to those tickers. A tie keeps the first
    listed row.
    """
    wanted = {s.upper() for s in symbols} if symbols is not None else None
    best: Dict[Tuple[str, Optional[date]], Dict[str, Any]] = {}
    for row in rows:
        symbol = _row_symbol(row)
        if not symbol or (wanted is not None and symbol not in wanted):
            continue
        key = (symbol, _row_date(row))
        held = best.get(key)
        if held is None or _row_rank(row, today) > _row_rank(held, today):
            best[key] = row
    return best


def _eps_estimate(row: Dict[str, Any]) -> Optional[float]:
    return finite(row.get("epsEstimated"))


def _revenue_estimate(row: Dict[str, Any]) -> Optional[float]:
    """A row's revenue consensus. Zero or negative is no consensus at all."""
    value = finite(row.get("revenueEstimated"))
    return value if value is not None and value > 0 else None


def _note_consensus(rec: _Record, ranked: List[Dict[str, Any]]) -> None:
    """Keep what one response says the consensus is, from ALL its rows and before anything
    folds, so a row left out of the fold still counts and every fold of the response judges
    alike. EPS takes the best row's. Revenue takes the largest: any material consensus in the
    response means a real business, whichever row FMP touched last, and a zero beside it is
    unfilled. A response stating none keeps the last one."""
    eps = [value for value in map(_eps_estimate, ranked) if value is not None]
    revenue = [value for value in map(_revenue_estimate, ranked) if value is not None]
    if eps:
        rec.eps_guard_estimate = eps[-1]
    if revenue:
        rec.revenue_guard_estimate = max(revenue)


def _storable_revenue(actual: Optional[float], held: Optional[float],
                      consensus: Optional[float]) -> Optional[float]:
    """The FMP revenue a row may store, or None. A negative one is junk. A zero is a result
    only with no positive figure held (records are sticky) and a consensus under the
    plausibility guard's floor; beside a material consensus, or with none to judge it by, it
    is an unfilled field, and storing it would read as a -100% miss and a landing."""
    if actual is None or actual > 0:
        return actual
    if actual == 0 and not held and consensus is not None \
            and consensus < _REVENUE_GUARD_MIN_ESTIMATE:
        return 0.0
    return None


def _storable_figures(eps: Optional[float], revenue: Optional[float],
                      held_eps: Optional[float], held_revenue: Optional[float],
                      consensus: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """What one FMP row may store, as (EPS, revenue), each None when refused. An EPS of 0.0 is
    a real breakeven, except beside a zero or negative revenue: that row is FMP's unfilled
    shape, unless the revenue stands by itself and no EPS is held, so it stores nothing.
    Storing it would read as a -100% miss and a landing, or take a real EPS back."""
    stored = _storable_revenue(revenue, held_revenue, consensus)
    if eps == 0 and revenue is not None and revenue <= 0 and (stored is None or held_eps):
        return None, None
    return eps, stored


def _older_than_own_fetch(rec: _Record, stored_at: Optional[float]) -> bool:
    """Is a memo row stored at ``stored_at`` older than the record's last own fetch? A row of
    unknown age counts as older: it must never beat an answer of known age."""
    last = rec.fmp_fetched_mono
    return last is not None and (stored_at is None or stored_at < last)


def _settled(actual: Optional[float], estimate: Optional[float],
             pick: Optional[Tuple[Dict[str, Any], float]], rows: List[Dict[str, Any]],
             estimate_of: Callable[[Dict[str, Any]], Optional[float]],
             ) -> Tuple[Optional[float], Optional[float]]:
    """One metric's (actual, estimate) after one response. A picked figure brings its own
    row's estimate and no other row's, so a row's place in the fold cannot cross a consensus.
    With no figure in the response and none held, the best row's estimate moves."""
    if pick is not None:
        row, value = pick
        own = estimate_of(row)
        return value, own if own is not None else estimate
    if actual is None:
        for row in reversed(rows):
            own = estimate_of(row)
            if own is not None:
                return None, own
    return actual, estimate


# ── The service ─────────────────────────────────────────────────────


class EarningsLiveService:
    """Per-process live state (the deploy runs ONE uvicorn worker, so it is coherent)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Forget everything (tests)."""
        self._records: Dict[Tuple[str, date], _Record] = {}
        # Own single-day fetches: day -> monotonic time fetched.
        self._fetched: Dict[date, float] = {}
        self._failed_until: float = 0.0
        self._extras: Dict[Tuple[str, date], Tuple[float, Optional[HistoryExtras]]] = {}
        # Keyed by (round kind, ET date) — the same shape as every other `_inflight` map, so
        # tests/test_inflight_cancellation_safety.py recognises the join below.
        self._inflight: Dict[Tuple[str, date], asyncio.Future] = {}
        self.fetches = 0

    # ── folding rows ───────────────────────────────────────────────

    def observe_rows(self, day: date, rows: Iterable[Any], now: datetime,
                     *, symbols: Optional[Collection[str]] = None,
                     fetched: bool = False,
                     stored_at: Optional[float] = None) -> None:
        """Fold FMP calendar rows dated `day` into their records. Sticky (module docstring).

        Each symbol's rows decide TOGETHER (`_rows_to_fold`, DUPLICATE ROWS in the module
        docstring), with one `_note_landing` per symbol: a pending row beside a reported one
        in the same response is "reported", not a landing, as in
        `earnings_window_service._absorb`, whichever FMP listed first. Their consensus is read
        first, so a repeat of the response decides what this fold decides.

        ``symbols`` narrows the fold to those tickers (a feed build passes its user's), so a
        reader never pays for the whole market's rows.

        ``fetched`` is True only for this module's OWN fetch, the one moment the sources were
        actually checked. Rows folded in from the Tracking memo (up to 30 min old) must not move
        `checked_at`, or the sheet would say "updated just now" over a half-hour-old answer.

        ``stored_at`` is when the memo stored these rows, on the clock of `_fetched`
        (`tracking_service.earnings_day_stored_at`); an own fetch ignores it. The newest FMP
        answer wins (module docstring): memo rows older than their record's last own fetch are
        skipped before anything folds, and so are rows of unknown age.
        """
        wanted = {s.upper() for s in symbols} if symbols is not None else None
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            symbol = _row_symbol(row)
            if not symbol or (wanted is not None and symbol not in wanted):
                continue
            if _row_date(row) == day:
                groups.setdefault(symbol, []).append(row)
        today = et_now(now).date()
        for symbol, group in groups.items():
            rec = self._records.get((symbol, day))
            if rec is None:
                rec = _Record(symbol=symbol, day=day, first_seen_at=now)
                self._records[(symbol, day)] = rec
            elif not fetched and _older_than_own_fetch(rec, stored_at):
                continue
            ranked = _ranked_rows(group, today)
            _note_consensus(rec, ranked)
            self._fold_report(rec, _rows_to_fold(ranked, today), now, fetched=fetched)
            self._note_landing(rec, now)

    def _fold_report(self, rec: _Record, rows: List[Dict[str, Any]], now: datetime,
                     *, fetched: bool) -> None:
        """One response's rows for one report, weakest first, into its record: each metric's
        figure is the strongest row's that may be stored (`_settled` settles its estimate).
        The surprise behind a figure keeps the figure's consensus through a later pending row,
        and the landing is the caller's, once per report. An own fetch also stamps the record,
        which is the line later memo rows must clear (`observe_rows`)."""
        eps_pick: Optional[Tuple[Dict[str, Any], float]] = None
        revenue_pick: Optional[Tuple[Dict[str, Any], float]] = None
        held_revenue = rec.fmp_revenue
        for row in rows:
            reported_eps = finite(row.get("epsActual"))
            reported_revenue = finite(row.get("revenueActual"))
            eps, revenue = _storable_figures(reported_eps, reported_revenue, rec.fmp_eps,
                                             held_revenue, rec.revenue_guard_estimate)
            if revenue is not None:
                revenue_pick, held_revenue = (row, revenue), revenue
            elif reported_revenue is not None and not rec.revenue_refusal_logged:
                rec.revenue_refusal_logged = True
                logger.warning(
                    "[earnings-live] %s %s: FMP revenue %s refused (consensus %s, holding %s)",
                    rec.symbol, rec.day.isoformat(), reported_revenue,
                    rec.revenue_guard_estimate, held_revenue,
                )
            if eps is not None:
                eps_pick = (row, eps)
            elif reported_eps is not None and not rec.eps_refusal_logged:
                rec.eps_refusal_logged = True
                logger.warning(
                    "[earnings-live] %s %s: FMP EPS %s refused beside revenue %s (holding %s)",
                    rec.symbol, rec.day.isoformat(), reported_eps, reported_revenue, rec.fmp_eps,
                )
            name = row.get("companyName")
            if isinstance(name, str) and name.strip():
                rec.company_name = name.strip()
        rec.fmp_eps, rec.eps_estimate = _settled(
            rec.fmp_eps, rec.eps_estimate, eps_pick, rows, _eps_estimate)
        rec.fmp_revenue, rec.revenue_estimate = _settled(
            rec.fmp_revenue, rec.revenue_estimate, revenue_pick, rows, _revenue_estimate)
        if fetched:
            rec.fmp_fetched_mono = time.monotonic()
            if rec.checked_at is None or now > rec.checked_at:
                rec.checked_at = now

    def _note_landing(self, rec: _Record, now: datetime) -> None:
        if has_results(eps_metric(rec), revenue_metric(rec)):
            if rec.landed_at is None:
                rec.landed_at = now
                rec.landed_from_pending = rec.seen_pending
        else:
            rec.seen_pending = True

    def absorb_filing(self, symbol: str, day: date, figures: FilingFigures,
                      now: datetime) -> None:
        """The company's own release for (symbol, day). The first verified one is kept.

        A release accepted more than `_FILING_LANDING_GRACE` before the record was first seen
        was already out at first sight, whatever FMP's row still says. `seen_pending` is
        cleared rather than the landing skipped, so `_note_landing` closes it for good: no
        later fold or FMP fill can reopen it, the end state of a row first seen reported.
        """
        rec = self._records.get((symbol.upper(), day))
        if rec is None:
            rec = _Record(symbol=symbol.upper(), day=day, first_seen_at=now)
            self._records[(rec.symbol, day)] = rec
            # A record born from a filing was certainly pending a moment ago: the filing
            # watcher only runs for symbols the loop saw without results.
            rec.seen_pending = True
        if rec.filing is None:
            rec.filing = figures
        if rec.checked_at is None or now > rec.checked_at:
            rec.checked_at = now
        accepted, seen = figures.accepted_at, rec.first_seen_at
        if (accepted is not None and seen is not None
                and et_now(seen) - et_now(accepted) > _FILING_LANDING_GRACE):
            rec.seen_pending = False
            logger.info(
                "[earnings-live] %s %s: filing accepted %s was already out at first sight (%s)",
                rec.symbol, day.isoformat(), iso_utc(accepted), iso_utc(seen),
            )
        self._note_landing(rec, now)

    # ── reading ────────────────────────────────────────────────────

    def snapshot(self, symbol: str, day: date, now: datetime) -> Optional[LiveSnapshot]:
        rec = self._records.get((symbol.upper(), day))
        if rec is None:
            return None
        n = et_now(now)
        return snapshot_of(rec, n.date(), n)

    def recent_snapshots(self, symbols: Collection[str], now: datetime) -> List[LiveSnapshot]:
        """The cards from before today worth showing (`recent_card_visible`), ONE per symbol
        (`newest_report`). iOS ids a card by ticker and date, so a Friday and a Saturday report
        would otherwise be two cards."""
        n = et_now(now)
        earlier = live_days(n.date())[:-1]
        out = []
        for symbol in sorted({s.upper() for s in symbols}):
            held = (self.snapshot(symbol, day, n) for day in earlier)
            card = newest_report(s for s in held if s is not None
                                 and recent_card_visible(s.status, s.day, n))
            if card is not None:
                out.append(card)
        return out

    def landed_snapshots(self, now: datetime) -> List[LiveSnapshot]:
        """Every record of the live days whose results were SEEN landing in this process, as of
        now, the whole market's: the tick narrows them to the watched list before the sender.
        The push sender keeps its own once-per-key bookkeeping, over the same days.

        A record already reported when first seen (a restart, a first fetch after the fact)
        is not a landing — the same rule as `earnings_window_service._absorb` — so a deploy
        never re-announces a morning's results in the afternoon. The same goes for a filing
        accepted more than `_FILING_LANDING_GRACE` before the record was first seen, even
        though FMP's row still reads pending (`absorb_filing`).
        """
        n = et_now(now)
        today = n.date()
        days = set(live_days(today))
        return [
            snapshot_of(rec, today, n)
            for rec in self._records.values()
            if rec.day in days and rec.landed_at is not None and rec.landed_from_pending
        ]

    def pending_symbols(self, day: date, symbols: Iterable[str]) -> List[str]:
        """The given symbols (order kept) with a record on `day` whose results are incomplete.
        This is the SEC watch's list: a filing ends that watch. FMP's fetch reads the wider
        `_fmp_wanted`."""
        out = []
        for symbol in symbols:
            rec = self._records.get((symbol.upper(), day))
            if rec is not None and not is_complete(eps_metric(rec), revenue_metric(rec)):
                out.append(symbol.upper())
        return out

    # ── refresh (the live loop only) ───────────────────────────────

    def _fmp_wanted(self, day: date, symbols: Collection[str]) -> bool:
        """Does a watched report on `day` still want FMP's answer (`needs_fmp`)?"""
        for symbol in symbols:
            rec = self._records.get((symbol.upper(), day))
            if rec is not None and needs_fmp(eps_metric(rec), revenue_metric(rec)):
                return True
        return False

    def _is_hot(self, day: date, n: datetime, symbols: Collection[str]) -> bool:
        return fmp_window_open(day, n) and self._fmp_wanted(day, symbols)

    def due_days(self, now: datetime, symbols: Collection[str]) -> List[date]:
        """The days this round must fetch, oldest first (see THE MEMO in the docstring)."""
        n = et_now(now)
        *earlier, today = live_days(n.date())
        mono = time.monotonic()
        cold_ttl = (EARNINGS_LIVE_COLD_TTL_SECONDS if is_trading_day(today)
                    else EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS)
        due = []
        for day in earlier:
            last = self._fetched.get(day)
            if last is None:
                due.append(day)
            elif self._is_hot(day, n, symbols):
                if mono - last >= EARNINGS_LIVE_HOT_TTL_SECONDS:
                    due.append(day)
            elif self._fmp_wanted(day, symbols) and mono - last >= cold_ttl:
                # Outside the hot window a late FMP fill is still worth picking up, slowly, and
                # once FMP has confirmed that day's watched reports nothing is left to ask.
                due.append(day)
        if self._is_hot(today, n, symbols):
            last = self._fetched.get(today)
            if last is None or mono - last >= EARNINGS_LIVE_HOT_TTL_SECONDS:
                due.append(today)
        return due

    def _prune(self, now: datetime) -> None:
        """Forget every day outside the live days."""
        days = set(live_days(et_now(now).date()))
        for key in [k for k in self._records if k[1] not in days]:
            self._records.pop(key, None)
        for day in [d for d in self._fetched if d not in days]:
            self._fetched.pop(day, None)

    async def refresh(self, now: datetime, *, fmp: Any, symbols: Collection[str]) -> None:
        """Prune, then fetch the due days and fold them in. The prune comes before every early
        return: the days roll with nothing due and during a failure wait too. Never raises (a
        failure keeps the records of the live days)."""
        self._prune(now)
        if time.monotonic() < self._failed_until:
            return
        days = self.due_days(now, symbols)
        if not days:
            return
        await self._run_once(("refresh", days[-1]), lambda: self._fetch(now, fmp, days))

    async def _fetch(self, now: datetime, fmp: Any, days: List[date]) -> None:
        getter = getattr(fmp, "get_earnings_calendar", None)
        if getter is None:
            return

        async def _one_day(*, from_date: str, to_date: str) -> Any:
            rows = await getter(from_date=from_date, to_date=to_date)
            if getattr(rows, "fetch_failed", False):
                # An `EmptyAfterFailure` is an outage, not a day nobody reports on.
                raise RuntimeError(f"earnings calendar for {from_date} came back empty after "
                                   f"a failed fetch")
            return rows

        try:
            fetched = await asyncio.wait_for(fetch_calendar_days(_one_day, days),
                                             timeout=_FETCH_DEADLINE_SECONDS)
        except Exception as e:
            self._failed_until = time.monotonic() + _FAILURE_RETRY_SECONDS
            logger.warning(
                "[earnings-live] calendar refresh of %s failed (%s: %s) — keeping the records; "
                "next try in %.0fs",
                ", ".join(d.isoformat() for d in days), type(e).__name__, e,
                _FAILURE_RETRY_SECONDS,
            )
            return
        mono = time.monotonic()
        self.fetches += len(days)
        for day, rows in fetched.items():
            self._fetched[day] = mono
            # Whole market on purpose: the next-morning card and the sheet read these records.
            self.observe_rows(day, rows, now, fetched=True)

    async def _run_once(self, key: Tuple[str, date],
                        factory: Callable[[], Awaitable[None]]) -> None:
        """Leader/joiner dedup — `earnings_window_service._run_once`'s shape."""
        inflight = self._inflight.get(key)
        if inflight is not None:
            try:
                await asyncio.shield(inflight)
            except asyncio.CancelledError:
                if inflight.cancelled():
                    return
                raise
            return
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        try:
            await factory()
            if not fut.done():
                fut.set_result(None)
        except Exception as e:
            logger.warning("[earnings-live] round %s raised (%s: %s)", key[0],
                           type(e).__name__, e)
            if not fut.done():
                fut.set_result(None)
        except BaseException:
            # Cancellation must still resolve the future or every joiner hangs.
            if not fut.done():
                fut.cancel()
            raise
        finally:
            self._inflight.pop(key, None)

    # ── history extras (the sheet) ─────────────────────────────────

    async def history_extras(self, symbol: str, report_day: date,
                             *, loader: Optional[Callable[[str], Awaitable[Any]]] = None,
                             ) -> Optional[HistoryExtras]:
        """Last 4 quarters + the year-ago quarter, once per (symbol, report date) for 6 h.

        From `earnings_service.get_earnings` — the Financials tab's own quarters. On report
        day that build is NOT served from its Supabase tier (it is stale once today reaches
        the next earnings date), so it is computed here once, never per sheet poll. A failure
        or a degraded build is kept for 5 min only.
        """
        key = (symbol.upper(), report_day)
        held = self._extras.get(key)
        if held is not None and time.monotonic() < held[0]:
            return held[1]
        if loader is None:
            from app.services.earnings_service import get_earnings_service

            loader = get_earnings_service().get_earnings
        extras: Optional[HistoryExtras] = None
        ttl = _EXTRAS_FAILURE_TTL_SECONDS
        # SHIELDED: `get_earnings` is a shared in-flight build (the Financials tab joins the
        # same one). Timing out here must not cancel it for everyone else — it finishes in the
        # background and fills its own cache, which the next request (in 20 s) then reads.
        task = asyncio.ensure_future(loader(key[0]))
        task.add_done_callback(_consume_result)
        try:
            response = await asyncio.wait_for(asyncio.shield(task),
                                              timeout=_EXTRAS_DEADLINE_SECONDS)
            extras = history_extras_from(response, report_day)
            if not list(getattr(response, "degraded", None) or []):
                ttl = _EXTRAS_TTL_SECONDS
        except asyncio.TimeoutError:
            ttl = _EXTRAS_SLOW_RETRY_SECONDS
            logger.info("[earnings-live] history for %s still building after %.0fs — served "
                        "without it", key[0], _EXTRAS_DEADLINE_SECONDS)
        except Exception as e:
            logger.info("[earnings-live] history for %s unavailable (%s: %s)",
                        key[0], type(e).__name__, e)
        if len(self._extras) >= _EXTRAS_MAX:
            self._extras.clear()
        self._extras[key] = (time.monotonic() + ttl, extras)
        return extras


def _consume_result(task: "asyncio.Future") -> None:
    """Retrieve a background history build's outcome, so a failure after the caller stopped
    waiting is logged once at INFO instead of as "Task exception was never retrieved"."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.info("[earnings-live] background history build failed (%s: %s)",
                    type(exc).__name__, exc)


_service: Optional[EarningsLiveService] = None


def get_earnings_live_service() -> EarningsLiveService:
    global _service
    if _service is None:
        _service = EarningsLiveService()
    return _service


# ── Payload for the sheet (`GET /tracking/earnings/{ticker}/live`) ──


def live_payload(
    symbol: str,
    report_day: date,
    snap: Optional[LiveSnapshot],
    *,
    company_name: Optional[str],
    eps_estimate: Optional[float],
    revenue_estimate: Optional[float],
    extras: Optional[HistoryExtras],
    today: date,
    found: bool = True,
) -> Dict[str, Any]:
    """The `EarningsLiveResponse` fields. A report with no record yet (a future date, or the
    loop has not seen it) still carries its consensus and its status."""
    if snap is None:
        eps = LiveMetric(eps_estimate)
        revenue = LiveMetric(revenue_estimate)
        status = STATUS_UPCOMING if report_day >= today else STATUS_AWAITING
        if found is False:
            status = STATUS_NONE
        preliminary, checked_at, filing, poll_seconds = False, None, None, None
    else:
        eps, revenue, status = snap.eps, snap.revenue, snap.status
        preliminary, checked_at, filing = snap.preliminary, snap.checked_at, snap.filing
        poll_seconds = snap.poll_seconds
        company_name = snap.company_name or company_name

    def _metric(m: LiveMetric) -> Dict[str, Any]:
        return {
            "estimate": m.estimate, "actual": m.actual, "surprise_pct": m.surprise_pct,
            "source": m.source, "basis": m.basis, "checking": m.checking,
        }

    year_ago = None
    last_quarters: List[Dict[str, Any]] = []
    if extras is not None:
        last_quarters = [
            {"quarter": q.quarter, "result": q.result, "surprise_pct": q.surprise_pct}
            for q in extras.last_quarters
        ]
        if extras.year_ago_quarter or extras.year_ago_revenue is not None or extras.year_ago_eps is not None:
            comparable = extras.year_ago_eps_comparable and eps.basis in (None, BASIS_ADJUSTED)
            year_ago = {
                "quarter": extras.year_ago_quarter,
                "revenue": extras.year_ago_revenue,
                "eps": extras.year_ago_eps if comparable else None,
                "revenue_growth_pct": yoy_growth_pct(revenue.actual, extras.year_ago_revenue),
                "eps_growth_pct": (yoy_growth_pct(eps.actual, extras.year_ago_eps)
                                   if comparable else None),
            }
    filing_payload = None
    guidance = None
    if filing is not None:
        filing_payload = {"url": filing.url, "accepted_at": iso_utc(filing.accepted_at)}
        guidance = filing.guidance
    return {
        "ticker": symbol.upper(),
        "company_name": company_name,
        "report_date": report_day.isoformat(),
        "status": status,
        "checked_at": iso_utc(checked_at),
        "preliminary": bool(preliminary),
        "eps": _metric(eps),
        "revenue": _metric(revenue),
        "filing": filing_payload,
        "guidance": guidance,
        "year_ago": year_ago,
        "last_quarters": last_quarters,
        "poll_seconds": poll_seconds,
    }


# ── The loop ────────────────────────────────────────────────────────

#: Everyone's watched tickers, most-watched first, re-read every 10 min rather than every tick
#: (it is one Supabase RPC; a watchlist edit reaches the live loop within 10 min).
_WATCHED_TTL_SECONDS = 600
_watched_memo: Optional[Tuple[float, List[str]]] = None


async def _watched_ranked_cached() -> List[str]:
    """`earnings_sender._watched_tickers_ranked`, memoised. A failed read RAISES (the tick is
    skipped and retried, and the loop logs it) unless a previous answer is held, which then
    serves with a WARNING and stays expired, so the next tick asks again."""
    global _watched_memo
    held = _watched_memo
    if held is not None and time.monotonic() - held[0] < _WATCHED_TTL_SECONDS:
        return held[1]
    from app.services.notification_senders.earnings_sender import _watched_tickers_ranked

    try:
        ranked = await asyncio.to_thread(_watched_tickers_ranked)
    except Exception as e:
        if held is not None:
            logger.warning(
                "[earnings-live] watched-list read failed (%s: %s); serving the list from "
                "%.0f s ago", type(e).__name__, e, time.monotonic() - held[0],
            )
            return held[1]
        raise
    _watched_memo = (time.monotonic(), ranked)
    return ranked


async def run_earnings_live_loop() -> None:
    """Background loop (main.py lifespan, with the other notification loops): every 60 s,
    refresh the live days for everyone's watched tickers, run the SEC watch for the ones
    reporting today, and hand their new landings to the push sender. Never dies on an error."""
    await asyncio.sleep(45)
    interval = max(int(settings.EARNINGS_LIVE_INTERVAL_SECONDS), 15)
    while True:
        try:
            if settings.EARNINGS_LIVE_ENABLED:
                await earnings_live_tick(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[earnings-live] tick failed (%s: %s)", type(e).__name__, e,
                         exc_info=True)
        await asyncio.sleep(interval)


async def earnings_live_tick(
    now: datetime,
    *,
    fmp: Any = None,
    watched: Optional[Sequence[str]] = None,
    sec: Any = None,
    gemini: Any = None,
    dispatch: Optional[Callable[[List[LiveSnapshot], datetime], Awaitable[Any]]] = None,
) -> Dict[str, int]:
    """One round. Every dependency is injectable so the suite never reaches a network."""
    from app.services import tracking_service as ts
    from app.services.earnings_filing_service import get_earnings_filing_watcher

    if fmp is None:
        from app.integrations.fmp import get_fmp_client

        fmp = get_fmp_client()
    if watched is None:
        watched = await _watched_ranked_cached()
    stats = {"watched": len(watched), "fetched": 0, "filings": 0, "landed": 0}
    if not watched:
        return stats
    service = get_earnings_live_service()
    n = et_now(now)
    today = n.date()

    # Today's rows ride on the Tracking memo (30 min), the same shared object every feed build
    # reads. Reading it through its front door pays for a round when it is old, so the tick does
    # that only in `tick_refreshes_memo`, or when it holds no rows for today (a restart).
    stored_at = ts.earnings_day_stored_at(fmp, today)
    if stored_at is not None and not tick_refreshes_memo(n):
        calendar, _ = ts._held_earnings_window(fmp, [today])
    else:
        try:
            calendar = await ts._cached_earnings_calendar(fmp, ts._et_today())
        except Exception as e:
            calendar = []
            logger.info("[earnings-live] tracking calendar unavailable (%s: %s)",
                        type(e).__name__, e)
        stored_at = ts.earnings_day_stored_at(fmp, today)
    service.observe_rows(today, calendar, now, symbols=watched, stored_at=stored_at)

    before = service.fetches
    await service.refresh(now, fmp=fmp, symbols=watched)
    stats["fetched"] = service.fetches - before

    pending_today = service.pending_symbols(today, watched)
    if pending_today and n.hour >= EARNINGS_LIVE_START_HOUR_ET:
        watcher = get_earnings_filing_watcher()
        watcher.prune(live_days(today))
        found = await watcher.run_once(now, pending_today, today, sec=sec, gemini=gemini)
        for symbol, figures in found.items():
            service.absorb_filing(symbol, today, figures, now)
        stats["filings"] = len(found)

    # Watched reports only, and only here, the one place holding both lists: the sender's cap
    # and its watcher read per send must not go on the rest of the market. Never narrow the
    # fold instead, because cards and the sheet read its records.
    watched_set = {s.upper() for s in watched}
    landed = [s for s in service.landed_snapshots(now) if s.symbol in watched_set]
    stats["landed"] = len(landed)
    if dispatch is None:
        from app.services.notification_senders.earnings_live_sender import dispatch_landings

        dispatch = dispatch_landings
    try:
        await dispatch(landed, now)
    except Exception as e:
        logger.warning("[earnings-live] dispatch failed (%s: %s)", type(e).__name__, e,
                       exc_info=True)
    return stats
