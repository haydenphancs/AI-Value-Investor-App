"""
News Fetching Background Jobs
Scheduled tasks for news aggregation and processing.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Set

from app.database import get_supabase
from app.services.news_service import NewsService

logger = logging.getLogger(__name__)


async def fetch_news_for_all_watchlists():
    """
    Fetch news for all stocks in user watchlists.
    Runs during market hours (every 30 minutes).
    Section 4.1 - Automated News Aggregation

    This job:
    1. Gets all unique stocks from active watchlists
    2. Fetches news for each stock
    3. Processes with AI summarization
    4. Identifies breaking news
    """
    try:
        start_time = datetime.utcnow()
        logger.info("Starting watchlist news fetching job")

        supabase = get_supabase()
        news_service = NewsService(supabase=supabase)

        # Get all active watchlist stocks
        # Only fetch for stocks in watchlists that were viewed in last 7 days
        cutoff_date = (datetime.utcnow() - timedelta(days=7)).isoformat()

        watchlist_stocks = supabase.table("watchlist_stocks").select(
            "stock_id, stock:stocks(id, ticker, company_name)"
        ).gte("updated_at", cutoff_date).execute()

        if not watchlist_stocks.data:
            logger.info("No active watchlist stocks found")
            return

        # Get unique stocks
        unique_stocks = {}
        for item in watchlist_stocks.data:
            stock = item.get("stock")
            if stock and stock.get("ticker"):
                unique_stocks[stock["id"]] = stock

        logger.info(f"Found {len(unique_stocks)} unique stocks in active watchlists")

        # Fetch news for each stock (with rate limiting)
        successful = 0
        failed = 0

        for stock_id, stock in unique_stocks.items():
            try:
                logger.info(f"Fetching news for {stock['ticker']}")

                article_ids = await news_service.fetch_and_process_stock_news(
                    ticker=stock["ticker"],
                    company_name=stock["company_name"],
                    days_back=1,  # Only last 1 day (since we run frequently)
                    max_articles=10  # Limit to 10 per stock
                )

                if article_ids:
                    successful += 1
                    logger.info(f"Processed {len(article_ids)} articles for {stock['ticker']}")

                    # Identify breaking news
                    await news_service.identify_breaking_news(
                        stock_id=stock_id,
                        time_window_hours=24
                    )

                # Small delay to avoid rate limits
                await asyncio.sleep(2)

            except Exception as e:
                failed += 1
                logger.error(f"Failed to fetch news for {stock['ticker']}: {e}")
                continue

        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Watchlist news fetching completed in {duration:.2f}s "
            f"(successful: {successful}, failed: {failed})"
        )

    except Exception as e:
        logger.error(f"Watchlist news fetching job failed: {e}", exc_info=True)


async def fetch_market_news():
    """
    Fetch general market news (not stock-specific).
    Runs twice daily at market open and close.
    Section 4.1 - Market News Aggregation

    This job:
    1. Fetches general business/finance news
    2. Processes with AI summarization
    3. Stores for homepage display
    """
    try:
        start_time = datetime.utcnow()
        logger.info("Starting market news fetching job")

        supabase = get_supabase()
        news_service = NewsService(supabase=supabase)

        # Fetch market news
        article_ids = await news_service.fetch_market_news(
            category="business",
            max_articles=20
        )

        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Market news fetching completed in {duration:.2f}s "
            f"(processed: {len(article_ids)} articles)"
        )

    except Exception as e:
        logger.error(f"Market news fetching job failed: {e}", exc_info=True)


async def cleanup_old_news():
    """
    Cleanup old news articles (older than 90 days).
    Keeps database size manageable.
    """
    try:
        logger.info("Starting news cleanup job")

        supabase = get_supabase()

        # Delete news older than 90 days
        cutoff_date = (datetime.utcnow() - timedelta(days=90)).isoformat()

        # Soft delete
        result = supabase.table("news_articles").update({
            "deleted_at": datetime.utcnow().isoformat()
        }).lt("published_at", cutoff_date).is_("deleted_at", "null").execute()

        count = len(result.data) if result.data else 0

        logger.info(f"News cleanup completed (deleted: {count} articles)")

    except Exception as e:
        logger.error(f"News cleanup job failed: {e}", exc_info=True)


async def refresh_breaking_news():
    """
    Refresh breaking news status for all stocks.
    Expires old breaking news and identifies new ones.
    """
    try:
        logger.info("Starting breaking news refresh job")

        supabase = get_supabase()

        # Expire old breaking news (older than 24 hours)
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()

        supabase.table("breaking_news").update({
            "is_active": False
        }).lt("created_at", cutoff).eq("is_active", True).execute()

        # Get all stocks with recent news
        news_service = NewsService(supabase=supabase)

        # Get distinct stock IDs from recent news
        recent_news = supabase.table("news_stocks").select(
            "stock_id"
        ).gte("created_at", cutoff).execute()

        if recent_news.data:
            unique_stock_ids = list(set([item["stock_id"] for item in recent_news.data]))

            logger.info(f"Refreshing breaking news for {len(unique_stock_ids)} stocks")

            # Identify breaking news for each stock
            for stock_id in unique_stock_ids:
                try:
                    await news_service.identify_breaking_news(
                        stock_id=stock_id,
                        time_window_hours=24
                    )
                    await asyncio.sleep(0.5)  # Rate limiting
                except Exception as e:
                    logger.warning(f"Failed to identify breaking news for {stock_id}: {e}")

        logger.info("Breaking news refresh completed")

    except Exception as e:
        logger.error(f"Breaking news refresh job failed: {e}", exc_info=True)
