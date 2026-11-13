"""Live earnings pushes — sent the moment results land (owner request 2026-10-29).

`earnings_live_service.run_earnings_live_loop` hands this module, every 60 s, the WATCHED
reports whose results it SAW land in this process. Two kinds, ONE dedup key per report
(`earnings_sender.result_dedup_key`), always sent one after the other in this coroutine —
never concurrently, so which copy a user gets can never depend on timing:

  1. `earnings_live` ("Live Results"): every watched report, the first time any figure lands.
     Held by `EARNINGS_LIVE_PUSH_ENABLED` (ships off: 1.02 has no toggle for it).
  2. `earnings_result` ("Results & Surprises"): the existing kind and copy (`result_copy`),
     sent the moment FMP's adjusted EPS exists and its surprise clears `MIN_SURPRISE` —
     instead of at the next 16:00 ET run. FMP numbers ONLY: this kind reaches 1.02 users,
     and a figure read from a filing is preliminary. Held by
     `EARNINGS_LIVE_RESULT_PUSH_ENABLED` (ships off: the owner's "Keep it off at first",
     2026-11-05).

With both off, as shipped, this module sends and claims nothing: a landed report keeps its card
and its sheet, and `run_earnings_notifications`' daily pass, which reads neither switch, tells
its result at 16:00 ET under the same key. For most reports the cost of off is that wait, up to a
day. Some lose the buzz outright, because the pass has rules of its own and tells only an EPS
its own answer gave: a report FMP lists once from that row alone, and one it lists more than once,
or whose EPS is 0, as the record reads it (`select_results`). These are the kinds known, and each
keeps the sheet:
  * A report FMP fills after the pass on the day after its date. That was the last pass whose
    window holds it (`in_result_window`), so a Thursday report filled on Friday at 17:05 is told
    by nobody.
  * A surprise with the dropped-digit signature that the filing's GAAP EPS clears. The record
    shows it. The pass judges the signature with no GAAP beside it, a report it reads as the
    record does included, and refuses it (`select_results`). Kept that way by the owner's choice
    of 2026-11-11, "Keep it quiet".
  * A report past `MAX_SYMBOLS_PER_PASS` on a pass's list. The next day's pass reads the same
    rows in the same order, so it stays cut, where this module's cap only moves a report to the
    next round.
  * A filled row with no estimate of its own, the report's only row, its EPS other than 0. The
    record keeps the estimate it held, so the sheet shows a surprise, and the pass has nothing in
    the row to divide by (`surprise_pct`). Beside a twin, or with an EPS of 0, the pass tells what
    the record shows.
  * A figure FMP takes back before the pass reads it: the row blank again, a row of zeros in its
    place, or off the calendar. The record keeps its figure through each, the zeros beside it
    being FMP's unfilled row, so the sheet still shows it, and the pass has no EPS from its answer
    to tell. A zero FMP sent with nothing real beside it is no such figure: the zeros show that
    field unfilled, so it leaves the sheet with them, and there is nothing left to tell.
No captured calendar shows a filled row with no estimate of its own, or a figure FMP takes back
before the pass reads it: those follow from the rules.

`push_dispatch_service` claims the key only for users who pass their toggle and cap (a failed
settings read claims it on a guess: A LIVE ROW TURNED OFF AT ITS WAKE), so a user with Live
Results on gets #1 and is skipped by #2 as a duplicate; a user with only Results & Surprises on
gets #2; and the daily pass later finds the key taken for both.
Preference, master switch, category cap and quiet hours stay the
dispatcher's job — nothing here decides who is told. That includes the hour: both kinds carry
`night_hold_by_default`, so a landing in the night of a user who never set Quiet Hours is
claimed at once and buzzes at 07:00 on their clock.

A HELD ROW'S TEXT is decided at its wake, not at its landing (`copy_at_wake`): the record can
have moved under it for hours, and the flush is what asks. The text comes from the record, a
surprise the record no longer holds is not buzzed, and the row keeps what the record says so the
inbox never shows the number the phone was spared. A figure the record has taken off since the
landing (an FMP zero its row of zeros showed unfilled, GUARDS in `earnings_live_service`) is not
buzzed either: a record with no figure that held one in this process, or a report an own fetch
ended after that (`EarningsLiveService.ended_after_a_figure`), holds the row in words that state
no figure, as a report with no answer yet waits, and the row ends unsent at the flush's bound
unless a figure comes back. Only a restart before the wake can still let that zero out: the new
process never held it, so its record says nothing against the row (recorded residual).
The row's own text stands only when the record has nothing to say: it is gone (a restart),
holds no figure and never held one in this process, or, to `earnings_result`, shows nothing FMP
said. That last is, in practice, a record a restart took back from the table before FMP's first
answer reached it (A RESTART in `earnings_live_service`), on any day: it holds the company's
release alone, a figure this kind may not state and nothing against the FMP figure that wrote
the row, so the row goes out in its own words, as with no record at all, never held an hour for
that answer nor lost to a look that fails. Its own words are the result its landing wrote,
though. A row a wake held in other words waits again wherever the record has nothing to say to
it, or cannot be read, so a restart never turns that wait into a buzz (`_left_waiting`): an
`earnings_result` row a wake held for FMP's answer, which carries what was known meanwhile (A
REPORT WITH NO ANSWER YET), the release's figures among them, which this kind never sends; and
an `earnings_live` row whose words state no figure, every metric still to come or being
confirmed, which no landing writes. A row from the daily pass reads the same way: the pass gives
the record what it read first, so the record is never the older of the two, and reads each
report from the row the record reads, one FMP lists more than once or whose EPS is 0 as the
record reads it, so the two start alike.

A LIVE ROW TURNED OFF AT ITS WAKE. A failed settings read at the landing parks the live kind for a
user whose toggles are unknown, under the one key, so the result kind behind it in the same round
finds the key taken and is done with the report here. A held live row's owner can also turn Live
Results off before its wake. Either way the flush finds the kind off, and asks `instead_at_wake`:
while the result push is on and the record holds a surprise it would start now, the row goes on as
`earnings_result` with that copy, as a landing that had read the settings would have sent it (the
dispatcher still asks that kind's toggle, cap and night). Otherwise it is deleted, which frees the
key for the round that decides once FMP's EPS stands, or for the daily pass while the push is off.

A REPORT WITH NO ANSWER YET is not a report that has changed its mind. A figure held as
"checking" is a figure FMP has yet to confirm, and it can come back right after the wake. So
instead of ending the row, the wake asks again (`WAKE_ASK_AGAIN`), with the row saying what is
known meanwhile, and only for a row that would otherwise end. `earnings_result` waits on FMP's own
EPS, the one figure it may state, once FMP has said anything of the report, and a row held for it
keeps waiting through a restart (A HELD ROW'S TEXT above). `earnings_live` waits only when it has
no figure to state at all: a revenue beside an EPS in doubt is a true sentence and goes out. The
dispatcher bounds it (`PushDispatchService.MAX_FLUSH_ATTEMPTS`). Each ask has the live loop read the report in the
minutes before the next look (`EarningsLiveService.read_before_look`), timed from the instant the
dispatcher holds the row to: a day before a closed today is read only every 2 h, and every look
of a row could read one answer while FMP had recovered in between. That read costs nothing once
the report has answered, whatever else on its day still waits, the rows of one hand-out share
it, and a row's last look, which cannot know it is the last, books at most one read nobody looks
at. A row still without an answer at
its last hand-out ends unbuzzed, and if FMP recovers later still that report gets no buzz: the
bound is the point.

THE WINDOW. Neither kind goes out for a report older than yesterday: the daily pass's own
rule (`earnings_sender.in_result_window`), which is narrower than the live days around a
weekend or a holiday. A Friday report FMP fills on Monday lands, keeps its card and its sheet,
and is marked handled here with nothing further sent: a live push from its filing, if one went
out inside the window, is all it gets. The rule decides what starts: a row claimed inside the
window is the dispatcher's, and still goes out at its wake if the report has aged past it.

ONE REPORT, TWO DATES. FMP's leftover row a trading day from a report lands like any report, and
the key carries its date, so the ledger decides: a user holding the result key on any day out to
the trading day either side was told about this report and is left out
(`earnings_sender.neighbouring_result_keys`, `unless_claimed`). Only the ledger knows who heard.
It outlives a restart, it answers per user, and a report that told nobody, a filing with the live
kind off or a landing past the window, holds nothing back. `_to_decide`'s oldest-first order is
what makes two rows landing in one round one push under the first date.

A FAILED WATCHER READ. A report's key is taken before its send, and the dispatcher's usual 0
for a failed read looks like a ticker nobody watches, so one failed read at the landing minute
would use up the report: no later round sends it, a restart cannot re-land it, and
`earnings_live` has no daily pass behind it. So `_notify` asks to hear about it (`strict`). The
read told nobody and claimed nothing, so the key goes back and a later round retries. The round
ends there: the live kind still goes first, and the tick, which awaits this inline, pays for one
failed read a round however many reports wait. The ticker then goes behind every other report
until a read of it works (`_went_back`), so one whose read keeps failing holds back nobody, and
two of them take turns. By ticker, never by report: the read is the ticker's, and a report dated
twice must stay in date order to go out under its first date. A failed read of who holds a
neighbouring key is the same. A failed claim or device read after those reads comes back as a
count like any other, so it does use up the key; only a failed claim leaves `earnings_result`
the daily pass.

COPY. Informational, never directive (SYSTEM_DESIGN_GUIDELINES §11.7). A lock-screen banner
sits OUTSIDE the authenticated app, which is all FMP's Order Form licenses for display, so an
absolute figure appears only when it comes from the company's own release (a public SEC
document); an FMP-sourced figure is stated as beat/missed by a percentage, the shape
`result_copy` already ships.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from app.config import settings
from app.services.earnings_live_service import (
    BASIS_GAAP,
    SOURCE_FILING,
    SOURCE_FMP,
    STATUS_AWAITING,
    LiveMetric,
    LiveSnapshot,
    et_now,
    get_earnings_live_service,
    has_results,
    live_days,
)
from app.services.notification_kinds import (
    KIND_EARNINGS_LIVE,
    KIND_EARNINGS_RESULT,
    ticker_route,
)
from app.services.notification_senders.earnings_sender import (
    MIN_SURPRISE,
    in_result_window,
    neighbouring_result_keys,
    record_surprise,
    result_copy,
    result_dedup_key,
    surprise_pct,
)
from app.services.push_dispatch_service import WatcherLookupFailed

logger = logging.getLogger(__name__)

#: Bound one round's fan-out, as `earnings_sender.MAX_SYMBOLS_PER_PASS` does. The tick hands over
#: watched reports only, so this counts sends for them and never the market. Anything over it
#: is picked up on the next 60 s round, while its report is still inside the result window.
MAX_SENDS_PER_ROUND = 60
#: How long a held row with no answer yet waits to ask again (A REPORT WITH NO ANSWER YET). Each
#: ask has the live loop read the report just before the next look
#: (`EarningsLiveService.read_before_look`), so that look reads FMP's answer of the moment
#: whatever the day's own cadence, which is 2 h for a day before a closed today.
WAKE_ASK_AGAIN = timedelta(hours=1)

# Once per (symbol, report date) per process. The dedup key is what stops a restart from
# re-sending; these sets stop the same process from re-dispatching every 60 s. A key is taken
# before its send and comes back only on a failed watcher read (`_give_back`).
_live_done: Set[Tuple[str, date]] = set()
_surprise_done: Set[Tuple[str, date]] = set()
# The tickers a failed watcher read sent to the back of the round, each with the turn it went
# back on, so the one that has waited longest goes next (A FAILED WATCHER READ in the docstring).
_went_back: Dict[str, int] = {}


def reset() -> None:
    """Forget what was dispatched (tests)."""
    _live_done.clear()
    _surprise_done.clear()
    _went_back.clear()


def _money(value: float, *, revenue: bool) -> str:
    sign = "-" if value < 0 else ""
    v = abs(value)
    if not revenue:
        return f"{sign}${v:,.2f}"
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= scale:
            return f"{sign}${v / scale:,.1f}{suffix}"
    return f"{sign}${v:,.0f}"


def _vs_consensus(m: LiveMetric) -> Optional[str]:
    """The verdict the card and the sheet show (`EarningsMetricOutcome.result`): a surprise that
    rounds to 0.00 on a figure off the consensus is still a beat or a miss, and only a figure
    equal to the consensus is in line."""
    if m.surprise_pct is None:
        return None
    direction = m.surprise_pct
    if direction == 0 and m.actual is not None and m.estimate is not None:
        direction = m.actual - m.estimate
    if direction == 0:
        return "in line with consensus"
    verb = "beat" if direction > 0 else "missed"
    pct = abs(m.surprise_pct)
    shown = f"{pct:.0f}"
    if pct >= 1000 or shown == "0":
        # A near-zero consensus makes the percentage explode, and "by 0%" says nothing: Python
        # rounds half to even, so a 0.50% surprise prints "0" too, under a sheet reading +0.5%.
        return f"{verb} consensus"
    return f"{verb} consensus by {shown}%"


def _phrase(label: str, m: LiveMetric, *, revenue: bool) -> str:
    if m.checking:
        # A figure exists but a sanity guard holds it (a dropped-digit signature).
        return f"{label} being confirmed"
    if m.actual is None:
        return f"{label} not out yet"
    vs = _vs_consensus(m)
    if m.source == SOURCE_FILING:
        figure = f"{label} {_money(m.actual, revenue=revenue)}"
        return f"{figure} ({vs})" if vs else figure
    return f"{label} {vs}" if vs else f"{label} reported"


def live_copy(snap: LiveSnapshot) -> Tuple[str, str]:
    """Title/body for an `earnings_live` push. States what landed and stops."""
    eps_label = "GAAP EPS" if snap.eps.basis == BASIS_GAAP else "EPS"
    body = " · ".join([
        _phrase("Revenue", snap.revenue, revenue=True),
        _phrase(eps_label, snap.eps, revenue=False),
    ]) + "."
    if snap.preliminary:
        body += " From the company's release."
    return f"{snap.symbol} results are out", body


@dataclass(frozen=True)
class WakeCopy:
    """What a held result says at its wake. `send` False keeps it off the phone: the text goes
    to its inbox row alone. `ask_again_in` says that `send` False is no last word: the report
    has yet to answer, so the row may wait that long and be read again."""

    title: str
    body: str
    send: bool
    ask_again_in: Optional[timedelta] = None


def _fmp_eps_stands(snap: LiveSnapshot) -> bool:
    """Is FMP's own EPS on show? `earnings_result` can state no other, so without one the record
    has not answered it: the figure may be held, or the filing's may stand in for one in doubt."""
    return snap.eps.actual is not None and snap.eps.source == SOURCE_FMP


