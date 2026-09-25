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
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
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
    get_kind,
    ticker_route,
)
from app.services.push_dispatch_service import (
    MAX_RECIPIENTS_PER_SCOPE,
    get_push_dispatch_service,
)
from app.services.updates_materiality import finite
from app.utils.market_hours import ET
from app.utils.postgrest_paging import PAGE_SIZE, fetch_all_rows

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

# Disclosure floor for CONGRESSIONAL rows, measured on `disclosure_date` (the day the
# PTR became public), falling back to the transaction `date` for rows that predate
# migration 076. 13F rows are NOT measured against this: their `date` is a QUARTER END,
# gated by quarter instead — see `_thirteen_f_floor` and `_recent_whale_rows`.
WHALE_TRADE_MAX_AGE_DAYS = 45
# Pages of 1,000 `whale_trades` rows one run will evaluate. The read is in `created_at`
# order (id as the tiebreak), so a run that fills every page has read the OLDEST rows
# since its cursor and advances the cursor to just below the last stamp it read — the
# remainder is genuinely evaluated next run (a 13F deadline day writes ~1,500 rows; ten
# pages is far above it). See `_capped_cursor` for why the cursor must move at all.
WHALE_PHASE_MAX_PAGES = 10

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


def equity_tickers(symbols) -> List[str]:
    """Form 4 filings exist for companies only. The watchlist universe now carries indices,
    coins and commodity pairs; each cost one real (entitled) `insider-trading/search` call
    per run for an empty answer."""
    from app.services.asset_class import detect_asset_class

    return [s for s in dict.fromkeys(symbols) if s and detect_asset_class(s) == "stock"]


async def _run_insider_phase(now: datetime) -> int:
    """Form 4 pass. Returns notifications delivered."""
    supabase = get_supabase()

    def _universe() -> List[str]:
        try:
            rows = supabase.rpc(
                "get_top_watchlist_tickers", {"n": INSIDER_UNIVERSE}
            ).execute().data or []
            return equity_tickers(str(r["ticker"]).upper() for r in rows if r.get("ticker"))
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


def _thirteen_f_floor(run_day: date) -> str:
    """First day of the calendar quarter BEFORE the one `run_day` falls in (ISO).

    A 13F row's `date` is the QUARTER END its filing describes — the hydrators write the
    `date` of FMP's `institutional-ownership/dates` entry — never the day it was filed.
    SEC Rule 13f-1 allows 45 days after the quarter closes, so the newest quarter a filing
    made during quarter Q can describe is Q-1. A row dated on or after Q-1's FIRST day
    therefore belongs to the latest filed quarter (or an early filing of a newer one);
    anything older is a previous quarter, which only a first hydration writes.

    The first DAY, not Q-1's end: the hydrators' fallback builds `{year}-{q*3:02d}-30`,
    which is 03-30 / 12-30 for Q1 / Q4 — a day before the true end — and an end-date floor
    would drop exactly those rows. `tracking_service._thirteen_f_floor` must agree with
    this one (`tests/test_whale_13f_quarter_floor.py`).
    """
    q0 = (run_day.month - 1) // 3                        # 0-based current quarter
    year, prev = (run_day.year, q0 - 1) if q0 else (run_day.year - 1, 3)
    return date(year, prev * 3 + 1, 1).isoformat()


def _is_congress_row(row: Dict[str, Any]) -> bool:
    """A STOCK Act row: it carries a range or a disclosure date, or its whale is congressional.

    13F rows carry neither column (both hydrators write None), so the source is the
    tiebreak only for a congressional row written before migration 076.
    """
    if row.get("amount_range") or row.get("disclosure_date"):
        return True
    whale = row.get("whales") if isinstance(row.get("whales"), dict) else {}
    return _whale_kind(whale.get("data_source")) == KIND_CONGRESS_TRADE


