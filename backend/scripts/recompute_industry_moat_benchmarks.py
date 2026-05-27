"""Bootstrap / on-demand re-run of the industry_moat_benchmarks table.

Use this once after migration 057 is applied to seed the table for the
first time (the quarterly background job in app.main also calls
recompute_all, but waiting for the next quarter is silly when we just
shipped the feature).

Examples:
    # Bootstrap a single industry (fast — useful for validation)
    backend/venv/bin/python backend/scripts/recompute_industry_moat_benchmarks.py \
        --industry "Software - Infrastructure"

    # Bootstrap top N industries (by ticker count — covers the high-traffic ones first)
    backend/venv/bin/python backend/scripts/recompute_industry_moat_benchmarks.py \
        --top-industries 50

    # Full backfill (~3-28 hours depending on FMP tier throughput)
    backend/venv/bin/python backend/scripts/recompute_industry_moat_benchmarks.py

Throttling is handled inside IndustryMoatBenchmarkService via its
per-industry + per-ticker semaphores; no extra sleep needed here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from dotenv import load_dotenv

load_dotenv(REPO / "backend" / ".env")

from app.services.industry_moat_benchmark_service import (
    get_industry_moat_benchmark_service,
    _load_universe_industries,
)


def _industries_sorted_by_ticker_count() -> list[str]:
    universe = _load_universe_industries()
    universe.sort(key=lambda u: len(u[1]), reverse=True)
    return [ind for ind, _ in universe]


async def main(args: argparse.Namespace) -> None:
    svc = get_industry_moat_benchmark_service()

    if args.industry:
        print(f"Computing benchmarks for industry: {args.industry!r}")
        written = await svc.compute_for_industry(args.industry)
        print(f"\nWritten {len(written)} pillar rows:")
        for pillar, stats in written.items():
            print(f"  {pillar:20s}  avg={stats['avg']}  n={stats['sample_size']}  "
                  f"p25={stats['p25']}  p75={stats['p75']}")
        return

    if args.top_industries:
        all_inds = _industries_sorted_by_ticker_count()
        targets = all_inds[: args.top_industries]
        print(f"Computing benchmarks for top {len(targets)} industries by ticker count")
        total_written = 0
        for i, ind in enumerate(targets, start=1):
            print(f"\n[{i}/{len(targets)}] {ind}")
            try:
                written = await svc.compute_for_industry(ind)
                total_written += len(written)
                for pillar, stats in written.items():
                    print(f"  {pillar:20s}  avg={stats['avg']}  n={stats['sample_size']}")
            except Exception as e:
                print(f"  ERROR: {e}")
        print(f"\nTotal pillar rows written: {total_written}")
        return

    print("Computing benchmarks for ALL industries — this may take hours.")
    summary = await svc.recompute_all()
    print(f"\nSummary: {json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--industry", type=str,
        help="Compute one industry by its exact name (e.g. 'Software - Infrastructure')",
    )
    g.add_argument(
        "--top-industries", type=int,
        help="Compute the top N industries by constituent ticker count",
    )
    asyncio.run(main(p.parse_args()))