def _fmp_shows_nothing(snap: LiveSnapshot) -> bool:
    """Is nothing FMP said on show: no figure of its own, no consensus, no figure due or held?
    Then the record holds the company's release alone, in practice a filing the table gave back
    after a restart that FMP's first answer has yet to reach (A RESTART in
    `earnings_live_service`). A held figure counts as FMP's word: the guards hold one against
    FMP's consensus or hold FMP's own zero, and the verifier never keeps a zero revenue."""
    return all(m.source != SOURCE_FMP and m.estimate is None and not m.due and not m.checking
               for m in (snap.eps, snap.revenue))


def _report_of(route: Mapping[str, Any]) -> Optional[Tuple[str, date]]:
    """The (symbol, report date) a stored route names, or None."""
    symbol, raw_day = route.get("ticker"), route.get("report_date")
    if not isinstance(symbol, str) or not symbol or not isinstance(raw_day, str):
        return None
    try:
        return symbol, date.fromisoformat(raw_day[:10])
    except ValueError:
        return None


#: How `live_copy` ends a metric with no figure on show (`_phrase`).
_NO_FIGURE_ENDINGS = (" not out yet", " being confirmed")


def _states_no_figure(body: str) -> bool:
    """Does a `live_copy` body state no figure, every metric still to come or being confirmed?
    No landing writes one: a report reaches the sender only with a figure that stands
    (`landed_snapshots`), and a figure being confirmed is never on show. A preliminary body ends
    on the release's sentence, after a figure, so it never matches."""
    phrases = body.strip().removesuffix(".").split(" · ")
    return all(phrase.endswith(_NO_FIGURE_ENDINGS) for phrase in phrases)


