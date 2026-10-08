"""
Financial Health Snapshot service — computes sector-relative health ratings
by reusing the existing HealthCheckService (Financials tab) to ensure
data consistency.

Extracts the overall rating and 4 metrics (Debt-to-Equity, P/E Ratio,
Return on Equity, Current Ratio) from the health check, maps them to
the 1-5 snapshot rating, and formats for display.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``snapshot_cache`` table (24-hour TTL)

Matches the iOS SnapshotItemDTO struct.
"""

import asyncio
import math
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.integrations.fmp import FMPNotEntitledException, get_fmp_client
from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
    snapshot_build_time,
    with_cached_build_time,
)
from app.services.financials_metric_gate import (
    CURRENT_RATIO,
    DEBT_TO_EQUITY,
    INTEREST_COVERAGE,
    QUICK_RATIO,
    company_metric_applicable,
    comparable_peer_metrics,
    peer_median_comparable,
    resolve_payment_network,
    resolve_withheld_company_rows,
    trailing_interest_row,
)
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup, lookup_failed
from app.services.sector_benchmark_service import _normalize_sector
from app.services.valuation_snapshot_service import split_peer_cells

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes

# Version stamped into this card's snapshot_cache rows (same key and pattern as
# valuation_snapshot_service). Bump it when a stored value's computation changes, so
# rows written under the old rules rebuild on their next read instead of serving 24h.
# 2 (2026-09-30): neutral sector metrics earn half credit in pass_rating (they counted as
#     misses), and Altman Z is omitted for banks / insurers / REITs (it read as distress).
# 3 (2026-09-30 deep check): health-check status rules changed.
# 4 (2026-10-07): each compared metric carries `peer_level` (the Health Check's own on the
#     main path, the lookup cell's on the local fallback) and the card `computed_at`; the
#     fallback reads the 2026-10-07 lookup (a mature same-period peer cell, never a partial
#     year). Also re-reads every row built over the pre-2026-10-07 Health Check, whose
#     metrics this card copies (its own rows carry a separate version).
# 5 (2026-10-07, review round 2): insurance brokers get their Interest Coverage row back
#     (`financials_metric_gate`; the Health Check is v6); a current / quick ratio /
#     interest-coverage median that is the Financial Services SECTOR's (bank-dominated) is
#     no comparison on either path; and a card with fewer than two SCORED rows is rated 0
#     (unavailable) with no `weighted_score` — a bank's card rested on D/E alone (see
#     `_MIN_SCORED_FOR_RATING`).
# 6 (2026-10-08, owner decision 3, first cut — never deployed): a card network / payment
#     processor in "Financial - Credit Services" (interest income < 25% of revenue) got
#     current ratio, interest coverage and quick ratio back on both paths, on absolute
#     bands — so its card is RATED again; a v5 row of such a company is an unrated
#     D/E-only card, rebuilt.
# 7 (2026-10-08, same day): only a LISTED payment network (`financials_metric_gate.
#     PAYMENT_NETWORKS`: V, MA, PYPL, WU, GPN, unless its own income reads as a lender's)
#     gets them back, and its debt-to-equity is judged on absolute bands too (no peer
#     median, `peer_level` None; the Health Check is v8). A v6 row may be a first-cut card
#     of an unlisted lender (ENVA, SEZL) with the three rows, or a network's D/E compared
#     against lenders — written only by a local run, rebuilt either way.
# 8 (2026-10-09, owner decisions NET-4 / NET-5; the Health Check is v9): the non-lender fee
#     businesses TREE and PMTS join the listed members (`NON_LENDER_MEMBERS`) and keep the
#     three rows on absolute bands; a curated per-company fact withholds WU's current
#     ratio, quick ratio and interest coverage on the fallback too (`CURATED_WITHHELD_ROWS`
#     — its card is then unrated, one scored row); the fallback asks the lookup only for
#     the metrics it may compare (`comparable_peer_metrics`) and none at all for a listed
#     member, so a lookup failure no longer degrades a peer-free card. A v7 row of WU may
#     carry its made-up rows (local runs only); one of TREE / PMTS lacks theirs.
_SNAPSHOT_PAYLOAD_VERSION = 8
_VERSION_KEY = "_schema_v"


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── Build status of the value each key serves ─────────────────────
# A degraded build is SERVED (Tier 1 for 5 min, and to every in-flight joiner) but never
# persisted. The report collector freezes what it receives into the close-aligned
# ticker_data_cache and a paid report — the partial card's `weighted_score` drives the
# Financial Health vital for every persona — so `get_health_snapshot_with_status` must
# report the status of the EXACT object handed out, on a Tier-1 hit and an in-flight join
# as much as on the build. Same role as growth_service's `_degraded_by_key`, but each entry
# holds `(value, degraded)` and a status is reported only for that very object (identity).
_degraded_by_key: Dict[str, Tuple[Any, List[str]]] = {}
# Larger than the Tier-1 cap: entries are written in lockstep with `_cache_set`, so the
# memo can only lose an entry Tier 1 still holds after twice as many writes.
_DEGRADED_MAX_ENTRIES = 2 * _CACHE_MAX_ENTRIES
# Reported when the served object has no memo entry (evicted, or a value reached Tier 1 by a
# path that never noted it). Fail CLOSED: an unknown provenance must not be frozen.
_STATUS_UNKNOWN = "status_unknown"


