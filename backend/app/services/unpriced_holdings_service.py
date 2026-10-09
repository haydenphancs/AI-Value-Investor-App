"""Tell the owner when FMP stops pricing a symbol users hold: a move, a delisting, a rename.

Why it exists (2026-10-08): Energy Transfer (ET), Dillard's (DDS) and USA Compression (USAC)
moved from NYSE to the Texas Stock Exchange on 2026-10-05. FMP has no TXSE prices, so for
users holding them every price froze at the 10-05 close, and nothing in the app said so; it
was found by hand three sessions later. More companies are moving (TCBI, OBK), and a plain
delisting looks the same.

A symbol is a candidate when ALL of these hold:
  held      — on a watchlist, in a portfolio, or under an active price alert;
  unpriced  — no traded close in the latest US session (`session_pricing`);
  inactive  — off FMP's actively-trading list, read only from a FRESH copy
              (`stock_search_service.get_fresh_active_listings`).
The third keeps an illiquid OTC name that merely did not trade out of the report.

Its last stored close (`market_close_snapshot.trade_date`) then decides:
  • the latest session itself: it printed a close without trading — not a stop, skipped;
  • the session before: it missed ONE session — not yet, an illiquid skip looks the same;
  • `_MIN_MISSED_SESSIONS` (2) to `_NEWLY_WINDOW_SESSIONS` (6) sessions back, and not yet
    reported by this process: an ERROR (Sentry and the error digest), unless FMP's profile
    still says it is actively trading. The window, not "exactly one session back", is what
    survives FMP's list dropping the symbol a session late, and a restart. The ERROR names
    the profile's exchange and flag, plus any other symbol under the same SEC CIK that DID
    trade: that is how a renamed ticker shows up;
  • anything older, or no stored close: one WARNING line per session.
Nothing is written: what to do with users' rows for a moved or renamed symbol is the
owner's decision (documents/OWNER_TASKS.md).

Runs from `_run_close_snapshot_loop` after each cycle, at most once per new session per
process, and only once that session's snapshot write has landed (the bellwether's stored
close carries the session's date). Every read failure DEFERS the run to the next hourly
cycle without spending the session. A restart may repeat an ERROR once inside the window.
Read-only on Supabase; on FMP, one profile + one CIK lookup per ERROR candidate, capped at
`_MAX_LOOKUPS`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Set

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.integrations.fmp_entitlements import is_blocked_symbol
from app.services.asset_class import uses_coingecko_price
from app.services.session_pricing import latest_priced_session
from app.utils.market_hours import previous_trading_day
from app.utils.postgrest_paging import fetch_all_rows

logger = logging.getLogger(__name__)

# Per session, FMP calls for ERROR candidates: a move is a handful of names, and a mass
# event (a feed outage that slipped past the registry floor) must not fan out.
_MAX_LOOKUPS = 10
_MAX_LISTED = 25  # symbols named in one log line
_LOOKUP_TIMEOUT = 15.0
_MIN_MISSED_SESSIONS = 2
_NEWLY_WINDOW_SESSIONS = 6
# Its stored close dates the snapshot write: `_US_SESSION_BELLWETHERS[0]` in price_service.
_BELLWETHER = "AAPL"

_reported_session: Optional[date] = None
# Symbols this process has reported at ERROR and that are still unpriced.
_errored: Set[str] = set()


def _reportable(sym: str) -> bool:
    """US equities and ETFs only: crypto is priced by CoinGecko, blocked symbols are
    outside the licence, and a dotted foreign listing is absent from the active list by
    construction (the list is held dot-free), so it would always read as inactive."""
    return bool(sym) and "." not in sym and not is_blocked_symbol(sym) and not uses_coingecko_price(sym)


def _held_symbols() -> Dict[str, Dict[str, int]]:
    """{SYMBOL: {table: rows}} across every place a user can hold a symbol: watchlists,
    portfolios, ACTIVE price alerts. Synchronous (Supabase SDK). Each read is spelled out
    with its literal table name and paged on its primary key `id`
    (`tests/test_postgrest_paging_order_key.py` reads both from source)."""
    sb = get_supabase()
    reads = {
        "watchlist_items": fetch_all_rows(
            lambda: sb.table("watchlist_items").select("id,ticker"),
            order_by="id", what="unpriced holdings: watchlist_items",
        ),
        "portfolio_items": fetch_all_rows(
            lambda: sb.table("portfolio_items").select("id,ticker"),
            order_by="id", what="unpriced holdings: portfolio_items",
        ),
        "price_alerts": fetch_all_rows(
            lambda: sb.table("price_alerts").select("id,ticker").eq("is_active", True),
            order_by="id", what="unpriced holdings: price_alerts",
        ),
    }
    held: Dict[str, Dict[str, int]] = {}
    for table, rows in reads.items():
        for row in rows:
            ticker = row.get("ticker") if isinstance(row, dict) else None
            if not isinstance(ticker, str) or not ticker.strip():
                continue
            per = held.setdefault(ticker.strip().upper(), {})
            per[table] = per.get(table, 0) + 1
    return held


def _stored_closes(symbols: List[str]) -> Dict[str, Optional[date]]:
    """{SYMBOL: stored trade_date} from `market_close_snapshot`. RAISES on a failed read:
    `PriceService.get_close_snapshots` folds a failure into `{}`, which would read as
    "no stored close" for every symbol and demote a real stop to a WARNING."""
    from app.services.price_service import PriceService

    out: Dict[str, Optional[date]] = {}
    for row in PriceService._select_closes(symbols):
        sym = str(row.get("symbol") or "").strip().upper()
        if sym:
            out[sym] = _as_date(row.get("trade_date"))
    return out


def _as_date(raw: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def _sessions_back(session: date, n: int) -> date:
    d = session
    for _ in range(n):
        d = previous_trading_day(d)
    return d


async def _lookup(sym: str, priced: Mapping[str, Any]) -> Dict[str, Any]:
    """The profile's exchange / flag and same-CIK symbols that traded. Never raises."""
    fmp = get_fmp_client()
    out: Dict[str, Any] = {}
    try:
        profile = await asyncio.wait_for(fmp.get_company_profile(sym), _LOOKUP_TIMEOUT)
    except Exception as e:
        out["lookup_error"] = f"profile {type(e).__name__}"
        return out
    profile = profile if isinstance(profile, dict) else {}
    out["exchange"] = profile.get("exchange")
    out["actively_trading"] = profile.get("isActivelyTrading")
    cik = profile.get("cik")
    if not isinstance(cik, str) or not cik.strip():
        return out
    try:
        # `search-cik` is entitled ("Search & Directory") and FMPClient has no wrapper
        # for it; the same private-call precedent as ticker_report_data_collector.
        rows = await asyncio.wait_for(
            fmp._make_request("search-cik", params={"cik": cik.strip()}), _LOOKUP_TIMEOUT,
        )
    except Exception as e:
        out["lookup_error"] = f"search-cik {type(e).__name__}"
        return out
    siblings = sorted({
        str(r.get("symbol")).strip().upper()
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and isinstance(r.get("symbol"), str)
    } - {sym})
    out["same_cik_trading"] = [s for s in siblings if s in priced][:5]
    return out


