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

A RESULT FMP CHANGES (owner, 2026-11-20: "Fix inbox text"). FMP can restate a report after its
push went out: FRO's first answer on 2026-11-20 said revenue beat by 31% and EPS by 10%, and two
hours later its row read revenue in line and EPS missed by 12%. The record follows FMP; the phone
cannot be changed and is not buzzed again (one push per report). So for each report a push of this
process told, each kind, its ticker watched or not (`_told_off_the_list`), the sender keeps every
text the report's inbox rows may hold (`_Told`): the push's, each correction's, and the record's own
as each look saw it, since a held row's wake writes the record's words (with every revenue surprise
those words stood at, round by round, looked at or not: `_seen_at_its_surprise`). A result past `MIN_SURPRISE`
is spelled as `result_copy` spells it from the unrounded surprise, the words a wake, a re-kind and
the 16:00 ET pass write it in (`_said_now`). Each look (the record's words changed, or rows were
still held at the last one) finds the texts the record now contradicts (`_changed`: a stated verdict
or percent), and rewrites the rows past their buzz still holding one of them,
marked "Updated: " (`PushDispatchService.revise_sent_text`), the 16:00 ET pass's own rows of that
kind among them when they hold such a text. Each text's rows take its own merged words (`_merged`):
the record's for every metric the record states, the text's own for one the record no longer states
(not out, or held), so a row never says "not out yet" or "being confirmed" of a figure its phone
showed, nor shows one its phone was spared, nor sets a row's own revenue verdict beside an EPS
that no longer bears it out (the revenue check, COPY below). What is no contradiction: a metric
that came in after a text was written, or a figure stated with no verdict ("reported": no
consensus, a print that covers it, a GAAP EPS, a revenue the check withholds) that gains one. A
text a correction wrote is the sender's own, though: the rows holding it marked follow the record
whatever moves (owner, 2026-11-20: "Follow FMP"), so a revenue verdict a correction withheld comes
back when the record gives one again. A row holding words the sender never saw is left as it is,
and so is a held row: its wake reads the record. One the user opened before its wake ends unread,
still holding old words, so while any row of the kind is held the look is repeated every
`HELD_RETRY_SECONDS`. The held read comes first, so a row its wake ends meanwhile takes the write.
A live push also follows the report's result rows, which a wake makes of a held live row whose
user turned Live Results off, and every followed report's result rows a wake wrote in the live
words (a surprise that no longer clears `MIN_SURPRISE` at the wake), a track of their own
(`RESULT_ROWS_IN_LIVE_WORDS`) that only keeps the record's texts until a wake has written such a
row (`copy_at_wake` marks the report): before that it reads and writes nothing. At most
`MAX_REVISIONS_PER_ROUND` looks with writes a round, those a record change owes first, then held
re-looks, longest due first, the rest next round; the first failed write ends the round's
corrections and waits `HELD_RETRY_SECONDS`, so the next rounds start with the others, and the
texts it may have written are kept first, since a write can commit and its call still fail. Each correction is a WARNING with the old and new words,
and `EARNINGS_LIVE_INBOX_CORRECTIONS_ENABLED` turns them off. Residuals: process-local, so a restart
forgets what it knew and a report FMP changes after it keeps its old rows (every deploy restarts the
web process); a report no push of this process told (the 16:00 ET pass's alone) is never followed;
a row whose wake wrote words between two looks holds words never seen, and keeps them; a row still
held shows the push's words in the inbox until its wake (or, opened before it, until the next
look), since a correction never writes a held row.

