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
     and a figure read from a filing is preliminary.

`push_dispatch_service` claims the key only for users who pass their toggle and cap, so a
user with Live Results on gets #1 and is skipped by #2 as a duplicate; a user with only
Results & Surprises on gets #2; and `run_earnings_notifications`' daily pass later finds
every key claimed. Preference, master switch, category cap and quiet hours stay the
dispatcher's job — nothing here decides who is told. That includes the hour: both kinds carry
`night_hold_by_default`, so a landing in the night of a user who never set Quiet Hours is
claimed at once and buzzes at 07:00 on their clock.

THE WINDOW. Neither kind goes out for a report older than yesterday: the daily pass's own
rule (`earnings_sender.in_result_window`), which is narrower than the live days around a
weekend or a holiday. A Friday report FMP fills on Monday lands, keeps its card and its sheet,
and is marked handled here with nothing further sent: a live push from its filing, if one went
out inside the window, is all it gets. The rule decides what starts: a row claimed inside the
window is the dispatcher's, and still goes out at its wake if the report has aged past it.

A FAILED WATCHER READ. A report's key is taken before its send, and the dispatcher's usual 0
for a failed read looks like a ticker nobody watches, so one failed read at the landing minute
would use up the report: no later round sends it, a restart cannot re-land it, and
`earnings_live` has no daily pass behind it. So `_notify` asks to hear about it (`strict`). The
read told nobody and claimed nothing, so the key goes back and the next round retries. The
round ends there: the live kind still goes first, and the tick, which awaits this inline, pays
for one failed read a round however many reports wait. A failed claim or device read after a
good watcher read comes back as a count like any other, so it does use up the key; only a
failed claim leaves `earnings_result` the daily pass.

COPY. Informational, never directive (SYSTEM_DESIGN_GUIDELINES §11.7). A lock-screen banner
sits OUTSIDE the authenticated app, which is all FMP's Order Form licenses for display, so an
absolute figure appears only when it comes from the company's own release (a public SEC
document); an FMP-sourced figure is stated as beat/missed by a percentage, the shape
`result_copy` already ships.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Set, Tuple

from app.config import settings
from app.services.earnings_live_service import (
    BASIS_GAAP,
    SOURCE_FILING,
    SOURCE_FMP,
    LiveMetric,
    LiveSnapshot,
    et_now,
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

# Once per (symbol, report date) per process. The dedup key is what stops a restart from
# re-sending; these sets stop the same process from re-dispatching every 60 s. A key is taken
# before its send and comes back only on a failed watcher read (`_give_back`).
_live_done: Set[Tuple[str, date]] = set()
_surprise_done: Set[Tuple[str, date]] = set()


def reset() -> None:
    """Forget what was dispatched (tests)."""
    _live_done.clear()
    _surprise_done.clear()


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
    if m.surprise_pct is None:
        return None
    if m.surprise_pct == 0:
        return "in line with consensus"
    verb = "beat" if m.surprise_pct > 0 else "missed"
    pct = abs(m.surprise_pct)
    if pct >= 1000 or pct < 0.5:
        # A near-zero consensus makes the percentage explode; a sub-1% one rounds to "0%".
        return f"{verb} consensus"
    return f"{verb} consensus by {pct:.0f}%"


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


async def _notify(kind: str, snap: LiveSnapshot, title: str, body: str) -> int:
    from app.services.push_dispatch_service import get_push_dispatch_service

    when = snap.day.isoformat()
    return await get_push_dispatch_service().notify_watchers(
        ticker=snap.symbol,
        title=title,
        body=body,
        dedup_key=result_dedup_key(snap.symbol, when),
        kind=kind,
        data=ticker_route(kind, snap.symbol, report_date=when),
        strict=True,
    )


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
    result window soonest, so the cap must hold back a newer one instead. One already outside
    the window is handled here, both kinds at once: no later round has anything left to decide
    on it, and it reaches neither the cap nor the log again. Its keys leave with the live days,
    like every other (`_prune`)."""
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
    return sorted(open_reports, key=lambda s: (s.day, s.symbol))


def _still_owed(snap: LiveSnapshot) -> bool:
    """Would `dispatch_landings` still send for this report? Its gates, for the cap warning."""
    key = (snap.symbol, snap.day)
    if settings.EARNINGS_LIVE_PUSH_ENABLED and key not in _live_done:
        return True
    if not settings.EARNINGS_LIVE_RESULT_PUSH_ENABLED or key in _surprise_done:
        return False
    eps = snap.eps
    if eps.actual is None or eps.source != SOURCE_FMP:
        return False
    surprise = surprise_pct({"epsEstimated": eps.estimate, "epsActual": eps.actual})
    return surprise is not None and abs(surprise) >= MIN_SURPRISE


def _give_back(done: Set[Tuple[str, date]], snap: LiveSnapshot, left: List[LiveSnapshot],
               error: WatcherLookupFailed) -> None:
    """A failed watcher read told nobody, so `snap`'s key comes out of `done` for the next round
    to retry, and `left`, the reports from `snap` on, wait with it (A FAILED WATCHER READ in the
    module docstring)."""
    done.discard((snap.symbol, snap.day))
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
