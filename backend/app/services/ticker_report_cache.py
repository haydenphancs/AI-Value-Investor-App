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
