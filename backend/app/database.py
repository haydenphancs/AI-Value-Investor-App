"""
Database Connection - Supabase Only
No SQLAlchemy. Uses Supabase Python client for all DB operations.
"""

from typing import Optional
from supabase import create_client, Client
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_supabase_client: Optional[Client] = None


def get_supabase() -> Client:
    """
    Get or create Supabase client singleton.
    Uses service role key for server-side operations (bypasses RLS).
    """
    global _supabase_client
    if _supabase_client is None:
        logger.info("Initializing Supabase client")
        _supabase_client = create_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_SERVICE_ROLE_KEY
        )
    return _supabase_client


async def check_supabase_health() -> bool:
    """Check Supabase connection health via the PostgREST root endpoint."""
    try:
        import httpx

        # Eagerly initialise the client singleton
        get_supabase()

        # Hit the PostgREST schema endpoint — no table permissions needed
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                f"{settings.SUPABASE_URL}/rest/v1/",
                headers={
                    "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
                },
                timeout=5.0,
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Supabase health check failed: {e}")
        return False
