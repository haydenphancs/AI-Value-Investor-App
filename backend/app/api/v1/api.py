"""
API V1 Router
Aggregates all endpoint routers for version 1 of the API.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    users,
    stocks,
    news,
    research,
    chat,
    widget,
    education
)

api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"]
)

api_router.include_router(
    users.router,
    prefix="/users",
    tags=["Users"]
)

api_router.include_router(
    stocks.router,
    prefix="/stocks",
    tags=["Stocks"]
)

api_router.include_router(
    news.router,
    prefix="/news",
    tags=["News"]
)

api_router.include_router(
    research.router,
    prefix="/research",
    tags=["Deep Research"]
)

api_router.include_router(
    chat.router,
    prefix="/chat",
    tags=["Chat & AI"]
)

api_router.include_router(
    widget.router,
    prefix="/widget",
    tags=["Widget"]
)

api_router.include_router(
    education.router,
    prefix="/education",
    tags=["Education"]
)
