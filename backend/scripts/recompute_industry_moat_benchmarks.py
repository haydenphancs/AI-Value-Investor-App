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
    DEFAULT_SKIP_IF_FRESH_HOURS,
    _load_universe_industries,
    get_industry_moat_benchmark_service,
)


def _industries_sorted_by_ticker_count() -> list[str]:
    universe = _load_universe_industries()
    universe.sort(key=lambda u: len(u[1]), reverse=True)
    return [ind for ind, _ in universe]


async def main(args: argparse.Namespace) -> None:
    svc = get_industry_moat_benchmark_service()

    skip_fresh = args.skip_recent_hours

    if args.industry:
        print(f"Computing benchmarks for industry: {args.industry!r}")
        written = await svc.compute_for_industry(
            args.industry, skip_if_fresh_hours=skip_fresh,
        )
        if written.get("_skipped") == "fresh":
            print(f"  Skipped — already fresh within {skip_fresh}h.")
            return
        print(f"\nWritten {len(written)} pillar rows:")
        for pillar, stats in written.items():
            print(f"  {pillar:20s}  avg={stats['avg']}  n={stats['sample_size']}  "
                  f"p25={stats['p25']}  p75={stats['p75']}")
        return

    if args.top_industries:
        all_inds = _industries_sorted_by_ticker_count()
        targets = all_inds[: args.top_industries]
        print(
            f"Computing benchmarks for top {len(targets)} industries by ticker count "
            f"(skip_if_fresh_hours={skip_fresh})"
        )
        total_written = 0
        total_skipped = 0
        for i, ind in enumerate(targets, start=1):
            print(f"\n[{i}/{len(targets)}] {ind}")
            try:
                written = await svc.compute_for_industry(
                    ind, skip_if_fresh_hours=skip_fresh,
                )
                if written.get("_skipped") == "fresh":
                    total_skipped += 1
                    print(f"  (skipped — already fresh)")
                    continue
                total_written += len(written)
                for pillar, stats in written.items():
                    print(f"  {pillar:20s}  avg={stats['avg']}  n={stats['sample_size']}")
            except Exception as e:
                print(f"  ERROR: {e}")
        print(
            f"\nTotal pillar rows written: {total_written}   "
            f"Industries skipped (fresh): {total_skipped}"
        )
        return

    print(
        f"Computing benchmarks for ALL industries "
        f"(skip_if_fresh_hours={skip_fresh}). This may take ~50-90 min."
    )
    summary = await svc.recompute_all(skip_if_fresh_hours=skip_fresh)
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
    p.add_argument(
        "--skip-recent-hours", type=int, default=DEFAULT_SKIP_IF_FRESH_HOURS,
        help=(
            "Skip any industry that already has a benchmark row newer than "
            "this. Lets a Ctrl-C-aborted backfill resume by re-running the "
            "same command. Default 24; pass 0 to force a full recompute."
        ),
    )
    asyncio.run(main(p.parse_args()))
