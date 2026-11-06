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
  A record is STICKY: a figure, once verified, never goes back to "not out yet" (only an FMP
  zero does, GUARDS below), a restart included (A RESTART below), and FMP's figure replaces the
  filing's the moment FMP has one that passes the guards, unless the filing outranks it
  (`_filing_outranks_fmp`).
  Between two FMP answers the NEWEST wins. The Tracking memo can be 30 min behind this
  module's own fetch, so a memo row older than the record's last own fetch is skipped whole at
  all three folds (the tick, the card, the sheet): no reader can put an older answer back over
  a record. A report own fetches ended has none to protect, so its row comes back for a while
  (A REPORT FMP MOVES). A memo refreshed after the fetch folds as usual: a report FMP has
  confirmed is no longer fetched, so the memo is how a later FMP correction reaches it.

DUPLICATE ROWS. FMP can list one report twice, a stale pending row beside the reported one,
in either order. One response therefore decides per report, never per row, and a repeat of it
decides the same again (every tick and feed build folds the same rows). Each metric's actual
comes from one row with that row's own estimate, a pending twin supplies neither, and the
record takes one landing decision. An estimate moves only with its own actual, or while that
metric has no FMP actual, so a surprise is never worked out against another row's consensus.
The card and the sheet read the same pick (`one_row_per_report`).
  A row left out still counts as evidence: the consensus every row states is read before
anything folds (`_note_consensus`) and kept GUARD-ONLY. The sanity guards, the zero rules and
"is a revenue still due" read it; a surprise, the card and the sheet never do, with one exception:
a filing figure has no row of its own, so with no FMP figure for the metric it reads the stated
consensus it sits on (`_record_for_filing`).

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
    and makes no call, unless none are held (a restart) or a held row is too old to open its
    record (below). A feed build still refreshes the memo at any hour. The rows first folded
    at midnight are what make an overnight fill a landing at the first read of the morning.
    This module fetches today ITSELF only while it is HOT.
  * A memo row held `EARNINGS_LIVE_HELD_ROWS_MAX_AGE_SECONDS` (12 h) or longer opens no record,
    at any of the three folds. FMP may have moved the report since, and an open record is a
    report day (LIVE, the hot fetch, an SEC slot, then a card) until a later answer ends it.
    After a closed day the rows held at midnight are the last trading day's, and a
    Sunday-dated report's are Friday's. So when a held row that old would open a record
    (`old_rows_would_open`) the tick asks FMP first, one round. Rows a failed round leaves
    that old open nothing until a round succeeds, and still fold into a record that exists.
    A move inside the 12 h, or after the record opened, is left to this module's own fetch
    (A REPORT FMP MOVES).
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
    a closed day adds up to 192 on that day. A first fold on rows too old to open a record adds
    one round, 15 calls: midnight on a Monday with a watched report, or on a Sunday with one of
    its own. Feed builds add the memo's rounds the tick no longer pays for: ~37 before 06:00 on
    a trading day and up to ~150 on a closed day.
  * Refreshed by the live loop ONLY (`run_earnings_live_loop`, 60 s). Feed builds and the
    sheet endpoint fold the Tracking memo and read; they never fetch, so the live state adds
    no latency to the feed.

A REPORT FMP MOVES. Each day is judged on its own rows, so a report moved off its date would
stay there: LIVE with the hot fetch and an SEC slot until 22:00, then a card the next morning
saying results are not in for a report that has not happened. So own fetches end the reports
they stop listing (`_end_unlisted`): a record goes once they have left its symbol out for
`EARNINGS_LIVE_UNLISTED_END_SECONDS` (10 min), unless it holds a raw figure from either source.
  * Never on one answer. A short answer looks exactly like a move, and it hits every report
    with no figure on the day at once, while the record is what carries `seen_pending` and keeps
    the day asked (`_fmp_wanted`). A day before today has no memo row to reopen it, so a report
    ended by mistake is never read again: no figure, no card, no push. 10 min, not two fetches:
    two hot fetches 5 min apart fit inside a 6-min gap. At the slower cadences it comes to two.
    A longer gap still ends it, because a move has to end. A report that then comes back filled
    is first seen reported, with no landing, and a day before today is asked again only for
    another report.
  * A raw figure, never "has results": a figure held as "checking" shows nothing and is still
    a result, and a company that has reported has reported, whatever the calendar says next.
  * Only an answer that can show a report is gone. One listing nothing on the day is the
    transient blip `earnings_window_service._hot_round` refuses too, and one at FMP's row cap
    may simply have been cut. Neither starts the 10 min or breaks them: only a fetch that lists
    the report does that.
  * Only an own fetch, never a fold: a fold can be narrowed to one reader's tickers, and the
    memo can be 30 min old. That age is why today's report does not end at once: the memo's row
    opens the record again until the memo's next round, and the card, drawn from that same row,
    keeps its status until then. So it settles within about 45 min on a trading day. On a
    closed day the tick pays for no memo round, so it waits for the next feed build, or at worst
    for the row to reach 12 h (`old_rows_would_open`). A day before today has no memo row, so
    the 10 min settle it.
  * A sheet pinned to the old date follows the report (`tracking_service.get_earnings_live`). The
    new date can sit on a memo day held for hours, so a sheet about to answer `none` asks for
    the days after today first, at most 14 calls per answer it rests on. Only a reader does:
    nothing here tells the memo a report ended, so a new date's card can take the memo's 6 h to
    appear.
  A stale row FMP leaves beside the new date is NOT seen: nothing here compares a symbol's
  dates, because a company reporting EARLIER leaves the later date pending, so the row dated
  today can be the real one. Nor is a lone leftover row dated today after the company reported.

TWO LOOPS UNDER ONE TASK (`run_earnings_live_loop`), each on its own 60 s clock, so neither
source can hold the other up.
  * The FMP tick (`earnings_live_tick`) folds the memo, fetches what is due and hands the
    watched landings to the push sender.
  * The filing tick (`earnings_filing_tick`) reads the filings. A round is serial, with a Gemini
    call per filing, so a burst or a stalled model can run it for minutes (a call may take
    `GEMINI_REQUEST_TIMEOUT_SECONDS`). Each figure goes into its record the moment it
    verifies, stamped with that moment, so the card and the sheet show it mid-round, and its
    push goes out on the next FMP tick, at most a minute later. The tick reads the records the
    FMP tick folded, so a stuck FMP tick leaves it working from the ones already held.
  Ticks START a minute apart however long they ran, and an FMP fetch stamps itself when it
  returns, so every FMP cadence comes due a few seconds early (`_DUE_SLACK_SECONDS`): an exact
  compare falls a moment short on the tick it was meant for, and "every 5 min" becomes every 6.
  A long round has no minute of rest after it either, so the rest between two attempts at a
  filing comes from `EarningsFilingWatcher.run_once`, which counts it from the end of the last
  round that spent one.

