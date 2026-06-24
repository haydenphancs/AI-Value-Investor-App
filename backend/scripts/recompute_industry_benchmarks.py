#!/usr/bin/env python3
"""
Recompute the broad-universe INDUSTRY + sector benchmarks (local / one-off).

Rebuilds BOTH levels in `sector_benchmarks` (industry rows + the '' sector aggregate)
over `benchmark_universe.json`. For the long full run prefer the Railway admin endpoint
(POST /api/v1/admin/refresh-industry-benchmarks) so it survives off your laptop; this
script is for local validation / a single sector.

Usage:
    cd backend
    python -m scripts.build_benchmark_universe            # build the universe first
    python -m scripts.recompute_industry_benchmarks                    # all sectors, skip <24h fresh
    python -m scripts.recompute_industry_benchmarks --skip-recent-hours 0   # force full
    python -m scripts.recompute_industry_benchmarks --sector Technology     # one sector (fast validation)
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.industry_benchmark_service import (  # noqa: E402
    get_industry_benchmark_service,
)

logger = logging.getLogger("recompute_industry_benchmarks")


async def main(args: argparse.Namespace) -> None:
    service = get_industry_benchmark_service()
    skip = args.skip_recent_hours if args.skip_recent_hours and args.skip_recent_hours > 0 else None
    sectors = [args.sector] if args.sector else None
    result = await service.recompute_all(skip_if_fresh_hours=skip, sectors=sectors)
    logger.info(f"Result: {result}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recompute industry + sector benchmarks")
    parser.add_argument("--sector", type=str, default=None, help="Process a single sector (e.g. 'Technology')")
    parser.add_argument("--skip-recent-hours", type=int, default=24, help="Skip sectors computed within N hours (0 = force full)")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(main(parser.parse_args()))