def _describe(sym: str, holdings: Dict[str, int], since: Optional[date], info: Dict[str, Any]) -> str:
    rows = ", ".join(f"{t} {n}" for t, n in sorted(holdings.items()))
    parts = [f"{sym} ({rows}"]
    parts.append(f"; last close {since}" if since is not None else "; no stored close")
    if "exchange" in info:
        parts.append(f"; profile exchange {info.get('exchange')}, "
                     f"isActivelyTrading {info.get('actively_trading')}")
    if info.get("same_cik_trading"):
        parts.append(f"; same CIK now trading: {', '.join(info['same_cik_trading'])}")
    if info.get("lookup_error"):
        parts.append(f"; lookup failed: {info['lookup_error']}")
    if info.get("note"):
        parts.append(f"; {info['note']}")
    return "".join(parts) + ")"


def _listed(items: List[str], render) -> str:
    text = "; ".join(render(s) for s in items[:_MAX_LISTED])
    return text + (f"; +{len(items) - _MAX_LISTED} more" if len(items) > _MAX_LISTED else "")


async def report_unpriced_holdings(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Log held symbols FMP stopped pricing. Returns the report (for tests), or None when
    it did not run: no current registry, the session was already reported, or a read is
    not ready / failed (the active list, the holdings, the stored closes) — all of which
    leave the session unspent for the next hourly cycle."""
    global _reported_session
    held_session = latest_priced_session(now)
    if held_session is None:
        return None
    session, priced = held_session
    if _reported_session == session:
        return None

    # Imported here, not at module level: stock_search_service pulls in the search schemas.
    from app.services.stock_search_service import get_fresh_active_listings
    directory = get_fresh_active_listings()
    if directory is None:
        logger.info("unpriced holdings: no fresh active-listing directory yet — next cycle")
        return None

    try:
        held = await asyncio.to_thread(_held_symbols)
    except Exception as e:
        logger.warning(
            "unpriced holdings: holdings read failed (%s: %s) — next cycle",
            type(e).__name__, e, exc_info=True,
        )
        return None

    unpriced = {
        sym: rows for sym, rows in held.items()
        if _reportable(sym) and sym not in priced and sym not in directory
    }
    report: Dict[str, Any] = {"session": session, "held": len(held), "newly": {}, "still": {}, "pending": []}
    if not unpriced:
        _errored.clear()
        _reported_session = session
        logger.info("unpriced holdings: none in session %s (%d held symbols)", session, len(held))
        return report

    try:
        stored = await asyncio.to_thread(_stored_closes, sorted(set(unpriced) | {_BELLWETHER}))
    except Exception as e:
        logger.warning(
            "unpriced holdings: stored-close read failed (%s: %s) — next cycle",
            type(e).__name__, e, exc_info=True,
        )
        return None
    if stored.get(_BELLWETHER) != session:
        logger.info(
            "unpriced holdings: the snapshot write for %s has not landed (%s's stored close: "
            "%s) — next cycle", session, _BELLWETHER, stored.get(_BELLWETHER),
        )
        return None

    _errored.intersection_update(unpriced)  # a symbol that traded again may ERROR again later
    one_back = _sessions_back(session, 1)
    newest_err = _sessions_back(session, _MIN_MISSED_SESSIONS)
    oldest_err = _sessions_back(session, _NEWLY_WINDOW_SESSIONS)
    candidates: List[str] = []
    still: List[str] = []
    for sym in sorted(unpriced, key=lambda s: (-sum(unpriced[s].values()), s)):
        last = stored.get(sym)
        if last is not None and last >= session:
            continue                      # printed a close this session without trading
        if last is not None and last >= one_back:
            report["pending"].append(sym)  # one missed session: an illiquid skip looks the same
        elif last is not None and oldest_err <= last <= newest_err and sym not in _errored:
            candidates.append(sym)
        else:
            still.append(sym)

    looked_up = candidates[:_MAX_LOOKUPS]
    lookups = await asyncio.gather(*(_lookup(s, priced) for s in looked_up), return_exceptions=True)
    info: Dict[str, Dict[str, Any]] = {
        s: (r if isinstance(r, dict) else {"lookup_error": type(r).__name__})
        for s, r in zip(looked_up, lookups)
    }
    newly: List[str] = []
    for sym in candidates:
        details = info.setdefault(sym, {})
        if sym not in looked_up:
            details["note"] = "not looked up (lookup cap)"
        if details.get("actively_trading") is True:
            # FMP's own profile disagrees; one missing batch-eod row is not proof enough.
            details["note"] = "profile still says actively trading"
            still.append(sym)
        else:
            newly.append(sym)

    for sym in newly:
        report["newly"][sym] = {"holdings": unpriced[sym], "since": stored.get(sym), **info.get(sym, {})}
    for sym in still:
        report["still"][sym] = {"holdings": unpriced[sym], "since": stored.get(sym), **info.get(sym, {})}

    if newly:
        logger.error(
            "unpriced holdings: %d held symbol(s) stopped trading in FMP's data as of session %s "
            "(no traded close for %d+ sessions, off the active list) — a move, delisting or "
            "rename; their prices are frozen for users: %s",
            len(newly), session, _MIN_MISSED_SESSIONS,
            _listed(newly, lambda s: _describe(s, unpriced[s], stored.get(s), info.get(s, {}))),
        )
        _errored.update(newly)
    if still:
        logger.warning(
            "unpriced holdings: %d held symbol(s) still unpriced as of session %s: %s",
            len(still), session,
            _listed(still, lambda s: _describe(s, unpriced[s], stored.get(s), info.get(s, {}))),
        )
    if report["pending"]:
        logger.info(
            "unpriced holdings: %d held symbol(s) missed only session %s (not reported yet): %s",
            len(report["pending"]), session, ", ".join(report["pending"][:_MAX_LISTED]),
        )
    _reported_session = session
    return report


def _reset_for_tests() -> None:
    global _reported_session
    _reported_session = None
    _errored.clear()
