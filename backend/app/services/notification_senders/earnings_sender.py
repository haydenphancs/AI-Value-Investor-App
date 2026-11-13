"""Earnings notifications — "reports tomorrow" and "beat/missed by X%".

THREE FMP CALLS PER RUN, one per ET day. `get_earnings_calendar(from, to)` is
market-wide, so yesterday, today and tomorrow serve BOTH passes for every ticker anyone
watches. The per-symbol `get_earning_calendar_full` would be ~200 calls to learn the same
thing — that is the "one call returns all fields" rule from
`project_fmp_api_architecture.md`, and it is the single most important design decision in
this file.

One call per DAY, never one three-day request (2026-10-22): FMP cuts an
`earnings-calendar` answer at 4,000 rows without saying so and keeps the NEWEST dates, and
in season three days pass that (Oct 28–30 2026 ≈ 4,528 rows, Nov 4–6 ≈ 4,471), so the
single request silently lost part of YESTERDAY — the late-arriving actuals the result pass
exists for. `fetch_calendar_days` is all-or-nothing: one failed day raises, which is the
retry (below).

Two passes over those rows:

  A. **Upcoming** — a ticker reports tomorrow (or later today, before the open).
     The value is preparation: a user holding a position wants to know a print is
     coming, not to be surprised by a 9% gap the next morning.
  B. **Result** — a ticker reported and the EPS surprise cleared a materiality bar.
     No surprise number, no notification; a print that landed on consensus is not news.

Scheduled after the close (16:00 ET) because that is when both halves are true at once:
tomorrow's calendar is settled, and today's after-close prints have started landing.

WHAT THE PASS READS REACHES THE LIVE RECORDS, BEFORE IT TELLS ANYONE. It asks FMP for itself, and
the live loop stops asking about a report once it is complete, so these rows can be newer than
the record. Told from such a row, New York's banner and the sheet it opens would disagree, and a
row held for Berlin's morning, whose text comes from the record at its wake
(`earnings_live_sender.copy_at_wake`), would be dropped or reversed by the older figure. So a
record is never older than a row it can overrule (`_fold_into_live_records`).

THE LIVE PUSH SWITCHES STOP AT `earnings_live_sender`. This pass reads neither
`EARNINGS_LIVE_PUSH_ENABLED` nor `EARNINGS_LIVE_RESULT_PUSH_ENABLED`. Both ship off, so as shipped
this pass is what tells a result; with one on, it finds the key taken for whoever the live send
reached and tells the rest (`result_dedup_key`).

THIS PASS FOLLOWS THE RECORD (the owner's "Follow the record", 2026-11-11). FMP can list one
report twice, one actual beside two consensus figures, and the record takes the EPS from the row
it ranks strongest (`earnings_live_service`, DUPLICATE ROWS). This pass reads each report from
that same row (`eps_row_per_report`, the record's own ranking, never a copy of it), whichever row
FMP lists first, and the other row never speaks for the report. Two kinds of report are told as
the record reads them, once this pass has folded what it read (`select_results`), because the
record holds what their rows cannot show. One listed more than once: the record can hold what
neither row says, the consensus it read while the report was pending beside a row with none of
its own. One whose EPS is 0: the zero rules weigh it against what the record already holds, a
consensus that makes a row of zeros a small name's real report or FMP's unfilled one, or a figure
the zeros cannot take back. So New York's banner, the sheet it opens and a row held for Berlin,
whose text comes from the record at its wake, say one thing; they can part only if FMP moves after
the pass. Only an EPS this answer gave the record is told, by `record_surprise`, the live sender's
own rule: the fold names the row it took it from (`EarningsLiveService.observe_rows`), because a
zero the record held reads just like a new one. The dropped-digit guard still reads no GAAP EPS
(the owner's "Keep it quiet", 2026-11-11). With no record to follow, live results off or that
day's fold failed, the pass reads the row alone. Any other report listed once is told from its own
row, as this pass always was, which its record reads the same way unless the row lacks an
estimate: what that leaves untold is in `earnings_live_sender`'s list of what off costs. A report
listed more than once, or read from its record, is logged when another of its rows would have told
something else, or when a figure past the bar goes untold, with the figures it was judged on and
those passed over.

WHAT THIS FILE DELIBERATELY DOES NOT DO
---------------------------------------
It does not decide whether any particular user gets a notification. Preference, group
master, per-category cap, quiet hours and dedup are all `push_dispatch_service`'s job.
A sender that reached past that would be a sender that can ignore an opt-out.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

from app.config import settings
from app.integrations.fmp import get_fmp_client
from app.services._earnings_common import (
    eps_digit_shift_suspect,
    eps_print_covers,
    parse_fmp_timing,
    timing_sentence,
)
from app.services.earnings_live_service import (
    SOURCE_FMP,
    LiveSnapshot,
    eps_row_per_report,
    get_earnings_live_service,
)
from app.services.earnings_window_service import fetch_calendar_days
from app.services.notification_jobs import JOB_EARNINGS, claimed_job
from app.services.notification_kinds import (
    KIND_EARNINGS_RESULT,
    KIND_EARNINGS_UPCOMING,
    ticker_route,
)
from app.services.push_dispatch_service import WatcherLookupFailed, get_push_dispatch_service
from app.services.updates_materiality import finite
from app.utils.market_hours import ET, next_trading_day, previous_trading_day

logger = logging.getLogger(__name__)

# A surprise smaller than this is noise. Analysts cluster tightly around consensus and a
# 1% beat is a rounding difference, not an event.
MIN_SURPRISE = 0.05

# Division guard. `est != 0` is NOT enough: a $0.001 estimate against a $0.40 actual
# yields a 39,900% "surprise", which is arithmetically true and completely meaningless.
# Below this magnitude there is no honest percentage to quote, so the notification is
# skipped rather than fabricated.
MIN_ABS_ESTIMATE = 0.01

# Bound the fan-out per pass. A heavy earnings day has ~200 reporters; without this a
# single evening could fan out to every ticker on the calendar. The per-user category
# cap (4/day) is the backstop, but doing the work at all is the cost being avoided.
MAX_SYMBOLS_PER_PASS = 60

#: How many distinct watched tickers the RPC returns.
#:
#: ⚠️ 1000, NOT a bigger number that reads as "no limit". This said 5000 and claimed to be
#: "far above any plausible watchlist breadth, so the filter itself never becomes the
#: truncation" — but PostgREST clamps every response on this project to ~1,000 rows
#: whatever the client asks for, RPC results included. So the effective value was already
#: 1000 and the constant was describing a bound that did not exist. The rows it silently
#: drops are the LEAST-watched tickers, which is the better half of the trade, but the
#: number has to tell the truth or the next reader will size a decision on it. If the
#: watched universe ever genuinely exceeds this, the RPC needs paging — raising the
#: constant alone changes nothing.
_WATCHED_LOOKUP_LIMIT = 1000


def _et_today(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(ET)).astimezone(ET).date()


def _row_date(row: Dict[str, Any]) -> Optional[str]:
    """The calendar row's date as `YYYY-MM-DD`, or None.

    FMP occasionally returns a full timestamp or an empty string here. Slicing to 10
    normalises the first; the length check rejects the second — a missing date makes the
    row unusable for BOTH the window filter and the dedup key, and a dedup key built on
    an empty date would collide across every symbol.
    """
    raw = str(row.get("date") or "")[:10]
    return raw if len(raw) == 10 and raw[4] == "-" and raw[7] == "-" else None


def _symbol(row: Dict[str, Any]) -> Optional[str]:
    sym = str(row.get("symbol") or "").strip().upper()
    return sym or None


def surprise_pct(row: Dict[str, Any]) -> Optional[float]:
    """EPS surprise as a signed fraction, or None when there is no honest one.

    Returns None — not 0.0 — when either side is missing, non-finite, or the estimate is
    too close to zero to divide by. `finite()` is the shared guard that already caught
    FMP's NaN/Infinity JSON tokens elsewhere in this repo; feeding NaN into a comparison
    silently answers False for every branch and disables the gate.

    None too when the actual is printed to the cent and the consensus sits inside its half
    cent (`eps_print_covers`): $0.01 against 0.0148 is a 32% miss on paper, and the print
    cannot say which side of that consensus the company landed on. Both senders read this,
    so the daily pass never states what the live sender held.
    """
    est = finite(row.get("epsEstimated"))
    act = finite(row.get("epsActual") if "epsActual" in row else row.get("eps"))
    if est is None or act is None:
        return None
    if abs(est) < MIN_ABS_ESTIMATE or eps_print_covers(act, est):
        return None
    return (act - est) / abs(est)


def select_upcoming(
    rows: List[Dict[str, Any]], today: date
) -> List[Tuple[str, str, str]]:
    """Rows worth a "reports soon" notification → (symbol, date, timing token).

    TOMORROW ONLY, and that narrowness is the point. The job runs after the close, so
    every today-dated row has already happened: a BMO print reported this morning and an
    AMC print is landing right now. Announcing either as "upcoming" would be telling
    someone to expect news they have already missed — worse than saying nothing. Today's
    AMC prints are the RESULT pass's job, where the actual numbers exist.

    De-duplicated by symbol: FMP can carry two rows for one company across a
    reschedule, and two banners for one earnings date is exactly the noise that gets an
    app's notifications switched off.
    """
    tomorrow = today + timedelta(days=1)
    out: List[Tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in rows:
        sym, when = _symbol(row), _row_date(row)
        if not sym or not when or sym in seen:
            continue
        try:
            row_date = date.fromisoformat(when)
        except ValueError:
            continue
        if row_date != tomorrow:
            continue
        seen.add(sym)
        out.append((sym, when, parse_fmp_timing(row.get("time"))))
    return out


def in_result_window(report_day: date, today: date) -> bool:
    """May a result push still go out for a report dated `report_day`? Today and calendar
    yesterday: an after-close print lands in the evening, and a calendar row can take until
    the next morning to carry `epsActual`. A wider window would start re-announcing last
    week's earnings on a slow news day.

    The ONE rule for every result push, this module's daily pass and
    `earnings_live_sender` alike. Calendar days, never trading days, so a Friday report is
    out by Sunday. It decides who is told, and so which of a pass's rows speak about a result
    (`_fold_into_live_records`), and nothing else: the card and the sheet keep the report.
    """
    return report_day in (today, today - timedelta(days=1))


@dataclass(frozen=True)
class FoldedRecord:
    """A report's live record as this pass's fold left it (`_live_record_reader`): the snapshot it
    reads as, and `eps_row`, the row of the pass's answer it took its EPS from, None when it took
    none (`EarningsLiveService.observe_rows`). The row is what tells an EPS this answer gave the
    record from one the record held before: a zero it held reads just like a new one."""

    snapshot: LiveSnapshot
    eps_row: Optional[Dict[str, Any]]


#: A report's live record as this pass's fold left it, by (symbol, report date), or None when
#: there is none to follow (`_live_record_reader`).
LiveRecordReader = Callable[[str, date], Optional[FoldedRecord]]

#: The rows each report's record took its EPS from, by day folded, then by symbol
#: (`_fold_into_live_records`).
FoldedDays = Dict[date, Dict[str, Optional[Dict[str, Any]]]]

#: Why a report read past the bar is told to nobody, in a log's words.
_UNDER_THE_BAR = "no surprise past the bar"
_DROPPED_DIGIT = "the dropped-digit signature"
_NO_EPS_FROM_THIS_ANSWER = "its live record keeps no EPS from this answer"


def _eps_of(row: Dict[str, Any]) -> Optional[float]:
    return finite(row.get("epsActual") if "epsActual" in row else row.get("eps"))


def _past_the_bar(row: Dict[str, Any]) -> bool:
    """Would this row, read alone, clear the bar?"""
    surprise = surprise_pct(row)
    return surprise is not None and abs(surprise) >= MIN_SURPRISE


def record_surprise(snap: LiveSnapshot) -> Optional[float]:
    """The surprise an `earnings_result` push may state for a report as its live record reads it,
    or None: FMP's own adjusted EPS, an honest percentage (`surprise_pct`), past `MIN_SURPRISE`.
    The one rule for it: `earnings_live_sender` sends and wakes on it, and this pass tells by it a
    report it reads as its record does (`select_results`)."""
    eps = snap.eps
    if eps.actual is None or eps.source != SOURCE_FMP:
        return None
    surprise = surprise_pct({"epsEstimated": eps.estimate, "epsActual": eps.actual})
    return surprise if surprise is not None and abs(surprise) >= MIN_SURPRISE else None


def select_results(
    rows: List[Dict[str, Any]], today: date, *, record: Optional[LiveRecordReader] = None,
) -> List[Tuple[str, str, float]]:
    """Rows worth a "beat/missed" notification → (symbol, date, surprise fraction), for the
    reports inside `in_result_window`. One per symbol: of its reports that clear the bar, the
    first listed.

    A report FMP lists more than once, or whose EPS is 0, is told as `record` reads it, its live
    record after this pass's fold, when there is one, and only with an EPS this answer gave that
    record (THIS PASS FOLLOWS THE RECORD in the module docstring). Every other report, and each of
    those with no record to follow, is read from the row its live record takes the EPS from
    (`eps_row_per_report`), and from no other: a twin past the bar never stands in for that row,
    even when the row says nothing. A report passed over leaves its symbol's report under another
    date free to speak."""
    picks = eps_row_per_report(rows, today)
    reports: Dict[Tuple[str, date], List[Dict[str, Any]]] = {}
    for row in rows:
        sym, when = _symbol(row), _row_date(row)
        if not sym or not when:
            continue
        try:
            row_date = date.fromisoformat(when)
        except ValueError:
            continue
        if in_result_window(row_date, today):
            reports.setdefault((sym, row_date), []).append(row)
    out: List[Tuple[str, str, float]] = []
    seen: Set[str] = set()
    for (sym, day), listed in reports.items():
        if sym in seen:
            continue
        surprise = _report_surprise(sym, day, listed, picks.get((sym, day)), record)
        if surprise is not None:
            seen.add(sym)
            out.append((sym, day.isoformat(), surprise))
    return out


def _follows_its_record(listed: List[Dict[str, Any]]) -> bool:
    """Is a report with a live record to follow told as that record reads it (`select_results`)?
    One FMP lists more than once is, since its record can hold what no row of it says, the
    consensus it read while the report was pending beside a row with none of its own. So is one
    whose EPS is 0: the zero rules weigh it against what the record already holds, a consensus
    that makes a row of zeros a small name's real report or FMP's unfilled one, or a figure the
    zeros cannot take back, and no row shows those. Any other row reads in its record as it does
    alone, an estimate it lacks aside (`earnings_live_sender`'s list of what off costs)."""
    return len(listed) > 1 or _eps_of(listed[0]) == 0


def _report_surprise(sym: str, day: date, listed: List[Dict[str, Any]],
                     pick: Optional[Dict[str, Any]],
                     record: Optional[LiveRecordReader]) -> Optional[float]:
    """The surprise one report is told with, or None (`select_results`). `listed` holds the
    report's rows, `pick` the one its live record would read, for a report read from its row. A
    figure past the bar told to nobody is logged with what it was judged on, and so is a report
    listed more than once whose other row would have told something else."""
    when = day.isoformat()
    folded = record(sym, day) if record is not None and _follows_its_record(listed) else None
    if folded is None:
        if pick is None:
            # No row of the report has an EPS the record would keep: an EPS of 0 beside a revenue
            # of 0 is FMP's unfilled row, not a "-100% miss". The live path's own rule, which
            # keeps a real breakeven and a pre-revenue name's zero report. Read from the rows
            # alone: with a record to follow the report reads as the record does, below.
            refused = next((row for row in listed if _past_the_bar(row)), None)
            if refused is not None:
                logger.warning(
                    "earnings notifications: %s %s EPS %s beside revenue %s (consensus %s) is an "
                    "unfilled row by the live record's rule, with no record to follow — no result "
                    "alert", sym, when, _eps_of(refused), refused.get("revenueActual"),
                    refused.get("revenueEstimated"),
                )
            return None
        act, est = _eps_of(pick), finite(pick.get("epsEstimated"))
        surprise = surprise_pct(pick)
        if surprise is not None and abs(surprise) < MIN_SURPRISE:
            surprise = None
        why = None if surprise is not None else _UNDER_THE_BAR
    else:
        # The record's own report, FMP's own figure, and the one this answer gave it: a figure kept
        # from before is one FMP has since taken back, and a row of zeros the record reads as
        # unfilled shows none.
        pick, snap = folded.eps_row, folded.snapshot
        act, est = snap.eps.actual, snap.eps.estimate
        if pick is None:
            surprise, why = None, _NO_EPS_FROM_THIS_ANSWER
        elif (snap.reads_as is None and snap.eps.source == SOURCE_FMP and act is not None
              and act == _eps_of(pick)):
            surprise = record_surprise(snap)
            why = None if surprise is not None else _UNDER_THE_BAR
        else:
            surprise = None
            why = f"its live record does not show this answer's EPS {_eps_of(pick)}"
    # A dropped/added-digit EPS (feed 0.169 for a real 1.69) is a "-90% miss" that never
    # happened. The Financials tab, the report and Earnings Shockers all refuse it
    # (`_earnings_common.eps_digit_shift_suspect`); a push is the worst place to print it. The
    # guard is never handed the filing's GAAP EPS, which the record reads: the owner's "Keep it
    # quiet" (2026-11-11).
    if surprise is not None and eps_digit_shift_suspect(act, est):
        surprise, why = None, _DROPPED_DIGIT
    if folded is None and len(listed) == 1:
        if why == _DROPPED_DIGIT:
            logger.warning(
                "earnings notifications: %s %s EPS %s vs estimate %s has the dropped-digit "
                "signature — no result alert", sym, when, act, est,
            )
        return surprise
    passed_over = [row for row in listed if row is not pick and _past_the_bar(row)
                   and (_eps_of(row), finite(row.get("epsEstimated"))) != (act, est)]
    untold = why not in (None, _UNDER_THE_BAR) and any(_past_the_bar(row) for row in listed)
    if passed_over or why == _DROPPED_DIGIT or untold:
        logger.warning(
            "earnings notifications: %s %s is listed %s — %s, from %s: EPS %s vs estimate %s%s",
            sym, when, "more than once" if len(listed) > 1 else "once, with an EPS of 0",
            "told" if why is None else f"not told ({why})",
            "the row its live record would read" if folded is None else "its live record",
            act, est, "; passed over: " + ", ".join(
                f"EPS {_eps_of(row)} vs estimate {finite(row.get('epsEstimated'))}"
                for row in passed_over) if passed_over else "",
        )
    return surprise


def _fold_into_live_records(
    by_day: Dict[date, List[Dict[str, Any]]], today: date, now: Optional[datetime]
) -> FoldedDays:
    """Give the live records what this pass just read (the module docstring has why), for the
    days a result can come from: tomorrow is read for the upcoming pass, and an actual on a date
    not reached yet is garbage. It is a real FMP answer, so it folds as one: the sheet's
    "updated" moves, and memo rows from before it can no longer put an older figure back.
    Returns the days it folded, the only ones whose records the pass may follow
    (`_live_record_reader`), each with the row every report's record took its EPS from, None
    when it took none (`EarningsLiveService.observe_rows`).

    Called the moment the fetch returns: a later await would let an own fetch's newer answer in
    underneath this one. With live results off no loop runs to prune a record, and nothing reads
    one.

    A failure costs the sheet and the reports only the record can tell: a report FMP lists twice
    whose told row has no estimate of its own, and an EPS of 0 the record holds on a consensus or
    an estimate from an earlier answer. The pass goes on and reads that day's rows alone, as it
    did before it followed the record, and a held row then wakes on the record as it stood.
    """
    folded: FoldedDays = {}
    if not settings.EARNINGS_LIVE_ENABLED:
        return folded
    at = now or datetime.now(ET)
    live = get_earnings_live_service()
    for day, rows in by_day.items():
        if not in_result_window(day, today):
            continue
        try:
            took = live.observe_rows(day, rows, at, fetched=True)
        except Exception as e:
            logger.warning(
                "earnings notifications: could not fold %d calendar row(s) for %s into the live "
                "records (%s: %s) — the pass goes on, reading that day's rows alone, and a result "
                "held on that day wakes on the record as it stood",
                len(rows), day.isoformat(), type(e).__name__, e, exc_info=True,
            )
            continue
        folded[day] = took
    return folded


def _live_record_reader(folded: Mapping[date, Mapping[str, Optional[Dict[str, Any]]]],
                        now: Optional[datetime]) -> Optional[LiveRecordReader]:
    """How `select_results` reads a report's live record: on a day this pass folded, so the
    record holds what the pass read, beside the row it took its EPS from, and never on another,
    where it can be older than the rows. A report the fold did not reach has none either. None
    when the pass folded no day, live results off included. A read that fails is logged and
    reads as no record, which leaves the report its row."""
    if not folded:
        return None
    live = get_earnings_live_service()
    at = now or datetime.now(ET)

    def read(symbol: str, day: date) -> Optional[FoldedRecord]:
        took = folded.get(day)
        if took is None or symbol not in took:
            return None
        try:
            snap = live.snapshot(symbol, day, at)
        except Exception as e:
            logger.warning(
                "earnings notifications: could not read the live record of %s %s (%s: %s) — "
                "read from the row its record would read", symbol, day.isoformat(),
                type(e).__name__, e, exc_info=True,
            )
            return None
        return None if snap is None else FoldedRecord(snap, took[symbol])

    return read


def upcoming_copy(symbol: str, timing: str) -> Tuple[str, str]:
    """Title/body for a "reports soon" notification.

    Informational, never directive. FINRA and the SEC name push notifications explicitly
    as a digital-engagement practice under supervision, so the copy states the fact and
    stops — no "position yourself", no "don't miss it".
    """
    sentence = timing_sentence(timing)
    when = f"tomorrow {sentence}" if sentence else "tomorrow"
    return f"{symbol} reports earnings", f"{symbol} is scheduled to report {when}."


def result_copy(symbol: str, surprise: float) -> Tuple[str, str]:
    """Title/body for a result notification.

    States the direction and the magnitude against consensus. It does NOT say whether
    that is good — a beat on a lowered bar is not good news, and the app is not going to
    resolve that in a 180-character banner.
    """
    pct = abs(surprise) * 100
    verb = "beat" if surprise > 0 else "missed"
    return (
        f"{symbol} earnings are out",
        f"EPS {verb} consensus by {pct:.0f}%. Tap for the full picture.",
    )


def result_dedup_key(symbol: str, when: str) -> str:
    """The ONE key a report's result alert is claimed under — whichever sender gets there
    first. While its switches are on, `earnings_live_sender` claims it for both `earnings_live`
    and the live-timed `earnings_result`, and this module's daily pass then finds it taken, so
    nobody is told twice.
    Keyed on the EARNINGS date (see `_dispatch`)."""
    return f"{KIND_EARNINGS_RESULT}:{symbol.upper()}:{str(when)[:10]}"


def neighbouring_result_keys(symbol: str, when: str) -> List[str]:
    """The result keys of `symbol`'s reports on every other calendar day from the trading day
    before `when` through the trading day after it, oldest first. FMP's leftover row after a
    reschedule dates one report twice, and a company does not report twice in two days, so a
    user holding one of these was told about this report: both senders pass them as
    `unless_claimed`.

    Either side, because FMP can fill the two rows in either order, and every calendar day, so
    the relation runs both ways across a closed day: a report can carry a Saturday's date, and
    `previous_trading_day` steps over it, so a Monday row would never name it."""
    day = date.fromisoformat(str(when)[:10])
    first = previous_trading_day(day)
    span = (next_trading_day(day) - first).days
    around = (first + timedelta(days=offset) for offset in range(span + 1))
    return [result_dedup_key(symbol, d.isoformat()) for d in around if d != day]


def _watched_tickers_ranked() -> List[str]:
    """Every ticker at least one user watches, MOST-WATCHED FIRST (the RPC's own order).

    The live loop ranks its SEC budget by it and pushes only for the tickers on it. Same RPC,
    same 1,000-row clamp, same warning when the clamp bites and the same RAISE-on-failure
    contract as `_watched_tickers`, for the same reason: an empty answer is indistinguishable
    from "nobody watches anything".
    """
    from app.database import get_supabase

    rows = get_supabase().rpc(
        "get_top_watchlist_tickers", {"n": _WATCHED_LOOKUP_LIMIT}
    ).execute().data or []
    if len(rows) >= _WATCHED_LOOKUP_LIMIT:
        # The raw rows, as in `_watched_tickers`: the clean list below can come up short.
        logger.warning(
            "[earnings-live] the watched-ticker read returned %d rows (the %d-row clamp) — "
            "less-watched tickers get no live push, stay outside the SEC watch and cannot "
            "keep a day hot",
            len(rows), _WATCHED_LOOKUP_LIMIT,
        )

    def _count(row: Dict[str, Any]) -> float:
        try:
            return float(row.get("watch_count") or 0)
        except (TypeError, ValueError):
            return 0.0

    ranked: List[str] = []
    seen: set = set()
    for row in sorted(rows, key=_count, reverse=True):
        sym = str(row.get("ticker") or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            ranked.append(sym)
    return ranked


def _watched_tickers() -> set:
    """Every ticker at least one user watches, upper-cased.

    RAISES on a read failure — it does NOT return an empty set.

    An empty set is indistinguishable from "nobody watches anything", and the caller
    filters the calendar down to it, sends nothing, and then marks the pass successful —
    which stamps `run_day` and stops the day from ever being retried. So a single
    Cloudflare 520 or a momentary 42501 on this one RPC used to cost EVERY user their
    whole day of earnings notifications, leaving one WARNING line as the only trace.
    Before this read existed the pass made no Supabase call at all, so no transient DB
    failure could consume a day; raising restores that property. `run_earnings_notifications`
    is explicitly documented to raise on an upstream failure because that IS the retry
    mechanism.

    `get_top_watchlist_tickers` is the same SECURITY DEFINER RPC the pre-warmers use, so
    this adds no new surface.
    """
    from app.database import get_supabase

    try:
        rows = get_supabase().rpc(
            "get_top_watchlist_tickers", {"n": _WATCHED_LOOKUP_LIMIT}
        ).execute().data or []
    except Exception as e:
        logger.warning(
            "earnings notifications: watched-ticker read failed (%s: %s) — failing the "
            "pass so the day is RETRIED rather than stamped complete",
            type(e).__name__, e,
        )
        raise
    if len(rows) >= _WATCHED_LOOKUP_LIMIT:
        # The clamp bit: anyone watching a ticker outside the top-N most-watched gets no
        # earnings alert today, silently. Loud so the day it first happens is visible; the
        # fix when it does is a DISTINCT-returning membership RPC fed the calendar's own
        # symbols (bounded by the calendar, never by watcher count) — NOT a bigger N, and
        # NOT `watchlist_items.select("ticker").in_(...)`, which is one row per WATCHER and
        # clamps on a different axis (F17-8).
        logger.warning(
            "earnings notifications: the watched-ticker read returned %d rows (the %d-row "
            "clamp) — watchers of less-popular tickers will be skipped this pass",
            len(rows), _WATCHED_LOOKUP_LIMIT,
        )
    return {str(r["ticker"]).upper() for r in rows if r.get("ticker")}


def _only_watched(selected, watched: set):
    """Keep the calendar rows somebody actually watches, in the selector's own order."""
    return [row for row in selected if row and str(row[0]).upper() in watched]


async def _dispatch(
    *,
    kind: str,
    symbol: str,
    when: str,
    title: str,
    body: str,
) -> int:
    """Fan out to everyone watching `symbol`.

    The dedup key is keyed on the EARNINGS date, not the run date. That matters twice:
    a re-run on the same day cannot re-buzz, and a company that reschedules its print
    gets a genuinely new key rather than being silently suppressed by yesterday's claim.
    A claim out to the trading day either side does stop a RESULT, for that user alone: that
    is one report FMP dated twice (`neighbouring_result_keys`). A report nobody was told about
    holds no claim, so a reschedule still gets its push.

    RAISES `WatcherLookupFailed` when the read of who watches `symbol`, or of who holds one of
    those keys, fails (`strict`): it told nobody and claimed nothing, and a 0 would read as a
    ticker nobody watches, so the pass would stamp the day with that ticker never told. The
    pass leaves the day open for it instead (`run_earnings_notifications`).
    """
    if kind == KIND_EARNINGS_RESULT:
        dedup_key = result_dedup_key(symbol, when)
        unless_claimed = neighbouring_result_keys(symbol, when)
    else:
        dedup_key = f"{kind}:{symbol}:{when}"
        unless_claimed = []
    return await get_push_dispatch_service().notify_watchers(
        ticker=symbol,
        title=title,
        body=body,
        dedup_key=dedup_key,
        unless_claimed=unless_claimed,
        strict=True,
        kind=kind,
        # FLAT SCALARS ONLY (iOS AnyCodable yields "" for anything nested).
        # The registry supplies the tab, so `earnings_result` lands on Financials
        # rather than making the user hunt for the numbers the alert is about. The report
        # date lets a result row open THAT report's live results and stand in for the same
        # report's card (`AlertDestination.Target.earningsLive`). With live results off there
        # are none to open, and the sheet would answer estimates under the announced figures.
        data=ticker_route(
            kind, symbol, report_date=when if settings.EARNINGS_LIVE_ENABLED else None,
        ),
    )


async def run_earnings_notifications(now: Optional[datetime] = None) -> Dict[str, int]:
    """One claimed pass. Returns per-pass counts.

    RAISES on an upstream failure, deliberately — that is the retry mechanism, not an
    oversight. `claimed_job`'s shielded `finally` releases the claim with success=False,
    which leaves `run_day` unset so the next hourly wake retries the SAME ET day. The
    lifespan loop catches and logs it. A swallowed exception here would look like a
    successful run of zero and skip the day silently.

    Degraded behaviour is the subtle part:
      * FMP raises → `run.success` stays False, so `run_day` is NOT stamped and the next
        hourly wake retries the SAME ET day. A transient upstream failure must become a
        retry, not a silently skipped day.
      * FMP returns `[]` for all three days → that is a market HOLIDAY (or a genuinely
        empty calendar), which is a SUCCESSFUL run of zero. Stamping `run_day` here is
        correct; retrying every hour against an empty calendar would be pure waste.
      * The read of who watches a ticker, or of who holds a neighbouring result key, fails
        (`WatcherLookupFailed`) → every other ticker is still told, then the pass raises, so
        the day stays open and the next hourly wake tells that ticker's watchers. The ledger's
        claim keeps anyone this wake told from hearing twice.
    """
    stats = {"upcoming": 0, "results": 0, "symbols": 0}

    async with claimed_job(JOB_EARNINGS) as run:
        if run is None:
            logger.debug("earnings notifications: not claimed (already run, or held)")
            return stats

        today = _et_today(now)
        # One market-wide call per ET day: yesterday (late-arriving actuals), today and
        # tomorrow (the upcoming pass). Both passes read the same rows. NEWEST day first,
        # the order FMP's own multi-day answer used, so `select_results`' one push per symbol
        # still prefers today's report over yesterday's for a symbol on both.
        # `fetch_calendar_days` raises on a failed or non-list day (the retry) and keeps
        # each row only under its own date.
        days = [today + timedelta(days=1), today, today - timedelta(days=1)]
        by_day = await fetch_calendar_days(get_fmp_client().get_earnings_calendar, days)
        folded = _fold_into_live_records(by_day, today, now)
        rows = [row for d in days for row in by_day.get(d, [])]

        if not rows:
            # Holiday / empty calendar. A successful run of zero — see the docstring.
            logger.info("earnings notifications: calendar empty for %s (holiday?)", today)
            run.success = True
            return stats

        # WATCHED FIRST, then cap.
        #
        # `rows` is the MARKET-WIDE calendar — hundreds of companies on a heavy day — and
        # the cap used to slice the selection positionally, before anyone asked who was
        # watching. So on exactly the days that matter most, whether a user heard about
        # their own holding came down to where FMP happened to place it in the response;
        # and `run.success = True` then stamped the day complete, so nothing retried.
        # Filtering to the watched set first makes the cap bite only when more than
        # MAX_SYMBOLS_PER_PASS *watched* companies report on the same day.
        watched = await asyncio.to_thread(_watched_tickers)
        upcoming_all = _only_watched(select_upcoming(rows, today), watched)
        # Judged on the watched rows alone: the same picks, and what it logs about a report is
        # about one someone can be told of.
        results_all = select_results([row for row in rows if _symbol(row) in watched], today,
                                     record=_live_record_reader(folded, now))
        upcoming = upcoming_all[:MAX_SYMBOLS_PER_PASS]
        results = results_all[:MAX_SYMBOLS_PER_PASS]
        if len(upcoming_all) > len(upcoming) or len(results_all) > len(results):
            logger.warning(
                "earnings notifications: %d upcoming / %d result symbol(s) WATCHED but the "
                "per-pass cap is %d — the tail is not notified today",
                len(upcoming_all), len(results_all), MAX_SYMBOLS_PER_PASS,
            )
        stats["symbols"] = len(upcoming) + len(results)

        # A failed read of who to tell costs that ticker this wake, never the others, and never
        # the day: it is retried at the next hourly wake, where the ledger keeps everyone this
        # one told from hearing twice. Stamped done, the day would leave that ticker untold, and
        # an after-close report is out of `in_result_window` before the next day's pass.
        unread: List[Tuple[str, str, str, WatcherLookupFailed]] = []
        sends = ([(KIND_EARNINGS_UPCOMING, symbol, when, upcoming_copy(symbol, timing))
                  for symbol, when, timing in upcoming]
                 + [(KIND_EARNINGS_RESULT, symbol, when, result_copy(symbol, surprise))
                    for symbol, when, surprise in results])
        for kind, symbol, when, (title, body) in sends:
            try:
                sent = await _dispatch(kind=kind, symbol=symbol, when=when,
                                       title=title, body=body)
            except WatcherLookupFailed as e:
                logger.warning(
                    "earnings notifications: %s %s %s: the read of who to tell failed (%s) — "
                    "nobody told for it on this wake; the day is left open so the next hourly "
                    "wake tells them", kind, symbol, when, e,
                )
                unread.append((kind, symbol, when, e))
                continue
            stats["upcoming" if kind == KIND_EARNINGS_UPCOMING else "results"] += sent

        run.notified = stats["upcoming"] + stats["results"]
        if unread:
            raise WatcherLookupFailed(
                f"earnings notifications: the read of who to tell failed for {len(unread)} of "
                f"{len(sends)} send(s) ("
                + ", ".join(f"{kind} {symbol} {when}" for kind, symbol, when, _ in unread)
                + f"); {run.notified} sent — the day stays open for the next wake"
            ) from unread[0][3]
        run.success = True

    logger.info(
        "earnings notifications: %d upcoming + %d result sends across %d symbol(s)",
        stats["upcoming"], stats["results"], stats["symbols"],
    )
    return stats
