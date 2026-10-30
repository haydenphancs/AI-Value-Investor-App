"""Live earnings pushes — sent the moment results land (owner request 2026-10-29).

`earnings_live_service.run_earnings_live_loop` hands this module, every 60 s, the reports
whose results it SAW land in this process. Two kinds, ONE dedup key per report
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
dispatcher's job — nothing here decides who is told.

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
)
from app.services.notification_kinds import (
    KIND_EARNINGS_LIVE,
    KIND_EARNINGS_RESULT,
    ticker_route,
)
from app.services.notification_senders.earnings_sender import (
    MIN_SURPRISE,
    result_copy,
    result_dedup_key,
    surprise_pct,
)

logger = logging.getLogger(__name__)

#: Bound one round's fan-out, as `earnings_sender.MAX_SYMBOLS_PER_PASS` does. Anything over
#: it is picked up on the next 60 s round, never dropped.
MAX_SENDS_PER_ROUND = 60

# Once per (symbol, report date) per process. The dedup key is what stops a restart from
# re-sending; these sets stop the same process from re-dispatching every 60 s.
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
    )


def _prune(now: datetime) -> None:
    """Forget keys older than the live loop's two days, so the sets stay bounded."""
    from app.services.earnings_live_service import et_now
    from app.utils.market_hours import previous_trading_day

    floor = previous_trading_day(et_now(now).date())
    for done in (_live_done, _surprise_done):
        for key in [k for k in done if k[1] < floor]:
            done.discard(key)


async def dispatch_landings(snapshots: List[LiveSnapshot], now: datetime) -> Dict[str, int]:
    """Send what each landed report is owed, live kind FIRST. Never raises per report."""
    _prune(now)
    stats = {"live": 0, "result": 0}
    sends = 0
    for snap in sorted(snapshots, key=lambda s: (s.day, s.symbol)):
        if sends >= MAX_SENDS_PER_ROUND:
            logger.warning("earnings live push: %d landings this round, cap %d — the rest "
                           "go next round", len(snapshots), MAX_SENDS_PER_ROUND)
            break
        key = (snap.symbol, snap.day)
        if key not in _live_done:
            _live_done.add(key)
            if settings.EARNINGS_LIVE_PUSH_ENABLED:
                title, body = live_copy(snap)
                try:
                    stats["live"] += await _notify(KIND_EARNINGS_LIVE, snap, title, body)
                except Exception as e:  # `notify_watchers` promises not to raise; belt only
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
        except Exception as e:
            logger.warning("earnings live result push for %s failed (%s: %s)",
                           snap.symbol, type(e).__name__, e)
        sends += 1
    if stats["live"] or stats["result"]:
        logger.info("earnings live push: %d live + %d result sends", stats["live"],
                    stats["result"])
    return stats
