"""
Social Mentions Service — manages ApeWisdom data for sentiment analysis.

Provides:
  1. Daily snapshots: fetches all Reddit mention data and stores in Supabase
  2. 24h lookups: fast in-memory cache from ApeWisdom API
  3. 7d lookups: queries accumulated daily snapshots from Supabase
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.database import get_supabase
from app.integrations.apewisdom import (
    get_all_mentions,
    get_ticker_mentions,
    is_cache_populated,
)

logger = logging.getLogger(__name__)


class SocialMentionsService:

    def __init__(self):
        self.supabase = get_supabase()

    # ── Daily snapshot (called by scheduled task) ─────────────────

    async def snapshot_all(self) -> int:
        """
        Fetch all mention data from ApeWisdom and store in DB.

        Called once per UTC day by `main._run_social_snapshot_loop` (it had NO caller from
        2025 until 2026-09-11 — the table stayed empty and every 7-day count was 0). The
        upsert is idempotent on (ticker, snapshot_date, source), so a retry within the day
        rewrites the same rows. Rate-limit-safe — the ApeWisdom client handles page delays
        internally. Supabase calls run off the event loop.

        Returns number of tickers stored.
        """
        all_data = await get_all_mentions()

        if not all_data:
            logger.warning("ApeWisdom returned no data for snapshot")
            return 0

        today = date.today().isoformat()
        rows = []

        for ticker, data in all_data.items():
            rows.append({
                "ticker": ticker,
                "mentions": data.get("mentions", 0),
                "upvotes": data.get("upvotes", 0),
                "rank": data.get("rank"),
                "source": "apewisdom",
                "snapshot_date": today,
            })

        # Batch upsert in chunks to avoid payload limits
        chunk_size = 500
        total_upserted = 0

        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            try:
                await asyncio.to_thread(
                    lambda c=chunk: self.supabase.table("social_mentions_history").upsert(
                        c, on_conflict="ticker,snapshot_date,source",
                    ).execute()
                )
                total_upserted += len(chunk)
            except Exception as e:
                logger.error(
                    f"Snapshot upsert failed for chunk {i}: {e}"
                )

        logger.info(
            f"Social mentions snapshot complete: "
            f"{total_upserted}/{len(rows)} tickers stored for {today}"
        )

        # Cleanup old data (>30 days)
        try:
            cutoff = (date.today() - timedelta(days=30)).isoformat()
            await asyncio.to_thread(
                lambda: self.supabase.table("social_mentions_history").delete().lt(
                    "snapshot_date", cutoff
                ).execute()
            )
        except Exception as e:
            logger.warning(f"Social mentions cleanup failed: {e}")

        return total_upserted

    # ── 24h lookups (fast, from ApeWisdom cache) ──────────────────

    async def get_mentions_24h(
        self, ticker: str
    ) -> Tuple[int, int, bool]:
        """
        Get 24h mention counts for a ticker.

        Returns (current_mentions, previous_24h_mentions, known).

        `known` is False when the answer could not be LOOKED UP — the ApeWisdom cache is
        still cold AND the DB fallback failed or is empty — as opposed to "looked up and
        Reddit is not talking about it", which is a real 0. Both used to come back as
        (0, 0), and the response published the fabricated zero as a measured count.
        Uses ApeWisdom in-memory cache (fast); falls back to the latest DB row, off the
        event loop.
        """
        ticker = ticker.upper()

        # Try ApeWisdom cache first
        data = await get_ticker_mentions(ticker)
        if data is not None:
            return data["mentions"], data["mentions_24h_ago"], True
        cache_consulted = is_cache_populated()

        # Fallback: latest DB row
        try:
            result = await asyncio.to_thread(
                lambda: self.supabase.table("social_mentions_history")
                .select("mentions")
                .eq("ticker", ticker)
                .order("snapshot_date", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                mentions = result.data[0].get("mentions", 0)
                return mentions, 0, True  # No previous data from single row
        except Exception as e:
            logger.warning(
                f"DB fallback for 24h mentions failed for {ticker}: {e}"
            )
            return 0, 0, False

        # Nothing anywhere: a real "not tracked" only if ApeWisdom was actually consulted.
        return 0, 0, cache_consulted

    # ── 7d lookups (from DB history) ──────────────────────────────

    async def get_mentions_7d(
        self, ticker: str
    ) -> Tuple[int, int, bool]:
        """
        Get 7-day mention counts for a ticker.

        Returns (current_7d_total, previous_7d_total, known).
        Queries accumulated daily snapshots from Supabase, off the event loop (two sync
        PostgREST round-trips used to run INSIDE the request coroutine).
        `known` is False only when the query FAILED (the 42501 of 2026-09-11 answered
        every ticker "0 mentions this week" for months); a successful empty query — the
        first-week warm-up, a ticker nobody mentions — is a real (0, 0, True).
        """
        ticker = ticker.upper()

        try:
            today = date.today()
            week_ago = (today - timedelta(days=7)).isoformat()
            two_weeks_ago = (today - timedelta(days=14)).isoformat()

            def _query():
                cur = (
                    self.supabase.table("social_mentions_history")
                    .select("mentions")
                    .eq("ticker", ticker)
                    .gte("snapshot_date", week_ago)
                    .execute()
                )
                prev = (
                    self.supabase.table("social_mentions_history")
                    .select("mentions")
                    .eq("ticker", ticker)
                    .gte("snapshot_date", two_weeks_ago)
                    .lt("snapshot_date", week_ago)
                    .execute()
                )
                return cur, prev

            cur_result, prev_result = await asyncio.to_thread(_query)
            current_total = sum(
                r.get("mentions", 0) for r in (cur_result.data or [])
            )
            previous_total = sum(
                r.get("mentions", 0) for r in (prev_result.data or [])
            )
            return current_total, previous_total, True

        except Exception as e:
            logger.warning(
                f"7d mentions query failed for {ticker}: {type(e).__name__}: {e}"
            )
            return 0, 0, False


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[SocialMentionsService] = None


def get_social_mentions_service() -> SocialMentionsService:
    global _service
    if _service is None:
        _service = SocialMentionsService()
    return _service
