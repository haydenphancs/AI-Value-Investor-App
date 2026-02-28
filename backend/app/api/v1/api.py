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
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(stocks.router, prefix="/stocks", tags=["Stocks"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist"])
api_router.include_router(news.router, prefix="/news", tags=["News"])
api_router.include_router(research.router, prefix="/research", tags=["Research"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
