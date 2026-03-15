"""
Admin endpoints — operational triggers for background jobs.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/refresh-sector-benchmarks")
async def refresh_sector_benchmarks():
    """Manually trigger sector benchmark recomputation. Returns immediately."""
    try:
        from app.services.sector_benchmark_service import get_sector_benchmark_service

        service = get_sector_benchmark_service()
        asyncio.create_task(service.compute_all_benchmarks(force=True))
        return {"status": "started", "message": "Sector benchmark computation started in background"}
    except Exception as e:
        logger.error(f"Manual benchmark refresh failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to start benchmark refresh")