def _left_waiting(kind: str, symbol: str, text: Optional[Tuple[str, str]]) -> bool:
    """Did an earlier wake leave this held row waiting, for a figure or for FMP's answer (A HELD
    ROW'S TEXT in the module docstring)? An `earnings_result` row lands in `result_copy`'s words,
    and a wake that holds it puts `live_copy`'s in their place (A REPORT WITH NO ANSWER YET). The
    title tells the two apart: `result_copy` gives a symbol one title whatever the surprise, and
    never `live_copy`'s. An `earnings_live` row lands and waits in `live_copy`'s words alike, so
    only a body that states no figure (`_states_no_figure`) tells a wait: no landing writes one.
    A row whose `text` is not given reads as it landed."""
    if text is None:
        return False
    if kind == KIND_EARNINGS_RESULT:
        return text[0] != result_copy(symbol, MIN_SURPRISE)[0]
    return kind == KIND_EARNINGS_LIVE and _states_no_figure(text[1])


def _no_figure_copy(symbol: str, day: date) -> Tuple[str, str]:
    """`live_copy`'s words for a report with no figure on show, for a held row whose record has
    gone since it took off the figure the row states (`EarningsLiveService.ended_after_a_figure`)."""
    nothing = LiveMetric(estimate=None)
    return live_copy(LiveSnapshot(
        symbol=symbol, day=day, status=STATUS_AWAITING, company_name=None, eps=nothing,
        revenue=nothing, preliminary=False, checked_at=None, filing=None,
    ))


