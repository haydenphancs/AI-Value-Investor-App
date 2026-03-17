#!/usr/bin/env python3
"""
Backfill Sector Benchmarks
===========================
Populates the Supabase `sector_benchmarks` table for all 11 GICS sectors.

Per-sector smart mode:
  - Sectors without historical data (year 2015) → full backfill (16 annual, 80 quarterly)
  - Sectors with history → daily refresh (3 annual, 12 quarterly)
  - Already-stored periods are skipped (historical benchmarks never change)

Usage:
    cd backend
    python -m scripts.backfill_sector_benchmarks                      # Smart mode (all sectors)
    python -m scripts.backfill_sector_benchmarks --sector Healthcare  # Single sector
    python -m scripts.backfill_sector_benchmarks --backfill           # Force full backfill for all
"""

import argparse
import asyncio
import logging
import os
import sys

# Ensure backend app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.sector_benchmark_service import (  # noqa: E402
    get_sector_benchmark_service,
    CANONICAL_SECTORS,
)

logger = logging.getLogger("backfill_sector_benchmarks")


async def main(args: argparse.Namespace) -> None:
    service = get_sector_benchmark_service()

    sectors_filter = None
    if args.sector:
        if args.sector not in CANONICAL_SECTORS:
            logger.error(
                f"Unknown sector '{args.sector}'. "
                f"Valid sectors: {sorted(CANONICAL_SECTORS)}"
            )
            sys.exit(1)
        sectors_filter = [args.sector]

    result = await service.compute_all_benchmarks(
        force=True,
        backfill=args.backfill,
        sectors=sectors_filter,
    )

    logger.info(f"Result: {result}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill sector benchmarks for all 11 GICS sectors"
    )
    parser.add_argument(
        "--sector",
        type=str,
        help="Process a single sector (e.g. 'Healthcare')",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Force full historical backfill for all sectors",
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    asyncio.run(main(parser.parse_args()))
