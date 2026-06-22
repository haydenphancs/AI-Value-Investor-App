"""
Ticker Report cache-aside helper (24h TTL, Supabase-backed).

Used by both:
  - GET /stocks/{ticker}/report (direct path, TickerReportService)
  - POST /research/generate (deep-research path, ResearchService writes
    successful agent output here so direct-path users benefit from
    the agentic loop that the iOS Reports flow paid for)

Cache key: (ticker, persona) — same TickerReportResponse JSONB, same
shape Swift decodes. When the row is older than CACHE_TTL_HOURS, the
read returns None and the caller regenerates.

All Supabase calls run via asyncio.to_thread to avoid blocking the
event loop. Read/write failures NEVER raise — they log and return
None / no-op so a transient DB blip cannot break a report request.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.database import get_supabase

logger = logging.getLogger(__name__)


CACHE_TTL_HOURS = 24
TABLE_NAME = "ticker_report_cache"

# Schema-floor: any row cached strictly before this UTC timestamp is treated
# as stale regardless of its 24h TTL. Bump this whenever the report payload
# shape changes (new fields, semantic flips, filter rules) so users on the
# 24h cache don't see structurally outdated entries.
#
# 2026-05-02 — Deep Dive Modules backend hydration:
#   * `revenue_forecast.projections[*].is_forecast` now always True
#   * `wall_street_consensus.hedge_fund_price_data[-1].price` pinned to current_price
#     (`hedge_fund_*` = FMP 13F institutional data; UI label "Institutions")
#   * `current_price` / target prices retain cents (was rounded to whole dollars)
#   * `insider_data` filtered to Informative trades only (parity with Holders tab)
#   * `price_action.prices` no longer falls back to a synthetic flat line
# 2026-05-02 (Phase 2 PR 1) — Recent Price Movement catalyst grounding:
#   * `price_action.event` may now be a news catalyst (FDA Approval, M&A,
#     Buyback, etc.) when its absolute price move beats the earnings event
#   * Stage B price-action narrative now grounds in real headlines
# 2026-05-02 (Phase 2 PR 2) — Industry & Moat real-data hydration:
#   * `moat_competition.competitors` now real FMP peers (was AI-invented)
#   * `moat_competition.market_dynamics.concentration` from sector HHI / top-N
#   * `moat_competition.market_dynamics.cagr_5yr` from S&P 500 sector revenue
#   * `moat_competition.market_dynamics.lifecycle_phase` derived from CAGR
# 2026-05-02 (Phase 2 PR 3) — TAM extraction from earnings transcripts:
#   * `moat_competition.market_dynamics.current_tam` / `future_tam` now
#     populated from AI extraction of FMP earnings-call transcripts
#     (only when the transcript explicitly quotes a TAM figure;
#     otherwise stays 0 with no fabrication)
#   * New `moat_competition.market_dynamics.tam_source_quote` field for
#     verbatim attribution
# 2026-05-02 (Phase 2 PR 4) — Macro commodities/FX/VIX/rates from FMP:
#   * `macro_data.risk_factors` now merges deterministic numeric factors
#     (Oil/Gold/Copper/VIX/10Y/DXY) from FMP with AI's qualitative
#     geopolitical factors. Deterministic wins on category collision.
# 2026-05-02 (Phase 2 PR 5) — Macro FRED indicators (CPI/Fed/Treasury):
#   * `macro_data.risk_factors` now also pulls deterministic factors
#     from FRED (CPIAUCSL / FEDFUNDS / DGS10 / T10Y2Y). FRED-derived
#     factors win over FMP-derived AND AI-generated ones in the same
#     category (real BLS/Treasury data is the most authoritative).
# 2026-05-02 (Phase 2 PR 6) — Forecast guidance from earnings transcript:
#   * `revenue_forecast.management_guidance` only escalates to
#     "raised"/"lowered" when AI extracted a verbatim quote from the
#     transcript; otherwise coerces to "maintained" (anti-fabrication).
#   * New `revenue_forecast.guidance_speaker` ("CFO" / "CEO" / "IR")
#     and `revenue_forecast.guidance_period` ("Q4 2025" / "FY 2026")
#     for iOS attribution.
# 2026-05-27 — FMP /profile + /ratios + /ratios-ttm field renames and
# PatentsView → USPTO ODP migration:
#   * `moat_competition.competitors` was empty for every ticker because
#     FMP renamed `mktCap` → `marketCap`; aliased back in fmp.py.
#   * Each pillar now carries `source ∈ {deterministic, grounded, ai_legacy}`
#     (new field on MoatDimensionResponse).
#   * Peer competitor scoring restored: /ratios-ttm renamed
#     `operatingProfitMargin` → `operatingProfitMarginTTM` and moved
#     `returnOnEquity` to /key-metrics-ttm, `revenueGrowth` to
#     /financial-growth. All three now fanned out per peer.
#   * Focal ratios block now handles FMP renames of priceEarningsRatio,
#     enterpriseValueOverEBITDA, debtEquityRatio, interestCoverage, and
#     reads returnOnEquity/Assets from /key-metrics fallback.
#   * Intangible Assets pillar's `patents_per_employee` driver now
#     resolves via the USPTO Open Data Portal API (search.patentsview.org
#     was decommissioned). Alias map at data/uspto_assignee_aliases.json
#     covers known subsidiary mismatches (MRNA → ModernaTX, GOOG → Google,
#     plus 16 more from the top-500 bulk scan).
# 2026-05-27 — Industry moat peer-average overlay:
#   * `moat_competition.dimensions[*].peer_score` is now sourced from
#     the new industry_moat_benchmarks table (migration 057) — one
#     row per (industry, pillar). Previously every pillar carried a
#     hardcoded 5.0 sector-median anchor regardless of industry, so
#     the iOS Moat radar's gray "Peer Avg" pentagon was a flat shape
#     on every ticker. Industries without a benchmark row yet (new
#     ticker / pre-bootstrap state) still fall through to the 5.0
#     baseline via `_apply_peer_score_baseline`.
#
# 2026-06-07: bumped to today so reports cached after the moat change but
#     before the Hidden Market Signals 12-month short-interest `history`
#     series re-assemble and surface the new trend chart.
# 2026-06-10: bumped again so cached reports re-assemble with the recent
#     additions — the 12-month insider window + 3-topic Insider & Management
#     Insight, the Capital Allocation net-share-count verdict, fiscal-year
#     quarter labels, and the Future Forecast `annual_timeline` (Earnings
#     Timeline view).
# 2026-06-11: bumped so the Earnings Timeline rows carry per-year analyst
#     coverage (revenue_analyst_count / eps_analyst_count) for the new
#     tap-to-inspect popup.
# 2026-06-13: bumped so the Future Forecast price overlay re-assembles at WEEKLY
#     granularity (timeline_prices: monthly → last close per ISO week) for a
#     smoother, more detailed price line.
# 2026-06-20: bumped so reports re-assemble with COMPLETE Fundamentals & Growth
#     sector-average history. Two fixes shipped: earnings_yield is now a populated
#     (computed) benchmark, and sector_benchmark_lookup now paginates past the
#     ~1000-row Supabase cap (a 14-metric × ~84-quarter query was truncating, so
#     whole metrics' quarterly sector lines silently vanished). Existing reports
#     baked the truncated data — invalidate them so they re-collect cleanly.
# 2026-06-21: bumped so reports/collections re-collect and bake the new rich
#     `growth_chart` payload (the paid Growth card now matches the free
#     TickerDetailView chart: absolute bars + YoY% + sector overlay, 5 metrics).
#     A field added to CollectedTickerData isn't present in older cached
#     collections, so the floor invalidates them → fresh fetch populates it.
CACHE_SCHEMA_FLOOR = datetime(2026, 6, 21, 20, 0, 0, tzinfo=timezone.utc)


# ── Close-aligned cache freshness ───────────────────────────────────
# Reports pin to the LAST COMPLETED market close, so a cached entry is fresh
# only until the NEXT close has settled — not a rolling wall-clock TTL (which
# would freeze "the last close as of first generation" and miss a close that
# lands mid-window). We treat a close as "settled" at a weekday 6pm ET (4pm ET
# close + a buffer so FMP's EOD bar is reliably available). The first viewer
# after that boundary regenerates with the new close; everyone that session
# shares it. Used by ticker_report_cache, ticker_data_cache, and
# research_service._lookup_shared_cache so all three layers refresh together.
_CLOSE_REFRESH_HOUR_ET = 18

try:
    _ET: Optional[ZoneInfo] = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - slim image without the tz database
    logger.warning(
        "tzdata/America/New_York unavailable — close-cycle cache boundary will "
        "use a DST-aware fixed-offset approximation (exact except within the "
        "twice-a-year transition hour). Add `tzdata` for full correctness."
    )
    _ET = None


def _us_eastern_is_dst(dt_utc: datetime) -> bool:
    """Approximate whether `dt_utc` falls in US Eastern Daylight Time.

    Only used for the fixed-offset fallback when tzdata is unavailable. US
    DST (post-2007): 2nd Sunday of March 07:00 UTC (02:00 EST) → 1st Sunday
    of November 06:00 UTC (02:00 EDT). Correct except within the transition
    hour itself, where the cache boundary may be ≤1h off twice a year.
    """
    year = dt_utc.year
    march1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    first_sun_mar = march1 + timedelta(days=(6 - march1.weekday()) % 7)
    dst_start = first_sun_mar + timedelta(days=7, hours=7)  # 2nd Sun, 07:00 UTC
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    first_sun_nov = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = first_sun_nov + timedelta(hours=6)  # 1st Sun, 06:00 UTC
    return dst_start <= dt_utc < dst_end


def _eastern_fallback_tz(now_utc: datetime) -> timezone:
    """DST-aware fixed offset (EDT=-4 / EST=-5) for the no-tzdata fallback."""
    offset = -4 if _us_eastern_is_dst(now_utc) else -5
    return timezone(timedelta(hours=offset))


def current_close_cycle_start(now: Optional[datetime] = None) -> datetime:
    """The most recent weekday 6pm ET at or before `now`, as a UTC-aware
    datetime. A cache row written before this is stale (a newer close has
    settled since). `now` is injectable for tests."""
    now = now or datetime.now(timezone.utc)
    # Prefer the real tz database; fall back to a DST-aware fixed offset only
    # when tzdata is missing from the deploy image.
    tz = _ET or _eastern_fallback_tz(now)
    local = now.astimezone(tz)
    boundary = local.replace(
        hour=_CLOSE_REFRESH_HOUR_ET, minute=0, second=0, microsecond=0
    )
    if boundary > local:
        boundary -= timedelta(days=1)
    while boundary.weekday() >= 5:  # Sat(5)/Sun(6) → step back to Friday's close
        boundary -= timedelta(days=1)
    return boundary.astimezone(timezone.utc)


def is_cache_fresh(cached_at: datetime, now: Optional[datetime] = None) -> bool:
    """True if a row cached at `cached_at` is still fresh: on/after the schema
    floor AND within the current trading-close cycle (written after the most
    recent settled close). Replaces the old rolling-TTL check."""
    if cached_at < CACHE_SCHEMA_FLOOR:
        return False
    return cached_at >= current_close_cycle_start(now)


def _short_interest_payload_stale(report: Any) -> bool:
    """A cached report whose short-interest signal has a 3-month change but an
    EMPTY `history` is a pre-feature artifact: current code always builds the
    12-month settlement series whenever `change_3m` exists (it needs >=2 FINRA
    rows ~90 days apart → >=2 history points). Treat such a report as stale so
    it regenerates and the trend chart fills — robust to entries cached AFTER
    the date floor by not-yet-redeployed code. Nasdaq/Yahoo-only tickers have
    change_3m=None and are never flagged, so this cannot loop once the backend
    runs the history-building code. Mirrors
    `finra_short_interest._is_stale_finra_snapshot` at the report layer."""
    try:
        si = ((report or {}).get("hidden_market_signals") or {}).get("short_interest") or {}
        return si.get("change_3m") is not None and not (si.get("history") or [])
    except Exception:
        return False


def _legacy_tier_from_change(pct: float) -> str:
    """Map |%| change to the 4-tier vocabulary for legacy entries that
    pre-date the σ-based classifier. No σ is stored on these payloads, so
    we approximate from magnitude alone. Bands chosen to mirror the live
    breakpoints for an average-volatility stock (~1.5% daily σ over a
    30-day window → 2σ ≈ ±8%)."""
    a = abs(pct)
    if a >= 15.0:
        return "Extreme"
    if a >= 7.0:
        return "Unusual"
    if a >= 2.0:
        return "Notable"
    return "Typical"


def patch_legacy_price_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill the ground-truth fields onto a `price_action` block stored
    before change_pct / direction / window_label / tag were added.

    Reports stored in `research_reports.ticker_report_data` are not
    invalidated by CACHE_SCHEMA_FLOOR — they're the user's history. We
    patch them on read so iOS's strict decoder doesn't crash. The math
    mirrors `_build_price_action` in the data collector exactly.
    """
    if not isinstance(payload, dict):
        return payload

    # Key Vitals is deleted from the product: strip the internal scoring field
    # so it never reaches iOS. It's retained in stored JSONB as the persona-
    # rating input (see compute_quality_score / _scoring_inputs) but is NOT part
    # of the client contract. This read chokepoint covers the two raw-return
    # endpoints; the schema-validated return path drops it via model_dump.
    # Pop both names: new reports use "_scoring_inputs"; older stored reports
    # still carry the legacy "key_vitals".
    payload.pop("_scoring_inputs", None)
    payload.pop("key_vitals", None)

    pa = payload.get("price_action")
    if not isinstance(pa, dict):
        return payload
    if all(k in pa for k in ("change_pct", "direction", "window_label", "tag")):
        return payload

    prices = pa.get("prices") or []
    current_price = pa.get("current_price") or 0.0
    event = pa.get("event") or {}

    change_pct = 0.0
    if prices:
        idx = event.get("index", -1) if isinstance(event, dict) else -1
        if event and isinstance(idx, int) and 0 <= idx < len(prices):
            ref = prices[idx]
        else:
            ref = prices[0]
        if ref:
            change_pct = (current_price - ref) / ref * 100
    change_pct = round(change_pct, 1)

    if abs(change_pct) < 1.0:
        direction = "flat"
    elif change_pct > 0:
        direction = "up"
    else:
        direction = "down"

    legacy_tier = _legacy_tier_from_change(change_pct)
    if event:
        window_label = f"Since {event.get('date', '')}".strip()
        tag = event.get("tag") or legacy_tier
    else:
        window_label = "Last 30 Days"
        tag = legacy_tier

    pa.setdefault("change_pct", change_pct)
    pa.setdefault("direction", direction)
    pa.setdefault("window_label", window_label)
    pa.setdefault("tag", tag)
    pa.setdefault("tier", legacy_tier)
    payload["price_action"] = pa
    return payload


