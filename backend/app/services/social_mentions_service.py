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
    crypto_ticker,
    get_all_mentions,
    get_ticker_mentions,
    is_cache_populated,
)
from app.utils.supabase_errors import retry_idempotent_async

logger = logging.getLogger(__name__)


def apewisdom_key(ticker: str, *, is_crypto: bool) -> str:
    """The key a ticker is stored under — in the ApeWisdom cache AND in
    `social_mentions_history`, which is written from that cache verbatim.

    Stocks are bare (`AAPL`). Coins carry ApeWisdom's `.X` suffix on the BASE symbol
    (`ETH.X`). `ticker` IS the base: every caller strips the pair's quote currency exactly
    once before it gets here (`crypto.py` → `_normalize_crypto_symbol`, chat → its trailing
    `USD` strip, `sentiment_service.get_sentiment` → `crypto_base_symbol` when no
    `social_ticker` is given). This helper must NOT strip again — a stablecoin's base
    itself ends in `USD` (`TUSD`, `PYUSD`, `FDUSD`), and a second strip turned it into
    `T.X`, the same populated-cache miss this key exists to prevent.
    `is_crypto` is REQUEST state (a stock that shares a coin's name — `COIN`, `LINK` — is a
    different row), so it is a parameter, never something inferred from the symbol.
    """
    if is_crypto:
        return crypto_ticker(ticker)
    return str(ticker or "").strip().upper()


def _int_or_zero(value: Any) -> int:
    """A snapshot `mentions` cell as an int; None / NaN / junk reads as 0 rather than raising
    inside a fallback whose whole job is to degrade quietly."""
    try:
        n = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(n, 0)


def _consecutive_days(latest: Any, prior: Any) -> bool:
    """True when `prior` is exactly the day before `latest` (both ISO date strings)."""
    try:
        d0 = date.fromisoformat(str(latest)[:10])
        d1 = date.fromisoformat(str(prior)[:10])
    except (TypeError, ValueError):
        return False
    return (d0 - d1).days == 1


