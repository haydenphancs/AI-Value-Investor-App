#!/usr/bin/env python3
"""
Backfill Sector Benchmarks — RETIRED. This script refuses to run.

It used to drive `SectorBenchmarkService.compute_all_benchmarks`, the path `app/main.py`
documents as "a data-corruption button": it reads the BLOCKED `sp500-constituent`
endpoint, falls back to 55 hardcoded tickers and upserts 5-company medians over the
~5,700-company rows the quarterly industry job produces — on the same
`uq_sector_industry_metric_period` key, so the good rows are silently overwritten.
`POST /admin/refresh-sector-benchmarks` was re-pointed away from it for exactly that
reason; this file was the last live handle on it (2026-09-11).

What to run instead:
  * the quarterly job in `app/main.py` (`_run_industry_dossier_job`), or
  * `POST /admin/refresh-sector-benchmarks`, which calls
    `industry_benchmark_service.recompute_all` over the full universe.

Kept as a tombstone so the old command fails LOUDLY instead of "command not found".
"""

import sys

_MESSAGE = (
    "backfill_sector_benchmarks is retired: it recomputed sector medians from 55 hardcoded "
    "tickers and overwrote the ~5,700-company rows (see app/main.py, 'data-corruption "
    "button'). Use POST /admin/refresh-sector-benchmarks or the quarterly industry job."
)


def main() -> "NoReturn":  # noqa: F821 — annotation only
    print(_MESSAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