def _note_degraded(key: str, value: Any, degraded: List[str]) -> None:
    """Record the degraded legs of ``value``, the object now served under ``key``.

    Call it on EVERY write of a value to Tier 1, and before resolving the in-flight future
    with it, so a joiner resuming after `set_result` reads the leader's status."""
    _degraded_by_key.pop(key, None)
    _degraded_by_key[key] = (value, list(degraded))
    if len(_degraded_by_key) > _DEGRADED_MAX_ENTRIES:
        for _old in list(_degraded_by_key.keys())[: len(_degraded_by_key) - _DEGRADED_MAX_ENTRIES]:
            _degraded_by_key.pop(_old, None)


def _degraded_of(key: str, value: Any) -> Optional[List[str]]:
    """The degraded legs noted for exactly ``value`` under ``key``; None when unknown."""
    entry = _degraded_by_key.get(key)
    if entry is None or entry[0] is not value:
        return None
    return list(entry[1])


# ── In-flight deduplication ───────────────────────────────────────
_inflight: Dict[str, asyncio.Future] = {}

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Display helpers ──────────────────────────────────────────────

# Map health check metric types to display names
_DISPLAY_NAMES = {
    "debt_to_equity": "Debt-to-Equity",
    "pe_ratio": "P/E Ratio",
    "roe": "Return on Equity (ROE)",
    "current_ratio": "Current Ratio",
    "altman_z_score": "Altman Z-Score",
    "interest_coverage": "Interest Coverage",
    "quick_ratio": "Quick Ratio",
}

# Snapshot intentionally hides these — P/E lives in the Valuation card
# (different domain) and ROE lives in the Profitability card (duplicate).
# Interest Coverage and Quick Ratio replace them as proper health metrics.
_HIDE_FROM_SNAPSHOT = {"pe_ratio", "roe"}

# Metric types that participate in the sector-comparable side of the rating
# blend. Altman Z-Score is excluded — it has its own anchor weight via
# `_zscore_rating()` and uses absolute thresholds, not sector comparisons.
_SECTOR_RATING_TYPES = {
    "debt_to_equity",
    "current_ratio",
    "interest_coverage",
    "quick_ratio",
}

# A card is RATED (1-5 + `weighted_score`) only when at least this many of its rows carry a
# score (decided 2026-10-07, review round 2, HC-3 / F1). For a bank, insurer, broker-dealer,
# asset manager or lender the Health Check omits Altman Z and the three liquidity rows, and
# this card hides P/E and ROE, so ONE row was left — Debt-to-Equity — and the card read
# Solid / Moderate / Soft (4.2 / 3.0 / 1.8 against the neutral Z anchor) on that ratio
# alone, persisted 24h, shown as the report's Health stars and fed into the persona
# health factor. One comparison is not a financial-health verdict. With fewer than two
# scored rows the card is rated 0 and carries no `weighted_score`. On the Overview, rating 0
# reads as unavailable ("--") on every iOS build, as the degraded Price card already does. In
# the paid report the card says "Not Rated" (card_verdict) — grey from 1.01 on, but RED on the
# shipped build 10, whose report card colours any 0-star label with its bearish arm (known
# cosmetic issue until 1.01 is adopted; no backend value avoids it without inventing a rating).
# The report's health vital is then unmeasured for these gated financials (no absolute D/E
# bands on a bank's balance sheet) instead of voting on one ratio. The rows keep
# their own scores: the D/E comparison is real, only the card-level verdict is withheld.
# It is a company state, not an outage, so the build is still persisted. (Alternatives
# rejected: folding in the Health Check's P/E and ROE would let a valuation multiple decide
# "health" and duplicate the Price / Profitability cards; a constant neutral 3 is a made-up
# verdict.) Same rule on the local fallback path.
_MIN_SCORED_FOR_RATING = 2

# Metrics where the value is a percentage (ROE)
_PCT_METRICS = {"roe"}


def _fmt_value(metric_type: str, value: Optional[float]) -> str:
    """Format metric value for display."""
    if value is None:
        return "—"
    if metric_type in _PCT_METRICS:
        return f"{value:.2f}%"
    return f"{value:.2f}"


def _metric_name(metric_type: str, value: Optional[float], comparison_value: Optional[float]) -> str:
    """Build metric name with optional sector context.

    The words stay "vs sector" whatever the peer level: shipped iOS builds strip the
    suffix by the word "sector", and newer builds swap the wording by `peer_level`."""
    label = _DISPLAY_NAMES.get(metric_type, metric_type)
    if _compared(value, comparison_value):
        return f"{label} (vs sector {comparison_value:.2f})"
    return label


