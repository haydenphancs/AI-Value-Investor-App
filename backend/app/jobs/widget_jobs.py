"""
Widget Update Background Jobs
Scheduled tasks for iOS widget data generation.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import List

from app.database import get_supabase
from app.services.widget_service import WidgetService

logger = logging.getLogger(__name__)


async def update_all_widgets():
    """
    Update widgets for ALL users.
    Runs after market close.
    Section 4.2 - Widget updates at least twice daily

    This job:
    1. Gets all users
    2. Generates widget updates for each
    3. Caches for fast retrieval
    """
    try:
        start_time = datetime.utcnow()
        logger.info("Starting widget update job for all users")

        supabase = get_supabase()
        widget_service = WidgetService(supabase=supabase)

        # Get all active users (not deleted)
        users = supabase.table("users").select("id").is_("deleted_at", "null").execute()

        if not users.data:
            logger.info("No active users found")
            return

        user_ids = [u["id"] for u in users.data]

        logger.info(f"Updating widgets for {len(user_ids)} users")

        # Bulk generate widgets with concurrency control
        stats = await widget_service.bulk_generate_widgets(
            user_ids=user_ids,
            max_concurrent=20  # Higher concurrency for scheduled job
        )

        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Widget update job completed in {duration:.2f}s "
            f"(successful: {stats['successful']}, failed: {stats['failed']})"
        )

    except Exception as e:
        logger.error(f"Widget update job failed: {e}", exc_info=True)


async def update_widgets_for_active_users():
    """
    Update widgets only for recently active users.
    Runs every hour during waking hours.
    More efficient than updating all users.

    This job:
    1. Gets users active in last 24 hours
    2. Updates their widgets
    3. Reduces unnecessary processing
    """
    try:
        start_time = datetime.utcnow()
        logger.info("Starting widget update job for active users")

        supabase = get_supabase()
        widget_service = WidgetService(supabase=supabase)

        # Get users active in last 24 hours
        cutoff_date = (datetime.utcnow() - timedelta(hours=24)).isoformat()

        users = supabase.table("users").select("id").gte(
            "last_login_at", cutoff_date
        ).is_("deleted_at", "null").execute()

        if not users.data:
            logger.info("No recently active users found")
            return

        user_ids = [u["id"] for u in users.data]

        logger.info(f"Updating widgets for {len(user_ids)} recently active users")

        # Bulk generate widgets
        stats = await widget_service.bulk_generate_widgets(
            user_ids=user_ids,
            max_concurrent=15
        )

        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Active user widget update completed in {duration:.2f}s "
            f"(successful: {stats['successful']}, failed: {stats['failed']})"
        )

    except Exception as e:
        logger.error(f"Active user widget update job failed: {e}", exc_info=True)


async def update_widgets_for_breaking_news():
    """
    Update widgets for users who have stocks with breaking news.
    Runs when breaking news is detected.
    Provides real-time updates for important events.

    This job:
    1. Gets stocks with recent breaking news
    2. Finds users watching those stocks
    3. Updates their widgets immediately
    """
    try:
        logger.info("Starting widget update job for breaking news")

        supabase = get_supabase()
        widget_service = WidgetService(supabase=supabase)

        # Get active breaking news from last 2 hours
        cutoff = (datetime.utcnow() - timedelta(hours=2)).isoformat()

        breaking = supabase.table("breaking_news").select(
            "stock_id"
        ).gte("created_at", cutoff).eq("is_active", True).execute()

        if not breaking.data:
            logger.info("No breaking news found")
            return

        stock_ids = list(set([b["stock_id"] for b in breaking.data]))

        logger.info(f"Found breaking news for {len(stock_ids)} stocks")

        # Get users watching these stocks
        watchlists = supabase.table("watchlist_stocks").select(
            "watchlist_id, watchlist:watchlists(user_id)"
        ).in_("stock_id", stock_ids).execute()

        if not watchlists.data:
            logger.info("No users watching stocks with breaking news")
            return

        # Extract unique user IDs
        user_ids = list(set([
            w["watchlist"]["user_id"]
            for w in watchlists.data
            if w.get("watchlist")
        ]))

        logger.info(f"Updating widgets for {len(user_ids)} users with breaking news")

        # Update widgets with force regenerate
        successful = 0
        failed = 0

        for user_id in user_ids:
            try:
                await widget_service.generate_widget_update(
                    user_id=user_id,
                    force_regenerate=True  # Force refresh for breaking news
                )
                successful += 1
                await asyncio.sleep(0.1)  # Small delay
            except Exception as e:
                logger.error(f"Failed to update widget for user {user_id}: {e}")
                failed += 1

        logger.info(
            f"Breaking news widget update completed "
            f"(successful: {successful}, failed: {failed})"
        )

    except Exception as e:
        logger.error(f"Breaking news widget update job failed: {e}", exc_info=True)


async def cleanup_old_widget_updates():
    """
    Cleanup old widget update records (older than 7 days).
    Keeps database size manageable.
    """
    try:
        logger.info("Starting widget update cleanup job")

        supabase = get_supabase()

        # Delete widget updates older than 7 days
        cutoff_date = (datetime.utcnow() - timedelta(days=7)).isoformat()

        result = supabase.table("widget_updates").delete().lt(
            "created_at", cutoff_date
        ).execute()

        count = len(result.data) if result.data else 0

        logger.info(f"Widget update cleanup completed (deleted: {count} records)")

    except Exception as e:
        logger.error(f"Widget update cleanup job failed: {e}", exc_info=True)
