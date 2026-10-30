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

THE MEMO. The previous trading day and today, one single-day calendar call each — a
multi-day request is cut at 4,000 rows with the NEWEST dates kept (`fetch_calendar_days`).
  * Today's rows arrive for free: the Tracking memo (`tracking_service._cached_earnings_
    calendar`, 30 min, the owner's 2026-10-22 cadence) already holds them, and every reader
    folds them in. This module fetches today ITSELF only while it is HOT.
  * The previous trading day is held only here (after-close actuals land overnight, and the
    card must still be there the next morning).
  * HOT = 06:00–22:00 ET, and a WATCHED row on that day still lacks results — the same gate
    as `earnings_window_service._hot_due`. The previous trading day stops being hot at 12:00
    ET. Hot days refresh every `EARNINGS_LIVE_HOT_TTL_SECONDS` (5 min); the previous day
    otherwise every 30 min. 5 min and not faster: the filing path supplies the speed, FMP is
    the confirmation. Cost, worst case: ~190 extra single-day calls on a report day plus ~70
    for the previous day's morning, against the Tracking memo's ~150 a day.
  * Refreshed by the live loop ONLY (`run_earnings_live_loop`, 60 s). Feed builds and the
    sheet endpoint read; they never fetch, so the live state adds no latency to the feed.

STATUSES (no time of day exists on `/stable` rows, so the window is the report day):
  * `upcoming` — a later date, or today before 06:00 ET with nothing in yet.
  * `live`     — today, 06:00–22:00 ET, results not complete. LIVE is drawn ONLY then, and
                 it is true: SEC is checked every minute in the release windows and FMP
                 every 5 min.
  * `reported` — complete (EPS, plus revenue when a revenue estimate exists), or the live
                 window ended with at least one figure in.
  * `awaiting` — the window ended with nothing in. "Results not in yet" — never "late", a
                 status `earnings_window_service` deliberately does not have (FMP lag and
                 reschedule duplicates make it misleading). An awaiting card drops off at
                 12:00 ET the next trading day.

GUARDS, each a defect found elsewhere in this repo:
  * a dropped-digit EPS (`_earnings_common.eps_digit_shift_suspect`) is held as "checking",
    never shown as a -90% miss;
  * a revenue more than 2x off a material consensus is held unless the other source agrees
    within 2% (FMP's dropped-digit revenue, `earnings_service._reconcile_revenue`);
  * a GAAP-only EPS is shown labelled GAAP with no beat/miss — the consensus is adjusted (the
    owner's 2026-09-27 caution, `news_insight_service._earnings_line`);
  * no surprise percentage against an estimate under $0.01 (`earnings_sender.MIN_ABS_ESTIMATE`).
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
from app.utils.market_hours import ET, previous_trading_day

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────

EARNINGS_LIVE_HOT_TTL_SECONDS = 300
EARNINGS_LIVE_COLD_TTL_SECONDS = 1800
#: The live window on the report day, in ET hours: [start, end).
EARNINGS_LIVE_START_HOUR_ET = 6
EARNINGS_LIVE_END_HOUR_ET = 22
#: The previous trading day stays hot, and an `awaiting` card stays up, until this ET hour.
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


@dataclass
class _Record:
    symbol: str
    day: date
    company_name: Optional[str] = None
    eps_estimate: Optional[float] = None
    revenue_estimate: Optional[float] = None
    fmp_eps: Optional[float] = None
    fmp_revenue: Optional[float] = None
    filing: Optional[FilingFigures] = None
    checked_at: Optional[datetime] = None
    # Observed WITHOUT results in this process — what makes a later landing a landing.
    seen_pending: bool = False
    landed_at: Optional[datetime] = None
    landed_from_pending: bool = False


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
    filing's GAAP EPS labelled GAAP and never coloured."""
    est = rec.eps_estimate
    filing = rec.filing
    gaap = filing.eps_gaap if filing is not None else None
    candidates: List[Tuple[float, str, str]] = []
    if rec.fmp_eps is not None:
        candidates.append((rec.fmp_eps, SOURCE_FMP, BASIS_ADJUSTED))
    if filing is not None and filing.eps_adjusted is not None:
        candidates.append((filing.eps_adjusted, SOURCE_FILING, BASIS_ADJUSTED))
    held = False
    for value, source, basis in candidates:
        if eps_digit_shift_suspect(value, est, gaap):
            held = True
            continue
        return LiveMetric(est, value, eps_surprise_pct(value, est), source, basis)
    if held:
        return LiveMetric(est, checking=True)
    if gaap is not None:
        return LiveMetric(est, gaap, None, SOURCE_FILING, BASIS_GAAP)
    return LiveMetric(est)


def revenue_metric(rec: _Record) -> LiveMetric:
    est = rec.revenue_estimate
    fmp = rec.fmp_revenue
    filed = rec.filing.revenue if rec.filing is not None else None
    if fmp is not None:
        value, source, other = fmp, SOURCE_FMP, filed
    elif filed is not None:
        value, source, other = filed, SOURCE_FILING, None
    else:
        return LiveMetric(est)
    if est is not None and est >= _REVENUE_GUARD_MIN_ESTIMATE and value > 0:
        lo, hi = _REVENUE_PLAUSIBLE
        if not lo <= value / est <= hi:
            agrees = other is not None and abs(other - value) <= _SOURCES_AGREE * value
            if not agrees:
                return LiveMetric(est, checking=True)
    return LiveMetric(est, value, revenue_surprise_pct(value, est), source)


def has_results(eps: LiveMetric, revenue: LiveMetric) -> bool:
    return eps.actual is not None or revenue.actual is not None


def is_complete(eps: LiveMetric, revenue: LiveMetric) -> bool:
    return eps.actual is not None and (revenue.actual is not None or revenue.estimate is None)


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


def recent_card_visible(status: str, now_et: datetime) -> bool:
    """A previous-day card: reported stays all day, awaiting drops at 12:00 ET."""
    if status == STATUS_REPORTED:
        return True
    return status == STATUS_AWAITING and now_et.hour < EARNINGS_LIVE_PREV_DAY_UNTIL_HOUR_ET


def snapshot_of(rec: _Record, today: date, now_et: datetime) -> LiveSnapshot:
    eps = eps_metric(rec)
    revenue = revenue_metric(rec)
    preliminary = SOURCE_FILING in (eps.source, revenue.source)
    return LiveSnapshot(
        symbol=rec.symbol, day=rec.day,
        status=live_status(rec.day, today, now_et, eps, revenue),
        company_name=rec.company_name, eps=eps, revenue=revenue,
        preliminary=preliminary, checked_at=rec.checked_at, filing=rec.filing,
    )


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
                     fetched: bool = False) -> None:
        """Fold FMP calendar rows dated `day` into their records. Sticky (module docstring).

        ``symbols`` narrows the fold to those tickers (a feed build passes its user's), so a
        reader never pays for the whole market's rows.

        ``fetched`` is True only for this module's OWN fetch, the one moment the sources were
        actually checked. Rows folded in from the Tracking memo (up to 30 min old) must not move
        `checked_at`, or the sheet would say "updated just now" over a half-hour-old answer.
        """
        wanted = {s.upper() for s in symbols} if symbols is not None else None
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _row_symbol(row)
            if not symbol or (wanted is not None and symbol not in wanted):
                continue
            if _row_date(row) != day:
                continue
            rec = self._records.get((symbol, day))
            if rec is None:
                rec = _Record(symbol=symbol, day=day)
                self._records[(symbol, day)] = rec
            self._fold(rec, row, now, fetched=fetched)

    def _fold(self, rec: _Record, row: Dict[str, Any], now: datetime,
              *, fetched: bool) -> None:
        eps_est = finite(row.get("epsEstimated"))
        if eps_est is not None:
            rec.eps_estimate = eps_est
        rev_est = finite(row.get("revenueEstimated"))
        if rev_est is not None and rev_est > 0:
            rec.revenue_estimate = rev_est
        eps_act = finite(row.get("epsActual"))
        if eps_act is not None:
            rec.fmp_eps = eps_act
        rev_act = finite(row.get("revenueActual"))
        if rev_act is not None and rev_act >= 0:
            rec.fmp_revenue = rev_act
        name = row.get("companyName")
        if isinstance(name, str) and name.strip():
            rec.company_name = name.strip()
        if fetched and (rec.checked_at is None or now > rec.checked_at):
            rec.checked_at = now
        self._note_landing(rec, now)

    def _note_landing(self, rec: _Record, now: datetime) -> None:
        if has_results(eps_metric(rec), revenue_metric(rec)):
            if rec.landed_at is None:
                rec.landed_at = now
                rec.landed_from_pending = rec.seen_pending
        else:
            rec.seen_pending = True

    def absorb_filing(self, symbol: str, day: date, figures: FilingFigures,
                      now: datetime) -> None:
        """The company's own release for (symbol, day). The first verified one is kept."""
        rec = self._records.get((symbol.upper(), day))
        if rec is None:
            rec = _Record(symbol=symbol.upper(), day=day)
            self._records[(rec.symbol, day)] = rec
            # A record born from a filing was certainly pending a moment ago: the filing
            # watcher only runs for symbols the loop saw without results.
            rec.seen_pending = True
        if rec.filing is None:
            rec.filing = figures
        if rec.checked_at is None or now > rec.checked_at:
            rec.checked_at = now
        self._note_landing(rec, now)

    # ── reading ────────────────────────────────────────────────────

    def snapshot(self, symbol: str, day: date, now: datetime) -> Optional[LiveSnapshot]:
        rec = self._records.get((symbol.upper(), day))
        if rec is None:
            return None
        n = et_now(now)
        return snapshot_of(rec, n.date(), n)

    def recent_snapshots(self, symbols: Collection[str], now: datetime) -> List[LiveSnapshot]:
        """The previous trading day's cards worth showing (reported / awaiting-before-noon)."""
        n = et_now(now)
        today = n.date()
        prev = previous_trading_day(today)
        out = []
        for symbol in sorted({s.upper() for s in symbols}):
            rec = self._records.get((symbol, prev))
            if rec is None:
                continue
            snap = snapshot_of(rec, today, n)
            if recent_card_visible(snap.status, n):
                out.append(snap)
        return out

    def landed_snapshots(self, now: datetime) -> List[LiveSnapshot]:
        """Every record whose results were SEEN landing in this process (previous trading day
        and today), as of now. The push sender keeps its own once-per-key bookkeeping.

        A record already reported when first seen (a restart, a first fetch after the fact)
        is not a landing — the same rule as `earnings_window_service._absorb` — so a deploy
        never re-announces a morning's results in the afternoon.
        """
        n = et_now(now)
        return [
            snapshot_of(rec, n.date(), n)
            for rec in self._records.values()
            if rec.landed_at is not None and rec.landed_from_pending
        ]

    def pending_symbols(self, day: date, symbols: Iterable[str]) -> List[str]:
        """The given symbols (order kept) with a record on `day` whose results are incomplete."""
        out = []
        for symbol in symbols:
            rec = self._records.get((symbol.upper(), day))
            if rec is not None and not is_complete(eps_metric(rec), revenue_metric(rec)):
                out.append(symbol.upper())
        return out

    # ── refresh (the live loop only) ───────────────────────────────

    def _is_hot(self, day: date, n: datetime, symbols: Collection[str]) -> bool:
        if not EARNINGS_LIVE_START_HOUR_ET <= n.hour < EARNINGS_LIVE_END_HOUR_ET:
            return False
        if day < n.date() and n.hour >= EARNINGS_LIVE_PREV_DAY_UNTIL_HOUR_ET:
            return False
        return bool(self.pending_symbols(day, symbols))

    def due_days(self, now: datetime, symbols: Collection[str]) -> List[date]:
        """The days this round must fetch, oldest first (see THE MEMO in the docstring)."""
        n = et_now(now)
        today = n.date()
        prev = previous_trading_day(today)
        mono = time.monotonic()
        due = []
        last = self._fetched.get(prev)
        if last is None:
            due.append(prev)
        elif self._is_hot(prev, n, symbols):
            if mono - last >= EARNINGS_LIVE_HOT_TTL_SECONDS:
                due.append(prev)
        elif self.pending_symbols(prev, symbols) and mono - last >= EARNINGS_LIVE_COLD_TTL_SECONDS:
            # Outside the hot window a late FMP fill is still worth picking up, slowly — and
            # once every watched report on that day is complete there is nothing left to ask.
            due.append(prev)
        if self._is_hot(today, n, symbols):
            last = self._fetched.get(today)
            if last is None or mono - last >= EARNINGS_LIVE_HOT_TTL_SECONDS:
                due.append(today)
        return due

    async def refresh(self, now: datetime, *, fmp: Any, symbols: Collection[str]) -> None:
        """Fetch the due days and fold them in. Never raises (a failure keeps the records)."""
        if time.monotonic() < self._failed_until:
            return
        days = self.due_days(now, symbols)
        if not days:
            return
        await self._run_once(("refresh", days[-1]), lambda: self._fetch(now, fmp, days))
        n = et_now(now)
        keep = {previous_trading_day(n.date()), n.date()}
        for key in [k for k in self._records if k[1] not in keep]:
            self._records.pop(key, None)
        for day in [d for d in self._fetched if d not in keep]:
            self._fetched.pop(day, None)

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
        preliminary, checked_at, filing = False, None, None
    else:
        eps, revenue, status = snap.eps, snap.revenue, snap.status
        preliminary, checked_at, filing = snap.preliminary, snap.checked_at, snap.filing
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
        "poll_seconds": 30 if status == STATUS_LIVE else None,
    }


# ── The loop ────────────────────────────────────────────────────────

#: Everyone's watched tickers, most-watched first, re-read every 10 min rather than every tick
#: (it is one Supabase RPC; a watchlist edit reaches the live loop within 10 min).
_WATCHED_TTL_SECONDS = 600
_watched_memo: Optional[Tuple[float, List[str]]] = None


async def _watched_ranked_cached() -> List[str]:
    """`earnings_sender._watched_tickers_ranked`, memoised. A failed read RAISES (the tick is
    skipped and retried) unless a previous answer is held, which then serves."""
    global _watched_memo
    held = _watched_memo
    if held is not None and time.monotonic() - held[0] < _WATCHED_TTL_SECONDS:
        return held[1]
    from app.services.notification_senders.earnings_sender import _watched_tickers_ranked

    try:
        ranked = await asyncio.to_thread(_watched_tickers_ranked)
    except Exception:
        if held is not None:
            return held[1]
        raise
    _watched_memo = (time.monotonic(), ranked)
    return ranked


async def run_earnings_live_loop() -> None:
    """Background loop (main.py lifespan, with the other notification loops): every 60 s,
    refresh the live days for everyone's watched tickers, run the SEC watch for the ones
    reporting today, and hand new landings to the push sender. Never dies on an error."""
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

    # Today's rows ride on the Tracking memo (30 min) — the same shared object every feed
    # build reads, so this costs nothing beyond what the feed already spends.
    try:
        calendar = await ts._cached_earnings_calendar(fmp, ts._et_today())
    except Exception as e:
        calendar = []
        logger.info("[earnings-live] tracking calendar unavailable (%s: %s)", type(e).__name__, e)
    service.observe_rows(today, calendar, now, symbols=watched)

    before = service.fetches
    await service.refresh(now, fmp=fmp, symbols=watched)
    stats["fetched"] = service.fetches - before

    pending_today = service.pending_symbols(today, watched)
    if pending_today and n.hour >= EARNINGS_LIVE_START_HOUR_ET:
        watcher = get_earnings_filing_watcher()
        watcher.prune([previous_trading_day(today), today])
        found = await watcher.run_once(now, pending_today, today, sec=sec, gemini=gemini)
        for symbol, figures in found.items():
            service.absorb_filing(symbol, today, figures, now)
        stats["filings"] = len(found)

    landed = service.landed_snapshots(now)
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