COPY. Informational, never directive (SYSTEM_DESIGN_GUIDELINES §11.7). A lock-screen banner
sits OUTSIDE the authenticated app, which is all FMP's Order Form licenses for display, so an
absolute figure appears only when it comes from the company's own release (a public SEC
document); an FMP-sourced figure is stated as beat/missed by a percentage, the shape
`result_copy` already ships. A revenue whose surprise is more likely two measures set against
each other than news (`_revenue_in_doubt`: 25%+ off a consensus of $100M or more, its EPS not
moving the same way by half as much; a release's GAAP EPS neither raises nor settles it) is told
with no beat or miss, "Revenue reported" or the release's figure alone (owner, 2026-11-20: "Add
the check"; scoped after the 2026-11-20 review, "fix what you need to fix first"); the card and
the sheet show FMP's numbers. The inbox corrections read it as a figure with no verdict, and a
doubt that rests on an EPS the record holds ("being confirmed") states nothing, so a passing hold
corrects nothing; an EPS not out yet is no hold, so a row's revenue verdict FMP has moved since is
corrected to the figure with none. A correction judges a row's own revenue verdict at every
surprise its text was written at, as the push did, and withholds it if the push would at any of
them: rows holding the same words cannot be told apart, and a figure with no verdict is never false
(`_merged`).
`EARNINGS_LIVE_REVENUE_CHECK_ENABLED` turns it off. INFO `EARNINGS_LIVE_REVENUE_WITHHELD` names
each landing whose push went out so, once, after the send; a row held for the night takes the
record's words at its wake, which may say otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import AbstractSet, Any, Dict, FrozenSet, List, Mapping, NamedTuple, Optional, Set, Tuple

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
# What each push this process sent stated, by report and kind (A RESULT FMP CHANGES in the module
# docstring). Process-local like the sets above: a restart forgets it.
_told: Dict[Tuple[str, date], Dict[str, "_Told"]] = {}
#: Looks with writes one round makes at most (each writes the texts the record contradicts): the
#: rest wait for the next round.
MAX_REVISIONS_PER_ROUND = 20
#: Leads a corrected inbox row, so it never reads as what the phone showed.
UPDATED_MARK = "Updated: "
#: How long a correction that met a held row waits to be written again, and how long one whose
#: write failed waits before it is tried again (A RESULT FMP CHANGES).
HELD_RETRY_SECONDS = 300
# When a correction that met held rows may be written again, by (symbol, day, kind), on the
# monotonic clock.
_revise_after: Dict[Tuple[str, date, str], float] = {}
# When a correction whose write failed may be tried again, keyed the same way: the round ends at a
# failed write, and the wait lets the next rounds start with the others.
_failed_until: Dict[Tuple[str, date, str], float] = {}

#: The result rows a wake wrote in the live kind's words: a held `earnings_result` row whose surprise
#: no longer clears `MIN_SURPRISE` at its wake takes `live_copy` (`_copy_from_record`). Followed as a
#: track of its own (A RESULT FMP CHANGES): result rows, judged and rewritten by the live rules.
RESULT_ROWS_IN_LIVE_WORDS = "earnings_result:live_words"
# The reports a wake has written result rows for in the live kind's words (`copy_at_wake`), by
# the route's own (symbol, report date), the `_told` key. Until a report is here its
# `RESULT_ROWS_IN_LIVE_WORDS` track only keeps the record's texts: no row could hold them, so it
# reads nothing, writes nothing and takes no look of the round's `MAX_REVISIONS_PER_ROUND`.
_result_rows_in_live_words: Set[Tuple[str, date]] = set()
#: Each followed track: (the kind of the rows it rewrites, the kind whose words it judges by).
_TRACKS: Dict[str, Tuple[str, str]] = {
    KIND_EARNINGS_LIVE: (KIND_EARNINGS_LIVE, KIND_EARNINGS_LIVE),
    KIND_EARNINGS_RESULT: (KIND_EARNINGS_RESULT, KIND_EARNINGS_RESULT),
    RESULT_ROWS_IN_LIVE_WORDS: (KIND_EARNINGS_RESULT, KIND_EARNINGS_LIVE),
}

#: What a push says about one metric: (verb, percent shown), ("reported", None) for a figure with
#: no consensus to judge it by, or None when it states no figure.
Stated = Optional[Tuple[str, Optional[str]]]


@dataclass(frozen=True)
class _Words:
    """What one text a report's rows may hold states, metric by metric (`Stated`), and, for the live
    kind, its words for each metric, (revenue, EPS)."""

    stated: Tuple[Stated, ...]
    phrases: Tuple[str, ...] = ()
    # Every revenue surprise behind `stated` the text was written at (live kind), in percent as the
    # record held it: the revenue check judges these, as the push did, never the whole percent the
    # text printed (`_merged`). One text prints at more than one ("25%" at 24.6% and at 25.3%), and
    # the rows holding it cannot be told apart. Empty with no revenue stated.
    revenue_pcts: FrozenSet[float] = frozenset()


@dataclass(frozen=True)
class _Told:
    """What this process knows a report's rows of one kind may hold (A RESULT FMP CHANGES)."""

    title: str
    # Every text those rows may hold, without the mark: the push's, each correction's, and the
    # record's own as each look saw it (a held row's wake writes the record's words).
    known: Mapping[str, _Words]
    # The record's text at the last look: a look is owed when it changes.
    seen: str
    # A row of this kind was still held at the last look, so one is owed every
    # `HELD_RETRY_SECONDS` until none is: its wake may end it unread, still holding old words.
    held: bool = False
    # The texts a correction wrote, without the mark: rows holding one marked are the sender's own,
    # and follow the record whatever moves (`_corrections`).
    written: FrozenSet[str] = frozenset()


def reset() -> None:
    """Forget what was dispatched (tests)."""
    _live_done.clear()
    _surprise_done.clear()
    _went_back.clear()
    _told.clear()
    _revise_after.clear()
    _failed_until.clear()
    _result_rows_in_live_words.clear()


def _money(value: float, *, revenue: bool) -> str:
    sign = "-" if value < 0 else ""
    v = abs(value)
    if not revenue:
        return f"{sign}${v:,.2f}"
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= scale:
            return f"{sign}${v / scale:,.1f}{suffix}"
    return f"{sign}${v:,.0f}"


def _verdict(m: LiveMetric) -> Optional[Tuple[str, Optional[str]]]:
    """(verb, percent shown) of a figure against its consensus, as `_vs_consensus` words it:
    "in line", "beat" or "missed", with the whole percent or None when none is printed. None with
    no surprise to state."""
    if m.surprise_pct is None:
        return None
    direction = m.surprise_pct
    if direction == 0 and m.actual is not None and m.estimate is not None:
        direction = m.actual - m.estimate
    if direction == 0:
        return "in line", None
    verb = "beat" if direction > 0 else "missed"
    pct = abs(m.surprise_pct)
    shown = f"{pct:.0f}"
    if pct >= 1000 or shown == "0":
        # A near-zero consensus makes the percentage explode, and "by 0%" says nothing: Python
        # rounds half to even, so a 0.50% surprise prints "0" too, under a sheet reading +0.5%.
        return verb, None
    return verb, shown


def _vs_consensus(m: LiveMetric) -> Optional[str]:
    """The verdict the card and the sheet show (`EarningsMetricOutcome.result`): a surprise that
    rounds to 0.00 on a figure off the consensus is still a beat or a miss, and only a figure
    equal to the consensus is in line."""
    verdict = _verdict(m)
    if verdict is None:
        return None
    verb, shown = verdict
    if verb == "in line":
        return "in line with consensus"
    return f"{verb} consensus by {shown}%" if shown is not None else f"{verb} consensus"


#: A revenue at least this far off its consensus, in percent, is told with no beat or miss unless
#: the EPS bears it out (`_revenue_in_doubt`).
REVENUE_DOUBT_PCT = 25.0
#: The EPS bears such a revenue out when it moves the same way by at least this share of it.
REVENUE_DOUBT_EPS_SHARE = 0.5
#: The check applies only from this revenue consensus up: the companies its research read. Below
#: it a small company landing far from a tiny consensus is routine, as the record's own revenue
#: guard says from the same figure (`_REVENUE_GUARD_MIN_ESTIMATE` in `earnings_live_service`).
REVENUE_DOUBT_MIN_ESTIMATE = 100e6


def _in_doubt(rev_estimate: Optional[float], rev_pct: Optional[float],
              eps_pct: Optional[float], *, eps_gaap: bool) -> bool:
    """The revenue check on its numbers (`_revenue_in_doubt`): a revenue surprise `rev_pct`, in
    percent, against a consensus of `rev_estimate`, beside an EPS surprise `eps_pct` (None: no EPS
    on show, or none with a surprise to state). `eps_gaap`: the EPS on show is a release's GAAP
    figure, which neither raises a doubt nor settles one (its consensus is usually adjusted), so
    the revenue keeps its verdict."""
    if not settings.EARNINGS_LIVE_REVENUE_CHECK_ENABLED or eps_gaap:
        return False
    if rev_pct is None or abs(rev_pct) < REVENUE_DOUBT_PCT:
        return False
    if rev_estimate is None or rev_estimate < REVENUE_DOUBT_MIN_ESTIMATE:
        return False
    if eps_pct is None:
        return True
    same_way = (eps_pct > 0) == (rev_pct > 0)
    return not (same_way and abs(eps_pct) >= abs(rev_pct) * REVENUE_DOUBT_EPS_SHARE)


def _revenue_in_doubt(snap: LiveSnapshot) -> bool:
    """Is the revenue's beat or miss more likely FMP setting one measure against a consensus on
    another than news (owner, 2026-11-20: "Add the check")? A revenue `REVENUE_DOUBT_PCT` or more
    off a consensus of at least `REVENUE_DOUBT_MIN_ESTIMATE`, whose EPS does not move the same way
    by at least `REVENUE_DOUBT_EPS_SHARE` of it (a held or absent EPS, or one with no surprise to
    state, included). A release's GAAP EPS neither raises the doubt nor settles it. The push then
    tells the revenue with no verdict (`_phrase`); the card and the sheet still show FMP's numbers.
    `EARNINGS_LIVE_REVENUE_CHECK_ENABLED` turns it off.

    FRO's first answer on 2026-11-20 set IFRS revenue against a consensus on the industry's TCE
    measure, +31%, beside an EPS +10%. That season 99 US reports had revenue 25%+ off a consensus
    of $100M or more; 16 of them were read against the company's own release: 9 real surprises
    and 7 measure mismatches. Through the record this rule keeps all 9, a release read first with
    a GAAP EPS alone (MRNA, RUN, IONS, GOLD) included, and withholds 3 of the 7 (TFC, CNA, KKRT)
    and FRO's first answer; the record's revenue guard already holds NAVI's and APLD's ("being
    confirmed"); it misses BEN and HGV, whose EPS rose with the mismatched revenue. It fires on 34
    of that season's 2,570 US reports with a consensus of $100M or more (1.3%; 0.7% of all; the
    2026-11-20 review measured it at 7.1% of all before the floor, 19.6% of reports under it)."""
    revenue, eps = snap.revenue, snap.eps
    # A held figure is never shown (`actual` None): a held revenue raises no doubt, and a held EPS
    # settles none.
    if revenue.actual is None:
        return False
    shown = eps.actual is not None
    return _in_doubt(revenue.estimate, revenue.surprise_pct,
                     eps.surprise_pct if shown else None,
                     eps_gaap=shown and eps.basis == BASIS_GAAP)


def _phrase(label: str, m: LiveMetric, *, revenue: bool, verdict: bool = True) -> str:
    """One metric's words in `live_copy`; `verdict` False tells a figure with no beat or miss."""
    if m.checking:
        # A figure exists but a sanity guard holds it (a dropped-digit signature).
        return f"{label} being confirmed"
    if m.actual is None:
        return f"{label} not out yet"
    vs = _vs_consensus(m) if verdict else None
    if m.source == SOURCE_FILING:
        figure = f"{label} {_money(m.actual, revenue=revenue)}"
        return f"{figure} ({vs})" if vs else figure
    return f"{label} {vs}" if vs else f"{label} reported"


def _live_phrases(snap: LiveSnapshot) -> Tuple[str, str]:
    """`live_copy`'s words for each metric, (revenue, EPS). A revenue in doubt
    (`_revenue_in_doubt`) is told with no verdict."""
    eps_label = "GAAP EPS" if snap.eps.basis == BASIS_GAAP else "EPS"
    return (_phrase("Revenue", snap.revenue, revenue=True, verdict=not _revenue_in_doubt(snap)),
            _phrase(eps_label, snap.eps, revenue=False))


def _live_body(phrases: Tuple[str, ...], preliminary: bool) -> str:
    body = " · ".join(phrases) + "."
    if preliminary:
        body += " From the company's release."
    return body


def live_copy(snap: LiveSnapshot) -> Tuple[str, str]:
    """Title/body for an `earnings_live` push. States what landed and stops."""
    return f"{snap.symbol} results are out", _live_body(_live_phrases(snap), snap.preliminary)


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
        wake = WakeCopy(*text, send=False, ask_again_in=WAKE_ASK_AGAIN)
    if (wake is not None and text is not None and kind == KIND_EARNINGS_RESULT
            and wake.title != result_copy(report[0], MIN_SURPRISE)[0]):
        # A result row a wake writes in the live kind's words: its track starts looking
        # (`_revise_changed`). Only at a wake (`text`): `instead_at_wake` asks with none, and
        # deletes a row it would not send, so no row ever holds what it read.
        _result_rows_in_live_words.add(report)
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
    for key in [k for k in _told if k[1] not in days]:
        del _told[key]
    for key in [k for k in _result_rows_in_live_words if k[1] not in days]:
        _result_rows_in_live_words.discard(key)
    for pending in (_revise_after, _failed_until):
        for key in [k for k in pending if k[1] not in days]:
            del pending[key]


#: A figure stated with no verdict: no consensus, a print that covers it, or a GAAP EPS.
_NO_VERDICT: Stated = ("reported", None)


def _stated(m: LiveMetric) -> Stated:
    """What the card states about one metric (`Stated`): no figure while it is not out yet or
    held, so a figure a guard takes back is never 'corrected' into silence."""
    if m.actual is None or m.checking:
        return None
    return _verdict(m) or _NO_VERDICT


class _Said(NamedTuple):
    """What a push of one kind would state about a report now (`_said_now`)."""

    stated: Tuple[Stated, ...]
    title: str
    body: str
    # The live kind's words for each metric, (revenue, EPS); none for the result kind.
    phrases: Tuple[str, ...] = ()
    # The live kind's revenue surprise behind `stated` now (`_Words.revenue_pcts`).
    revenue_pct: Optional[float] = None


def _words_of(said: _Said) -> _Words:
    """What a text the record's words make states (`_Words`)."""
    return _Words(said.stated, said.phrases,
                  frozenset() if said.revenue_pct is None else frozenset({said.revenue_pct}))


def _know(known: Dict[str, _Words], text: str, words: _Words) -> None:
    """Keep `text` among those a report's rows may hold, with every revenue surprise it was written
    at so far (`_Words.revenue_pcts`): the same words seen at another surprise add it, never
    replace what was known."""
    held = known.get(text)
    known[text] = words if held is None else replace(
        held, revenue_pcts=held.revenue_pcts | words.revenue_pcts)


def _result_stated(surprise: float) -> Stated:
    """`result_copy`'s statement of a surprise past `MIN_SURPRISE`: its verb and its whole
    percent, worked out as it works them out."""
    return ("beat" if surprise > 0 else "missed", f"{abs(surprise) * 100:.0f}")


def _said_now(kind: str, snap: LiveSnapshot) -> Optional[_Said]:
    """What a push of `kind` would state about `snap` now, and its words, or None when it would
    state nothing a correction could stand on. The result kind speaks for FMP's EPS alone. Past
    `MIN_SURPRISE` it speaks in the words every result row there is written in (`result_copy`: the
    push, a wake, a re-kind, the 16:00 ET pass), from the same unrounded surprise: the card's
    whole percent of the record's two-decimal one can print another number ("8%" against
    `result_copy`'s "7%" at 7.4999%, none at all from 1,000%), and a row holding words no look saw
    is out of every correction's reach. Under it only a correction writes a result row, in the
    card's words (`_vs_consensus`), so a restatement to the consensus reads "in line", and one
    under half a percent prints no "by 0%"."""
    if kind == KIND_EARNINGS_LIVE:
        phrases = _live_phrases(snap)
        revenue, eps = _stated(snap.revenue), _stated(snap.eps)
        if revenue is not None and _revenue_in_doubt(snap):
            # Told with no verdict, as `_live_phrases` words it. While the doubt rests on an EPS
            # the record holds ("being confirmed"), the revenue states nothing either: a passing
            # hold corrects nothing, and a row keeps its own words (`_merged`). An EPS not out yet
            # is no passing state, so there the revenue is what the words say, a figure with no
            # verdict, and a row whose verdict FMP has since moved is corrected to it.
            revenue = None if snap.eps.checking else _NO_VERDICT
        return _Said((revenue, eps), live_copy(snap)[0],
                     _live_body(phrases, snap.preliminary), phrases,
                     snap.revenue.surprise_pct if revenue is not None else None)
    eps = snap.eps
    stated = _stated(eps)
    if eps.source != SOURCE_FMP or stated is None:
        return None
    # `result_copy`'s title, one per symbol whatever the surprise: the one the push went out with.
    title = result_copy(snap.symbol, MIN_SURPRISE)[0]
    surprise = record_surprise(snap)
    if surprise is not None:
        return _Said((_result_stated(surprise),), title, result_copy(snap.symbol, surprise)[1])
    return _Said((stated,), title,
                 f"EPS {_vs_consensus(eps) or 'reported'}. Tap for the full picture.")


def _contradicts(was: Stated, new: Stated) -> bool:
    """Does the record's statement on one metric now (`new`) contradict what a push stated
    (`was`)? Only a verdict can be contradicted: a figure sent with none that gains one is news,
    and a metric the record no longer states (not out, or held) contradicts nothing."""
    return was is not None and new is not None and was != new and was != _NO_VERDICT


def _changed(then: Tuple[Stated, ...], now: Tuple[Stated, ...]) -> bool:
    """Does `now` contradict what was sent, on any metric (`_contradicts`)?"""
    return any(_contradicts(was, new) for was, new in zip(then, now))


def _out_of_words(was: Stated, new: Stated) -> bool:
    """Did a push state a metric that the record no longer states (not out, or held)?"""
    return was is not None and new is None


def _without_verdict(phrase: str) -> str:
    """`_phrase`'s words for the same figure with no verdict: the release's figure alone, or
    "reported" for FMP's."""
    if phrase.endswith(")") and " (" in phrase:
        return phrase[:phrase.rindex(" (")]
    return f"{phrase.split(' ', 1)[0]} reported"


def _own_revenue_in_doubt(revenue: Stated, revenue_pcts: AbstractSet[float],
                          snap: LiveSnapshot) -> bool:
    """The revenue check (`_in_doubt`) on a row's own revenue verdict, set beside the record's
    EPS, at the record's revenue consensus, at each two-decimal surprise its text was written at
    (`_Words.revenue_pcts`; the percent it printed only when none is known): in doubt if a push of
    the pair would withhold at any of them. The push judged that surprise, never its whole
    percent, and the rows holding one text cannot be told apart: a figure with no verdict is never
    false."""
    if revenue is None or revenue[0] not in ("beat", "missed"):
        return False
    pcts = set(revenue_pcts)
    if not pcts:
        if revenue[1] is None:
            return False
        pcts = {float(revenue[1]) if revenue[0] == "beat" else -float(revenue[1])}
    eps = snap.eps
    shown = eps.actual is not None
    return any(_in_doubt(snap.revenue.estimate, pct, eps.surprise_pct if shown else None,
                         eps_gaap=shown and eps.basis == BASIS_GAAP) for pct in pcts)


def _prints_release_figure(phrases: Tuple[str, ...]) -> bool:
    """Does a `live_copy` body of `phrases` print a figure from the company's release? `_phrase`
    prints a dollar figure for a release figure alone, so its words say it."""
    return any("$" in phrase for phrase in phrases)


def _merged(kind: str, held: _Words, said: _Said, snap: LiveSnapshot) -> Tuple[str, _Words]:
    """The words a row holding `held` takes (`_corrections`), unmarked: the record's words for
    every metric the record states, and the row's own for one the record no longer states (not
    out, or held), so the row never says "not out yet" or "being confirmed" of a figure its phone
    showed, nor shows a figure its phone was spared. A row's own revenue verdict set beside the
    record's EPS goes through the revenue check on that pair (`_own_revenue_in_doubt`), so no
    correction writes a pair a push would never send. A body that prints a release figure, the
    record's or the row's own, ends on the release's sentence, as the push did."""
    if kind != KIND_EARNINGS_LIVE or len(held.phrases) != len(said.phrases):
        return said.body, _words_of(said)
    own = [_out_of_words(was, new) for was, new in zip(held.stated, said.stated)]
    stated = [w if k else n for k, w, n in zip(own, held.stated, said.stated)]
    phrases = [h if k else r for k, h, r in zip(own, held.phrases, said.phrases)]
    revenue_pcts = held.revenue_pcts if own[0] else _words_of(said).revenue_pcts
    if own[0] and not own[1] and _own_revenue_in_doubt(stated[0], revenue_pcts, snap):
        stated[0], phrases[0] = _NO_VERDICT, _without_verdict(phrases[0])
    words = tuple(phrases)
    return (_live_body(words, _prints_release_figure(words)),
            _Words(tuple(stated), words, revenue_pcts))


class _Write(NamedTuple):
    """One correction: the rows past their buzz holding one of `bodies` take `text`, marked."""

    was: str
    text: str
    words: _Words
    bodies: Tuple[str, ...]


def _corrections(kind: str, known: Mapping[str, _Words], written: AbstractSet[str],
                 said: _Said, snap: LiveSnapshot) -> List[_Write]:
    """Every write the record now owes one track's rows. A known text the record contradicts
    (`_changed`) is rewritten on the rows holding it, marked or not. A text a correction wrote
    (`written`) is the sender's own, so its marked rows follow the record whatever moves (owner,
    2026-11-20: "Follow FMP"): a verdict the record took off and later gives again reaches them,
    while a row its own push or wake left with no verdict takes a later one as news. The
    record's own text is never one: it says what the record says."""
    out = []
    for was, held in known.items():
        if was == said.body:
            continue
        contradicted = _changed(held.stated, said.stated)
        if not contradicted and was not in written:
            continue
        text, words = _merged(kind, held, said, snap)
        if text == was:
            continue
        bodies = (was, UPDATED_MARK + was) if contradicted else (UPDATED_MARK + was,)
        out.append(_Write(was, text, words, bodies))
    return out


def _write_corrections(key: str, kind: str, title: str,
                       writes: List[_Write]) -> Tuple[List[int], bool]:
    """Whether any row of `kind` is still held, then each correction on the rows past their buzz
    holding one of its bodies. Held is read FIRST: a row its wake ends after that read is past its
    buzz by the write and takes it, and one the read saw held is owed another look. On a worker
    thread: the client is sync."""
    from app.services.push_dispatch_service import get_push_dispatch_service

    service = get_push_dispatch_service()
    held = service.holds_rows(key, kind)
    changed = [service.revise_sent_text(key, kind, title, UPDATED_MARK + w.text, list(w.bodies))
               for w in writes]
    return changed, held


def _note_told(kind: str, snap: LiveSnapshot, title: str, body: str) -> None:
    """A push of `kind` went out for `snap` in `body`: its rows hold those words. A live push also
    starts following the report's result rows, which a wake can make of a held live row."""
    told = _told.setdefault((snap.symbol, snap.day), {})
    said = _said_now(kind, snap)
    words = (_words_of(said) if said is not None
             else _Words((None,) * (2 if kind == KIND_EARNINGS_LIVE else 1)))
    then = told.get(kind)
    known: Dict[str, _Words] = dict(then.known) if then is not None else {}
    _know(known, body, words)
    if said is not None:
        _know(known, said.body, words)
    told[kind] = _Told(title, known, said.body if said is not None else body,
                       then.held if then is not None else False,
                       then.written if then is not None else frozenset())
    if kind == KIND_EARNINGS_LIVE and KIND_EARNINGS_RESULT not in told:
        result = _said_now(KIND_EARNINGS_RESULT, snap)
        told[KIND_EARNINGS_RESULT] = _Told(
            result_copy(snap.symbol, MIN_SURPRISE)[0],
            {result.body: _words_of(result)} if result is not None else {},
            result.body if result is not None else "")
    if RESULT_ROWS_IN_LIVE_WORDS not in told:
        # The result rows a wake can write in live words, whichever push told the report.
        live = _said_now(KIND_EARNINGS_LIVE, snap)
        told[RESULT_ROWS_IN_LIVE_WORDS] = _Told(
            live.title, {live.body: _words_of(live)}, live.body)


def _seen_at_its_surprise(told: Dict[str, _Told], track: str, then: _Told, said: _Said) -> _Told:
    """The record's words, already known, at a revenue surprise they were not known at yet: kept
    every round, though no look is owed (the words did not change), since a held row's wake may
    write them at it now, and a correction then judges that row at it too (`_Words.revenue_pcts`;
    the ninth 2026-11-26 review). Returns the track as it now stands."""
    words = then.known.get(said.body)
    if words is None or said.revenue_pct is None or said.revenue_pct in words.revenue_pcts:
        return then
    known = dict(then.known)
    _know(known, said.body, _words_of(said))
    told[track] = replace(then, known=known)
    return told[track]


async def _revise_changed(snapshots: List[LiveSnapshot]) -> int:
    """A RESULT FMP CHANGES (module docstring): look again at each followed report whose record's
    words changed since the last look, or whose rows were still held then (every
    `HELD_RETRY_SECONDS`), and correct the rows still holding a text the record now contradicts.
    Looks a record change owes go first, then held re-looks, longest due first, so recurring
    re-looks never starve a report that just changed, nor each other. Returns the looks that
    changed a row. Never raises: the first failed write ends the round's corrections and waits
    `HELD_RETRY_SECONDS`, so the next rounds start with the others."""
    if not settings.EARNINGS_LIVE_INBOX_CORRECTIONS_ENABLED:
        return 0
    revised = tried = put_off = 0
    mono = time.monotonic()
    owed: List[Tuple[int, float, int, LiveSnapshot, str, _Told, _Said]] = []
    for snap in snapshots:
        told = _told.get((snap.symbol, snap.day))
        if not told:
            continue
        for track, then in list(told.items()):
            again = (snap.symbol, snap.day, track)
            if _failed_until.get(again, 0.0) > mono:
                continue
            said = _said_now(_TRACKS[track][1], snap)
            if said is None:
                continue
            then = _seen_at_its_surprise(told, track, then, said)
            due = _revise_after.get(again, 0.0)
            if said.body != then.seen:
                owed.append((0, 0.0, len(owed), snap, track, then, said))
            elif then.held and due <= mono:
                owed.append((1, due, len(owed), snap, track, then, said))
    owed.sort(key=lambda o: o[:3])
    for _, _, _, snap, track, then, said in owed:
        rows_kind, kind = _TRACKS[track]
        again = (snap.symbol, snap.day, track)
        told = _told.get((snap.symbol, snap.day))
        if told is None:
            continue
        known: Dict[str, _Words] = dict(then.known)
        _know(known, said.body, _words_of(said))
        writes = _corrections(kind, known, then.written, said, snap)
        if not writes or (track == RESULT_ROWS_IN_LIVE_WORDS
                          and (snap.symbol, snap.day) not in _result_rows_in_live_words):
            # Nothing the record contradicts (held rows wake to the record's own words), or no
            # result row in the live words yet: the texts are kept for when a wake writes one.
            told[track] = _Told(then.title, known, said.body, False, then.written)
            _revise_after.pop(again, None)
            continue
        if tried >= MAX_REVISIONS_PER_ROUND:
            put_off += 1
            continue
        tried += 1
        key = result_dedup_key(snap.symbol, snap.day.isoformat())
        # Kept before the write: a write can commit and its call still fail, and a row holding a
        # text the sender forgot is out of every later look's reach. An empty `seen` owes a look
        # after the back-off whatever the record says then.
        for write in writes:
            _know(known, write.text, write.words)
        written = then.written | {write.text for write in writes}
        told[track] = _Told(then.title, known, "", then.held, written)
        try:
            changed, held = await asyncio.to_thread(_write_corrections, key, rows_kind,
                                                    then.title, writes)
        except Exception as e:
            _failed_until[again] = mono + HELD_RETRY_SECONDS
            logger.warning("earnings live push: %s %s changed after its %s push, but its inbox "
                           "rows could not be updated (%s: %s); no more corrections this "
                           "round, this one again in %ds", snap.symbol, snap.day.isoformat(),
                           track, type(e).__name__, e, HELD_RETRY_SECONDS)
            return revised
        _failed_until.pop(again, None)
        told[track] = _Told(then.title, known, said.body, held, written)
        if held:
            _revise_after[again] = mono + HELD_RETRY_SECONDS
        else:
            _revise_after.pop(again, None)
        if sum(changed):
            revised += 1
            for write, rows in zip(writes, changed):
                if rows:
                    logger.warning("earnings live push: %s %s changed after its %s push: %d "
                                   "inbox row(s) saying %r now say %r, no second buzz%s",
                                   snap.symbol, snap.day.isoformat(), track, rows,
                                   write.bodies[0], UPDATED_MARK + write.text,
                                   "; rows still held" if held else "")
    if put_off:
        logger.warning("earnings live push: %d inbox correction(s) tried this round, %d more go "
                       "next round", tried, put_off)
    return revised


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
                    told = await _notify(KIND_EARNINGS_LIVE, snap, title, body)
                    stats["live"] += told
                    _note_told(KIND_EARNINGS_LIVE, snap, title, body)
                    if _revenue_in_doubt(snap):
                        # The landing's words. A row held for the night takes the record's words at
                        # its wake, which may differ (A HELD ROW'S TEXT in the module docstring).
                        logger.info(
                            "EARNINGS_LIVE_REVENUE_WITHHELD sym=%s day=%s rev_pct=%s eps_pct=%s "
                            "rev_est=%s told=%d: the landing's push tells the revenue with no beat "
                            "or miss (`_revenue_in_doubt`)", snap.symbol, snap.day.isoformat(),
                            snap.revenue.surprise_pct, snap.eps.surprise_pct,
                            snap.revenue.estimate, told)
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
            _note_told(KIND_EARNINGS_RESULT, snap, title, body)
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
    # Every landed report, the ones with nothing left to send among them (`_to_decide` drops
    # those): a correction is owed long after the last send. And every report this process told
    # whose ticker the watched list no longer holds (`_told_off_the_list`).
    await _revise_changed(snapshots + _told_off_the_list(snapshots, now))
    return stats


def _told_off_the_list(snapshots: List[LiveSnapshot], now: datetime) -> List[LiveSnapshot]:
    """The reports this process told (`_told`) that `snapshots` leaves out, as `landed_snapshots`
    reads them: the tick hands over the watched list's landings only, and a ticker its last
    watcher took off after its push, or one the list's row cap dropped, still has its inbox rows,
    and the 16:00 ET pass still folds its record. Never raises: a failed read leaves them for the
    next round."""
    have = {(snap.symbol, snap.day) for snap in snapshots}
    missing = {key for key in _told if key not in have}
    if not missing:
        return []
    try:
        landed = get_earnings_live_service().landed_snapshots(now)
    except Exception as e:
        logger.warning("earnings live push: could not read %d told report(s) off the watched "
                       "list for their inbox corrections (%s: %s); next round",
                       len(missing), type(e).__name__, e, exc_info=True)
        return []
    return [snap for snap in landed if (snap.symbol, snap.day) in missing]