def copy_at_wake(kind: str, route: Mapping[str, Any], now: datetime, *,
                 text: Optional[Tuple[str, str]] = None) -> Optional[WakeCopy]:
    """What a held row of `kind`, stored with `route`, says now, from the record as it stands
    (A HELD ROW'S TEXT in the module docstring), or None to keep the row's own text.

    `earnings_result` goes out only on what would start it now (`record_surprise`), since FMP
    can correct a figure inside the hold and a push must not state a beat the sheet it opens
    calls a miss. `earnings_live` goes out on any figure that stands. A row that no longer goes
    out takes `live_copy`, which can say what `result_copy` cannot: in line, being confirmed.
    It carries `ask_again_in` when the report has yet to answer (A REPORT WITH NO ANSWER YET in
    the module docstring), never for a figure that stands and simply no longer clears the bar,
    and then asks the live loop to read the report just before that next look, `now` plus that
    wait: `now` is the flush's own clock, the one it holds the row by.
    An `earnings_result` row whose record shows nothing FMP said keeps its own text
    (`_fmp_shows_nothing`, A HELD ROW'S TEXT in the module docstring), but only while `text`, the
    row's title and body as they stand, is still the result it landed with. One an earlier wake
    left waiting for FMP's answer (`_left_waiting`) waits again instead wherever the record has
    nothing to say to it, and where the record cannot be read: that row never raises here, while
    any other row's failure reaches the caller.
    PURE apart from reading the record, logging a read that fails and that request, bookkeeping
    in memory, so the flush can call it on the event loop.
    """
    report = _report_of(route)
    if report is None or kind not in (KIND_EARNINGS_LIVE, KIND_EARNINGS_RESULT):
        return None
    waiting = _left_waiting(kind, report[0], text)
    try:
        wake = _copy_from_record(kind, report, now, waiting=waiting)
    except Exception as e:
        if not waiting:
            raise
        logger.warning(
            "earnings live push: could not re-read %s %s for a held %s row an earlier wake left "
            "waiting for FMP (%s: %s) — it waits again, never sent in that wake's words",
            report[0], report[1].isoformat(), kind, type(e).__name__, e, exc_info=True,
        )
        wake = None
    if wake is None and waiting:
        # The row waits for FMP's answer as any other ask does: the loop reads the report just
        # before the next look (A REPORT WITH NO ANSWER YET in the module docstring).
        get_earnings_live_service().read_before_look(report[0], report[1], now + WAKE_ASK_AGAIN)
        return WakeCopy(*text, send=False, ask_again_in=WAKE_ASK_AGAIN)
    return wake