def _recent_whale_rows(rows: Any, *, cutoff_date: str) -> List[Dict[str, Any]]:
    """Filter freshly-ingested whale trades down to genuinely recent ones.

    `cutoff_date` is the run's ET date minus `WHALE_TRADE_MAX_AGE_DAYS`; the 13F quarter
    floor is derived from the same run date, so the one argument carries both.

    ⚠️ THE BACKFILL TRAP, and this repo has shipped it once already.

    `created_at` is when WE ingested a row; `date` is when the trade happened. The first
    hydration of a newly-added whale inserts hundreds of quarter-old filings with a
    brand-new `created_at`. Windowing on `created_at` alone would announce a fund's
    entire historical book as "this week's activity" — one notification per position.

    ⚠️ AND THE TWO ROW TYPES DATE DIFFERENTLY — one floor for both dropped real filings:

      * **13F** — `date` is the QUARTER END, and the filing deadline is quarter end + 45
        days. A 45-day floor on it therefore EQUALLED the deadline: a fund filing on
        deadline day (large filers routinely do) is hydrated that night and read the next
        evening, when every one of its rows sat one day under the floor. The cursor then
        moved past them for good. Gated by quarter instead (`_thirteen_f_floor`).
      * **Congress** — `date` is the TRANSACTION date; the news is the DISCLOSURE. A PTR
        filed at the 45-day legal limit (or late, which is common) was dropped on its
        transaction date while the Home congress card showed it. Gated on
        `disclosure_date`, falling back to `date` for rows written before migration 076.

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
    try:
        quarter_floor = _thirteen_f_floor(
            date.fromisoformat(str(cutoff_date)[:10]) + timedelta(days=WHALE_TRADE_MAX_AGE_DAYS)
        )
    except (TypeError, ValueError) as e:
        # Internal input, so this is a bug, not data. Fall back to the strictest floor
        # (the old behaviour) rather than to none, and say so.
        logger.warning(
            "smart money: unusable whale cutoff %r (%s: %s) — gating 13F rows on it too",
            cutoff_date, type(e).__name__, e,
        )
        quarter_floor = str(cutoff_date)
    keep: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _is_congress_row(row):
            when = str(row.get("disclosure_date") or row.get("date") or "")[:10]
            floor = cutoff_date
        else:
            when = str(row.get("date") or "")[:10]
            floor = quarter_floor
        if when and when < floor:
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
    whale_name: str,
    action: str,
    tickers: List[str],
    amount_label: str,
    *,
    on_watchlist: bool = True,
) -> Tuple[str, str]:
    """Rolled-up copy for one whale's activity, worded for ONE reader variant.

    A 40-position 13F becomes ONE notification naming up to three tickers, not forty.
    The per-user `smart_money` cap of 3 is the backstop, not the design.

    `tickers` and `amount_label` must describe what THIS reader is told about: for a
    watcher, only the tickers on their watchlist and the amount over only those (see
    `_run_whale_phase`); for a follower who watches none of them, the whole filing, and
    `on_watchlist=False` — "on your watchlist" would be false for them.
    """
    verb = "bought" if action == "bought" else "sold"
    shown = ", ".join(tickers[:3])
    more = f" +{len(tickers) - 3} more" if len(tickers) > 3 else ""
    if on_watchlist:
        # "on your watchlist", NOT "stocks you follow" — the second reads as an
        # instruction to follow the investor, which is exactly the framing to avoid on a
        # surface FINRA/SEC treat as a supervised digital-engagement practice.
        body = f"Disclosed activity totalling {amount_label} on your watchlist."
    else:
        body = f"New disclosed activity totalling {amount_label}."
    return f"{whale_name} {verb} {shown}{more}", body


def _ranked_tickers(per_ticker: Dict[str, Dict[str, Any]]) -> List[str]:
    """A roll-up's tickers, largest first (by the low bound), then by symbol.

    Deterministic on purpose: the read order is `created_at, id`, and every row of one
    bulk upsert shares `created_at` while `id` is a random uuid — so "the first three
    tickers" used to be an arbitrary pick that could change between identical re-reads.
    """
    return sorted(per_ticker, key=lambda t: (-per_ticker[t]["low"], t))


def _amount_label(per_ticker: Dict[str, Dict[str, Any]], tickers: List[str]) -> str:
    """The summed range over `tickers` ONLY — never the whole filing's total."""
    low = sum(per_ticker[t]["low"] for t in tickers)
    high = sum(per_ticker[t]["high"] for t in tickers)
    open_ended = any(per_ticker[t]["open_ended"] for t in tickers)
    return format_amount_range(low, None if open_ended else max(high, low))


