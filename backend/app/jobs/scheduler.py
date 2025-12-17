"""
APScheduler Configuration
Central scheduler for all background jobs.
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime
import pytz

from app.config import settings

logger = logging.getLogger(__name__)

# Initialize scheduler
scheduler = AsyncIOScheduler(
    timezone=pytz.timezone("America/New_York"),  # US Eastern Time (market timezone)
    job_defaults={
        "coalesce": True,  # Combine multiple pending executions into one
        "max_instances": 1,  # Don't run multiple instances of the same job
        "misfire_grace_time": 300  # 5 minutes grace period
    }
)


def init_scheduler():
    """
    Initialize and start the scheduler with all jobs.
    Called during application startup.
    """
    if scheduler.running:
        logger.warning("Scheduler is already running")
        return

    logger.info("Initializing background job scheduler")

    # Import job functions
    from app.jobs.news_jobs import (
        fetch_news_for_all_watchlists,
        fetch_market_news
    )
    from app.jobs.widget_jobs import (
        update_all_widgets,
        update_widgets_for_active_users
    )
    from app.jobs.maintenance_jobs import (
        monthly_credit_reset,
        cleanup_old_activities
    )

    # ========================================
    # NEWS FETCHING JOBS
    # ========================================

    # Job 1: Fetch news for watchlist stocks (every 30 minutes during market hours)
    # Monday-Friday, 9:00 AM - 5:00 PM ET
    scheduler.add_job(
        func=fetch_news_for_all_watchlists,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-16",
            minute="*/30",
            timezone="America/New_York"
        ),
        id="fetch_watchlist_news",
        name="Fetch News for Watchlist Stocks",
        replace_existing=True
    )

    # Job 2: Fetch general market news (twice daily)
    # 9:30 AM and 4:00 PM ET (market open and close)
    scheduler.add_job(
        func=fetch_market_news,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9,16",
            minute="30,0",
            timezone="America/New_York"
        ),
        id="fetch_market_news",
        name="Fetch General Market News",
        replace_existing=True
    )

    # ========================================
    # WIDGET UPDATE JOBS
    # ========================================

    # Job 3: Update widgets for active users (every hour during waking hours)
    # 6:00 AM - 10:00 PM ET
    scheduler.add_job(
        func=update_widgets_for_active_users,
        trigger=CronTrigger(
            hour="6-22",
            minute="15",  # 15 minutes after the hour
            timezone="America/New_York"
        ),
        id="update_active_widgets",
        name="Update Widgets for Active Users",
        replace_existing=True
    )

    # Job 4: Update all widgets after market close
    # 4:30 PM ET (30 minutes after market close)
    scheduler.add_job(
        func=update_all_widgets,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="16",
            minute="30",
            timezone="America/New_York"
        ),
        id="update_all_widgets_market_close",
        name="Update All Widgets (Market Close)",
        replace_existing=True
    )

    # ========================================
    # MAINTENANCE JOBS
    # ========================================

    # Job 5: Monthly credit reset (1st of each month at midnight)
    scheduler.add_job(
        func=monthly_credit_reset,
        trigger=CronTrigger(
            day="1",
            hour="0",
            minute="0",
            timezone="America/New_York"
        ),
        id="monthly_credit_reset",
        name="Monthly Credit Reset",
        replace_existing=True
    )

    # Job 6: Cleanup old user activity records (weekly on Sunday at 2:00 AM)
    # Keep last 90 days only
    scheduler.add_job(
        func=cleanup_old_activities,
        trigger=CronTrigger(
            day_of_week="sun",
            hour="2",
            minute="0",
            timezone="America/New_York"
        ),
        id="cleanup_old_activities",
        name="Cleanup Old Activity Records",
        replace_existing=True
    )

    # Start the scheduler
    scheduler.start()

    logger.info("Background job scheduler started successfully")
    logger.info(f"Scheduled jobs: {len(scheduler.get_jobs())}")

    # Log all scheduled jobs
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name} (ID: {job.id}, Next run: {job.next_run_time})")


def shutdown_scheduler():
    """
    Shutdown the scheduler gracefully.
    Called during application shutdown.
    """
    if scheduler.running:
        logger.info("Shutting down background job scheduler")
        scheduler.shutdown(wait=True)
        logger.info("Scheduler shut down successfully")
    else:
        logger.warning("Scheduler is not running")


def pause_scheduler():
    """Pause all scheduled jobs (for maintenance)."""
    if scheduler.running:
        scheduler.pause()
        logger.info("Scheduler paused")


def resume_scheduler():
    """Resume scheduled jobs after pause."""
    if scheduler.running:
        scheduler.resume()
        logger.info("Scheduler resumed")


def get_scheduler_status():
    """
    Get scheduler status and job information.

    Returns:
        dict: Scheduler status
    """
    if not scheduler.running:
        return {
            "running": False,
            "jobs": []
        }

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        })

    return {
        "running": True,
        "timezone": str(scheduler.timezone),
        "jobs_count": len(jobs),
        "jobs": jobs
    }
