#!/usr/bin/env python3
"""
Hedge-Fund Flow Hydration Engine
================================
Bulk pre-computes the quarterly 13F net-share flow into the existing
``hedge_fund_quarters`` Supabase table for ~thousands of popular tickers, so
the holders / Smart-Money "Institutions" chart serves instantly without each
report paying the cold-build FMP cost.

FMP-only feature — no Gemini / AI cost.

Correctness equals the live reader because this script REUSES
``HoldersService`` verbatim:
  * static math   : ``_generate_quarter_keys`` / ``_compute_quarter_flow``
                    (net = numberOf13FsharesChange / 1e6; buy/sell estimated)
  * DB helpers     : ``_load_existing_quarters`` (resume) + ``_save_quarters``
  * floor constant : ``_HFQ_SHARES_FLOOR`` and ``_REFRESH_RECENT_QUARTERS``

Settled quarters are immutable: once stored above the floor they are NEVER
re-fetched. Only the most-recent ``--refresh-recent`` (default 2) "volatile"
quarters are refetched, because 13F filings keep getting amended for months
after quarter-end. A resume run (``--skip-fresh``, default on) skips any ticker
already fully fresh → 0 FMP calls, so a Railway restart picks up where it left
off. Persistence is AWAITED per ticker, so a kill loses at most the in-flight
ticker.

Usage:
    cd backend

    # 1) DRY-RUN / VERIFY — first 10 tickers, print values + checks, NO writes
    python -m scripts.hydrate_hedge_fund_flow --dry-run

    # 2) Small staged write (first 50 tickers, conservative rate)
    python -m scripts.hydrate_hedge_fund_flow --limit 50 --rate 3 --clear-holders-cache

    # 3) FULL run (~4000 tickers), resumable, bust holders_cache so reports refresh
    python -m scripts.hydrate_hedge_fund_flow --top-n 4000 --rate 5 --clear-holders-cache

    # 4) Resume after a Railway restart (skips already-fresh tickers automatically)
    python -m scripts.hydrate_hedge_fund_flow --top-n 4000 --rate 5 --clear-holders-cache

    # 5) Force a full recompute (ignore freshness; refetch all 8 quarters)
    python -m scripts.hydrate_hedge_fund_flow --top-n 4000 --no-skip-fresh --rate 5

    # 6) Spot-fix one or more tickers (bypass the universe)
    python -m scripts.hydrate_hedge_fund_flow --ticker AAPL MSFT --no-skip-fresh
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure backend app package is importable (mirrors hydrate_whales.py).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402,F401  (import triggers .env load)
from app.database import get_supabase  # noqa: E402
from app.integrations.fmp import FMPClient, FMPRateLimitException  # noqa: E402
from app.services.holders_service import (  # noqa: E402
    HoldersService,
    _REFRESH_RECENT_QUARTERS,
    _TICKER_RE,
)

logger = logging.getLogger("hydrate_hedge_fund_flow")

# ── Defaults / throttling ────────────────────────────────────────────
# FMP Premium = 3000 req/min = 50/sec ceiling. Go deliberately slow. Two gates
# stacked: a Semaphore caps in-flight concurrency, a min-interval limiter caps
# the SUSTAINED rate. With DEFAULT_RATE=5 the script makes at most 5 calls/sec
# regardless of concurrency, so wall-clock keys off --rate (~10% of the cap).
DEFAULT_RATE = 5.0          # sustained FMP calls/sec
DEFAULT_CONCURRENCY = 4     # max simultaneous in-flight FMP calls
DEFAULT_TOP_N = 4000
DEFAULT_REFRESH_RECENT = _REFRESH_RECENT_QUARTERS   # 2
RETRY_AFTER_DEFAULT = 60    # seconds to back off on FMPRateLimitException
TARGET_QUARTERS = 8

# industry_universe.json lives at backend/data/ — from backend/scripts/ that is
# parents[1] / "data" (parents[0] == scripts, parents[1] == backend).
_UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "data" / "industry_universe.json"


class _RateLimiter:
    """Global min-interval gate: ensures >= 1/rate seconds between FMP calls."""

    def __init__(self, rate_per_sec: float):
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self._min_interval


# ── Ticker universe ──────────────────────────────────────────────────


# Screener pulls a broad pool of REAL US stocks; we then rank + cap to top_n.
# floor 0 returns ~5.8k actively-traded, non-fund/non-ETF US-listed names.
_SCREENER_MIN_MARKET_CAP = 0
_SCREENER_LIMIT = 30000


async def _screener_stock_caps(fmp: FMPClient) -> Dict[str, float]:
    """Real, actively-traded, US-listed stocks (NOT ETF/fund) → {symbol: cap}.

    Mirrors the app's "Select Your Target" search filter — US exchange, exclude
    ETF/fund — but uses FMP's authoritative ``isEtf``/``isFund`` screener flags
    (the search uses a name heuristic because its endpoint lacks those flags).
    Same ``company-screener`` endpoint that generated industry_universe.json
    (discover_industries.py), plus the fund/ETF exclusion the search applies.
    """
    try:
        rows = await fmp._make_request("company-screener", params={
            "isEtf": "false",
            "isFund": "false",
            "isActivelyTrading": "true",
            "exchange": "NASDAQ,NYSE,AMEX",
            "marketCapMoreThan": _SCREENER_MIN_MARKET_CAP,
            "limit": _SCREENER_LIMIT,
        })
        rows = rows if isinstance(rows, list) else []
    except Exception as exc:
        logger.warning(
            "company-screener failed (%s) — falling back to industry_universe.json", exc
        )
        return {}
    caps: Dict[str, float] = {}
    for r in rows:
        sym = (r.get("symbol") or "").upper().strip()
        if sym and _TICKER_RE.match(sym):
            caps[sym] = float(r.get("marketCap") or 0.0)
    return caps


def _json_stock_caps() -> Dict[str, float]:
    """Fallback universe from industry_universe.json when the screener is
    unavailable. NOTE: this static file is screened only by isActivelyTrading,
    so it can contain mutual funds; _TICKER_RE trims foreign symbols but some
    5-letter fund tickers may remain. The screener path is strongly preferred.
    """
    caps: Dict[str, float] = {}
    try:
        data = json.loads(_UNIVERSE_PATH.read_text())
    except Exception as exc:
        logger.error("Failed to read %s: %s", _UNIVERSE_PATH, exc)
        return caps
    for entry in data.get("industries", []) or []:
        for t, c in (entry.get("market_caps") or {}).items():
            t = (t or "").upper().strip()
            if t and _TICKER_RE.match(t):
                caps[t] = max(caps.get(t, 0.0), float(c or 0.0))
    return caps


async def _build_ticker_universe(fmp: FMPClient, top_n: int) -> List[str]:
    """Top-N most-popular REAL stocks (by market cap) UNION user watchlist names.

    Source: FMP company-screener (stock-only: US-listed, actively-traded, not
    ETF/fund). Watchlist tickers are unioned ONLY when they are themselves real
    stocks (present in the screener pool), so user-tracked names are kept
    without re-introducing funds/crypto/foreign listings.
    """
    caps = await _screener_stock_caps(fmp)
    source = "screener (stock-only)"
    if not caps:
        caps = _json_stock_caps()
        source = "industry_universe.json (fallback)"

    cap_ranked = [s for s, _ in sorted(caps.items(), key=lambda kv: kv[1], reverse=True)]
    top = cap_ranked[:top_n]
    top_set = set(top)

    # Watchlist union — only real stocks (must be in the screener pool), ordered
    # by user-occurrence (mirror competitor_intel_service._load_top_watchlist_tickers).
    wl_counts: Dict[str, int] = {}
    try:
        sb = get_supabase()
        res = sb.table("watchlist_items").select("ticker").limit(50_000).execute()
        for row in res.data or []:
            t = (row.get("ticker") or "").upper().strip()
            if t and t in caps and t not in top_set:
                wl_counts[t] = wl_counts.get(t, 0) + 1
    except Exception as exc:
        logger.warning("Failed to read watchlist_items: %s", exc)
    wl_only = [t for t, _ in sorted(wl_counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    final = top + wl_only
    logger.info(
        "Universe [%s]: %d cap-ranked stocks + %d watchlist-only = %d tickers "
        "(top_n=%d, stock pool=%d)",
        source, len(top), len(wl_only), len(final), top_n, len(caps),
    )
    return final


# ── Hydration engine ─────────────────────────────────────────────────


class HedgeFundFlowHydrator:
    """Pre-computes ``hedge_fund_quarters`` rows for a list of tickers."""

    def __init__(
        self,
        fmp: FMPClient,
        *,
        rate: float,
        concurrency: int,
        refresh_recent: int,
        dry_run: bool,
        clear_holders_cache: bool,
        skip_fresh: bool,
    ):
        self.fmp = fmp
        self.sb = get_supabase()
        # Reuse HoldersService for its static math + DB helpers. Its internal
        # get_fmp_client() singleton is never used for HTTP here (we call FMP
        # through self.fmp, which main() closes).
        self.svc = HoldersService()
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.limiter = _RateLimiter(rate)
        self.refresh_recent = max(0, refresh_recent)
        self.dry_run = dry_run
        self.clear_holders_cache = clear_holders_cache
        self.skip_fresh = skip_fresh
        self.stats = {
            "tickers": 0,
            "hydrated": 0,
            "skipped_fresh": 0,
            "fmp_calls": 0,
            "quarters_written": 0,
            "errors": 0,
            "warnings": 0,
        }

    async def run(self, tickers: List[str]) -> None:
        total = len(tickers)
        mode = "DRY-RUN (no writes)" if self.dry_run else "WRITE"
        logger.info(
            "Starting hedge-fund flow hydration [%s] for %d ticker(s) "
            "(rate=%.1f/s, concurrency=%d, refresh_recent=%d, skip_fresh=%s)...",
            mode, total, 1.0 / self.limiter._min_interval if self.limiter._min_interval else 0,
            self.sem._value, self.refresh_recent, self.skip_fresh,
        )
        for i, ticker in enumerate(tickers, start=1):
            self.stats["tickers"] += 1
            t0 = time.monotonic()
            logger.info("[%d/%d] %s", i, total, ticker)
            try:
                await self._hydrate_one(ticker)
                logger.info("  %s — done in %.1fs", ticker, time.monotonic() - t0)
            except Exception as e:  # never let one ticker kill the run
                self.stats["errors"] += 1
                logger.error("  %s — FAILED: %s", ticker, e, exc_info=True)
        self._print_summary()

    def _print_summary(self) -> None:
        s = self.stats
        logger.info(
            "Hydration complete. tickers=%d  hydrated=%d  skipped_fresh=%d  "
            "fmp_calls=%d  quarters_written=%d  warnings=%d  errors=%d",
            s["tickers"], s["hydrated"], s["skipped_fresh"], s["fmp_calls"],
            s["quarters_written"], s["warnings"], s["errors"],
        )
        if self.dry_run:
            print(
                f"\nDRY-RUN complete. tickers={s['tickers']}  "
                f"fmp_calls={s['fmp_calls']}  warnings={s['warnings']}  "
                f"errors={s['errors']}  (NO writes performed)"
            )
            if s["warnings"] == 0 and s["errors"] == 0:
                print("ALL CHECKS PASSED — safe to run full hydration.")
            else:
                print(
                    "DRY-RUN found issues — review the WARN/FAIL rows above "
                    "before running the full hydration."
                )

    # ── Per-ticker pipeline ──────────────────────────────────────────

    async def _hydrate_one(self, ticker: str) -> None:
        target_pairs = HoldersService._generate_quarter_keys(TARGET_QUARTERS)
        volatile = set(target_pairs[-self.refresh_recent:]) if self.refresh_recent else set()
        settled = [p for p in target_pairs if p not in volatile]

        # Read current DB state. _load_existing_quarters already applies the
        # floor + zero-row + one-sided filters, so any present key is good/fresh.
        existing = await asyncio.to_thread(
            self.svc._load_existing_quarters, ticker, target_pairs
        )
        settled_missing = [p for p in settled if p not in existing]

        if self.dry_run:
            # Always fetch all 8 so the printout shows every quarter + checks.
            to_fetch = list(target_pairs)
        else:
            # Resume fast-path: nothing missing among settled AND every volatile
            # quarter already present & fresh → skip entirely (0 FMP calls).
            if (
                self.skip_fresh
                and not settled_missing
                and all(p in existing for p in volatile)
            ):
                self.stats["skipped_fresh"] += 1
                logger.info("  %s — all quarters fresh, skip (0 FMP calls)", ticker)
                return
            # skip_fresh: only fetch missing settled + always-volatile.
            # no-skip-fresh: force a full recompute (all settled + volatile).
            settled_to_fetch = settled_missing if self.skip_fresh else list(settled)
            to_fetch_set = set(settled_to_fetch) | volatile
            to_fetch = [p for p in target_pairs if p in to_fetch_set]

        if not to_fetch:
            return

        results = await asyncio.gather(
            *[self._fetch_one_quarter(ticker, y, q) for (y, q) in to_fetch],
            return_exceptions=True,
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        new_rows: List[Dict[str, Any]] = []
        verify: List[Dict[str, Any]] = []
        for (y, q), data in zip(to_fetch, results):
            if isinstance(data, BaseException):
                self.stats["errors"] += 1
                logger.warning("  %s %dQ%d fetch error: %s", ticker, y, q, data)
                if self.dry_run:
                    verify.append({"y": y, "q": q, "status": "error"})
                continue
            if not data:
                if self.dry_run:
                    verify.append({"y": y, "q": q, "status": "nodata"})
                continue
            buy_m, sell_m, net_m, buyers, sellers = HoldersService._compute_quarter_flow(data)
            # Mirrors holders_service.py live row dict + the computed_at stamp.
            row = {
                "ticker": ticker,
                "year": y,
                "quarter": q,
                "quarter_date": (data.get("date") or "")[:10],
                "buy_volume": buy_m,
                "sell_volume": sell_m,
                "net_flow": net_m,
                "buyers_count": buyers,
                "sellers_count": sellers,
                "computed_at": now_iso,
            }
            new_rows.append(row)
            if self.dry_run:
                verify.append({
                    "y": y, "q": q, "status": "ok", "row": row,
                    "raw_net_m": float(data.get("numberOf13FsharesChange") or 0) / 1_000_000,
                    "total_sh_m": float(data.get("numberOf13Fshares") or 0) / 1_000_000,
                })

        if self.dry_run:
            self._print_verify(ticker, len(existing), verify)
            return

        if new_rows:
            await asyncio.to_thread(self.svc._save_quarters, ticker, new_rows)
            self.stats["quarters_written"] += len(new_rows)
            self.stats["hydrated"] += 1
            if self.clear_holders_cache:
                await asyncio.to_thread(self._delete_holders_cache, ticker)

    async def _fetch_one_quarter(
        self, ticker: str, year: int, quarter: int
    ) -> Optional[Dict[str, Any]]:
        """Throttled single-quarter FMP fetch with one rate-limit retry."""
        async with self.sem:
            await self.limiter.acquire()
            try:
                data = await self.fmp.get_institutional_ownership_for_quarter(
                    ticker, year, quarter
                )
            except FMPRateLimitException as e:
                ra = getattr(e, "retry_after", None)
                wait = int(ra) if (ra is not None and str(ra).isdigit()) else RETRY_AFTER_DEFAULT
                logger.warning(
                    "  429 on %s %dQ%d — backing off %ss then retrying once",
                    ticker, year, quarter, wait,
                )
                await asyncio.sleep(wait)
                await self.limiter.acquire()
                data = await self.fmp.get_institutional_ownership_for_quarter(
                    ticker, year, quarter
                )
            self.stats["fmp_calls"] += 1
            return data

    def _delete_holders_cache(self, ticker: str) -> None:
        """Bust the assembled 24h holders_cache row so the next report rebuild
        reads the freshly-hydrated quarters (mirrors the whale_profile_cache
        invalidation idiom in hydrate_whales.py)."""
        try:
            self.sb.table("holders_cache").delete().eq("ticker", ticker).execute()
        except Exception as exc:
            logger.warning("  holders_cache delete failed for %s: %s", ticker, exc)

    # ── Verify-mode printout ─────────────────────────────────────────

    def _print_verify(
        self, ticker: str, n_in_db: int, verify: List[Dict[str, Any]]
    ) -> None:
        n_ok = sum(1 for v in verify if v["status"] == "ok")
        print(f"=== {ticker} ===  ({n_ok} quarter(s) computed, {n_in_db} already in DB)")
        tot_buy = tot_sell = tot_net = 0.0
        flags = 0
        for v in verify:
            label = HoldersService._quarter_label(v["y"], v["q"]).replace("\n", "")
            if v["status"] == "nodata":
                print(f"  {label}  (no FMP data)")
                continue
            if v["status"] == "error":
                print(f"  {label}  (fetch error)")
                continue
            row = v["row"]
            net = row["net_flow"]
            buy = row["buy_volume"]
            sell = row["sell_volume"]
            raw_net = v["raw_net_m"]
            total_sh = v["total_sh_m"]

            # net == buy - sell. buy and sell are each rounded to 2 decimals
            # independently, so the reconstructed (buy - sell) can drift up to
            # ~0.015 from the stored net — a 1-cent rounding artifact, not a
            # real inconsistency. Tolerance 0.02 catches genuine breaks (which
            # are off by far more) without crying wolf on rounding.
            c1 = abs(net - (buy - sell)) <= 0.02          # net == buy - sell
            c2 = abs(net - round(raw_net, 2)) <= 0.01     # net == FMP field / 1e6
            c5 = buy >= 0 and sell >= 0                    # non-negativity
            c4_warn = (total_sh > 0 and abs(net) > total_sh) or (
                total_sh == 0 and abs(net) > 0
            )                                              # implausible magnitude

            row_flags = (not c1) + (not c2) + (not c5) + c4_warn
            if row_flags:
                self.stats["warnings"] += row_flags
                flags += row_flags

            checks = (
                f"[net=buy-sell:{'OK' if c1 else 'FAIL'} "
                f"net=fmp:{'OK' if c2 else 'FAIL'} "
                f"nonneg:{'OK' if c5 else 'FAIL'} "
                f"magnitude:{'OK' if not c4_warn else 'WARN'}]"
            )
            tot_buy += buy
            tot_sell += sell
            tot_net += net
            print(
                f"  {label}  net={net:+.2f}  buy={buy:.2f}  sell={sell:.2f}  "
                f"buyers={row['buyers_count']} sellers={row['sellers_count']}  "
                f"total13F={total_sh:.1f}M  {checks}"
            )
        print(
            f"  {ticker} summary: total_buy={tot_buy:.2f}  total_sell={tot_sell:.2f}  "
            f"total_net={tot_net:.2f}  flags={flags}"
        )


# ── CLI ──────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hedge-Fund Flow Hydration Engine — bulk pre-compute "
        "hedge_fund_quarters for popular tickers (FMP-only, resumable)."
    )
    parser.add_argument(
        "--top-n", type=int, default=DEFAULT_TOP_N,
        help=f"Size of the popular-ticker universe (default {DEFAULT_TOP_N}).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Hard cap on tickers actually processed (after ranking). Staged rollout.",
    )
    parser.add_argument(
        "--dry-run", "--verify", dest="dry_run", action="store_true",
        help="Process only the FIRST 10 tickers, print values + correctness "
             "checks, NO writes. (--verify is an alias.)",
    )
    parser.add_argument(
        "--rate", type=float, default=DEFAULT_RATE,
        help=f"Sustained FMP calls/sec, global rate gate (default {DEFAULT_RATE}).",
    )
    parser.add_argument(
        "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"Max simultaneous in-flight FMP calls (default {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--refresh-recent", type=int, default=DEFAULT_REFRESH_RECENT,
        help=f"Most-recent (volatile) quarters to always refetch "
             f"(default {DEFAULT_REFRESH_RECENT}).",
    )
    parser.add_argument(
        "--skip-fresh", dest="skip_fresh", action="store_true",
        help="Skip tickers already fully fresh (default ON; makes the run resumable).",
    )
    parser.add_argument(
        "--no-skip-fresh", dest="skip_fresh", action="store_false",
        help="Force a full recompute — refetch all 8 quarters regardless of freshness.",
    )
    parser.set_defaults(skip_fresh=True)
    parser.add_argument(
        "--clear-holders-cache", action="store_true",
        help="DELETE the holders_cache row per (re)hydrated ticker so the next "
             "report rebuild reflects the fresh flow.",
    )
    parser.add_argument(
        "--ticker", nargs="*", default=None,
        help="Explicit ticker list (bypasses the universe). Handy for spot fixes.",
    )
    args = parser.parse_args()

    fmp = FMPClient()
    try:
        if args.ticker:
            tickers = [t.upper().strip() for t in args.ticker]
        else:
            tickers = await _build_ticker_universe(fmp, args.top_n)

        if args.dry_run:
            tickers = tickers[:10]              # verify the first 10 only
        elif args.limit:
            tickers = tickers[: args.limit]

        hydrator = HedgeFundFlowHydrator(
            fmp,
            rate=args.rate,
            concurrency=args.concurrency,
            refresh_recent=args.refresh_recent,
            dry_run=args.dry_run,
            clear_holders_cache=args.clear_holders_cache,
            skip_fresh=args.skip_fresh,
        )
        await hydrator.run(tickers)
    finally:
        await fmp.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(main())