def _whale_audience_variants(
    ranked: List[str], watched: Dict[str, List[str]], followers: List[str]
) -> List[Tuple[Tuple[str, ...], List[str]]]:
    """Split one roll-up's audience into copy variants: `[(subset, users), …]`.

    `subset` is the tickers those users watch, in `ranked` order; `()` is the
    follower-only variant (follows the whale, watches none of its tickers). Every user
    lands in exactly ONE variant, so nobody gets two alerts for one filing.
    """
    in_group = set(ranked)
    by_subset: Dict[Tuple[str, ...], List[str]] = {}
    for uid, symbols in (watched or {}).items():
        mine = {str(t).upper() for t in (symbols or [])} & in_group
        if not uid or not mine:
            continue
        subset = tuple(t for t in ranked if t in mine)
        by_subset.setdefault(subset, []).append(uid)
    placed = {u for users in by_subset.values() for u in users}
    variants = [
        (subset, sorted(users))
        for subset, users in sorted(by_subset.items(), key=lambda kv: [ranked.index(t) for t in kv[0]])
    ]
    follower_only = sorted(u for u in dict.fromkeys(followers or []) if u and u not in placed)
    if follower_only:
        variants.append(((), follower_only))
    return variants


def whale_dedup_key(whale_id: Any, action: str, latest_date: str, tickers: List[str]) -> str:
    """Dedup key for one (whale, direction) roll-up.

    Carries a digest of the TICKER SET. The key used to be `whale:{id}:{action}:{date}`
    alone, so a second filing whose newest trade shares a date with an earlier one (a
    senator's second PTR: NVDA on 09-12 notified Monday, AAPL on 09-12 filed Wednesday)
    collided and was dropped for every user. Trade-off: a retry after a PARTIAL failure,
    in which new rows joined the group, re-notifies once with the larger set — bounded by
    the per-user `smart_money` cap.
    """
    digest = hashlib.sha1(",".join(sorted(set(tickers))).encode()).hexdigest()[:10]
    return f"whale:{whale_id}:{action}:{latest_date or 'nodate'}:{digest}"


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
            # PAGED, IN TIME ORDER. The old `.order(desc).limit(1000)` read the NEWEST
            # 1,000 rows since the cursor and then advanced the cursor to the newest
            # stamp — so on a 13F deadline day (~1,500 rows from one hydration) the ~500
            # written first were never evaluated and never re-read.
            #
            # ⚠️ The first paged rewrite ordered on `id` ALONE and, on a capped read, held
            # the cursor "so the remainder is evaluated next run". `whale_trades.id` is
            # `gen_random_uuid()` — a random order unrelated to time — so the next run
            # issued the identical query from the identical `since`, got the identical
            # first 10,000 rows, and the rows beyond the cap were never reached; nothing
            # deletes `whale_trades`, so once >10,000 rows sat past the cursor (a held job
            # across a 13F season, a registry backfill) whale/congress notifications
            # stopped for good while the log promised otherwise.
            #
            # `created_at` first, so the cap falls on the OLDEST unread rows and the
            # cursor can advance past them; `id` as the tiebreak, because `created_at`
            # is `DEFAULT now()` — per-TRANSACTION — and every row of one bulk upsert
            # shares a stamp, on which OFFSET paging alone can skip/duplicate a boundary
            # row. postgrest-py `.order()` APPENDS (verified: `order=created_at.asc,id.asc`),
            # so the primary key here plus `fetch_all_rows`'s `order_by="id"` is the
            # two-column ORDER BY; `idx_whale_trades_created_at` supports it.
            return fetch_all_rows(
                lambda: supabase.table("whale_trades")
                .select(
                    "id, ticker, company_name, action, amount, amount_range, date, "
                    "disclosure_date, created_at, whale_id, "
                    "whales(name, firm_name, data_source)"
                )
                .gt("created_at", since.isoformat())
                .order("created_at"),
                order_by="id",
                what="smart money: whale_trades since cursor",
                max_pages=WHALE_PHASE_MAX_PAGES,
            )
        except Exception as e:
            logger.warning(
                "smart money: whale_trades read failed (%s: %s) — skipping the whale "
                "phase this run",
                type(e).__name__, e,
            )
            return []

    raw = await asyncio.to_thread(_query)
    # A read that filled every page may have rows beyond it. The read is in `created_at`
    # order, so what arrived is the OLDEST rows since the cursor, and the cursor moves to
    # just below the last stamp read (`_capped_cursor`) — the boundary tie group is
    # re-read next run (the dedup claim absorbs that), everything after it is reached for
    # the first time. Holding the cursor here was the bug: see the comment in `_query`.
    capped = len(raw) >= WHALE_PHASE_MAX_PAGES * PAGE_SIZE
    if capped:
        next_cursor = _capped_cursor(raw, since)
        logger.error(
            "smart money: whale_trades read hit the %d-row cap since %s — cursor "
            "advanced to %s; the remainder is evaluated next run%s",
            WHALE_PHASE_MAX_PAGES * PAGE_SIZE, since.isoformat(), next_cursor.isoformat(),
            "" if next_cursor > since else
            " — ⚠️ it could NOT advance (every row read shares one created_at), so the "
            "same rows will be re-read until the cap is raised",
        )
    else:
        next_cursor = _max_created_at(raw, since)
    rows = _recent_whale_rows(raw, cutoff_date=cutoff_date)
    if not rows:
        return 0, next_cursor

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
            # ticker → its OWN summed bounds, so each reader is told the amount over the
            # tickers THEY watch rather than the whole filing's total.
            "tickers": {},
            "low": 0.0,          # the whole roll-up's low bound — event ordering only
            "latest_date": "",
        })
        bounds = group["tickers"].setdefault(
            ticker, {"low": 0.0, "high": 0.0, "open_ended": False}
        )
        group["latest_date"] = max(group["latest_date"], str(row.get("date") or "")[:10])

        amount_range = row.get("amount_range")
        if amount_range:
            # Congressional: an honest STOCK Act RANGE. Never collapse this into a
            # precise figure — the disclosure genuinely does not contain one.
            low, high = parse_congress_amount_bounds(amount_range)
            low = finite(low) or 0.0
            bounds["low"] += low
            group["low"] += low
            if high is None:
                bounds["open_ended"] = True
            else:
                bounds["high"] += finite(high) or 0.0
        else:
            # 13F: an exact point. Summing it as low == high keeps the range machinery
            # honest when a group somehow mixes both.
            amount = finite(row.get("amount")) or 0.0
            bounds["low"] += amount
            bounds["high"] += amount
            group["low"] += amount

    ordered = sorted(groups.items(), key=lambda kv: kv[1]["low"], reverse=True)

    dispatcher = get_push_dispatch_service()
    sent = 0
    for (whale_id, action), group in ordered[:MAX_EVENTS_PER_PHASE]:
        per_ticker = group["tickers"]
        ranked = _ranked_tickers(per_ticker)
        # Audience is the UNION of two selectors: people watching ANY of the roll-up's
        # tickers — ALL of them, in one paged read; this used to take the first five in
        # read order, so a watcher of ticker #6 of a 12-ticker PTR was never told — and
        # people following this whale.
        watched = await asyncio.to_thread(dispatcher.watchers_of_any, ranked)
        followers = await asyncio.to_thread(dispatcher.followers_of_whale, whale_id)
        audience = list(dict.fromkeys([*watched, *followers]))
        if not audience:
            continue
        if len(audience) > MAX_RECIPIENTS_PER_SCOPE:
            # The per-scope ceiling applies to the EVENT, as it did when this was one
            # `notify_users` call — splitting it into copy variants must not multiply it.
            # Same preference-first, rotating cut `notify_users` applies to one call.
            keep = set(await asyncio.to_thread(
                dispatcher._cap_after_preferences,
                audience, get_kind(group["kind"]),
                whale_dedup_key(whale_id, action, group["latest_date"], ranked), now,
            ))
            watched = {u: t for u, t in watched.items() if u in keep}
            followers = [u for u in followers if u in keep]

        # One notification per reader, worded for THAT reader: a watcher is told about
        # the tickers on their watchlist and the amount over only those, and routed to
        # the largest of them; a follower who watches none of them gets the whole filing
        # WITHOUT "on your watchlist", which would be false for them. Each variant has
        # its own key (the reader's ticker set), so a retry of the same data re-derives
        # the same keys and the claim absorbs it.
        for subset, users in _whale_audience_variants(ranked, watched, followers):
            shown = list(subset) or ranked
            title, body = whale_copy(
                group["name"], action, shown, _amount_label(per_ticker, shown),
                on_watchlist=bool(subset),
            )
            sent += await dispatcher.notify_users(
                users,
                kind=group["kind"],
                title=title,
                body=body,
                dedup_key=whale_dedup_key(whale_id, action, group["latest_date"], shown),
                # `whale_id` rides along so the client can offer the investor's profile,
                # not just the ticker. Part of this audience follows the WHALE rather
                # than the ticker.
                route=ticker_route(group["kind"], shown[0], whale_id=whale_id),
            )

    return sent, next_cursor


