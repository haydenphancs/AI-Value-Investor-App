"""
Daily σ (daily-return volatility) precompute + cache for the Updates trigger.

The Updates insight gate scores a session move as ``z = |change%| / (σ_daily·100)``
to decide if it is *abnormal for THIS ticker* (see updates_materiality.classify_move).
σ_daily is a slow-moving 180-day statistic, so it is PRECOMPUTED once per day (a
lifespan job calls :meth:`recompute_universe`) and the 5-minute sweeper reads it
via :meth:`get_sigmas_bulk` — it never fetches 180 daily closes per ticker per sweep.

Canonical two-tier cache-aside shape (CLAUDE.md invariant #4): in-memory dict
(30 min) → Supabase ``ticker_volatility_cache`` (migration 090, ~36h). The σ math is
the SHARED `price_volatility` module — the exact same computation the report's
"Recent Price Movement" section uses, so a stock gets the same tier on both.

DEGRADE, NEVER CRASH THE SWEEP: any read failure yields no σ for that symbol,
which the gate treats as "σ unavailable" → it falls back to the fixed price band.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import (
    FMPAuthException,
    FMPRateLimitException,
    get_fmp_client,
)
from app.services.price_volatility import _BASELINE_DAYS, _daily_returns, _std_dev_pop

logger = logging.getLogger(__name__)

_TABLE = "ticker_volatility_cache"
_MEM_TTL_SECONDS = 1800          # Tier-1 in-memory (30 min); σ changes daily
_CACHE_TTL_HOURS = 36            # DB row lifetime — survives a single missed daily run
# Need >= _BASELINE_DAYS + 1 TRADING closes; ~1.5x calendar days covers weekends/holidays.
_HISTORY_LOOKBACK_DAYS = int((_BASELINE_DAYS + 10) * 1.5)
_MIN_CLOSES = 30                 # matches price_volatility._compute_price_volatility's floor
_RECOMPUTE_CONCURRENCY = 6

# symbol(upper) -> (monotonic_ts, sigma_daily|None)
_mem: Dict[str, Tuple[float, Optional[float]]] = {}


def _hist_list(historical: Any) -> List[Dict[str, Any]]:
    """FMP /historical-price-eod/full returns a flat list or {"historical":[...]}."""
    if isinstance(historical, list):
        return historical
    if isinstance(historical, dict):
        return historical.get("historical", []) or []
    return []


def _chronological_closes(historical: Any) -> List[float]:
    """Oldest→newest close prices (FMP returns newest-first).

    Drops non-finite closes: FMP returns ``NaN``/``Infinity`` JSON tokens on thin
    / just-listed symbols, and ``float("nan")`` succeeds — so without an
    ``isfinite`` gate one bad row would flow into the σ math and poison the whole
    baseline (CLAUDE.md hardening rule).
    """
    closes: List[float] = []
    for p in _hist_list(historical):
        c = p.get("close")
        if c is None:
            continue
        try:
            v = float(c)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        closes.append(v)
    closes.reverse()
    return closes


def _sigma_from_closes(closes: List[float]) -> Tuple[Optional[float], int]:
    """(σ_daily, sample_size) over the last ``_BASELINE_DAYS+1`` closes.

    σ is None when there is too little history (< _MIN_CLOSES closes) or the std is
    non-positive — the gate then uses the fixed band for this ticker. Mirrors
    ``price_volatility._compute_price_volatility`` (same floor, same baseline slice).
    """
    if len(closes) < _MIN_CLOSES:
        return None, max(0, len(closes) - 1)
    baseline = closes[-(_BASELINE_DAYS + 1):]
    returns = _daily_returns(baseline)
    sigma = _std_dev_pop(returns)
    # `not isfinite` in addition to `<= 0`: a nan escapes `<= 0` (nan<=0 is False)
    # and must NEVER be written to the cache — a NaN in the row serializes to an
    # invalid JSON body (rejected upsert) or a poisoned DOUBLE PRECISION NaN.
    if sigma is None or not math.isfinite(sigma) or sigma <= 0:
        return None, len(returns)
    return sigma, len(returns)


class VolatilityCacheService:
    def __init__(self) -> None:
        self.supabase = get_supabase()
        self.fmp = get_fmp_client()

    # ── Sweeper read path ─────────────────────────────────────────────

    async def get_sigmas_bulk(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """``{SYMBOL: sigma_daily|None}`` for the requested symbols.

        In-memory tier, then ONE batched Supabase select for the misses.
        Best-effort — a Supabase failure yields ``{}`` for the misses (σ None →
        gate falls back to the fixed band) and NEVER raises into the sweep.
        """
        wanted = [str(s).upper() for s in dict.fromkeys(symbols) if s]
        if not wanted:
            return {}
        out: Dict[str, Optional[float]] = {}
        misses: List[str] = []
        mono = time.monotonic()
        for s in wanted:
            hit = _mem.get(s)
            if hit and (mono - hit[0]) < _MEM_TTL_SECONDS:
                out[s] = hit[1]
            else:
                misses.append(s)
        if misses:
            rows = await asyncio.to_thread(self._select_fresh, misses)
            now_mono = time.monotonic()
            for s in misses:
                sigma = rows.get(s)   # absent/expired → None
                out[s] = sigma
                _mem[s] = (now_mono, sigma)
        return out

    def _select_fresh(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """Blocking read — always via asyncio.to_thread. Returns {} on any error."""
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            res = (
                self.supabase.table(_TABLE)
                .select("ticker,sigma_daily,expires_at")
                .in_("ticker", symbols)
                .gte("expires_at", now_iso)
                .execute()
            )
        except Exception as e:
            logger.warning(
                "Volatility cache read failed for %d symbols: %s: %s",
                len(symbols), type(e).__name__, e,
            )
            return {}
        out: Dict[str, Optional[float]] = {}
        for r in (res.data or []):
            t = r.get("ticker")
            if not t:
                continue
            sig = r.get("sigma_daily")
            val: Optional[float] = None
            if sig is not None:
                try:
                    f = float(sig)
                    # A legacy/poisoned NaN row must read back as None (→ fixed
                    # band), never as a nan that could reach the tier math.
                    val = f if math.isfinite(f) else None
                except (TypeError, ValueError):
                    val = None
            out[str(t).upper()] = val
        return out

    # ── Daily precompute ──────────────────────────────────────────────

    async def recompute_universe(
        self, symbols: List[str], *, skip_if_fresh_hours: int = 20
    ) -> int:
        """Precompute σ for ``symbols`` and upsert. Returns rows written.

        Bounded concurrency; per-symbol failures are logged, not fatal.
        ``skip_if_fresh_hours`` lets a redeploy-retriggered run RESUME (skip
        tickers computed in the last N hours) instead of redoing everything.
        """
        syms = [str(s).upper() for s in dict.fromkeys(symbols) if s]
        if not syms:
            return 0
        fresh: set = (
            await asyncio.to_thread(self._recently_computed, syms, skip_if_fresh_hours)
            if skip_if_fresh_hours
            else set()
        )
        todo = [s for s in syms if s not in fresh]
        logger.info(
            "Volatility precompute: %d symbols (%d skipped fresh)",
            len(todo), len(syms) - len(todo),
        )
        if not todo:
            return 0

        sem = asyncio.Semaphore(_RECOMPUTE_CONCURRENCY)
        from_date = (
            datetime.now(timezone.utc) - timedelta(days=_HISTORY_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")

        async def _one(sym: str) -> int:
            async with sem:
                try:
                    hist = await self.fmp.get_historical_prices(sym, from_date=from_date)
                except (FMPRateLimitException, FMPAuthException) as e:
                    logger.warning("Volatility precompute quota/auth on %s: %s", sym, e)
                    return 0
                except Exception as e:
                    logger.warning(
                        "Volatility precompute fetch failed for %s: %s: %s",
                        sym, type(e).__name__, e,
                    )
                    return 0
                sigma, sample = _sigma_from_closes(_chronological_closes(hist))
                return await asyncio.to_thread(self._upsert, sym, sigma, sample)

        results = await asyncio.gather(*[_one(s) for s in todo], return_exceptions=True)
        written = sum(r for r in results if isinstance(r, int))
        logger.info("Volatility precompute complete: %d/%d rows written", written, len(todo))
        return written

    def _recently_computed(self, symbols: List[str], hours: int) -> set:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            res = (
                self.supabase.table(_TABLE)
                .select("ticker,computed_at")
                .in_("ticker", symbols)
                .gte("computed_at", cutoff)
                .execute()
            )
            return {
                str(r["ticker"]).upper()
                for r in (res.data or []) if r.get("ticker")
            }
        except Exception as e:
            logger.warning(
                "Volatility precompute freshness probe failed: %s: %s",
                type(e).__name__, e,
            )
            return set()

    def _upsert(self, ticker: str, sigma: Optional[float], sample: int) -> int:
        now = datetime.now(timezone.utc)
        row = {
            "ticker": ticker,
            "sigma_daily": sigma,
            "sample_size": sample,
            "computed_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=_CACHE_TTL_HOURS)).isoformat(),
        }
        try:
            self.supabase.table(_TABLE).upsert(row, on_conflict="ticker").execute()
            _mem[ticker] = (time.monotonic(), sigma)  # keep Tier-1 consistent
            return 1
        except Exception as e:
            logger.warning(
                "Volatility precompute upsert failed for %s: %s: %s",
                ticker, type(e).__name__, e,
            )
            return 0


_singleton: Optional[VolatilityCacheService] = None


def get_volatility_cache_service() -> VolatilityCacheService:
    global _singleton
    if _singleton is None:
        _singleton = VolatilityCacheService()
    return _singleton
