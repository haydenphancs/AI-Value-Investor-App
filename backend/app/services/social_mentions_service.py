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
)

logger = logging.getLogger(__name__)


class SocialMentionsService:

    def __init__(self):
        self.supabase = get_supabase()

    # ── Daily snapshot (called by scheduled task) ─────────────────

    async def snapshot_all(self) -> int:
        """
        Fetch all mention data from ApeWisdom and store in DB.

        Should be called once every 24h. Rate-limit-safe — the
        ApeWisdom client handles page delays internally.

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
                self.supabase.table("social_mentions_history").upsert(
                    chunk,
                    on_conflict="ticker,snapshot_date,source",
                ).execute()
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
            self.supabase.table("social_mentions_history").delete().lt(
                "snapshot_date", cutoff
            ).execute()
        except Exception as e:
            logger.warning(f"Social mentions cleanup failed: {e}")

        return total_upserted

    # ── 24h lookups (fast, from ApeWisdom cache) ──────────────────

    async def get_mentions_24h(
        self, ticker: str
    ) -> Tuple[int, int]:
        """
        Get 24h mention counts for a ticker.

        Returns (current_mentions, previous_24h_mentions).
        Uses ApeWisdom in-memory cache (fast).
        Falls back to latest DB row if cache is empty.
        """
        ticker = ticker.upper()

        # Try ApeWisdom cache first
        data = await get_ticker_mentions(ticker)
        if data is not None:
            return data["mentions"], data["mentions_24h_ago"]

        # Fallback: latest DB row
        try:
            result = (
                self.supabase.table("social_mentions_history")
                .select("mentions")
                .eq("ticker", ticker)
                .order("snapshot_date", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                mentions = result.data[0].get("mentions", 0)
                return mentions, 0  # No previous data from single row
        except Exception as e:
            logger.warning(
                f"DB fallback for 24h mentions failed for {ticker}: {e}"
            )

        return 0, 0

    # ── 7d lookups (from DB history) ──────────────────────────────

    async def get_mentions_7d(
        self, ticker: str
    ) -> Tuple[int, int]:
        """
        Get 7-day mention counts for a ticker.

        Returns (current_7d_total, previous_7d_total).
        Queries accumulated daily snapshots from Supabase.
        Returns (0, 0) during warmup period (first 7 days).
        """
        ticker = ticker.upper()

        try:
            today = date.today()
            week_ago = (today - timedelta(days=7)).isoformat()
            two_weeks_ago = (today - timedelta(days=14)).isoformat()

            # Current 7 days
            cur_result = (
                self.supabase.table("social_mentions_history")
                .select("mentions")
                .eq("ticker", ticker)
                .gte("snapshot_date", week_ago)
                .execute()
            )
            current_total = sum(
                r.get("mentions", 0) for r in (cur_result.data or [])
            )

            # Previous 7 days (for % change)
            prev_result = (
                self.supabase.table("social_mentions_history")
                .select("mentions")
                .eq("ticker", ticker)
                .gte("snapshot_date", two_weeks_ago)
                .lt("snapshot_date", week_ago)
                .execute()
            )
            previous_total = sum(
                r.get("mentions", 0) for r in (prev_result.data or [])
            )

            return current_total, previous_total

        except Exception as e:
            logger.warning(
                f"7d mentions query failed for {ticker}: {e}"
            )
            return 0, 0


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[SocialMentionsService] = None


def get_social_mentions_service() -> SocialMentionsService:
    global _service
    if _service is None:
        _service = SocialMentionsService()
    return _service