def _created_at_stamps(rows: Any) -> List[datetime]:
    """Every parseable `created_at` in `rows`, tz-aware (naive → UTC). Unparseable and
    missing stamps are skipped, never guessed."""
    out: List[datetime] = []
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
        out.append(stamp)
    return out


def _max_created_at(rows: Any, fallback: datetime) -> datetime:
    """High-water mark from the RAW rows, not the filtered ones.

    Deliberately advanced past rows the backfill guard rejected: they were evaluated and
    correctly declined, so re-reading them tomorrow is pure waste. Advancing only past
    ACCEPTED rows would make a quiet week re-scan the same backfill forever.
    """
    return max([fallback, *_created_at_stamps(rows)])


def _capped_cursor(rows: Any, fallback: datetime) -> datetime:
    """Where to resume after a read that filled every page.

    The rows are the OLDEST `WHALE_PHASE_MAX_PAGES × PAGE_SIZE` since `fallback`, in
    `created_at` order, and rows beyond the cap may share the LAST stamp read: `created_at`
    is `DEFAULT now()`, i.e. per-transaction, so every row of one bulk upsert (≤50 in
    `hydrate_whales`, ≤600 in `whale_service._bulk_write_trades`) carries one value. The
    read filter is `gt`, so advancing to the last stamp itself would skip the unread tail
    of that tie group FOREVER. Advance to the highest stamp STRICTLY BELOW it instead: the
    whole boundary group is re-read next run (the dedup claim makes that harmless) and
    nothing after it is skipped.

    Falls back to `fallback` — no progress — only when every row read shares one stamp,
    which no bulk writer here can produce; the caller logs that case by name. Never moves
    the cursor backwards.
    """
    stamps = _created_at_stamps(rows)
    if not stamps:
        return fallback
    last = max(stamps)
    below = [s for s in stamps if s < last]
    if not below:
        return fallback
    return max(fallback, max(below))


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
