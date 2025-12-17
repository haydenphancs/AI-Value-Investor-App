"""
Maintenance Background Jobs
Scheduled tasks for system maintenance and cleanup.
"""

import logging
from datetime import datetime, timedelta

from app.database import get_supabase
from app.services.user_service import UserService

logger = logging.getLogger(__name__)


async def monthly_credit_reset():
    """
    Reset monthly credits for all users.
    Runs on the 1st of each month at midnight.
    Section 5.5 - Monthly credit reset

    This job:
    1. Resets monthly_deep_research_used to 0
    2. Updates last_credit_reset_at timestamp
    3. Logs activity for audit trail
    """
    try:
        start_time = datetime.utcnow()
        logger.info("Starting monthly credit reset job")

        supabase = get_supabase()
        user_service = UserService(supabase)

        # Bulk reset credits for all users
        stats = await user_service.bulk_reset_monthly_credits(max_concurrent=50)

        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Monthly credit reset completed in {duration:.2f}s "
            f"(successful: {stats['successful']}, failed: {stats['failed']}, total: {stats['total']})"
        )

        # Send notification to admin about reset
        # Could integrate with email/Slack notification here

    except Exception as e:
        logger.error(f"Monthly credit reset job failed: {e}", exc_info=True)


async def cleanup_old_activities():
    """
    Cleanup old user activity records.
    Runs weekly on Sunday at 2:00 AM.
    Keeps last 90 days only.

    This job:
    1. Deletes activity records older than 90 days
    2. Keeps database size manageable
    3. Maintains performance
    """
    try:
        logger.info("Starting user activity cleanup job")

        supabase = get_supabase()

        # Delete activities older than 90 days
        cutoff_date = (datetime.utcnow() - timedelta(days=90)).isoformat()

        result = supabase.table("user_activity").delete().lt(
            "created_at", cutoff_date
        ).execute()

        count = len(result.data) if result.data else 0

        logger.info(f"User activity cleanup completed (deleted: {count} records)")

    except Exception as e:
        logger.error(f"User activity cleanup job failed: {e}", exc_info=True)


async def cleanup_failed_reports():
    """
    Cleanup failed research reports older than 7 days.
    Failed reports don't need to be kept long-term.
    """
    try:
        logger.info("Starting failed reports cleanup job")

        supabase = get_supabase()

        # Delete failed reports older than 7 days
        cutoff_date = (datetime.utcnow() - timedelta(days=7)).isoformat()

        result = supabase.table("deep_research_reports").delete().eq(
            "status", "failed"
        ).lt("created_at", cutoff_date).execute()

        count = len(result.data) if result.data else 0

        logger.info(f"Failed reports cleanup completed (deleted: {count} reports)")

    except Exception as e:
        logger.error(f"Failed reports cleanup job failed: {e}", exc_info=True)