def _compared(value: Optional[float], comparison_value: Optional[float]) -> bool:
    """True when `_metric_name` prints a peer median — the one condition under which a
    metric carries a `peer_level`."""
    return value is not None and comparison_value is not None


def _peer_level(level: Any, value: Optional[float], comparison_value: Optional[float]) -> Optional[str]:
    """`peer_level` for a metric: the level of the median its name prints, else None."""
    if not _compared(value, comparison_value):
        return None
    return level if level in ("industry", "sector") else None


def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _sum_ttm_income(quarterly: List[Dict[str, Any]]) -> Dict[str, float]:
    """Sum the last 4 quarters of income-statement records into a TTM dict.

    FMP returns quarterly statements newest-first when sorted by date desc,
    but we re-sort here defensively. Skips a field if any of the last 4
    quarters is missing it (rather than partial-summing 2 or 3 quarters,
    which would understate the TTM number and silently corrupt ratios).

    🔴 That last sentence used to be false. Below four quarters the function fell back to
    `sorted_q = quarterly[:4]` — which is the SAME records, minus the sort — under the
    comment "Better to expose partial data than render '—' for new tickers". So a company
    with two filings (a recent IPO, or a symbol FMP holds partial history for — GMRS
    returns exactly 2 quarterly records, verified live) had its **half-year** EBIT and
    revenue summed and published as a trailing-twelve-month figure.

    That is not a cosmetic understatement. Altman Z weights `ebit/assets` at **3.3** and
    `revenue/assets` at 1.0, so halving both numerators can move the verdict a whole band,
    and the Z is 40% of this card's rating. `_compute_z_score` already OMITS the metric
    when a field is absent — that is the honest degradation, and this makes it fire.
    `health_check_service._sum_ttm_income` was fixed for exactly this; the twin was not.
    """
    if not isinstance(quarterly, list):
        logger.warning(
            "health_snapshot TTM: expected a list of income statements, got %s",
            type(quarterly).__name__,
        )
        return {}
    quarterly = [r for r in quarterly if isinstance(r, dict)]
    if not quarterly:
        return {}
    sorted_q = sorted(quarterly, key=lambda r: r.get("date") or "", reverse=True)[:4]
    if len(sorted_q) < 4:
        logger.warning(
            "health_snapshot TTM: need 4 quarters, got %d — omitting the TTM totals "
            "rather than publishing a partial-period sum as trailing-twelve-month",
            len(sorted_q),
        )
        return {}
    summed: Dict[str, float] = {}
    for field in (
        "operatingIncome", "interestExpense", "revenue",
        "netIncome", "ebitda", "depreciationAndAmortization",
    ):
        vals: List[float] = []
        for rec in sorted_q:
            v = _safe_float(rec, field)
            if v is None:
                vals = []
                break
            vals.append(v)
        if vals:
            summed[field] = sum(vals)
        else:
            # A partial sum would understate a TTM flow figure, so the field is dropped —
            # but dropping it SILENTLY is how a downstream `or 0` fabricated a zero EBIT.
            logger.warning(
                "health_snapshot TTM: %r missing in at least one of the last 4 quarters "
                "— field omitted from the TTM sum", field,
            )
    return summed


def _compute_z_score(
    bs: Dict, inc: Dict, mcap: Optional[float], *,
    sector: Optional[str] = None, industry: Optional[str] = None,
) -> Optional[float]:
    """Compute Altman Z-Score — delegated to the ONE implementation.

    ``sector`` / ``industry`` reach the canonical gate, which returns None for banks,
    insurers and REITs (the model reads their deposit funding as distress).

    🔴 This was a byte-for-byte transcription of the pre-fix version, kept green by having
    its own tests: `(ebit or 0)`, `(mcap or 0)` and `(rev or 0)` substituted **0** for a
    missing term, so a failed profile fetch valued the equity at zero and turned a fortress
    balance sheet into "Grey zone. Moderate financial stress signals". The Z also feeds
    `_zscore_rating`, which is 40% of this card's rating.

    Same name and signature as before, so every call site here is unchanged; only the
    substitution is gone. See `health_check_service._compute_z_score` for the full
    reasoning and `tests/test_altman_z_single_implementation.py` for the anti-drift guard.
    """
    from app.services.health_check_service import _compute_z_score as _canonical

    return _canonical(bs, inc, mcap, sector=sector, industry=industry)


def _zscore_rating(z: Optional[float]) -> int:
    """Score Z-Score 1-5 using Altman's universal thresholds."""
    if z is None:
        return 3
    if z > 3.0:
        return 5  # Safe zone
    if z > 2.5:
        return 4
    if z > 1.8:
        return 3  # Grey zone
    if z > 1.0:
        return 2
    return 1      # Distress zone


# ── Verdict scoring (drivers for card_verdict.generate_card_verdict) ──
# HealthCheck metric type → the canonical key the deterministic card verdict
# uses; plus a 1-5 score (Altman Z from its absolute zones, the sector-comparable
# ratios from their positive/neutral/negative status).
_VERDICT_KEY = {
    "altman_z_score": "altman_z",
    "debt_to_equity": "debt_to_equity",
    "current_ratio": "current_ratio",
    "interest_coverage": "interest_coverage",
    "quick_ratio": "quick_ratio",
}


