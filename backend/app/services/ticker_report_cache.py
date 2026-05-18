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
CACHE_SCHEMA_FLOOR = datetime(2026, 5, 18, 23, 0, 0, tzinfo=timezone.utc)


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

    if event:
        window_label = f"Since {event.get('date', '')}".strip()
        tag = event.get("tag") or "Normal"
    else:
        window_label = "Last 30 Days"
        if abs(change_pct) > 10:
            tag = "Momentum" if direction == "up" else "Correction"
        else:
            tag = "Normal"

    pa.setdefault("change_pct", change_pct)
    pa.setdefault("direction", direction)
    pa.setdefault("window_label", window_label)
    pa.setdefault("tag", tag)
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
            if cached_at < CACHE_SCHEMA_FLOOR:
                logger.info(
                    f"ticker_report_cache PRE-FLOOR for {ticker}/{persona} "
                    f"(cached_at={cached_at.isoformat()} < "
                    f"floor={CACHE_SCHEMA_FLOOR.isoformat()})"
                )
                return None
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=CACHE_TTL_HOURS):
                logger.info(
                    f"ticker_report_cache STALE for {ticker}/{persona} "
                    f"(age={age.total_seconds() / 3600:.1f}h)"
                )
                return None

            data = entry.get("ticker_report_data")
            if not isinstance(data, dict):
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