def _copy_from_record(kind: str, report: Tuple[str, date], now: datetime, *,
                      waiting: bool) -> Optional[WakeCopy]:
    """`copy_at_wake`'s answer from the record, or None where it has nothing to say to `kind`.
    `waiting`: an earlier wake left the row waiting for FMP's answer (`_left_waiting`)."""
    live = get_earnings_live_service()
    snap = live.snapshot(*report, now)
    if snap is None:
        if live.ended_after_a_figure(*report):
            # An own fetch ended the record after it took off the figure the row states.
            return _held_with_no_figure(live, report, now, _no_figure_copy(*report))
        return None
    eps, revenue = snap.eps, snap.revenue
    if not (has_results(eps, revenue) or eps.checking or revenue.checking):
        if not (snap.landed or live.ended_after_a_figure(*report)):
            # A record rebuilt after a restart, or one that never held a figure: it says
            # nothing against a row a figure did land to write.
            return None
        # This process held a figure for the report and has taken it off since (an FMP zero its
        # row of zeros showed unfilled, GUARDS in `earnings_live_service`): the row's text states
        # a figure the sheet it opens no longer shows.
        return _held_with_no_figure(live, (snap.symbol, snap.reads_as or snap.day), now,
                                    live_copy(snap))
    if kind == KIND_EARNINGS_RESULT:
        surprise = record_surprise(snap)
        if surprise is not None:
            return WakeCopy(*result_copy(snap.symbol, surprise), send=True)
        if _fmp_shows_nothing(snap) and not waiting:
            return None
        send, again = False, None if _fmp_eps_stands(snap) else WAKE_ASK_AGAIN
    else:
        send = has_results(eps, revenue)
        again = None if send else WAKE_ASK_AGAIN
    if again is not None:
        # The report whose figures the row reads (a leftover row reads as the report before it),
        # at the instant the dispatcher holds the row to: its `now`, the one this was given, and
        # the same wait.
        live.read_before_look(snap.symbol, snap.reads_as or snap.day, now + again)
    return WakeCopy(*live_copy(snap), send=send, ask_again_in=again)