A RESTART. The records are this process's own, but what only a filing says must outlive it: FMP
may still have no figures, and it never has the outlook or the link. So each filing goes to
`earnings_filing_cache` (migration 209: figures from SEC filings only, never FMP data) the moment
it is on its record (`keep_filing`), best effort, and the first thing either tick does in a new
process is take back what the table holds for the live days (`restore_filings`).
  * Once per process, and before anything decides, so the filing tick cannot read a filing the
    table holds, and whichever tick comes second joins the same read.
  * A restored figure is never a landing, and FMP's fill on it after a restart is none either
    (`restore_filing`). The daily pass is the net for `earnings_result`, as for any report first
    seen reported. `earnings_live` has no such net, so a filing read in the minute before a
    deploy, its push not yet sent, goes without one.
  * A restored filing is not read again, by SEC or by Gemini, even while its report stays
    incomplete and pending (`EarningsFilingWatcher.restore`).
  * A failed read, or a table not there yet, is a WARNING and a retry every
    `_FAILURE_RETRY_SECONDS`, and until then a restart is what it was before this table: a filing
    of today's is read again, one of a day before today waits for FMP. Code can deploy before the
    owner applies the migration. `EARNINGS_LIVE_FILINGS_ENABLED` off restores nothing, so that
    switch still takes the figures away.
  * In the first seconds after boot, before the first tick, a card reads what it always did.

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
  * a revenue more than 2x off a material consensus does not show unless the other source
    agrees within 2% (FMP's dropped-digit revenue, `earnings_service._reconcile_revenue`). Each
    source is judged in turn, FMP first, so a figure the band refuses never hides a sound one,
    and revenue is held as "checking" only when a figure exists and none passes;
  * the filing goes first when a stated consensus bears it out and none bears out FMP's figure,
    at any gap short of agreement, and when FMP's revenue is more than 5x off it, unless a
    consensus stands behind FMP's figure and none behind the filing's (`_filing_outranks_fmp`,
    the same ratio as `_reconcile_revenue`). A dropped digit always lands nearer a tiny
    consensus, so only a consensus inside the band counts. Under 5x with no consensus to judge
    them FMP keeps its place: nothing there can say which figure is the bad one;
  * a figure with a second source beside it and outside 0.5-2x of the estimate on show carries
    no surprise, at any size of estimate: two sources prove the actual, not the consensus it fell
    outside, which may be what dropped a digit ("+918%"). A lone figure keeps its percentage:
    pre-revenue names land far from a tiny consensus all the time;
  * a tiny revenue estimate on show that a material consensus has overtaken judges nothing and
    is not printed: the material one guards the figure, and it shows with no surprise. A twin's
    larger consensus overtakes nothing beside a figure inside its own estimate's band. Two figures
    inside each other's band are one consensus either side of the floor, which judges as it would
    alone, and stays printed, until a figure is more than 5x off the larger: that figure is held
    (`_estimate_overtaken`);
  * a filing figure with no FMP figure beside it reads whichever stated consensus it sits on
    when the estimate on show does not bear it out, so the same evidence decides alike whichever
    twin FMP touched last (`_record_for_filing`);
  * a GAAP-only EPS is shown labelled GAAP with no beat/miss — the consensus is adjusted (the
    owner's 2026-09-27 caution, `news_insight_service._earnings_line`);
  * a filing's figure is only as exact as its print: "$17.1 billion" is anything from $17.05B
    to $17.15B and "$0.41" anything from $0.405 to $0.415 (FMP's consensus carries four
    decimals), so against a consensus inside that band it shows with no surprise, never a 0.0,
    until FMP's exact figure replaces it;
  * no surprise percentage against an estimate under $0.01 (`earnings_sender.MIN_ABS_ESTIMATE`);
  * a zero revenue from FMP is stored only against a consensus under $100M, with no figure
    held, the filing's included (a pre-revenue name can report nothing): beside a material
    consensus, or none, it is an unfilled field, never a -100% miss and never a landing. It
    shows only while that holds: a consensus that turns material, or a positive filing figure,
    takes it off, so a shown zero stays on the SEC watch until its filing has been read
    (`pending_symbols`), and a report left with nothing on show leaves `landed_snapshots`;
  * a row with an EPS of exactly 0 beside a zero or negative revenue is unfilled as a whole,
    unless that revenue stands and no EPS is held, the filing's included. Such a row stored
    earlier as a real report gives way as a whole to the same later evidence, to any EPS the
    filing prints (`_fmp_zeros_unfilled`), or to a real figure from FMP for either field. The next
    fold then drops both zeros (`_zeros_gave_way`), so neither comes back with a later fill, the
    estimates they stood on can move, and the record ends as if the row had never come, in either
    order. Beside a real revenue, or with no revenue on the row, 0 is a real breakeven. The daily
    push pass asks the same rule (`eps_is_unfilled`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Collection,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from app.config import settings
from app.services._earnings_common import eps_digit_shift_suspect
from app.services.earnings_filing_service import _DUE_SLACK_SECONDS, FilingFigures
from app.services.earnings_window_service import (
    _TRUNCATION_ROWS,
    _row_date,
    _row_symbol,
    fetch_calendar_days,
)
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
#: A memo row held this long opens no record (THE MEMO in the module docstring). Longer than the
#: night the tick does not pay for, midnight to 06:00, and shorter than the day a closed day adds.
EARNINGS_LIVE_HELD_ROWS_MAX_AGE_SECONDS = 12 * 3600
#: How long own fetches must leave a report out before it ends (A REPORT FMP MOVES in the module
#: docstring). Two hot rounds, because one is not enough: a 6-min gap can hold two fetches 5 min
#: apart.
EARNINGS_LIVE_UNLISTED_END_SECONDS = 600
#: The live window on the report day, in ET hours: [start, end).
EARNINGS_LIVE_START_HOUR_ET = 6
EARNINGS_LIVE_END_HOUR_ET = 22
#: The previous trading day stays hot until this ET hour, and an `awaiting` card stays up until
#: this ET hour on the first trading day after its report.
EARNINGS_LIVE_PREV_DAY_UNTIL_HOUR_ET = 12

#: Below this |estimate| no surprise percentage is honest (`earnings_sender.MIN_ABS_ESTIMATE`:
#: a $0.001 estimate against a $0.40 actual is a 39,900% "surprise").
MIN_ABS_EPS_ESTIMATE = 0.01
#: Floating-point room at the edge of an EPS print's rounding: 0.415 - 0.41 is 0.0050000000000000044.
_EPS_ROUNDING_SLACK = 1e-9
#: A revenue estimate this large is material enough for the plausibility guard. Smaller
#: (pre-revenue) companies routinely land far from a tiny consensus, and holding their
#: figure would hide a real result.
_REVENUE_GUARD_MIN_ESTIMATE = 100e6
_REVENUE_PLAUSIBLE = (0.5, 2.0)
_SOURCES_AGREE = 0.02
#: A revenue outside this of another figure for it is a digit or unit glitch, not a difference
#: of basis: the ratio `earnings_service._reconcile_revenue` applies, a copy because that module
#: loads the FMP client and the database (pinned equal by a test of the same name as the ratio's:
#: `test_the_far_apart_ratio_is_the_financials_tabs_own`).
_FEED_FILED_RATIO = (0.2, 5.0)
#: A filing accepted longer than this before its record was first seen was already out at
#: first sight, like an FMP row first seen reported. Long enough to cover a deploy gap, so a
#: release first read just after a deploy keeps the landing it can still announce. One an earlier
#: process read comes back with no landing at all (`restore_filing`).
_FILING_LANDING_GRACE = timedelta(minutes=15)

_FETCH_DEADLINE_SECONDS = 20.0
_FAILURE_RETRY_SECONDS = 120.0
#: Migration 209. Each call is bounded as well as on a thread, because a tick waits for it and the
#: tick is what sends the pushes: a stalled Supabase must cost a retry, never the minute.
_FILING_CACHE_TABLE = "earnings_filing_cache"
_FILING_CACHE_COLUMNS = ("symbol", "report_date", "accession", "accepted_at", "url", "revenue",
                         "eps_adjusted", "eps_gaap", "fiscal_period", "guidance",
                         "revenue_rounding", "eps_rounding", "computed_at")
_FILING_CACHE_NUMBERS = ("revenue", "eps_adjusted", "eps_gaap", "revenue_rounding", "eps_rounding")
_FILING_CACHE_DEADLINE_SECONDS = 5.0
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

    # The consensus to print: None when there is none, or when the code has ruled it out of date
    # (`_estimate_out_of_date`). A filing figure prints the one it reads (`_record_for_filing`).
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
    # The smallest revenue figure it stated, for a filing figure only (`_record_for_filing`).
    revenue_smallest_estimate: Optional[float] = None
    fmp_eps: Optional[float] = None
    fmp_revenue: Optional[float] = None
    filing: Optional[FilingFigures] = None
    checked_at: Optional[datetime] = None
    # Monotonic time of the last own fetch that held this report. Per record, not per day: a
    # ticker that fetch left out keeps folding the memo. None until a fetch has held it.
    fmp_fetched_mono: Optional[float] = None
    # Monotonic time of the first own fetch that left this report out, of those since one last
    # listed it. Answers that cannot show a report gone do not count (`_end_unlisted`).
    unlisted_since: Optional[float] = None
    # When this process first held the record: read against a filing's acceptance time.
    first_seen_at: Optional[datetime] = None
    # Observed WITHOUT results in this process — what makes a later landing a landing.
    seen_pending: bool = False
    landed_at: Optional[datetime] = None
    landed_from_pending: bool = False
    # The fold runs on every tick and feed build: a refusal is logged once per record.
    revenue_refusal_logged: bool = False
    eps_refusal_logged: bool = False
    # What the guards last did to this revenue, None after a clean answer: each change is logged
    # once (`_log_guarded_revenue`).
    revenue_guard_note: Optional[str] = None


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


@dataclass(frozen=True)
class HeldFiling:
    """One `earnings_filing_cache` row read back. `computed_at` is when its figures reached
    their record, None when the row's time cannot be read."""

    symbol: str
    day: date
    figures: FilingFigures
    computed_at: Optional[datetime]


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
    the guard-only one; the surprise and the metric's `estimate` never do. A filing figure whose
    rounding covers the estimate carries no verdict, as in `revenue_metric`. FMP's 0 from a row
    of zeros that turned out unfilled reads as if it never came (`_fmp_zeros_unfilled`), so the
    filing's figure shows and the result push waits."""
    est = rec.eps_estimate
    guard_est = est if est is not None else rec.eps_guard_estimate
    filing = rec.filing
    gaap = filing.eps_gaap if filing is not None else None
    candidates: List[Tuple[float, str, str]] = []
    if rec.fmp_eps is not None and not _fmp_zeros_unfilled(rec):
        candidates.append((rec.fmp_eps, SOURCE_FMP, BASIS_ADJUSTED))
    if filing is not None and filing.eps_adjusted is not None:
        candidates.append((filing.eps_adjusted, SOURCE_FILING, BASIS_ADJUSTED))
    held = False
    for value, source, basis in candidates:
        if eps_digit_shift_suspect(value, guard_est, gaap):
            held = True
            continue
        rounding = filing.eps_rounding if source == SOURCE_FILING else None
        if est is not None and rounding is not None \
                and abs(value - est) <= rounding + _EPS_ROUNDING_SLACK:
            return LiveMetric(est, value, None, source, basis)
        return LiveMetric(est, value, eps_surprise_pct(value, est), source, basis)
    if held:
        return LiveMetric(est, checking=True)
    if gaap is not None:
        return LiveMetric(est, gaap, None, SOURCE_FILING, BASIS_GAAP)
    return LiveMetric(est)


def _near(value: float, estimate: float) -> bool:
    """Is `value` inside the 0.5-2x band of `estimate`, whatever its size?"""
    lo, hi = _REVENUE_PLAUSIBLE
    return estimate > 0 and lo <= value / estimate <= hi


def _revenue_in_band(value: float, estimate: Optional[float]) -> bool:
    """Is `value` plausible against `estimate`? With no material consensus nothing can say it
    is not: pre-revenue names land far from a tiny one all the time."""
    return estimate is None or estimate < _REVENUE_GUARD_MIN_ESTIMATE or _near(value, estimate)


def _sources_agree(value: float, other: Optional[float]) -> bool:
    """Is `other`, the other source's figure, within `_SOURCES_AGREE` of `value`?"""
    return other is not None and abs(other - value) <= _SOURCES_AGREE * value


def _revenue_passes(value: float, estimate: Optional[float], other: Optional[float]) -> bool:
    """May this source's `value` show, with `other` the other source's figure? In the band, or
    within `_SOURCES_AGREE` of the other source. A zero is neither, and two zeros agree on
    nothing: it stands only against a consensus under the guard's floor, with no positive
    figure beside it (GUARDS in the module docstring)."""
    if value <= 0:
        return (value == 0 and estimate is not None
                and estimate < _REVENUE_GUARD_MIN_ESTIMATE and not other)
    return _revenue_in_band(value, estimate) or _sources_agree(value, other)


def _estimate_overtaken(rec: _Record, value: float) -> bool:
    """Has a material consensus overtaken the tiny estimate on show, for this figure? An estimate
    moves only with its own figure, so that consensus sits in the guard-only field: after a zero
    was stored against the tiny one, or beside a figure from a row that states a tiny one.

    Two things keep the estimate. A figure inside the estimate's band is borne out by it, so the
    larger figure is a stale twin, and it must not hold a real small-company result. The two
    consensus figures inside each other's band are one consensus either side of the floor, which
    judges as it would alone, until the figure is `_far_apart` from the larger: a digit off a
    consensus that size is the slip the guard exists for, whichever row FMP put it on."""
    est, stated = rec.revenue_estimate, rec.revenue_guard_estimate
    if est is None or stated is None or not est < _REVENUE_GUARD_MIN_ESTIMATE <= stated:
        return False
    if _near(value, est):
        return False
    return not _near(stated, est) or _far_apart(value, stated)


def _estimate_out_of_date(rec: _Record, value: float) -> bool:
    """Must the estimate on show not be printed beside this figure? It is overtaken, and by a
    different consensus: its other half either side of the floor judges the figure, but that says
    nothing against the estimate, so it stays."""
    return (_estimate_overtaken(rec, value)
            and not _near(rec.revenue_guard_estimate, rec.revenue_estimate))


def _record_for_filing(rec: _Record, value: float) -> _Record:
    """The record a filing figure reads against. With no FMP figure for the metric the estimate
    on show is only the row FMP touched last, and it may move (DUPLICATE ROWS in the module
    docstring). So when it does not bear this figure out and another stated consensus does, that
    one stands in for this figure alone, and which twin FMP touched last decides nothing. An FMP
    figure, a zero aside, keeps its own row's estimate, or its lack of one. A figure the estimate
    on show does bear out stays with it, even if a twin would too: only `lastUpdated` can choose
    between two consensus figures that close."""
    est = rec.revenue_estimate
    if rec.fmp_revenue or (est is not None and _near(value, est)):
        return rec
    for stated in (rec.revenue_smallest_estimate, rec.revenue_guard_estimate):
        if stated is not None and _near(value, stated):
            return replace(rec, revenue_estimate=stated)
    return rec


def _guard_estimate(rec: _Record, value: float) -> Optional[float]:
    """The consensus that judges this figure: the estimate on show, unless it is missing or
    overtaken, then the guard-only one. A zero reads the larger of the two, as the fold that
    stored it does, so one stored before the larger figure arrived does not stand on the smaller."""
    est, stated = rec.revenue_estimate, rec.revenue_guard_estimate
    if value <= 0 and est is not None and stated is not None:
        return max(est, stated)
    return stated if est is None or _estimate_overtaken(rec, value) else est


def _far_apart(value: Optional[float], other: Optional[float]) -> bool:
    """Are two positive revenues further apart than `_FEED_FILED_RATIO`?"""
    if value is None or other is None or value <= 0 or other <= 0:
        return False
    lo, hi = _FEED_FILED_RATIO
    return not lo <= value / other <= hi


def _filing_outranks_fmp(rec: _Record, fmp: Optional[float], filed: Optional[float]) -> bool:
    """Does the filing go first? Whenever a stated consensus bears it out and none bears out FMP's
    figure, at any gap short of agreement: a figure that far from the consensus must not replace
    one sitting on it, as in `_reconcile_revenue`. And with the two `_far_apart`, where the band
    may have nothing to say, unless a consensus stands behind FMP's figure alone: a dropped digit
    does not land on the consensus by chance, and a release read off a segment line can be this
    far off. Under 5x with nothing to judge them FMP keeps its place. This only orders the queue:
    a filing the guard refuses still leaves FMP's figure on show."""
    if fmp is None or filed is None or min(fmp, filed) <= 0 or _sources_agree(fmp, filed):
        return False
    stated = [c for c in (rec.revenue_estimate, rec.revenue_guard_estimate) if c is not None]
    filing_borne_out = any(_near(filed, c) for c in stated)
    fmp_borne_out = any(_near(fmp, c) for c in stated)
    if _far_apart(fmp, filed):
        return filing_borne_out or not fmp_borne_out
    return filing_borne_out and not fmp_borne_out


def _guards_take_the_verdict(rec: _Record, value: float, other: Optional[float]) -> bool:
    """Does this figure, with `other` the other source's, show with no surprise? Beside an
    overtaken estimate; past the band of the consensus that judged it, which only agreement lets
    through; or, at any size of estimate, outside the band of the estimate on show with a second
    positive figure beside it. Two sources prove the actual, never the consensus it fell outside,
    which may be what dropped a digit. A lone figure far from a tiny consensus keeps its
    percentage."""
    if _estimate_overtaken(rec, value) or not _revenue_in_band(value, _guard_estimate(rec, value)):
        return True
    est = rec.revenue_estimate
    return est is not None and other is not None and other > 0 and not _near(value, est)


def _fmp_zeros_unfilled(rec: _Record) -> bool:
    """Has a stored row of zeros turned out to be FMP's unfilled shape? `_storable_figures` keeps
    an EPS of 0 beside a revenue of 0 only as a pre-revenue name's real report, and later
    evidence undoes that: any figure the filing prints above zero, or a consensus turning
    material. Both metrics then read as if the row never came, what the fold does when that
    evidence came first, so one row is never unfilled for revenue and a real -100% for EPS. The
    next fold makes that true of the record (`_zeros_gave_way`)."""
    if rec.fmp_eps != 0 or rec.fmp_revenue != 0:
        return False
    filing = rec.filing
    if filing is not None and (filing.revenue or filing.eps_adjusted or filing.eps_gaap):
        return True
    return not _revenue_passes(0.0, _guard_estimate(rec, 0.0), None)


def revenue_metric(rec: _Record) -> LiveMetric:
    """FMP first, then the filing, each judged by `_revenue_passes` beside the other, so a figure
    the band refuses never hides a sound one, and the filing first when it outranks FMP's
    (`_filing_outranks_fmp`). Held only when a figure exists and none passes. Like `eps_metric`,
    the plausibility guard falls back to the guard-only estimate, and here it also reads that one
    in place of an overtaken estimate; the surprise and the metric's `estimate` never do, and an
    estimate out of date is not shown. The one exception is a filing figure with another stated
    consensus to read (`_record_for_filing`).

    A figure carries no verdict when `_guards_take_the_verdict` says so, or when it is a filing
    figure whose rounding covers the estimate, because the print cannot say which side of the
    consensus the true figure is on."""
    est = rec.revenue_estimate
    fmp = None if _fmp_zeros_unfilled(rec) else rec.fmp_revenue
    filed = rec.filing.revenue if rec.filing is not None else None
    candidates: List[Tuple[float, str, Optional[float]]] = []
    if fmp is not None:
        candidates.append((fmp, SOURCE_FMP, filed))
    if filed is not None:
        candidates.append((filed, SOURCE_FILING, fmp))
    if _filing_outranks_fmp(rec, fmp, filed):
        candidates.reverse()
    for value, source, other in candidates:
        judged = _record_for_filing(rec, value) if source == SOURCE_FILING else rec
        if not _revenue_passes(value, _guard_estimate(judged, value), other):
            continue
        read = judged.revenue_estimate
        rounding = rec.filing.revenue_rounding if source == SOURCE_FILING else None
        within_rounding = (read is not None and rounding is not None
                           and abs(value - read) <= rounding)
        if within_rounding or _guards_take_the_verdict(judged, value, other):
            shown = None if _estimate_out_of_date(judged, value) else read
            return LiveMetric(shown, value, None, source)
        return LiveMetric(read, value, revenue_surprise_pct(value, read), source)
    if candidates:
        out_of_date = any(_estimate_out_of_date(rec, value) for value, _, _ in candidates)
        return LiveMetric(None if out_of_date else est, checking=True)
    return LiveMetric(est, due=est is not None or rec.revenue_guard_estimate is not None)


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


def _zero_awaits_filing(rec: _Record, revenue: LiveMetric) -> bool:
    """Is a zero revenue on show with the company's release still unread? Small companies can
    file after the wire release, and only the release can take a zero off, so it must not end
    the SEC watch. The report stays complete: the status is not this rule's to change."""
    return revenue.actual == 0 and rec.filing is None


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
    what still reads today's late fills. At any other hour the tick folds what the memo holds,
    unless a held row is too old to open its record (THE MEMO in the module docstring)."""
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


def _stored_time(value: Any) -> Optional[datetime]:
    """A timestamptz as PostgREST writes it, aware, or None when it cannot be read. No offset
    reads as UTC, the rule of `et_now`: a naive time beside an aware one raises at the next
    compare, and a fold compares `checked_at` on every fetch."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def _stored_text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def filing_cache_row(symbol: str, day: date, figures: FilingFigures,
                     computed_at: datetime) -> Dict[str, Any]:
    """PURE. One `earnings_filing_cache` row: every field of the figures, as verified, because
    a restored record must read as the one that wrote it (`held_filing_from`)."""
    return {
        "symbol": symbol.upper(),
        "report_date": day.isoformat(),
        "accession": figures.accession,
        "accepted_at": iso_utc(figures.accepted_at),
        "url": figures.url,
        "revenue": figures.revenue,
        "eps_adjusted": figures.eps_adjusted,
        "eps_gaap": figures.eps_gaap,
        "fiscal_period": figures.fiscal_period,
        "guidance": figures.guidance,
        "revenue_rounding": figures.revenue_rounding,
        "eps_rounding": figures.eps_rounding,
        "computed_at": iso_utc(computed_at),
    }


def held_filing_from(row: Any) -> Optional[HeldFiling]:
    """PURE. An `earnings_filing_cache` row back as what it was written from, or None when it
    cannot be trusted. All or nothing for what decides a figure: half a row could show one
    without the rounding that keeps it honest, and a row with neither a figure nor an outlook is
    no filing. A time or a label that cannot be read costs only itself."""
    if not isinstance(row, Mapping):
        return None
    symbol, accession = _stored_text(row.get("symbol")), _stored_text(row.get("accession"))
    day = _parse_day(row.get("report_date"))
    if symbol is None or accession is None or day is None:
        return None
    numbers: Dict[str, Optional[float]] = {}
    for name in _FILING_CACHE_NUMBERS:
        numbers[name] = finite(row.get(name))
        if numbers[name] is None and row.get(name) is not None:
            return None
    figures = FilingFigures(
        accession=accession.strip(), accepted_at=_stored_time(row.get("accepted_at")),
        url=_stored_text(row.get("url")), fiscal_period=_stored_text(row.get("fiscal_period")),
        guidance=_stored_text(row.get("guidance")), **numbers,
    )
    if not figures.has_figures and figures.guidance is None:
        return None
    return HeldFiling(symbol.strip().upper(), day, figures, _stored_time(row.get("computed_at")))


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
    unfilled. It takes the smallest too, for a filing figure sitting on that one
    (`_record_for_filing`). A response stating none keeps the last one."""
    eps = [value for value in map(_eps_estimate, ranked) if value is not None]
    revenue = [value for value in map(_revenue_estimate, ranked) if value is not None]
    if eps:
        rec.eps_guard_estimate = eps[-1]
    if revenue:
        rec.revenue_guard_estimate = max(revenue)
        rec.revenue_smallest_estimate = min(revenue)


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


def _held_figures(rec: _Record) -> Tuple[Optional[float], Optional[float]]:
    """The (EPS, revenue) a row of zeros must not take back or stand beside: FMP's, else the
    company's own print."""
    filing = rec.filing
    if filing is None:
        return rec.fmp_eps, rec.fmp_revenue
    return (rec.fmp_eps or filing.eps_adjusted or filing.eps_gaap,
            rec.fmp_revenue or filing.revenue)


def _zeros_gave_way(rec: _Record, *picks: Optional[Tuple[Dict[str, Any], float]]) -> bool:
    """Is the stored row of zeros over, with this response's `picks` about to fold? It is once
    `_fmp_zeros_unfilled` says so, or once FMP has a real figure for either field: a row half of
    which was never real was never a report. Left in the record the other zero would read as a
    real -100% beside that figure, because nothing could tell it from a real one any more."""
    filled = any(pick is not None and pick[1] != 0 for pick in picks)
    return rec.fmp_eps == 0 and rec.fmp_revenue == 0 and (filled or _fmp_zeros_unfilled(rec))


def eps_is_unfilled(eps: Optional[float], row: Dict[str, Any]) -> bool:
    """PURE. Would `_storable_figures` refuse this calendar row's `eps`, read alone with nothing
    held? For the daily push pass (`earnings_sender.select_results`), which has no record behind
    it: a row the fold reads as unfilled must not go out as a -100% miss at 16:00 ET either."""
    revenue = finite(row.get("revenueActual"))
    kept, _ = _storable_figures(eps, revenue, None, None, _revenue_estimate(row))
    return eps is not None and kept is None


def _revenue_guard_outcome(rec: _Record, shown: LiveMetric) -> Optional[str]:
    """What the guards did to this revenue, in a log's words, or None when it shows as it came.
    Read from the same rules `revenue_metric` applied, with the same consensus."""
    fmp, value = rec.fmp_revenue, shown.actual
    if value is None:
        return "held" if shown.checking or fmp is not None else None
    filed = rec.filing.revenue if rec.filing is not None else None
    if shown.source == SOURCE_FILING and fmp is not None:
        return "the filing stands in"
    if shown.source == SOURCE_FMP and _far_apart(fmp, filed):
        return "the filing is set aside"
    if shown.source == SOURCE_FILING and shown.estimate not in (None, rec.revenue_estimate):
        return "the filing reads the consensus it sits on"
    other = filed if shown.source == SOURCE_FMP else None
    return "no verdict" if _guards_take_the_verdict(rec, value, other) else None


def _log_guarded_revenue(rec: _Record, shown: LiveMetric) -> None:
    """One WARNING each time the guards' answer on a record changes, a clean answer in between
    included, so a report's log reads as what happened to it. Here and not in `revenue_metric`,
    which is a read that runs on every tick and feed build."""
    outcome = _revenue_guard_outcome(rec, shown)
    if outcome == rec.revenue_guard_note:
        return
    rec.revenue_guard_note = outcome
    if outcome is None:
        return
    logger.warning(
        "[earnings-live] %s %s: revenue guard: %s (FMP %s, filing %s, consensus %s, stated %s to "
        "%s, showing %s against %s)", rec.symbol, rec.day.isoformat(), outcome, rec.fmp_revenue,
        rec.filing.revenue if rec.filing is not None else None, rec.revenue_estimate,
        rec.revenue_smallest_estimate, rec.revenue_guard_estimate, shown.actual, shown.estimate,
    )


def _older_than_own_fetch(rec: _Record, stored_at: Optional[float]) -> bool:
    """Is a memo row stored at ``stored_at`` older than the record's last own fetch? A row of
    unknown age counts as older: it must never beat an answer of known age."""
    last = rec.fmp_fetched_mono
    return last is not None and (stored_at is None or stored_at < last)


def _too_old_to_open(stored_at: Optional[float]) -> bool:
    """Has a memo row stored at ``stored_at`` been held too long to open a record (THE MEMO in
    the module docstring)? A row of unknown age is not in the memo, so this has no say on it."""
    return (stored_at is not None
            and time.monotonic() - stored_at >= EARNINGS_LIVE_HELD_ROWS_MAX_AGE_SECONDS)


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


# ── The filing cache (migration 209) ────────────────────────────────


async def _read_held_filings(days: Sequence[date]) -> List[HeldFiling]:
    """What `earnings_filing_cache` holds for `days`. A failed read RAISES, for the caller to
    retry. A row that cannot be trusted is left out with a WARNING, and costs only itself."""
    wanted = [day.isoformat() for day in days]

    def _read() -> List[Any]:
        from app.database import get_supabase

        res = (get_supabase().table(_FILING_CACHE_TABLE)
               .select(",".join(_FILING_CACHE_COLUMNS))
               .in_("report_date", wanted)
               .execute())
        return res.data or []

    rows = await asyncio.wait_for(asyncio.to_thread(_read),
                                  timeout=_FILING_CACHE_DEADLINE_SECONDS)
    held: List[HeldFiling] = []
    for row in rows:
        filing = held_filing_from(row)
        if filing is not None:
            held.append(filing)
            continue
        named = row if isinstance(row, Mapping) else {}
        logger.warning("[earnings-live] %s %s: a filing cache row could not be read, left out",
                       named.get("symbol"), named.get("report_date"))
    return held


async def _store_filing(row: Dict[str, Any]) -> None:
    """Upsert one `earnings_filing_cache` row. Best effort: the figure is already on its record,
    and a failed write costs only what a restart would have had before this table."""

    def _write() -> None:
        from app.database import get_supabase

        (get_supabase().table(_FILING_CACHE_TABLE)
         .upsert(row, on_conflict="symbol,report_date")
         .execute())

    try:
        await asyncio.wait_for(asyncio.to_thread(_write),
                               timeout=_FILING_CACHE_DEADLINE_SECONDS)
    except Exception as e:
        logger.warning("[earnings-live] %s %s: filing not kept (%s: %s), a restart will not find "
                       "it", row["symbol"], row["report_date"], type(e).__name__, e)


# ── The service ─────────────────────────────────────────────────────


class EarningsLiveService:
    """Per-process live state (the deploy runs ONE uvicorn worker, so it is coherent). What a
    filing said also sits in `earnings_filing_cache` (A RESTART in the module docstring)."""

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
        # Has this process taken back `earnings_filing_cache`, and when may a failed read try
        # again (`restore_filings`)?
        self._filings_restored = False
        self._restore_retry_at: float = 0.0

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
        skipped before anything folds, and so are rows of unknown age. Memo rows too old to open
        a record (`_too_old_to_open`) open none, with one WARNING per fold, and still fold into
        a record that exists.
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
        too_old = not fetched and _too_old_to_open(stored_at)
        unopened: List[str] = []
        for symbol, group in groups.items():
            rec = self._records.get((symbol, day))
            if rec is None:
                if too_old:
                    unopened.append(symbol)
                    continue
                rec = _Record(symbol=symbol, day=day, first_seen_at=now)
                self._records[(symbol, day)] = rec
            elif not fetched and _older_than_own_fetch(rec, stored_at):
                continue
            ranked = _ranked_rows(group, today)
            _note_consensus(rec, ranked)
            self._fold_report(rec, _rows_to_fold(ranked, today), now, fetched=fetched)
            self._note_landing(rec, now)
        if unopened:
            logger.warning(
                "[earnings-live] %s: memo rows held %.0f h opened no record for %d report(s) "
                "(%s): FMP may have moved them, so the next stored round decides",
                day.isoformat(), (time.monotonic() - stored_at) / 3600, len(unopened),
                ", ".join(sorted(unopened)[:5]),
            )

    def old_rows_would_open(self, day: date, rows: Iterable[Any], stored_at: Optional[float],
                            symbols: Collection[str]) -> bool:
        """Are these held rows too old to open a record (`_too_old_to_open`), and does one of
        them name a watched report on `day` with no record yet? Only then is a round worth
        paying for: a record that exists takes old rows as it is, and on a closed day with
        nothing of its own this is what keeps the tick from paying at all."""
        if not _too_old_to_open(stored_at):
            return False
        wanted = {s.upper() for s in symbols}
        for row in rows:
            symbol = _row_symbol(row)
            if symbol in wanted and _row_date(row) == day and (symbol, day) not in self._records:
                return True
        return False

    def _fold_report(self, rec: _Record, rows: List[Dict[str, Any]], now: datetime,
                     *, fetched: bool) -> None:
        """One response's rows for one report, weakest first, into its record: each metric's
        figure is the strongest row's that may be stored (`_settled` settles its estimate).
        The surprise behind a figure keeps the figure's consensus through a later pending row,
        and the landing is the caller's, once per report. A row of zeros that has given way
        (`_zeros_gave_way`) leaves the record first, with both its zeros, so from here on the
        record is the one that row never reached and its estimates can move again. An own fetch
        also stamps the record, which is the line later memo rows must clear (`observe_rows`),
        and having listed the report it ends any gap `_end_unlisted` was counting."""
        eps_pick: Optional[Tuple[Dict[str, Any], float]] = None
        revenue_pick: Optional[Tuple[Dict[str, Any], float]] = None
        held_eps, held_revenue = _held_figures(rec)
        for row in rows:
            reported_eps = finite(row.get("epsActual"))
            reported_revenue = finite(row.get("revenueActual"))
            eps, revenue = _storable_figures(reported_eps, reported_revenue, held_eps,
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
                    rec.symbol, rec.day.isoformat(), reported_eps, reported_revenue, held_eps,
                )
            name = row.get("companyName")
            if isinstance(name, str) and name.strip():
                rec.company_name = name.strip()
        if _zeros_gave_way(rec, eps_pick, revenue_pick):
            logger.warning(
                "[earnings-live] %s %s: revenue guard: FMP's row of zeros gave way (this answer "
                "EPS %s, revenue %s; filing %s; consensus %s, largest stated %s)",
                rec.symbol, rec.day.isoformat(), eps_pick and eps_pick[1],
                revenue_pick and revenue_pick[1], rec.filing is not None,
                rec.revenue_estimate, rec.revenue_guard_estimate,
            )
            rec.fmp_eps = rec.fmp_revenue = None
        rec.fmp_eps, rec.eps_estimate = _settled(
            rec.fmp_eps, rec.eps_estimate, eps_pick, rows, _eps_estimate)
        rec.fmp_revenue, rec.revenue_estimate = _settled(
            rec.fmp_revenue, rec.revenue_estimate, revenue_pick, rows, _revenue_estimate)
        if fetched:
            rec.fmp_fetched_mono = time.monotonic()
            rec.unlisted_since = None
            if rec.checked_at is None or now > rec.checked_at:
                rec.checked_at = now

    def _note_landing(self, rec: _Record, now: datetime) -> None:
        revenue = revenue_metric(rec)
        _log_guarded_revenue(rec, revenue)
        if has_results(eps_metric(rec), revenue):
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

    def restore_filing(self, symbol: str, day: date, figures: FilingFigures, now: datetime,
                       *, checked_at: Optional[datetime] = None) -> None:
        """A filing an earlier process read for (symbol, day) comes back onto its record, a
        filing this process already holds staying (the first verified one is kept).

        A restored figure is never a landing, so the landing is closed here rather than left to
        `_note_landing`: a feed build may already have marked the record seen pending, and FMP's
        fill would then read as one. It is left open only for a row with no figure, an outlook
        alone: nothing was restored, and FMP's first figure really is the first. `checked_at`
        is when the figures reached their record, so the sheet's "updated" stays true.
        """
        key = (symbol.upper(), day)
        rec = self._records.get(key)
        if rec is None:
            rec = _Record(symbol=key[0], day=day, first_seen_at=now)
            self._records[key] = rec
        if rec.filing is None:
            rec.filing = figures
        if checked_at is not None and (rec.checked_at is None or checked_at > rec.checked_at):
            rec.checked_at = checked_at
        if figures.has_figures and rec.landed_at is None:
            rec.landed_at = now
            rec.landed_from_pending = False

    async def restore_filings(self, now: datetime) -> None:
        """Take back what `earnings_filing_cache` holds for the live days, once per process, in
        the first tick of either loop, before it decides anything (A RESTART in the module
        docstring). Never raises. A failed read tries again `_FAILURE_RETRY_SECONDS` later."""
        if self._filings_restored or not settings.EARNINGS_LIVE_FILINGS_ENABLED:
            return
        if time.monotonic() < self._restore_retry_at - _DUE_SLACK_SECONDS:
            return
        await self._run_once(("restore", et_now(now).date()),
                             lambda: self._restore_filings(now))

    async def _restore_filings(self, now: datetime) -> None:
        from app.services.earnings_filing_service import get_earnings_filing_watcher

        days = live_days(et_now(now).date())
        try:
            held = await _read_held_filings(days)
            watcher = get_earnings_filing_watcher()
            for filing in held:
                self.restore_filing(filing.symbol, filing.day, filing.figures, now,
                                    checked_at=filing.computed_at)
                watcher.restore(filing.symbol, filing.day, filing.figures)
        except Exception as e:
            self._restore_retry_at = time.monotonic() + _FAILURE_RETRY_SECONDS
            logger.warning(
                "[earnings-live] filing restore failed for %s to %s (%s: %s): the records go "
                "without what the table holds; next try in %.0fs", days[0].isoformat(),
                days[-1].isoformat(), type(e).__name__, e, _FAILURE_RETRY_SECONDS,
            )
            return
        self._filings_restored = True
        logger.info("[earnings-live] restored %d filing(s) for %s to %s (%s)", len(held),
                    days[0].isoformat(), days[-1].isoformat(),
                    ", ".join(f"{f.symbol} {f.day.isoformat()}" for f in held[:20]))

    async def keep_filing(self, symbol: str, day: date, at: datetime) -> None:
        """Write what the record holds for (symbol, day) to `earnings_filing_cache`: the record's
        filing, the first verified one, whatever the round just brought. Never raises."""
        rec = self._records.get((symbol.upper(), day))
        if rec is not None and rec.filing is not None:
            await _store_filing(filing_cache_row(rec.symbol, day, rec.filing, at))

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
        though FMP's row still reads pending (`absorb_filing`), and for a filing an earlier
        process read (`restore_filing`). A report whose only figure has since been taken off
        waits for one to show: a push must have something to report.
        """
        n = et_now(now)
        today = n.date()
        days = set(live_days(today))
        landed = (
            snapshot_of(rec, today, n)
            for rec in self._records.values()
            if rec.day in days and rec.landed_at is not None and rec.landed_from_pending
        )
        return [snap for snap in landed if has_results(snap.eps, snap.revenue)]

    def pending_symbols(self, day: date, symbols: Iterable[str]) -> List[str]:
        """The given symbols (order kept) with a record on `day` whose results are incomplete,
        or whose revenue is a zero its filing could still take off (`_zero_awaits_filing`). This
        is the SEC watch's list: a filing ends that watch. FMP's fetch reads the wider
        `_fmp_wanted`."""
        out = []
        for symbol in symbols:
            rec = self._records.get((symbol.upper(), day))
            if rec is None:
                continue
            revenue = revenue_metric(rec)
            if not is_complete(eps_metric(rec), revenue) or _zero_awaits_filing(rec, revenue):
                out.append(symbol.upper())
        return out

    def fetched_at(self, day: date) -> Optional[float]:
        """When this module's own fetch last answered for `day`, on the monotonic clock the
        Tracking memo stamps its days with, or None when none has."""
        return self._fetched.get(day)

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
        hot_ttl = EARNINGS_LIVE_HOT_TTL_SECONDS - _DUE_SLACK_SECONDS
        cold_ttl = (EARNINGS_LIVE_COLD_TTL_SECONDS if is_trading_day(today)
                    else EARNINGS_LIVE_CLOSED_DAY_TTL_SECONDS) - _DUE_SLACK_SECONDS
        due = []
        for day in earlier:
            last = self._fetched.get(day)
            if last is None:
                due.append(day)
            elif self._is_hot(day, n, symbols):
                if mono - last >= hot_ttl:
                    due.append(day)
            elif self._fmp_wanted(day, symbols) and mono - last >= cold_ttl:
                # Outside the hot window a late FMP fill is still worth picking up, slowly, and
                # once FMP has confirmed that day's watched reports nothing is left to ask.
                due.append(day)
        if self._is_hot(today, n, symbols):
            last = self._fetched.get(today)
            if last is None or mono - last >= hot_ttl:
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
        if time.monotonic() < self._failed_until - _DUE_SLACK_SECONDS:
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
            self._end_unlisted(day, rows)

    def _end_unlisted(self, day: date, rows: List[Dict[str, Any]]) -> None:
        """Note the reports on `day` that an own fetch's `rows` leave out and that hold no raw
        figure from either source, and forget those left out since at least
        `EARNINGS_LIVE_UNLISTED_END_SECONDS` ago (A REPORT FMP MOVES in the module docstring).
        One fetch only starts that clock: the record it keeps is what keeps the day asked and
        remembers the report was seen pending. The fold before this one stopped the clock of
        every report these rows list."""
        listed = {symbol for symbol in map(_row_symbol, rows) if symbol}
        if not listed or len(rows) >= _TRUNCATION_ROWS:
            return
        mono = time.monotonic()
        hold = EARNINGS_LIVE_UNLISTED_END_SECONDS - _DUE_SLACK_SECONDS
        left_out: List[str] = []
        ended: List[str] = []
        for (symbol, rec_day), rec in self._records.items():
            if (rec_day != day or symbol in listed or rec.fmp_eps is not None
                    or rec.fmp_revenue is not None or rec.filing is not None):
                continue
            if rec.unlisted_since is None:
                rec.unlisted_since = mono
                left_out.append(symbol)
            elif mono - rec.unlisted_since >= hold:
                ended.append(symbol)
        for symbol in ended:
            del self._records[(symbol, day)]
        if left_out:
            logger.info(
                "[earnings-live] %s: FMP's answer left out %d report(s) with no figure, asking "
                "again before ending them (%s)", day.isoformat(), len(left_out),
                ", ".join(sorted(left_out)[:20]),
            )
        if ended:
            logger.info(
                "[earnings-live] %s: FMP's answers no longer list %d report(s) with no figure, "
                "ended (%s)", day.isoformat(), len(ended), ", ".join(sorted(ended)[:20]),
            )

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


#: The least a loop rests between two ticks, however long the last one ran.
_MIN_TICK_GAP_SECONDS = 1.0
#: How far the filing loop runs behind the FMP loop. Its first tick then finds the records the
#: first fold opened (a restart in a release window keeps its minute), and the watched list is
#: one read, not the same read twice at one moment.
_FILING_LOOP_DELAY_SECONDS = 10.0


async def _forever(
    tick: Callable[[datetime], Awaitable[Any]],
    interval: int,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    """Run `tick` every `interval` seconds, counted from each tick's start, so a round that read
    25 symbols in 30 s does not stretch "every minute" to 90 s. Never returns: a tick that
    fails is logged and the next one runs. Only the cancel of this task ends it."""
    while True:
        started = time.monotonic()
        try:
            if settings.EARNINGS_LIVE_ENABLED:
                await tick(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            # Not ours: a future the tick joined was cancelled by someone else. Re-raising would
            # end this loop for good with nothing logged, and the other would run on alone.
            logger.error("[earnings-live] %s: cancelled from outside the loop, kept as a "
                         "failed tick", tick.__name__, exc_info=True)
        except Exception as e:
            logger.error("[earnings-live] %s failed (%s: %s)", tick.__name__, type(e).__name__,
                         e, exc_info=True)
        await sleep(max(_MIN_TICK_GAP_SECONDS, interval - (time.monotonic() - started)))


async def run_earnings_live_loop(
    *, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    """Background loop (main.py lifespan, with the other notification loops). Two forever-loops
    under the one spawned task, every 60 s each (TWO LOOPS in the module docstring): the FMP tick
    refreshes the live days for everyone's watched tickers and hands their new landings to the
    push sender, and the filing tick watches the ones reporting today. Neither dies on an error or
    holds up the other. Cancelling the spawned task cancels both, through `gather`, and it ends
    only once both have unwound."""
    await sleep(45)
    interval = max(int(settings.EARNINGS_LIVE_INTERVAL_SECONDS), 15)

    async def _filing_loop() -> None:
        await sleep(_FILING_LOOP_DELAY_SECONDS)
        await _forever(earnings_filing_tick, interval, sleep=sleep)

    # Neither loop raises, so `return_exceptions` does one job: without it `gather` ends at the
    # first child cancelled, and `main.py` closes the shared HTTP clients under the other.
    await asyncio.gather(
        _forever(earnings_live_tick, interval, sleep=sleep),
        _filing_loop(),
        return_exceptions=True,
    )


async def earnings_filing_tick(
    now: datetime,
    *,
    watched: Optional[Sequence[str]] = None,
    sec: Any = None,
    gemini: Any = None,
) -> Dict[str, int]:
    """One SEC round over the watched reports still pending today, from the records the FMP tick
    folded. Each verified figure goes straight into its record, then into
    `earnings_filing_cache`, and nothing here sends a push: the next FMP tick does. Every
    dependency is injectable, the table through `get_supabase`, so the suite never reaches a
    network."""
    from app.services.earnings_filing_service import get_earnings_filing_watcher

    started = time.monotonic()
    service = get_earnings_live_service()
    await service.restore_filings(now)
    if watched is None:
        watched = await _watched_ranked_cached()
    n = et_now(now)
    today = n.date()
    pending_today = service.pending_symbols(today, watched)
    stats = {"watched": len(watched), "pending": len(pending_today), "filings": 0}
    if not pending_today or n.hour < EARNINGS_LIVE_START_HOUR_ET:
        return stats

    async def _absorb(symbol: str, figures: FilingFigures) -> None:
        # The figure's own moment: `checked_at` and the landing must not say a long round's start.
        at = now + timedelta(seconds=time.monotonic() - started)
        service.absorb_filing(symbol, today, figures, at)
        await service.keep_filing(symbol, today, at)

    watcher = get_earnings_filing_watcher()
    watcher.prune(live_days(today))
    found = await watcher.run_once(now, pending_today, today, sec=sec, gemini=gemini,
                                   on_figures=_absorb)
    stats["filings"] = len(found)
    return stats


async def earnings_live_tick(
    now: datetime,
    *,
    fmp: Any = None,
    watched: Optional[Sequence[str]] = None,
    dispatch: Optional[Callable[[List[LiveSnapshot], datetime], Awaitable[Any]]] = None,
) -> Dict[str, int]:
    """One FMP round: take back what the table holds once per process, fold the memo, fetch what
    is due, hand the watched landings to the push sender, the filing tick's among them. Every
    dependency is injectable, the table through `get_supabase`, so the suite never reaches a
    network."""
    from app.services import tracking_service as ts

    service = get_earnings_live_service()
    await service.restore_filings(now)
    if fmp is None:
        from app.integrations.fmp import get_fmp_client

        fmp = get_fmp_client()
    if watched is None:
        watched = await _watched_ranked_cached()
    stats = {"watched": len(watched), "fetched": 0, "landed": 0}
    if not watched:
        return stats
    n = et_now(now)
    today = n.date()

    # Today's rows ride on the Tracking memo (30 min), the same shared object every feed build
    # reads. Reading it through its front door pays for a round when it is old, so the tick does
    # that only in `tick_refreshes_memo`, when it holds no rows for today (a restart), or when a
    # held row is too old to open its record. Should that round fail, the fold below refuses the
    # same rows, so a moment of FMP trouble cannot turn them into a report day.
    stored_at = ts.earnings_day_stored_at(fmp, today)
    calendar, _ = ts._held_earnings_window(fmp, [today])
    if (stored_at is None or tick_refreshes_memo(n)
            or service.old_rows_would_open(today, calendar, stored_at, watched)):
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
