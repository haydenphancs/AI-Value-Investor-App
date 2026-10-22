"""Read-only live probe of FMP's intraday history (`/stable/historical-chart/{interval}`).

Answers the two numbers the paged chart history depends on, per pageable interval:

  1. DEPTH — how far back full weeks of bars exist. One 7-day window at increasing ages;
     the first empty window bounds it. Sets `price_history_service._INTRADAY_MAX_LOOKBACK_DAYS`.
  2. PER-REQUEST CAP — how much of one long request FMP answers (it keeps the NEWEST
     part and drops the rest without saying so). Asked with and without `extended`, so a
     calendar cap (same sessions) tells apart from a row cap (fewer sessions). Sets
     `chart_helper.FMP_INTRADAY_SPAN_DAYS`; every paging window must fit inside it.

Run from backend/ (about 40-45 FMP calls):

    ./venv/bin/python scripts/probe_fmp_intraday_depth.py [SYMBOL]

The API key is read by the app's Settings (backend/.env) and never printed: logging is
disabled and every printed line is scrubbed of it. Last run 2026-10-21 on INTC: depth ≥ 15
years on every interval; caps 5min 7 / 15min 42 / 30min 28 / 1hour 89 days, calendar-based.
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)

from app.config import settings  # noqa: E402
from app.integrations.fmp import FMPClient  # noqa: E402

INTERVALS = ("5min", "15min", "30min", "1hour")
LADDER = (60, 180, 365, 730, 1095, 1825, 2920, 3650, 5475)  # days back: 2 months .. 15 years
_KEY = settings.FMP_API_KEY or "\x00"


def _out(line: str) -> None:
    print(line.replace(_KEY, "***"), flush=True)


async def _read(client, symbol, interval, start, end, extended=False):
    try:
        rows = await client.get_intraday_prices(
            symbol, interval, start.isoformat(), end.isoformat(), strict=True,
            **({"extended": True} if extended else {}),
        )
    except Exception as e:  # a failure is reported, never read as "no data"
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    dates = sorted(r["date"] for r in rows if isinstance(r, dict) and r.get("date"))
    sessions = len({d[:10] for d in dates})
    span = f"{dates[0]} .. {dates[-1]}" if dates else "-"
    return len(dates), f"sessions={sessions}  {span}"


async def main(symbol: str) -> None:
    client = FMPClient()
    today = datetime.now(timezone.utc).date()
    calls = 0
    try:
        _out(f"-- depth: one 7-day window per age ({symbol})")
        for interval in INTERVALS:
            for age in LADDER:
                end = today - timedelta(days=age)
                n, info = await _read(client, symbol, interval, end - timedelta(days=6), end)
                calls += 1
                _out(f"{interval:6} {age:5}d back  rows={n}  {info}")
                if not n:
                    break
                await asyncio.sleep(0.25)
        _out("-- per-request cap: one 370-day request (regular, then extended)")
        start, end = today - timedelta(days=400), today - timedelta(days=30)
        for interval in INTERVALS:
            for extended in (False, True):
                n, info = await _read(client, symbol, interval, start, end, extended)
                calls += 1
                _out(f"{interval:6} ext={extended!s:5} rows={n}  {info}")
                await asyncio.sleep(0.25)
    finally:
        await client.close()
    _out(f"-- FMP calls made: {calls}")


if __name__ == "__main__":
    asyncio.run(main((sys.argv[1] if len(sys.argv) > 1 else "INTC").upper()))