class SocialMentionsService:

    def __init__(self):
        self.supabase = get_supabase()

    # ── Daily snapshot (called by scheduled task) ─────────────────

    async def snapshot_all(self) -> Tuple[int, int]:
        """
        Fetch all mention data from ApeWisdom and store in DB.

        Called once per UTC day by `main._run_social_snapshot_loop` (it had NO caller from
        2025 until 2026-09-11 — the table stayed empty and every 7-day count was 0). The
        upsert is idempotent on (ticker, snapshot_date, source), so a retry within the day
        rewrites the same rows. Rate-limit-safe — the ApeWisdom client handles page delays
        internally. Supabase calls run off the event loop.

        Returns `(stored, expected)` — the tickers written and the tickers the cache held.
        The pair, not a bare count, because the caller decides whether the DAY is done:
        this used to return `stored` alone and `_social_snapshot_once` marked the day done
        on any `stored > 0`, so a chunk lost to a Supabase 520 ("complete: 500/974") left
        474 tickers without that day's row, `get_mentions_7d` summed six days instead of
        seven for a week, and nothing retried because the writer had said "stored 500".
        """
        all_data = await get_all_mentions()

        if not all_data:
            logger.warning("ApeWisdom returned no data for snapshot")
            return 0, 0

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
                # Retried in place on a transient edge error (the Cloudflare 520 this
                # project has seen): the upsert is idempotent on the unique key, so a
                # replay converges. Runs off the loop — `retry_idempotent_async` supplies
                # the `to_thread` hop. A non-transient failure still lets the other chunks
                # land; the (stored, expected) pair below reports the hole.
                await retry_idempotent_async(
                    lambda c=chunk: self.supabase.table("social_mentions_history").upsert(
                        c, on_conflict="ticker,snapshot_date,source",
                    ).execute(),
                    what=f"social_mentions_history upsert chunk {i}",
                    logger=logger,
                )
                total_upserted += len(chunk)
            except Exception as e:
                logger.error(
                    "Snapshot upsert failed for chunk %d (%d rows, %s): %s: %s",
                    i, len(chunk), today, type(e).__name__, e,
                )

        if total_upserted < len(rows):
            logger.warning(
                "Social mentions snapshot PARTIAL: %d/%d tickers stored for %s — the day "
                "is not done, the hourly tick re-upserts",
                total_upserted, len(rows), today,
            )
        else:
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
            logger.warning(f"Social mentions cleanup failed: {type(e).__name__}: {e}")

        return total_upserted, len(rows)

    # ── 24h lookups (fast, from ApeWisdom cache) ──────────────────

    async def get_mentions_24h(
        self, ticker: str, *, is_crypto: bool = False
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

        `is_crypto` selects the key (`apewisdom_key`): a coin lives under `ETH.X`, and a
        lookup of the bare `ETH` on a populated cache is — correctly — a real zero. That is
        exactly how every coin read "Reddit data unavailable" for months: right semantics,
        wrong key. The flag is required, not inferred, because `COIN`/`LINK` are also stocks.
        """
        key = apewisdom_key(ticker, is_crypto=is_crypto)

        # Try ApeWisdom cache first
        data = await get_ticker_mentions(key)
        if data is not None:
            return data["mentions"], data["mentions_24h_ago"], True
        if is_cache_populated(key):
            # Both filters have landed and the ticker is not on either list: Reddit is
            # not talking about it TODAY, and that is the answer. The DB fallback below
            # must not run here — it would serve the latest snapshot row (up to 30 days
            # old: a name that trended three weeks ago) as the CURRENT count, and with a
            # fabricated 0 as the previous window the Sentiment card printed "+100% today"
            # in the gain colour with `known=True`.
            return 0, 0, True

        # Fallback while the cache is COLD (boot, or a filter that has never landed): the
        # two latest daily snapshots. `previous` is only honest when the rows are
        # consecutive days — the snapshot's `mentions` is that day's 24h figure, so
        # yesterday vs the day before is the same shape as `mentions_24h_ago`. A single
        # row, or a gap, has no previous window; handing `_pct_change` a 0 there is what
        # fabricated the +100%. The tuple has no per-field flag, so the CHANGE being
        # unknown makes the pair `known=False` (iOS renders "—" / muted); the count is
        # ≤24h stale in any case.
        try:
            result = await asyncio.to_thread(
                lambda: self.supabase.table("social_mentions_history")
                .select("mentions, snapshot_date")
                .eq("ticker", key)
                .order("snapshot_date", desc=True)
                .limit(2)
                .execute()
            )
        except Exception as e:
            logger.warning(
                f"DB fallback for 24h mentions failed for {key}: {type(e).__name__}: {e}"
            )
            return 0, 0, False

        rows = result.data or []
        if rows:
            mentions = _int_or_zero(rows[0].get("mentions"))
            if len(rows) >= 2 and _consecutive_days(rows[0].get("snapshot_date"),
                                                    rows[1].get("snapshot_date")):
                return mentions, _int_or_zero(rows[1].get("mentions")), True
            return mentions, 0, False

        # Nothing anywhere and ApeWisdom was never consulted: unknown, not "not tracked".
        return 0, 0, False

    # ── 7d lookups (from DB history) ──────────────────────────────

    async def get_mentions_7d(
        self, ticker: str, *, is_crypto: bool = False
    ) -> Tuple[int, int, bool]:
        """
        Get 7-day mention counts for a ticker.

        Returns (current_7d_total, previous_7d_total, known).
        Queries accumulated daily snapshots from Supabase, off the event loop (two sync
        PostgREST round-trips used to run INSIDE the request coroutine).
        `known` is False only when the query FAILED (the 42501 of 2026-09-11 answered
        every ticker "0 mentions this week" for months); a successful empty query — the
        first-week warm-up, a ticker nobody mentions — is a real (0, 0, True).

        The rows are keyed exactly as ApeWisdom serves them (`ETH.X` for a coin), so the
        same `apewisdom_key` selects them — see `get_mentions_24h`.
        """
        key = apewisdom_key(ticker, is_crypto=is_crypto)

        try:
            today = date.today()
            week_ago = (today - timedelta(days=7)).isoformat()
            two_weeks_ago = (today - timedelta(days=14)).isoformat()

            def _query():
                cur = (
                    self.supabase.table("social_mentions_history")
                    .select("mentions")
                    .eq("ticker", key)
                    .gte("snapshot_date", week_ago)
                    .execute()
                )
                prev = (
                    self.supabase.table("social_mentions_history")
                    .select("mentions")
                    .eq("ticker", key)
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
                f"7d mentions query failed for {key}: {type(e).__name__}: {e}"
            )
            return 0, 0, False


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[SocialMentionsService] = None


def get_social_mentions_service() -> SocialMentionsService:
    global _service
    if _service is None:
        _service = SocialMentionsService()
    return _service
