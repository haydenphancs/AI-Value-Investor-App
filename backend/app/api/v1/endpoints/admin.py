"""
Admin endpoints — operational triggers for background jobs.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/refresh-sector-benchmarks")
async def refresh_sector_benchmarks(backfill: bool = False):
    """Manually trigger sector benchmark recomputation. Returns immediately.

    Args:
        backfill: If True, forces deep historical computation (16 annual, 80 quarterly).
                  If False (default), only refreshes recent periods.
    """
    try:
        from app.services.sector_benchmark_service import get_sector_benchmark_service

        service = get_sector_benchmark_service()
        asyncio.create_task(service.compute_all_benchmarks(force=True, backfill=backfill))
        mode = "backfill (full history)" if backfill else "daily (recent periods)"
        return {"status": "started", "message": f"Sector benchmark computation started in background — mode: {mode}"}
    except Exception as e:
        logger.error(f"Manual benchmark refresh failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to start benchmark refresh")
