"""
Background Jobs Module
Scheduled tasks for news fetching, widget updates, and maintenance.
"""

from app.jobs.scheduler import scheduler, init_scheduler, shutdown_scheduler
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

__all__ = [
    "scheduler",
    "init_scheduler",
    "shutdown_scheduler",
    "fetch_news_for_all_watchlists",
    "fetch_market_news",
    "update_all_widgets",
    "update_widgets_for_active_users",
    "monthly_credit_reset",
    "cleanup_old_activities"
]
