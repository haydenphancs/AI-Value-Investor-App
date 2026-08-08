"""Earnings notifications — "reports tomorrow" and "beat/missed by X%".

ONE FMP CALL PER DAY. `get_earnings_calendar(from, to)` is market-wide, so a single
four-day window serves BOTH passes for every ticker anyone watches. The per-symbol
`get_earning_calendar_full` would be ~200 calls to learn the same thing — that is the
"one call returns all fields" rule from `project_fmp_api_architecture.md`, and it is the
single most important design decision in this file.

Two passes over that one response:

  A. **Upcoming** — a ticker reports tomorrow (or later today, before the open).
     The value is preparation: a user holding a position wants to know a print is
     coming, not to be surprised by a 9% gap the next morning.
  B. **Result** — a ticker reported and the EPS surprise cleared a materiality bar.
     No surprise number, no notification; a print that landed on consensus is not news.

Scheduled after the close (16:00 ET) because that is when both halves are true at once:
tomorrow's calendar is settled, and today's after-close prints have started landing.

WHAT THIS FILE DELIBERATELY DOES NOT DO
---------------------------------------
It does not decide whether any particular user gets a notification. Preference, group
master, per-category cap, quiet hours and dedup are all `push_dispatch_service`'s job.
A sender that reached past that would be a sender that can ignore an opt-out.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client
from app.services._earnings_common import parse_fmp_timing, timing_sentence
from app.services.notification_jobs import JOB_EARNINGS, claimed_job
from app.services.notification_kinds import (
    KIND_EARNINGS_RESULT,
    KIND_EARNINGS_UPCOMING,
)
from app.services.push_dispatch_service import get_push_dispatch_service
from app.services.updates_materiality import finite
from app.utils.market_hours import ET

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
    """
    est = finite(row.get("epsEstimated"))
    act = finite(row.get("epsActual") if "epsActual" in row else row.get("eps"))
    if est is None or act is None:
        return None
    if abs(est) < MIN_ABS_ESTIMATE:
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


def select_results(
    rows: List[Dict[str, Any]], today: date
) -> List[Tuple[str, str, float]]:
    """Rows worth a "beat/missed" notification → (symbol, date, surprise fraction).

    Window is today and yesterday: an after-close print lands in the evening, and a
    calendar row can take until the next morning to carry `epsActual`. A wider window
    would start re-announcing last week's earnings on a slow news day.
    """
    yesterday = today - timedelta(days=1)
    out: List[Tuple[str, str, float]] = []
    seen: set[str] = set()
    for row in rows:
        sym, when = _symbol(row), _row_date(row)
        if not sym or not when or sym in seen:
            continue
        try:
            row_date = date.fromisoformat(when)
        except ValueError:
            continue
        if row_date not in (today, yesterday):
            continue
        surprise = surprise_pct(row)
        if surprise is None or abs(surprise) < MIN_SURPRISE:
            continue
        seen.add(sym)
        out.append((sym, when, surprise))
    return out


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
    """
    return await get_push_dispatch_service().notify_watchers(
        ticker=symbol,
        title=title,
        body=body,
        dedup_key=f"{kind}:{symbol}:{when}",
        kind=kind,
        # FLAT SCALARS ONLY (iOS AnyCodable yields "" for anything nested).
        data={"ticker": symbol, "asset_type": "stock", "route": "ticker"},
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
      * FMP returns `[]` → that is a market HOLIDAY (or a genuinely empty calendar),
        which is a SUCCESSFUL run of zero. Stamping `run_day` here is correct; retrying
        every hour against an empty calendar would be pure waste.
    """
    stats = {"upcoming": 0, "results": 0, "symbols": 0}

    async with claimed_job(JOB_EARNINGS) as run:
        if run is None:
            logger.debug("earnings notifications: not claimed (already run, or held)")
            return stats

        today = _et_today(now)
        # One market-wide call, four-day window: yesterday (late-arriving actuals)
        # through tomorrow (the upcoming pass). Both passes read this same response.
        rows = await get_fmp_client().get_earnings_calendar(
            from_date=(today - timedelta(days=1)).isoformat(),
            to_date=(today + timedelta(days=1)).isoformat(),
        )
        if not isinstance(rows, list):
            raise TypeError(f"earnings calendar returned {type(rows).__name__}, not a list")

        if not rows:
            # Holiday / empty calendar. A successful run of zero — see the docstring.
            logger.info("earnings notifications: calendar empty for %s (holiday?)", today)
            run.success = True
            return stats

        upcoming = select_upcoming(rows, today)[:MAX_SYMBOLS_PER_PASS]
        results = select_results(rows, today)[:MAX_SYMBOLS_PER_PASS]
        stats["symbols"] = len(upcoming) + len(results)

        for symbol, when, timing in upcoming:
            title, body = upcoming_copy(symbol, timing)
            stats["upcoming"] += await _dispatch(
                kind=KIND_EARNINGS_UPCOMING, symbol=symbol, when=when,
                title=title, body=body,
            )

        for symbol, when, surprise in results:
            title, body = result_copy(symbol, surprise)
            stats["results"] += await _dispatch(
                kind=KIND_EARNINGS_RESULT, symbol=symbol, when=when,
                title=title, body=body,
            )

        run.notified = stats["upcoming"] + stats["results"]
        run.success = True

    logger.info(
        "earnings notifications: %d upcoming + %d result sends across %d symbol(s)",
        stats["upcoming"], stats["results"], stats["symbols"],
    )
    return stats