def _held_with_no_figure(live: Any, report: Tuple[str, date], now: datetime,
                         text: Tuple[str, str]) -> WakeCopy:
    """A held row whose figure the record has taken off since it landed: it waits for one to
    stand, as a report with no answer yet does, in `text`, words that state none, and ends unsent
    at the flush's bound (A HELD ROW'S TEXT in the module docstring). The loop reads `report`
    just before the next look, at `now` plus the same wait: `now` is the flush's own clock."""
    live.read_before_look(report[0], report[1], now + WAKE_ASK_AGAIN)
    return WakeCopy(*text, send=False, ask_again_in=WAKE_ASK_AGAIN)


def instead_at_wake(kind: str, route: Mapping[str, Any],
                    now: datetime) -> Optional[Tuple[str, WakeCopy]]:
    """The kind a held row of `kind` goes out as when the user's settings turn `kind` off at its
    wake, with what it says now, or None to delete the row (A LIVE ROW TURNED OFF AT ITS WAKE in the
    module docstring).

    Only `earnings_live` has one, `earnings_result`: the same key, category and cap. Only while
    `EARNINGS_LIVE_RESULT_PUSH_ENABLED` is on, and only on a surprise the record would start that
    push with now (`copy_at_wake` sends it). A figure still to come is None, never a held row
    asking again: until FMP's EPS stands the result kind here has not decided, and it decides with
    the key free. No window rule: the row was claimed inside it, and a held result row outlives it
    too. PURE apart from reading the record, like `copy_at_wake`.
    """
    if kind != KIND_EARNINGS_LIVE or not settings.EARNINGS_LIVE_RESULT_PUSH_ENABLED:
        return None
    copy = copy_at_wake(KIND_EARNINGS_RESULT, route, now)
    if copy is None or not copy.send:
        return None
    return KIND_EARNINGS_RESULT, copy


