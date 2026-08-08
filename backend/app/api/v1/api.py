"""
API V1 Router — aggregates all endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    auth,
    users,
    billing,
    stocks,
    watchlist,
    research,
    chat,
    home,
    indices,
    etfs,
    crypto,
    commodities,
    ticker_report,
    tracking,
    portfolios,
    price_alerts,
    whales,
    live_price,
    learn,
    updates,
    analytics,
)

api_router = APIRouter()

api_router.include_router(home.router, prefix="/home", tags=["Home"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(billing.router, prefix="/billing", tags=["Billing"])
api_router.include_router(stocks.router, prefix="/stocks", tags=["Stocks"])
api_router.include_router(ticker_report.router, prefix="/stocks", tags=["Ticker Report"])
api_router.include_router(indices.router, prefix="/indices", tags=["Indices"])
api_router.include_router(etfs.router, prefix="/etfs", tags=["ETFs"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist"])
# NOTE: the legacy /news router was REMOVED. Its two endpoints served the
# `news_articles` table, which nothing writes and no client called — so it served
# stale third-party headlines, summaries, thumbnails and publisher logos
# indefinitely, with no expiry. Live news is per-asset (/stocks/{t}/news,
# /updates/feed) off `ticker_news_cache`, which has a 6h expiry + cleanup.
# See migration 104 for the row cleanup.
api_router.include_router(updates.router, prefix="/updates", tags=["Updates"])
api_router.include_router(research.router, prefix="/research", tags=["Research"])
api_router.include_router(crypto.router, prefix="/crypto", tags=["Crypto"])
api_router.include_router(commodities.router, prefix="/commodities", tags=["Commodities"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
api_router.include_router(tracking.router, prefix="/tracking", tags=["Tracking"])
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["Portfolios"])
api_router.include_router(whales.router, prefix="/whales", tags=["Whales"])
api_router.include_router(
    price_alerts.router, prefix="/alerts/price", tags=["Price Alerts"]
)
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(learn.router, prefix="/learn", tags=["Learn"])
api_router.include_router(live_price.router, tags=["Live Price"])
# First-party product analytics ingest (POST /events). Bounded + rate-limited;
# see app/api/v1/endpoints/analytics.py for why it always returns 200.
api_router.include_router(analytics.router, tags=["Analytics"])
