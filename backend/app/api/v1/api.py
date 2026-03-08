"""
API V1 Router — aggregates all endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    users,
    stocks,
    watchlist,
    news,
    research,
    chat,
    home,
    indices,
    etfs,
    crypto,
    commodities,
    ticker_report,
    tracking,
    whales,
)

api_router = APIRouter()

api_router.include_router(home.router, prefix="/home", tags=["Home"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(stocks.router, prefix="/stocks", tags=["Stocks"])
api_router.include_router(ticker_report.router, prefix="/stocks", tags=["Ticker Report"])
api_router.include_router(indices.router, prefix="/indices", tags=["Indices"])
api_router.include_router(etfs.router, prefix="/etfs", tags=["ETFs"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist"])
api_router.include_router(news.router, prefix="/news", tags=["News"])
api_router.include_router(research.router, prefix="/research", tags=["Research"])
api_router.include_router(crypto.router, prefix="/crypto", tags=["Crypto"])
api_router.include_router(commodities.router, prefix="/commodities", tags=["Commodities"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
api_router.include_router(tracking.router, prefix="/tracking", tags=["Tracking"])
api_router.include_router(whales.router, prefix="/whales", tags=["Whales"])