def _normalize_key(ticker: str, persona: str) -> tuple[str, str]:
    return ticker.upper().strip(), persona.lower().strip()


async def get_cached_report(
    ticker: str, persona: str
) -> Optional[Dict[str, Any]]:
    """Return the cached ticker_report_data JSONB if fresh (< 24h), else None.

    On any DB error, logs the underlying type+message and returns None so the
    caller falls through to regeneration. The error is intentionally swallowed
    here because cache misses are recoverable; cache lookups must never break
    the request path.
    """
    ticker, persona = _normalize_key(ticker, persona)

    def _query() -> Optional[Dict[str, Any]]:
        try:
            supabase = get_supabase()
            row = (
                supabase.table(TABLE_NAME)
                .select("ticker_report_data, cached_at")
                .eq("ticker", ticker)
                .eq("persona", persona)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(
                cached_at_str.replace("Z", "+00:00")
            )
            # Close-aligned freshness (not a rolling TTL): stale once a newer
            # market close has settled, so the first post-close viewer regenerates.
            if not is_cache_fresh(cached_at):
                logger.info(
                    f"ticker_report_cache STALE/PRE-FLOOR for {ticker}/{persona} "
                    f"(cached_at={cached_at.isoformat()}, "
                    f"cycle_start={current_close_cycle_start().isoformat()})"
                )
                return None

            data = entry.get("ticker_report_data")
            if not isinstance(data, dict):
                return None
            if _short_interest_payload_stale(data):
                logger.info(
                    f"ticker_report_cache SHORT-INTEREST-STALE for "
                    f"{ticker}/{persona} (change_3m present, history empty) — "
                    f"regenerating so the 12-month chart fills"
                )
                return None
            return data
        except Exception as e:
            logger.warning(
                f"ticker_report_cache read failed for {ticker}/{persona}: "
                f"{type(e).__name__}: {e}"
            )
            return None

    return await asyncio.to_thread(_query)


async def upsert_cached_report(
    ticker: str, persona: str, ticker_report_data: Dict[str, Any]
) -> None:
    """Write or refresh the cache row for (ticker, persona).

    Fire-and-forget: failures are logged but never raised. Callers can
    `await` this for sequencing but it should never block the response.
    """
    ticker, persona = _normalize_key(ticker, persona)

    def _upsert() -> None:
        try:
            supabase = get_supabase()
            supabase.table(TABLE_NAME).upsert(
                {
                    "ticker": ticker,
                    "persona": persona,
                    "ticker_report_data": ticker_report_data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,persona",
            ).execute()
            logger.info(
                f"ticker_report_cache UPSERTED for {ticker}/{persona}"
            )
        except Exception as e:
            logger.warning(
                f"ticker_report_cache upsert failed for {ticker}/{persona}: "
                f"{type(e).__name__}: {e}"
            )

    await asyncio.to_thread(_upsert)