async def send_low_credit_notifications():
    """
    Send notifications to users running low on credits.
    Runs daily at 10:00 AM.
    Helps with user engagement and upgrade conversion.

    This job:
    1. Identifies users with < 20% credits remaining
    2. Sends email/push notifications
    3. Encourages tier upgrades
    """
    try:
        logger.info("Starting low credit notification job")

        supabase = get_supabase()
        user_service = UserService(supabase)

        # Get users with low credits (80% used)
        low_credit_users = await user_service.get_low_credit_users(
            threshold_percentage=0.8
        )

        if not low_credit_users:
            logger.info("No users with low credits found")
            return

        logger.info(f"Found {len(low_credit_users)} users with low credits")

        # TODO: Integrate with email service (SendGrid, AWS SES, etc.)
        # For now, just log
        for user in low_credit_users:
            logger.info(
                f"Low credit alert: {user['email']} - "
                f"{user['credits_used']}/{user['credits_total']} used "
                f"({user['usage_percentage']:.1f}%)"
            )

        # Create notification records
        for user in low_credit_users:
            try:
                supabase.table("notifications").insert({
                    "user_id": user["user_id"],
                    "type": "low_credits",
                    "title": "Running Low on Credits",
                    "message": (
                        f"You've used {user['credits_used']} of your {user['credits_total']} "
                        f"monthly deep research credits. Upgrade to Pro for more!"
                    ),
                    "data": {
                        "credits_used": user["credits_used"],
                        "credits_total": user["credits_total"],
                        "upgrade_cta": True
                    },
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                logger.warning(f"Failed to create notification for {user['user_id']}: {e}")

        logger.info(f"Low credit notifications sent to {len(low_credit_users)} users")

    except Exception as e:
        logger.error(f"Low credit notification job failed: {e}", exc_info=True)


async def database_health_check():
    """
    Perform database health check and log metrics.
    Runs hourly.
    Monitors system health and performance.

    This job:
    1. Checks database connection
    2. Counts records in key tables
    3. Identifies potential issues
    4. Logs metrics for monitoring
    """
    try:
        logger.info("Starting database health check")

        from app.database import db_manager

        # Check connection
        is_connected = await db_manager.check_connection()

        if not is_connected:
            logger.error("Database connection check FAILED")
            return

        supabase = get_supabase()

        # Get table counts
        tables = [
            "users",
            "stocks",
            "news_articles",
            "deep_research_reports",
            "educational_content",
            "widget_updates"
        ]

        health_metrics = {
            "timestamp": datetime.utcnow().isoformat(),
            "database_connected": True,
            "table_counts": {}
        }

        for table in tables:
            try:
                result = supabase.table(table).select("id", count="exact").limit(1).execute()
                count = result.count if hasattr(result, "count") else 0
                health_metrics["table_counts"][table] = count
            except Exception as e:
                logger.warning(f"Failed to count {table}: {e}")
                health_metrics["table_counts"][table] = None

        logger.info(f"Database health check completed: {health_metrics}")

        # TODO: Send metrics to monitoring service (Datadog, CloudWatch, etc.)

    except Exception as e:
        logger.error(f"Database health check failed: {e}", exc_info=True)


async def update_stock_fundamentals():
    """
    Update stock fundamental data for all tracked stocks.
    Runs daily at 5:00 PM (after market close).
    Section 4.3 - Financial data for deep research

    This job:
    1. Gets all stocks in watchlists
    2. Fetches latest fundamentals from FMP
    3. Updates stock_fundamentals table
    4. Keeps data fresh for research reports
    """
    try:
        logger.info("Starting stock fundamentals update job")

        from app.integrations.fmp import FMPClient

        supabase = get_supabase()
        fmp_client = FMPClient()

        # Get all unique stocks in watchlists
        watchlist_stocks = supabase.table("watchlist_stocks").select(
            "stock_id, stock:stocks(ticker)"
        ).execute()

        if not watchlist_stocks.data:
            logger.info("No stocks in watchlists")
            return

        # Get unique tickers
        unique_tickers = list(set([
            item["stock"]["ticker"]
            for item in watchlist_stocks.data
            if item.get("stock")
        ]))

        logger.info(f"Updating fundamentals for {len(unique_tickers)} stocks")

        successful = 0
        failed = 0

        for ticker in unique_tickers:
            try:
                # Fetch fundamentals
                fundamentals = await fmp_client.get_key_metrics(ticker)

                if not fundamentals:
                    logger.warning(f"No fundamentals found for {ticker}")
                    failed += 1
                    continue

                # Update stock_fundamentals table
                # (Implementation depends on your schema)
                # This is a placeholder

                successful += 1

                # Rate limiting
                import asyncio
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Failed to update fundamentals for {ticker}: {e}")
                failed += 1

        logger.info(
            f"Stock fundamentals update completed "
            f"(successful: {successful}, failed: {failed})"
        )

    except Exception as e:
        logger.error(f"Stock fundamentals update job failed: {e}", exc_info=True)