async def _notify(kind: str, snap: LiveSnapshot, title: str, body: str) -> int:
    from app.services.push_dispatch_service import get_push_dispatch_service

    when = snap.day.isoformat()
    sent = await get_push_dispatch_service().notify_watchers(
        ticker=snap.symbol,
        title=title,
        body=body,
        dedup_key=result_dedup_key(snap.symbol, when),
        unless_claimed=neighbouring_result_keys(snap.symbol, when),
        kind=kind,
        data=ticker_route(kind, snap.symbol, report_date=when),
        strict=True,
    )
    _went_back.pop(snap.symbol, None)  # the read worked, so the ticker is back in date order
    return sent


def _prune(now: datetime) -> None:
    """Forget keys outside the live days, so the sets stay bounded. The same window on the same
    `now` as `landed_snapshots`: a key must never leave here while its report is still handed
    over, or that report reads as new on every round."""
    days = set(live_days(et_now(now).date()))
    for done in (_live_done, _surprise_done):
        for key in [k for k in done if k[1] not in days]:
            done.discard(key)


def _to_decide(snapshots: List[LiveSnapshot], today: date) -> List[LiveSnapshot]:
    """The reports this round still has a decision on, oldest first: the oldest leaves the
    result window soonest, so the cap must hold back a newer one instead, and a report dated
    twice is told under its first date. The tickers in `_went_back` come after all of them, the
    one that went back longest ago first: a ticker whose read is failing gives up its place
    under the cap, and is forgotten here once it has no report left to decide. One already
    outside the window is handled here, both kinds at once: no later round has anything left to
    decide on it, and it reaches neither the cap nor the log again. Its keys leave with the live
    days, like every other (`_prune`)."""
    open_reports = []
    for snap in snapshots:
        key = (snap.symbol, snap.day)
        if key in _live_done and key in _surprise_done:
            continue
        if in_result_window(snap.day, today):
            open_reports.append(snap)
            continue
        _live_done.add(key)
        _surprise_done.add(key)
        logger.info("earnings live push: %s %s is older than yesterday on %s — nothing further "
                    "will be sent; its card and sheet stay", snap.symbol, snap.day.isoformat(),
                    today.isoformat())
    still_open = {snap.symbol for snap in open_reports}
    for symbol in [s for s in _went_back if s not in still_open]:
        del _went_back[symbol]
    return sorted(open_reports, key=lambda s: (_went_back.get(s.symbol, 0), s.day, s.symbol))


