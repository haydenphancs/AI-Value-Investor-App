"""Smart-money notifications — insider Form 4s, 13F filings, congressional trades.

This is the app's differentiator: nobody else pushes "a senator bought a stock you
watch". It is also the easiest category in the whole system to get wrong, because the
underlying data has two properties that fight each other:

  * it is DISCLOSED in bursts (one 13F carries forty positions), and
  * its timestamps mean different things (`created_at` is when WE ingested a row;
    `date` is when the trade happened, which can be a quarter earlier).

Both are handled explicitly below, and both have already produced production bugs in
this repo — see the backfill guard in `_recent_whale_rows`.

Two phases, one claimed job, because they share a schedule and a category budget:

  1. **Insider (Form 4)** — ~200 FMP calls, one per watchlisted ticker. The only
     FMP spend in this file.
  2. **Whale / congress** — ZERO FMP calls. `whale_trades` is already hydrated daily by
     `_run_whale_hydration_job`; this reads what that job wrote.

Scheduled at 18:00 ET: Form 4s land through the afternoon and evening, and a filing
notification has no intraday urgency — hence `passive` delivery, which lets iOS batch it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.services._insider_common import classify_for_alerts, normalize_insider_name
from app.services._whale_common import (
    format_amount_range,
    format_amount_short,
    parse_congress_amount_bounds,
)
from app.services.notification_jobs import JOB_SMART_MONEY, claimed_job, last_cursor
from app.services.notification_kinds import (
    KIND_CONGRESS_TRADE,
    KIND_INSIDER_TRADE,
    KIND_WHALE_13F,
    ticker_route,
)
from app.services.push_dispatch_service import get_push_dispatch_service
from app.services.updates_materiality import finite
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# Dollar floor for an insider trade worth interrupting someone about. Mirrors
# `tracking_service._get_insider_transaction_alerts` so the push and the in-app
# Tracking feed never disagree about what counts as notable.
MIN_INSIDER_AMOUNT = 100_000

# Universe size for the insider pass. Same RPC and same cap the Updates sweeper uses, so
# the two jobs agree on what "the tickers people actually watch" means.
INSIDER_UNIVERSE = 200

# Concurrent FMP calls. Matches the news refresher's ceiling — FMP is shared with the
# report pipeline and the sweeper, and this job runs while both may be active.
INSIDER_CONCURRENCY = 5

# How far back a Form 4 counts as news. Filings lag the trade by up to two business
# days; beyond that the market has priced it.
INSIDER_LOOKBACK_DAYS = 3

# Trade-date floor for whale rows. A 13F is a quarterly snapshot, so its `date` is
# legitimately weeks old — but not months. See `_recent_whale_rows`.
WHALE_TRADE_MAX_AGE_DAYS = 45

# Ceiling on distinct notifications per phase, before per-user caps. A heavy filing day
# must not turn into a fan-out storm; the per-user `smart_money` cap of 3 is the
# backstop, but the work itself is what this bounds.
MAX_EVENTS_PER_PHASE = 40


# ── insider ──────────────────────────────────────────────────────────────────


def _filing_date(tx: Dict[str, Any]) -> Optional[str]:
    """The FILING date, `YYYY-MM-DD`.

    ⚠️ FILING date, not transaction date, and the distinction is the whole dedup story.
    A Form 4 for a trade three days ago that files TODAY is new information and should
    fire. The same row seen again tomorrow must not. Keying on `transactionDate` would
    do both jobs badly: it would miss late filings entirely (their transaction date is
    already outside the window) and it would re-fire on an amendment, which files again
    under the same transaction date.
    """
    raw = str(tx.get("filingDate") or "")[:10]
    return raw if len(raw) == 10 else None


def notable_insider_trade(
    trades: Any, *, cutoff: str
) -> Optional[Tuple[str, str, str, float]]:
    """The single most notable informative Form 4 for one ticker.

    Returns `(filing_date, insider_name, action_word, dollars)` or None.

    One notification per ticker, not one per filing. A CFO selling in four tranches
    files four rows for one decision, and four banners for one decision is precisely how
    an app trains people to disable its notifications.

    Every arithmetic input goes through `finite()`: FMP emits NaN/Infinity JSON tokens on
    thin names, and NaN silently answers False for every comparison — which would
    disable the `>= MIN_INSIDER_AMOUNT` gate rather than trip it.
    """
    if not isinstance(trades, list):
        return None

    # Aggregate by (insider, filing date, direction): one decision, however many rows.
    buckets: Dict[Tuple[str, str, str], float] = {}
    for tx in trades:
        if not isinstance(tx, dict):
            continue
        filed = _filing_date(tx)
        if not filed or filed < cutoff:
            continue
        action, informative = classify_for_alerts(tx.get("transactionType") or "")
        if not informative:
            # Option exercises, tax withholding, composite S+OE sales — mechanical
            # events that carry no sentiment. Same filter the Holders tab applies.
            continue
        shares = finite(tx.get("securitiesTransacted"))
        price = finite(tx.get("price"))
        if shares is None or price is None or shares <= 0 or price <= 0:
            continue
        name = normalize_insider_name(tx.get("reportingName")) or "An insider"
        key = (name, filed, action)
        buckets[key] = buckets.get(key, 0.0) + shares * price

    best: Optional[Tuple[str, str, str, float]] = None
    for (name, filed, action), dollars in buckets.items():
        if dollars < MIN_INSIDER_AMOUNT:
            continue
        if best is None or dollars > best[3]:
            best = (filed, name, action, dollars)
    return best


def insider_copy(symbol: str, name: str, action: str, dollars: float) -> Tuple[str, str]:
    """Informational only. States who, which direction, and how much — and stops.

    No "follow the smart money", no implication that an insider sale is bearish (they
    sell for tuition and divorces too). FINRA/SEC name push notifications explicitly as a
    supervised digital-engagement practice; a banner that reads as a trade suggestion is
    the thing to avoid.
    """
    verb = "bought" if action == "bought" else "sold"
    return (
        f"Insider activity in {symbol}",
        f"{name} {verb} {format_amount_short(dollars)} of {symbol}.",
    )


async def _run_insider_phase(now: datetime) -> int:
    """Form 4 pass. Returns notifications delivered."""
    supabase = get_supabase()

    def _universe() -> List[str]:
        try:
            rows = supabase.rpc(
                "get_top_watchlist_tickers", {"n": INSIDER_UNIVERSE}
            ).execute().data or []
            return [str(r["ticker"]).upper() for r in rows if r.get("ticker")]
        except Exception as e:
            logger.warning(
                "smart money: watchlist universe read failed (%s: %s) — skipping the "
                "insider phase this run",
                type(e).__name__, e,
            )
            return []

    tickers = await asyncio.to_thread(_universe)
    if not tickers:
        return 0

    cutoff = (now.astimezone(ET).date() - timedelta(days=INSIDER_LOOKBACK_DAYS)).isoformat()
    fmp = get_fmp_client()
    gate = asyncio.Semaphore(INSIDER_CONCURRENCY)

    async def _one(symbol: str):
        async with gate:
            try:
                trades = await fmp.get_insider_trading(symbol, limit=30)
            except Exception as e:
                # Per-ticker isolation: one bad symbol must not abandon the other 199.
                logger.warning(
                    "smart money: insider fetch for %s failed (%s: %s)",
                    symbol, type(e).__name__, e,
                )
                return None
            return symbol, notable_insider_trade(trades, cutoff=cutoff)

    found = await asyncio.gather(*[_one(t) for t in tickers], return_exceptions=True)

    candidates: List[Tuple[str, Tuple[str, str, str, float]]] = []
    for item in found:
        if isinstance(item, Exception) or item is None:
            continue
        symbol, best = item
        if best is not None:
            candidates.append((symbol, best))

    # Largest first, so a bounded run keeps the most notable events.
    candidates.sort(key=lambda c: c[1][3], reverse=True)

    sent = 0
    for symbol, (filed, name, action, dollars) in candidates[:MAX_EVENTS_PER_PHASE]:
        title, body = insider_copy(symbol, name, action, dollars)
        sent += await get_push_dispatch_service().notify_watchers(
            ticker=symbol,
            title=title,
            body=body,
            # Filing date + normalized name + direction: an amendment that changes the
            # filing date intentionally re-fires (it is a corrected disclosure), and a
            # re-run on the same day cannot.
            dedup_key=f"insider:{symbol}:{filed}:{name}:{action}",
            kind=KIND_INSIDER_TRADE,
            data=ticker_route(KIND_INSIDER_TRADE, symbol),
        )
    return sent


# ── whale / congress ─────────────────────────────────────────────────────────


def _recent_whale_rows(rows: Any, *, cutoff_date: str) -> List[Dict[str, Any]]:
    """Filter freshly-ingested whale trades down to genuinely recent ones.

    ⚠️ THE BACKFILL TRAP, and this repo has shipped it once already.

    `created_at` is when WE ingested a row; `date` is when the trade happened. The first
    hydration of a newly-added whale inserts hundreds of quarter-old filings with a
    brand-new `created_at`. Windowing on `created_at` alone would announce a fund's
    entire historical book as "this week's activity" — one notification per position.

    ⚠️ AND THE PARENTHESES ARE LOAD-BEARING:

        (row.get("date") or "") < cutoff_date          # correct
        row.get("date") or "" < cutoff_date            # NO GUARD AT ALL

    The second parses as `row.get("date") or ("" < cutoff_date)`, i.e. truthy for ANY
    non-empty date string. It looks like a filter and is a no-op. `tests/
    test_whale_alert_backfill_guard.py` exists because of exactly this line.

    A row with a MISSING date is kept: `created_at` is then the only signal available,
    and dropping it would silently lose congressional rows whose transaction date FMP
    omits.
    """
    if not isinstance(rows, list):
        return []
    keep: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        trade_date = str(row.get("date") or "")[:10]
        if trade_date and trade_date < cutoff_date:
            continue
        keep.append(row)
    return keep


def _whale_kind(data_source: Any) -> Optional[str]:
    """Route a whale row to its notification kind by `whales.data_source`.

    Returns None for an unknown source. Defaulting to institutional would be worse than
    skipping: `whale_13f` ships OFF, so an unknown source silently defaulted there would
    be invisible, while defaulting the other way would push congressional-preference
    users about something else entirely.
    """
    source = str(data_source or "").strip().lower()
    if source == "13f":
        return KIND_WHALE_13F
    if source.startswith("congressional"):
        return KIND_CONGRESS_TRADE
    return None


def whale_copy(
    whale_name: str, action: str, tickers: List[str], amount_label: str
) -> Tuple[str, str]:
    """Rolled-up copy for one whale's activity.

    A 40-position 13F becomes ONE notification naming up to three tickers, not forty.
    The per-user `smart_money` cap of 3 is the backstop, not the design.
    """
    verb = "bought" if action == "bought" else "sold"
    shown = ", ".join(tickers[:3])
    more = f" +{len(tickers) - 3} more" if len(tickers) > 3 else ""
    return (
        f"{whale_name} {verb} {shown}{more}",
        # "on your watchlist", NOT "stocks you follow" — the second reads as an
        # instruction to follow the investor, which is exactly the framing to avoid on a
        # surface FINRA/SEC treat as a supervised digital-engagement practice.
        f"Disclosed activity totalling {amount_label} on your watchlist.",
    )


async def _run_whale_phase(now: datetime, cursor: Optional[datetime]) -> Tuple[int, Optional[datetime]]:
    """13F + congressional pass. Returns (delivered, new cursor).

    Reads Supabase only — `whale_trades` is hydrated by `_run_whale_hydration_job`.
    """
    supabase = get_supabase()

    # No baseline (first ever run, or a cursor read failure) → a conservative 24h window
    # rather than the whole table. Notifying on every historical row once is exactly the
    # failure the backfill guard exists to prevent, and it would happen on the very first
    # deploy of this job.
    since = cursor or (now - timedelta(days=1))
    cutoff_date = (now.astimezone(ET).date() - timedelta(days=WHALE_TRADE_MAX_AGE_DAYS)).isoformat()

    def _query() -> List[Dict[str, Any]]:
        try:
            return (
                supabase.table("whale_trades")
                .select(
                    "ticker, company_name, action, amount, amount_range, date, "
                    "created_at, whale_id, whales(name, firm_name, data_source)"
                )
                .gt("created_at", since.isoformat())
                .order("created_at", desc=True)
                .limit(1000)
                .execute()
                .data
                or []
            )
        except Exception as e:
            logger.warning(
                "smart money: whale_trades read failed (%s: %s) — skipping the whale "
                "phase this run",
                type(e).__name__, e,
            )
            return []

    raw = await asyncio.to_thread(_query)
    rows = _recent_whale_rows(raw, cutoff_date=cutoff_date)
    if not rows:
        return 0, _max_created_at(raw, since)

    # Roll up per (whale, direction). One filing = one notification.
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        whale_id = row.get("whale_id")
        action = str(row.get("action") or "").strip().lower()
        ticker = str(row.get("ticker") or "").strip().upper()
        if not whale_id or not ticker or action not in ("bought", "sold"):
            continue
        whale = row.get("whales") if isinstance(row.get("whales"), dict) else {}
        kind = _whale_kind(whale.get("data_source"))
        if kind is None:
            logger.warning(
                "smart money: whale %s has unknown data_source %r — skipping (never "
                "defaulted, since 13F ships OFF and congress ships ON)",
                whale_id, whale.get("data_source"),
            )
            continue

        key = (str(whale_id), action)
        group = groups.setdefault(key, {
            "kind": kind,
            "name": (whale.get("firm_name") or whale.get("name") or "A tracked investor").strip(),
            "tickers": [],
            "low": 0.0,
            "high": 0.0,
            "open_ended": False,
            "is_congress": False,
            "latest_date": "",
        })
        if ticker not in group["tickers"]:
            group["tickers"].append(ticker)
        group["latest_date"] = max(group["latest_date"], str(row.get("date") or "")[:10])

        amount_range = row.get("amount_range")
        if amount_range:
            # Congressional: an honest STOCK Act RANGE. Never collapse this into a
            # precise figure — the disclosure genuinely does not contain one.
            group["is_congress"] = True
            low, high = parse_congress_amount_bounds(amount_range)
            group["low"] += finite(low) or 0.0
            if high is None:
                group["open_ended"] = True
            else:
                group["high"] += finite(high) or 0.0
        else:
            # 13F: an exact point. Summing it as low == high keeps the range machinery
            # honest when a group somehow mixes both.
            amount = finite(row.get("amount")) or 0.0
            group["low"] += amount
            group["high"] += amount

    ordered = sorted(groups.items(), key=lambda kv: kv[1]["low"], reverse=True)

    sent = 0
    for (whale_id, action), group in ordered[:MAX_EVENTS_PER_PHASE]:
        label = format_amount_range(
            group["low"], None if group["open_ended"] else max(group["high"], group["low"])
        )
        title, body = whale_copy(group["name"], action, group["tickers"], label)
        route_ticker = group["tickers"][0]
        # Audience is the UNION of two selectors: people watching any of the tickers,
        # and people following this whale. De-duplicated in `notify_users`, so a user in
        # both gets exactly one notification.
        dispatcher = get_push_dispatch_service()
        audience: List[str] = []
        for ticker in group["tickers"][:5]:
            audience.extend(await asyncio.to_thread(dispatcher.watchers_of, ticker))
        audience.extend(await asyncio.to_thread(dispatcher.followers_of_whale, whale_id))
        if not audience:
            continue

        sent += await dispatcher.notify_users(
            audience,
            kind=group["kind"],
            title=title,
            body=body,
            dedup_key=f"whale:{whale_id}:{action}:{group['latest_date'] or 'nodate'}",
            # `whale_id` rides along so the client can offer the investor's profile, not
            # just the ticker. Half this audience follows the WHALE rather than the ticker.
            route=ticker_route(group["kind"], route_ticker, whale_id=whale_id),
        )

    return sent, _max_created_at(raw, since)


def _max_created_at(rows: Any, fallback: datetime) -> datetime:
    """High-water mark from the RAW rows, not the filtered ones.

    Deliberately advanced past rows the backfill guard rejected: they were evaluated and
    correctly declined, so re-reading them tomorrow is pure waste. Advancing only past
    ACCEPTED rows would make a quiet week re-scan the same backfill forever.
    """
    high = fallback
    for row in rows if isinstance(rows, list) else []:
        raw = str((row or {}).get("created_at") or "").replace("Z", "+00:00")
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        high = max(high, stamp)
    return high


# ── entry point ──────────────────────────────────────────────────────────────


async def run_smart_money_notifications(now: Optional[datetime] = None) -> Dict[str, int]:
    """One claimed pass over both phases.

    Each phase handles its OWN recoverable failures internally (a per-ticker FMP error, a
    Supabase read blip) and degrades to zero rather than aborting the other — the insider
    pass costs ~200 FMP calls and the whale pass costs none, so letting one kill the other
    would waste the expensive half over a transient read.

    An UNRECOVERABLE failure propagates, deliberately: `claimed_job`'s shielded `finally`
    releases with success=False, leaving `run_day` unset so the next hourly wake retries
    the same ET day, and the lifespan loop logs it. Swallowing it would look like a
    successful run of zero and skip the day in silence.
    """
    stats = {"insider": 0, "whale": 0}
    now = now or datetime.now(timezone.utc)

    async with claimed_job(JOB_SMART_MONEY) as run:
        if run is None:
            logger.debug("smart money: not claimed (already run today, or held)")
            return stats

        cursor = await asyncio.to_thread(last_cursor, JOB_SMART_MONEY)

        stats["insider"] = await _run_insider_phase(now)
        stats["whale"], new_cursor = await _run_whale_phase(now, cursor)

        run.notified = stats["insider"] + stats["whale"]
        run.cursor = new_cursor
        run.success = True

    logger.info(
        "smart money notifications: %d insider + %d whale/congress send(s)",
        stats["insider"], stats["whale"],
    )
    return stats