def _status_score(status: Optional[str]) -> Optional[int]:
    if status == "positive":
        return 4
    if status == "negative":
        return 2
    if status == "neutral":
        return 3
    return None


def _health_metric_score(m: Any) -> Optional[int]:
    """1-5 verdict score for a HealthCheck metric (None when its value is missing)."""
    if getattr(m, "value", None) is None:
        return None
    if getattr(m, "type", None) == "altman_z_score":
        return _zscore_rating(m.value)
    return _status_score(getattr(m, "status", None))


def _fallback_sector_score(
    value: Optional[float], benchmark: Optional[float], *, lower_is_better: bool,
) -> Optional[int]:
    """Quick 4 (beats) / 2 (lags) score for the local-fallback path — used only
    when HealthCheckService is down (no per-metric status to read)."""
    if value is None or benchmark is None or benchmark <= 0:
        return None
    beats = (value < benchmark) if lower_is_better else (value > benchmark)
    return 4 if beats else 2


# ── Service ───────────────────────────────────────────────────────

class HealthSnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_health_snapshot_with_status(
        self, ticker: str,
    ) -> Tuple[SnapshotItemResponse, List[str]]:
        """`get_health_snapshot` plus the degraded legs of the build being served.

        ``degraded`` is ``[]`` for a clean build and for a Supabase-tier hit (only clean
        builds are persisted). A Tier-1 hit and an in-flight join report the status of the
        build that produced the value they received — e.g. ``["health_check:ratios"]``
        while the health check served a build whose ratios leg 429'd. A served object with
        no recorded status reports ``["status_unknown"]`` (fail closed).
        """
        snapshot = await self.get_health_snapshot(ticker)
        # No await between the return above and this read: the memo describes this object.
        cache_key = f"health_snapshot:{_validate_ticker(ticker)}"
        degraded = _degraded_of(cache_key, snapshot)
        if degraded is None:
            logger.warning(
                "Health snapshot status UNKNOWN for %s — reporting it degraded so it is "
                "not frozen into a long-lived cache", ticker,
            )
            degraded = [_STATUS_UNKNOWN]
        return snapshot, degraded

    async def get_health_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup.

        Every value it hands out has its build status noted (`_note_degraded`), which
        `get_health_snapshot_with_status` reads back."""
        ticker = _validate_ticker(ticker)
        cache_key = f"health_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Health snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Health snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            # Only clean builds are persisted, so a Tier-2 row is clean by construction.
            _note_degraded(cache_key, db_cached, [])
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Health snapshot in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Health snapshot cache MISS for {ticker} — computing")
            result, degraded = await self._compute_with_status(ticker)

            # NEVER persist a degraded build. HealthCheckService refuses to persist a
            # result with no metrics; this card would turn it into "Financial Health —"
            # rated with the neutral sentinel 3 and pin that in snapshot_cache for 24h,
            # where the Overview card and the report's snap_health read it. Serve it, keep
            # the 5-min memory tier, rebuild on the next miss. Mirrors
            # valuation_snapshot_service.
            if degraded:
                logger.warning(
                    "Health snapshot NOT persisted for %s (degraded: %s) — will rebuild "
                    "after the in-memory TTL", ticker, ", ".join(degraded),
                )
            else:
                # Persist to Supabase in background thread
                asyncio.get_running_loop().run_in_executor(
                    None, self._upsert_supabase_cache, ticker, result,
                )

            _cache_set(cache_key, result)
            # Noted BEFORE set_result: a joiner reads it when it resumes.
            _note_degraded(cache_key, result, degraded)
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, NOT an Exception, so it skips the handler
            # below and used to leave this future unresolved forever — every joiner attached
            # via `await _inflight[...]` then hung for the life of the process. Reachable
            # whenever the LEADER is a cancellable caller: a report run hitting
            # RESEARCH_PIPELINE_TIMEOUT_SECONDS, or any pre-warm task cancelled at shutdown.
            # Hand waiters a normal exception so they fail fast through their own error path.
            fail_shared_future(future, RuntimeError("in-flight fetch was cancelled"))
            raise
        except Exception as e:
            fail_shared_future(future, e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[SnapshotItemResponse]:
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Financial Health")
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Health snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = dict(entry.get("response_json") or {})
            version = json_data.pop(_VERSION_KEY, 1)
            if version != _SNAPSHOT_PAYLOAD_VERSION:
                logger.info(
                    "Health snapshot payload v%s != v%s for %s — rebuilding",
                    version, _SNAPSHOT_PAYLOAD_VERSION, ticker,
                )
                return None
            return SnapshotItemResponse(**with_cached_build_time(json_data, cached_at))

        except Exception as e:
            logger.warning(
                "Health snapshot cache check failed for %s: %s: %s",
                ticker, type(e).__name__, e,
            )
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Financial Health",
                    "response_json": {**result.model_dump(),
                                      _VERSION_KEY: _SNAPSHOT_PAYLOAD_VERSION},
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(
                "Health snapshot upsert failed for %s: %s: %s",
                ticker, type(e).__name__, e,
            )

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """`_compute_with_status` without the degradation list."""
        snapshot, _degraded = await self._compute_with_status(ticker)
        return snapshot

    async def _compute_with_status(
        self, ticker: str,
    ) -> Tuple[SnapshotItemResponse, List[str]]:
        """Reuse HealthCheckService (Financials tab) + compute Altman Z-Score.

        Returns ``(snapshot, degraded)``; `get_health_snapshot` refuses to persist a build
        with a non-empty list. Degraded means: the health check RAISED (the local
        fallback then stands in, plus any of its own bs/income/profile legs that raised),
        the health check RETURNED a build it marked degraded (``health_check:<reason>``),
        the fallback's peer-benchmark lookup failed (``benchmarks``), or no metric in the
        card carries a value, which includes a health check that
        returned no metrics (the build it refuses to persist itself). A permanent
        `FMPNotEntitledException` is not degradation — that slice will not come back on
        retry.

        Income-statement fields (operatingIncome, interestExpense, revenue)
        are summed across the last 4 quarters to get TTM values. The balance
        sheet itself is a snapshot — the latest quarterly filing is used so
        ratios like Quick Ratio and Debt-to-Equity reflect current state
        rather than the prior fiscal year-end.
        """
        from app.services.health_check_service import get_health_check_service

        # Fetch health check + Z-Score data in parallel.
        # `bs` is a point-in-time figure — quarterly = latest available.
        # `inc` is a flow figure — summed over 4 quarters for TTM.
        health_task = get_health_check_service().get_health_check(ticker)
        bs_task = self.fmp.get_balance_sheet(ticker, period="quarter", limit=1)
        inc_task = self.fmp.get_income_statement(ticker, period="quarter", limit=4)
        profile_task = self.fmp.get_company_profile(ticker)

        results = await asyncio.gather(
            health_task, bs_task, inc_task, profile_task, return_exceptions=True,
        )

        degraded: List[str] = []
        for name, raw in zip(("health_check", "balance_sheet", "income", "profile"), results):
            if isinstance(raw, Exception) and not isinstance(raw, FMPNotEntitledException):
                degraded.append(name)
        if not isinstance(results[0], Exception):
            # The bs/income/profile legs only feed the local fallback below; with a
            # usable health check they change nothing on the card.
            degraded = []
            # ...but a health check that RETURNED can still be partial (one FMP leg
            # 429'd, the peer lookup failed). HealthCheckService serves that build from
            # memory and refuses to persist it; this card would otherwise freeze the
            # same partial verdict into snapshot_cache for 24h. The health check's flag
            # rides on its response, so a Tier-1 hit there carries it too. The report
            # reads this list through `get_health_snapshot_with_status`, which is what
            # keeps the partial card out of its snap_health and ticker_data_cache.
            degraded.extend(
                f"health_check:{reason}"
                for reason in (getattr(results[0], "degraded", None) or [])
            )

        health = results[0] if not isinstance(results[0], Exception) else None
        bs_raw = results[1] if not isinstance(results[1], Exception) else []
        inc_raw = results[2] if not isinstance(results[2], Exception) else []
        profile_raw = results[3] if not isinstance(results[3], Exception) else {}

        # Parse data
        bs = bs_raw[0] if isinstance(bs_raw, list) and bs_raw else {}
        inc = _sum_ttm_income(inc_raw) if isinstance(inc_raw, list) else {}
        profile = {}
        if isinstance(profile_raw, dict):
            profile = profile_raw
        elif isinstance(profile_raw, list) and profile_raw:
            profile = profile_raw[0]

        mcap = _safe_float(profile, "mktCap")
        if mcap is None:
            mcap = _safe_float(profile, "marketCap")

        # Build the snapshot metrics. The happy path reuses HealthCheckService's
        # metrics directly (it now emits D/E, P/E, ROE, Current Ratio, Altman Z,
        # Interest Coverage, Quick Ratio — see METRIC_DEFS in
        # health_check_service.py). P/E and ROE are hidden from the snapshot
        # because they live in the Valuation and Profitability cards. The
        # explicit-compute block below is the **fallback** for when the upstream
        # HealthCheckService call failed — without it we'd silently drop to a
        # single-metric "Financial Health: —" card.
        metrics: List[SnapshotMetricResponse] = []
        z_rating = 3

        if health is not None:
            for m in health.metrics:
                if m.type in _HIDE_FROM_SNAPSHOT:
                    continue
                name = _metric_name(m.type, m.value, m.comparison_value)
                value = _fmt_value(m.type, m.value)
                metrics.append(SnapshotMetricResponse(
                    name=name, value=value,
                    metric_key=_VERDICT_KEY.get(m.type),
                    score=_health_metric_score(m),
                    # The Health Check's own level for its `comparison_value` (absent on a
                    # build that predates the field → None, generic wording on iOS).
                    peer_level=_peer_level(
                        getattr(m, "peer_level", None), m.value, m.comparison_value,
                    ),
                ))

            # Z-Score for the rating blend
            z_val_from_hc = next(
                (m.value for m in health.metrics if m.type == "altman_z_score"), None
            )
            z_rating = _zscore_rating(z_val_from_hc)
        else:
            # ── Fallback path: compute D/E, CR, IC, QR + Z-Score locally ──
            logger.warning(
                f"HealthCheckService returned None for {ticker} — using local fallback"
            )
            from app.services.health_check_service import (
                _absolute_status,
                _bank_pooled_sector_cell,
                altman_z_applicable,
            )

            raw_sector = profile.get("sector", "")
            sector = _normalize_sector(raw_sector) if raw_sector else ""
            # Industry-relative: prefer INDUSTRY peers, fall back to sector per cell.
            industry = profile.get("industry", "") if isinstance(profile, dict) else ""
            # A listed non-lender member of a MIXED industry keeps the liquidity and coverage
            # rows (the same verdict as the Health Check: the curated list, vetoed only by
            # a trailing-four-quarter income that reads as a lender's — from the quarters
            # fetched above). Every other member, and a call with no ticker, is gated.
            network = resolve_payment_network(
                ticker, industry, trailing_interest_row(inc_raw),
                source="health_snapshot_fallback",
            )
            # The medians this card may compare at all (`comparable_peer_metrics`): none for
            # a listed non-lender member (every row on absolute bands), D/E alone for a bank
            # or a lender. Nothing to compare → no lookup, so a failed read cannot mark a
            # peer-free card degraded.
            fb_metrics = comparable_peer_metrics(
                ["interest_coverage", "quick_ratio", "debt_to_equity", "current_ratio"],
                industry, network=network,
            )
            sector_ic = sector_qr = sector_de = sector_cr = None
            fb_levels: Dict[str, Optional[str]] = {}
            if sector and not fb_metrics:
                logger.info(
                    "Health snapshot fallback %s: no metric is compared with a peer median "
                    "(industry=%r) — no benchmark lookup; every row on absolute bands",
                    ticker, industry,
                )
            elif sector:
                try:
                    # CURRENT benchmark per metric (`get_current_benchmarks`: mature
                    # industry TTM → mature sector TTM → newest complete mature annual →
                    # none), with the level of each cell for `peer_level`.
                    # Sync lookup (supabase-py + time.sleep retry): keep it off the loop.
                    cells = await asyncio.to_thread(
                        get_sector_benchmark_lookup().get_current_benchmarks,
                        industry,
                        sector,
                        fb_metrics,
                    )
                    cur, fb_levels = split_peer_cells(cells)
                    # A FAILED lookup (swallowed DB error) answers the same all-None
                    # shape as "this peer group has no rows" — but it is a transient
                    # hole, not an answer: every ratio falls back to absolute heuristics.
                    # Serve it; never persist it (same rule as the health check's own).
                    if lookup_failed(cells):
                        logger.warning(
                            "Health snapshot fallback: benchmark lookup FAILED for %s "
                            "(industry=%r, sector=%r) — scoring on absolute heuristics, "
                            "build marked degraded", ticker, industry, sector,
                        )
                        degraded.append("benchmarks")
                    # The Financial Services SECTOR median of a liquidity / coverage
                    # ratio is never a peer group — bank and insurer values, or, once the
                    # producer leaves those out, shells, exchanges and developers: no
                    # comparison (the Health Check's PERMANENT rule,
                    # `_bank_pooled_sector_cell`).
                    # Nor is ANY median of these ratios for a gated or mixed industry, nor
                    # any median at all for a listed non-lender member
                    # (`peer_median_comparable`; not even asked for above — this guard is
                    # the backstop for a lookup that answers more than it was asked).
                    for metric in (
                        "interest_coverage", "quick_ratio", "current_ratio", "debt_to_equity",
                    ):
                        if cur.get(metric) is None:
                            continue
                        if not peer_median_comparable(metric, industry, network=network):
                            logger.info(
                                "Health snapshot fallback %s: %s median %r (%s) is no peer "
                                "group for industry=%r — no comparison",
                                ticker, metric, cur.get(metric), fb_levels.get(metric),
                                industry,
                            )
                            cur = {**cur, metric: None}
                        elif _bank_pooled_sector_cell(metric, fb_levels.get(metric), sector):
                            logger.info(
                                "Health snapshot fallback %s: %s median %r is the Financial "
                                "Services SECTOR's (industry=%r) — not a peer group, no "
                                "comparison",
                                ticker, metric, cur.get(metric), industry,
                            )
                            cur = {**cur, metric: None}
                    # A non-positive median is no benchmark — `_fallback_sector_score`
                    # already refused to score on one, but the label printed it
                    # ("vs sector -0.50").
                    sector_ic, sector_qr, sector_de, sector_cr = (
                        v if v is not None and v > 0 else None
                        for v in (
                            cur.get("interest_coverage"), cur.get("quick_ratio"),
                            cur.get("debt_to_equity"), cur.get("current_ratio"),
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Health snapshot fallback: benchmark lookup raised for %s: %s: %s "
                        "— scoring on absolute heuristics, build marked degraded",
                        ticker, type(e).__name__, e,
                    )
                    degraded.append("benchmarks")

            # Z-Score from balance sheet + TTM income + market cap. Omitted outright for a
            # bank / insurer / REIT, exactly as the health check omits it, so the two
            # paths of this card show the same rows for the same company.
            z_score = _compute_z_score(bs, inc, mcap, sector=raw_sector, industry=industry)
            if altman_z_applicable(raw_sector, industry):
                z_value = f"{z_score}" if z_score is not None else "—"
                metrics.append(SnapshotMetricResponse(
                    name="Altman Z-Score", value=z_value,
                    metric_key="altman_z",
                    # An "—" row scores nothing (`_zscore_rating(None)` is the neutral
                    # blend anchor, not a reading of this company).
                    score=_zscore_rating(z_score) if z_score is not None else None,
                ))
            z_rating = _zscore_rating(z_score)

            def _row_score(
                metric: str, value: Optional[float], median: Optional[float],
                *, lower_is_better: bool = False,
            ) -> Optional[int]:
                """The fallback's 4 / 2 score against a peer median — or, for a row that is
                never compared (`peer_median_comparable` False: every row of a listed
                non-lender member), the Health Check's own ABSOLUTE band, so both paths
                judge it alike."""
                if peer_median_comparable(metric, industry, network=network):
                    return _fallback_sector_score(
                        value, median, lower_is_better=lower_is_better,
                    )
                if value is None:
                    return None
                return _status_score(_absolute_status(metric, value))

            # Debt-to-Equity = total debt / shareholders' equity
            total_debt = _safe_float(bs, "totalDebt")
            equity = (
                _safe_float(bs, "totalStockholdersEquity")
                or _safe_float(bs, "totalEquity")
            )
            de = None
            if total_debt is not None and equity is not None and equity > 0:
                de = round(total_debt / equity, 2)
            metrics.append(SnapshotMetricResponse(
                name=_metric_name("debt_to_equity", de, sector_de),
                value=_fmt_value("debt_to_equity", de),
                metric_key="debt_to_equity",
                peer_level=_peer_level(fb_levels.get("debt_to_equity"), de, sector_de),
                score=_row_score(DEBT_TO_EQUITY, de, sector_de, lower_is_better=True),
            ))

            # Current ratio, interest coverage and quick ratio are omitted for a bank /
            # insurer / capital-markets firm / lender, and current + quick ratio only for an
            # insurance broker, exactly as the health check omits them
            # (`financials_metric_gate`), so the two paths of this card show the same rows;
            # a mixed-industry company keeps them only when it is a listed non-lender member
            # (`network`), and a curated per-company fact withholds them in any industry
            # (`CURATED_WITHHELD_ROWS`: WU — logged once here). Omitted values stay None, so
            # they are not scored in pass_rating below either.
            curated_withheld = resolve_withheld_company_rows(
                ticker, source="health_snapshot_fallback",
            )
            liquidity_ok = company_metric_applicable(
                CURRENT_RATIO, industry, network=network, ticker=ticker,
            )
            coverage_ok = company_metric_applicable(
                INTEREST_COVERAGE, industry, network=network, ticker=ticker,
            )
            quick_ok = company_metric_applicable(
                QUICK_RATIO, industry, network=network, ticker=ticker,
            )

            industry_omitted = [
                name for name, key, ok in (
                    ("current ratio", CURRENT_RATIO, liquidity_ok),
                    ("interest coverage", INTEREST_COVERAGE, coverage_ok),
                    ("quick ratio", QUICK_RATIO, quick_ok),
                ) if not ok and key not in curated_withheld
            ]
            if industry_omitted:
                logger.info(
                    "Health snapshot fallback %s: %s omitted — not meaningful for "
                    "industry=%r", ticker, ", ".join(industry_omitted), industry,
                )

            # Current Ratio = total current assets / total current liabilities
            curr_assets = _safe_float(bs, "totalCurrentAssets")
            curr_liab = _safe_float(bs, "totalCurrentLiabilities")
            cr = None
            if (
                liquidity_ok and curr_assets is not None and curr_liab is not None
                and curr_liab > 0
            ):
                cr = round(curr_assets / curr_liab, 2)
            if liquidity_ok:
                metrics.append(SnapshotMetricResponse(
                    name=_metric_name("current_ratio", cr, sector_cr),
                    value=_fmt_value("current_ratio", cr),
                    metric_key="current_ratio",
                    peer_level=_peer_level(fb_levels.get("current_ratio"), cr, sector_cr),
                    score=_row_score("current_ratio", cr, sector_cr),
                ))

            # Interest Coverage = EBIT / |Interest Expense|. interestExpense
            # is reported as a positive number on the income statement.
            op_income = _safe_float(inc, "operatingIncome")
            int_expense = _safe_float(inc, "interestExpense")
            ic = None
            if (
                coverage_ok and op_income is not None and int_expense is not None
                and abs(int_expense) > 0
            ):
                ic = round(op_income / abs(int_expense), 2)
            if coverage_ok:
                metrics.append(SnapshotMetricResponse(
                    name=_metric_name("interest_coverage", ic, sector_ic),
                    value=_fmt_value("interest_coverage", ic),
                    metric_key="interest_coverage",
                    peer_level=_peer_level(fb_levels.get("interest_coverage"), ic, sector_ic),
                    score=_row_score("interest_coverage", ic, sector_ic),
                ))

            # Quick Ratio = (cash + receivables) / current liabilities
            cash = _safe_float(bs, "cashAndCashEquivalents")
            receivables = _safe_float(bs, "netReceivables")
            qr = None
            if quick_ok and curr_liab is not None and curr_liab > 0:
                qr_numerator = (cash or 0) + (receivables or 0)
                if qr_numerator > 0:
                    qr = round(qr_numerator / curr_liab, 2)
            if quick_ok:
                metrics.append(SnapshotMetricResponse(
                    name=_metric_name("quick_ratio", qr, sector_qr),
                    value=_fmt_value("quick_ratio", qr),
                    metric_key="quick_ratio",
                    peer_level=_peer_level(fb_levels.get("quick_ratio"), qr, sector_qr),
                    score=_row_score("quick_ratio", qr, sector_qr),
                ))

        # ── Rating blend: 40% Altman Z + 60% pass-rate over the 4 sector-
        # comparable metrics (D/E, Current Ratio, Interest Coverage, Quick
        # Ratio). Z-Score keeps its anchor weight because Altman thresholds
        # are calibrated to bankruptcy risk, not sector-relative comparisons.
        pass_rating = 3
        if health is not None:
            positives = sum(
                1 for m in health.metrics
                if m.type in _SECTOR_RATING_TYPES and m.status == "positive"
            )
            # Neutral = in line with peers, HALF credit — the same rule the Financials
            # tab's own rating uses (health_check_service Phase 5). Counting only strict
            # passes made four in-line ratios score the same pass_rating 1 as four
            # failures, so one company read "Mix" on the tab and 2/5 in its report.
            neutrals = sum(
                1 for m in health.metrics
                if m.type in _SECTOR_RATING_TYPES and m.status == "neutral"
            )
            total_sector = sum(
                1 for m in health.metrics if m.type in _SECTOR_RATING_TYPES
            )
            if total_sector > 0:
                ratio = (positives + 0.5 * neutrals) / total_sector
                if ratio >= 1.0:
                    pass_rating = 5
                elif ratio >= 0.75:
                    pass_rating = 4
                elif ratio >= 0.5:
                    pass_rating = 3
                elif ratio >= 0.25:
                    pass_rating = 2
                else:
                    pass_rating = 1
        else:
            # Fallback path ran → derive pass_rating from the 4 sector-comparable
            # ratios we computed locally. Previously pass_rating stayed the constant
            # 3, so a leveraged/illiquid company's D/E, CR, IC, QR did not move the
            # overall Financial Health rating at all (it was driven by Altman Z alone).
            _fb = [
                _row_score(DEBT_TO_EQUITY, de, sector_de, lower_is_better=True),
                _row_score("current_ratio", cr, sector_cr),
                _row_score("interest_coverage", ic, sector_ic),
                _row_score("quick_ratio", qr, sector_qr),
            ]
            _fb = [s for s in _fb if s is not None]
            if _fb:
                pass_rating = round(sum(_fb) / len(_fb))

        weighted: Optional[float] = 0.4 * z_rating + 0.6 * pass_rating
        rating = max(1, min(5, round(weighted)))
        # Fewer than two scored rows: no card-level verdict (`_MIN_SCORED_FOR_RATING`).
        scored_rows = [
            m for m in metrics
            if m.score is not None and m.value not in (None, "", "—", "N/M")
        ]
        if len(scored_rows) < _MIN_SCORED_FOR_RATING:
            logger.info(
                "Health snapshot %s: %d scored row(s) (%s) — card rated 0 (unavailable), "
                "no weighted_score; one comparison is not a health verdict",
                ticker, len(scored_rows),
                ", ".join(m.metric_key or m.name for m in scored_rows) or "none",
            )
            weighted = None
            rating = 0

        # Also covers a health check that returned NO metrics, the case HealthCheckService
        # itself refuses to persist: the card then has nothing but the placeholder below.
        if not any(m.value not in (None, "", "—") for m in metrics):
            degraded.append("no_values")
        if not metrics:
            metrics.append(SnapshotMetricResponse(name="Financial Health", value="—"))

        snapshot = SnapshotItemResponse(
            category="Financial Health",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3) if weighted is not None else None,
            computed_at=snapshot_build_time(),
        )
        return snapshot, degraded


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[HealthSnapshotService] = None


def get_health_snapshot_service() -> HealthSnapshotService:
    global _service
    if _service is None:
        _service = HealthSnapshotService()
    return _service