def _still_owed(snap: LiveSnapshot) -> bool:
    """Would `dispatch_landings` still send for this report? Its gates, for the cap warning."""
    key = (snap.symbol, snap.day)
    if settings.EARNINGS_LIVE_PUSH_ENABLED and key not in _live_done:
        return True
    if not settings.EARNINGS_LIVE_RESULT_PUSH_ENABLED or key in _surprise_done:
        return False
    return record_surprise(snap) is not None


def _give_back(done: Set[Tuple[str, date]], snap: LiveSnapshot, left: List[LiveSnapshot],
               error: WatcherLookupFailed) -> None:
    """A failed watcher read told nobody, so `snap`'s key comes out of `done` for a later round
    to retry, `left`, the reports from `snap` on, wait with it, and its ticker goes behind every
    other report (A FAILED WATCHER READ in the module docstring)."""
    done.discard((snap.symbol, snap.day))
    _went_back[snap.symbol] = max(_went_back.values(), default=0) + 1
    logger.warning("earnings live push for %s %s not sent, its watcher read failed (%s): %d "
                   "report(s) still owed a push go next round", snap.symbol, snap.day.isoformat(),
                   error, sum(1 for s in left if _still_owed(s)))


async def dispatch_landings(snapshots: List[LiveSnapshot], now: datetime) -> Dict[str, int]:
    """Send what each landed report is owed, live kind FIRST. Never raises per report, and a
    failed watcher read ends the round (`_give_back`)."""
    _prune(now)
    stats = {"live": 0, "result": 0}
    sends = 0
    ordered = _to_decide(snapshots, et_now(now).date())
    for i, snap in enumerate(ordered):
        if sends >= MAX_SENDS_PER_ROUND:
            left = sum(1 for s in ordered[i:] if _still_owed(s))
            if left:
                logger.warning("earnings live push: cap %d reached, %d report(s) still owed a "
                               "push go next round", MAX_SENDS_PER_ROUND, left)
            break
        key = (snap.symbol, snap.day)
        if key not in _live_done:
            _live_done.add(key)
            if settings.EARNINGS_LIVE_PUSH_ENABLED:
                title, body = live_copy(snap)
                try:
                    stats["live"] += await _notify(KIND_EARNINGS_LIVE, snap, title, body)
                except WatcherLookupFailed as e:
                    _give_back(_live_done, snap, ordered[i:], e)
                    break
                except Exception as e:
                    # Belt only: `notify_watchers` raises nothing else. The key stays taken,
                    # or a bug here would retry every 60 s for two days.
                    logger.warning("earnings live push for %s failed (%s: %s)",
                                   snap.symbol, type(e).__name__, e)
                sends += 1
        if key in _surprise_done or not settings.EARNINGS_LIVE_RESULT_PUSH_ENABLED:
            continue
        eps = snap.eps
        if eps.actual is None or eps.source != SOURCE_FMP:
            # Not decidable yet (only a filing figure, or nothing): wait for FMP's figure.
            continue
        _surprise_done.add(key)
        surprise = surprise_pct({"epsEstimated": eps.estimate, "epsActual": eps.actual})
        if surprise is None or abs(surprise) < MIN_SURPRISE:
            continue
        title, body = result_copy(snap.symbol, surprise)
        try:
            stats["result"] += await _notify(KIND_EARNINGS_RESULT, snap, title, body)
        except WatcherLookupFailed as e:
            _give_back(_surprise_done, snap, ordered[i:], e)
            break
        except Exception as e:
            logger.warning("earnings live result push for %s failed (%s: %s)",
                           snap.symbol, type(e).__name__, e)
        sends += 1
    if stats["live"] or stats["result"]:
        logger.info("earnings live push: %d live + %d result sends", stats["live"],
                    stats["result"])
    return stats
