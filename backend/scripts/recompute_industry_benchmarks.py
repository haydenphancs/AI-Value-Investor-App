#!/usr/bin/env python3
"""
Recompute the broad-universe INDUSTRY + sector benchmarks (local / one-off).

Rebuilds BOTH levels in `sector_benchmarks` (industry rows + the '' sector aggregate)
over `benchmark_universe.json`. For the long full run prefer the Railway admin endpoint
(POST /api/v1/admin/refresh-industry-benchmarks) so it survives off your laptop; this
script is for local validation / a single sector.

WHICH UNIVERSE A RUN USES (2026-10-07):
  * By default, the copy in the private `universe-data` Supabase Storage bucket — the one the
    scheduled quarterly and weekly runs use. A local backend/data/benchmark_universe.json is
    NOT read; when one exists and differs, the run says so at WARNING (newer local file) or
    INFO (older one).
  * `--universe PATH` — that file instead, and Storage is not asked. Use it to validate a
    freshly built universe BEFORE uploading it. A file that cannot be read stops the run
    (nothing written).
  * With no `--universe`, the file in `UNIVERSE_DATA_DIR` when that variable is set — the
    same explicit choice, made through the environment.
  ⚠️ Without `--dry-run`, a run from a local file writes PRODUCTION medians from a universe
  the scheduled runs do not use: upload the file to the bucket first unless that is the point.

THE JOB CLAIM (2026-10-07, review round 3 P3-4). A run that WRITES sector rows (no
`--industry`, no `--dry-run`) takes the same durable claim as the scheduled job and the admin
routes — `industry_benchmark_quarterly` (fiscal) or `ttm_benchmark_weekly` (`--ttm`) — so it
can never run on top of the quarterly chain, the weekly TTM job or an admin refresh (two
~47k-call FMP bursts writing the same rows). When the claim is refused (another run holds it,
a run already settled the UTC day, the job is switched off, or the ledger is unreadable) the
script starts nothing, says why, and exits 4. The claim is released on every path. A full
sweep that returns settles the day exactly as the scheduled run does; a `--sector` run never
settles it (it is not the whole sweep), and a run that is incomplete, refused by the service or
crashes leaves the day open for a retry. `--industry` and `--dry-run` runs take no claim.

LOGGING. FMP's key travels in the query string, and httpx logs every request URL at INFO:
`configure_logging` lowers httpx/httpcore to WARNING and scrubs every console line (traceback
included) with `SecretRedactingFilter`, as app/main.py does.

Exit codes: 0 done, 1 crashed (logged with its stack), 3 not completed — the service refused
the run or left sectors behind (`IndustryBenchmarkRecomputeSkipped` / `...Incomplete`; its
ERROR above says why; retry), 4 claim refused (nothing started).

Usage:
    cd backend
    ./venv/bin/python -m scripts.build_benchmark_universe            # build the universe (backend/data/)
    # validate it BEFORE uploading — one industry, nothing written:
    ./venv/bin/python -m scripts.recompute_industry_benchmarks --universe data/benchmark_universe.json \\
        --industry 'Asset Management' --dry-run
    # then upload data/benchmark_universe.json to the `universe-data` bucket, and:
    ./venv/bin/python -m scripts.recompute_industry_benchmarks                    # all sectors, skip <24h fresh
    ./venv/bin/python -m scripts.recompute_industry_benchmarks --skip-recent-hours 0   # force full
    ./venv/bin/python -m scripts.recompute_industry_benchmarks --sector Technology     # one sector (fast validation)
    ./venv/bin/python -m scripts.recompute_industry_benchmarks --ttm                   # the TTM rows instead
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, TextIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.log_redaction import SecretRedactingFilter, redact_secrets  # noqa: E402
from app.services import notification_jobs  # noqa: E402
from app.services.industry_benchmark_service import (  # noqa: E402
    IndustryBenchmarkRecomputeIncomplete,
    IndustryBenchmarkRecomputeSkipped,
    get_industry_benchmark_service,
)
# `_ENV_DIR` is the resolver's own name for UNIVERSE_DATA_DIR — imported, not retyped, so the
# script and `universe_data.universe_dir` cannot disagree.
from app.services.universe_data import (  # noqa: E402
    BENCHMARK_UNIVERSE,
    _ENV_DIR,
    universe_path,
)

logger = logging.getLogger("recompute_industry_benchmarks")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INCOMPLETE = 3
EXIT_CLAIM_REFUSED = 4

# The scheduled jobs' ledger keys and claim stale window. They live in app/main.py, which this
# script must not import (it configures logging, starts Sentry in production and pulls in
# every router); `tests/test_benchmark_producer_round4_script.py` pins these equal to it.
JOB_FISCAL = "industry_benchmark_quarterly"   # main.JOB_INDUSTRY_BENCHMARK_QUARTERLY
JOB_TTM = "ttm_benchmark_weekly"              # main.JOB_TTM_BENCHMARK_WEEKLY
CLAIM_STALE_SECONDS = 3 * 3600                # main._CHAIN_PHASE_STALE_SECONDS

# Why a claim was refused → what to do (the admin route's `_BENCHMARK_REFUSAL_COPY`, for an
# operator at a terminal).
_REFUSAL_COPY: Dict[str, str] = {
    "held": (
        "another run holds the claim right now — the scheduled run (the quarterly chain's "
        "medians phase, or the Sunday TTM job), an admin refresh, or an earlier script run — "
        "or one crashed less than 3 h ago. Nothing was started: wait for it to finish (or for "
        "the claim to go stale)"
    ),
    "already_ran_today": (
        "a run already completed and settled today (UTC). Nothing was started: retry after "
        "00:00 UTC, or clear run_day for this job in notification_job_state if a re-run is "
        "truly needed"
    ),
    "disabled": (
        "the job is switched off (notification_job_state.enabled = false). Nothing was started"
    ),
    "claim_failed": (
        "the claim RPC failed (the ledger answered no without a holder). Nothing was started — "
        "retry shortly"
    ),
    "ledger_unreadable": (
        "the job ledger could not be read. Nothing was started — retry shortly"
    ),
}


def configure_logging(stream: Optional[TextIO] = None) -> None:
    """INFO to the console with every line scrubbed of secrets, and httpx's per-request INFO
    lines (`HTTP Request: GET …&apikey=<key>`) silenced. Called by `__main__` before anything
    logs; `stream` is for tests (default: stderr)."""
    options: Dict[str, Any] = {
        "level": logging.INFO, "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
    }
    if stream is not None:
        options["stream"] = stream
    logging.basicConfig(**options)
    # As app/main.py does: every record that reaches the console, from ANY module (the FMP
    # client's own warnings echo the request URL), is scrubbed of `apikey=` and the rest.
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
            handler.addFilter(SecretRedactingFilter())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def resolve_universe_file(cli_path: Optional[str]) -> Optional[str]:
    """The universe file this run is explicitly told to use, or None for the bucket copy:
    `--universe PATH` first, else the file in `UNIVERSE_DATA_DIR` when that is set."""
    if cli_path:
        return cli_path
    if os.environ.get(_ENV_DIR):
        return str(universe_path(BENCHMARK_UNIVERSE))
    return None


def claim_job_for(args: argparse.Namespace) -> Optional[str]:
    """The ledger job a run must claim, or None: only a run that writes sector rows (no
    `--industry` validation, no `--dry-run`) competes with the scheduled job."""
    if args.dry_run or args.industry:
        return None
    return JOB_TTM if args.ttm else JOB_FISCAL


def claim_refusal_reason(state: Optional[dict], today: str) -> str:
    """Why `claim_scheduled` said no, from the ledger row — the admin route's
    `_dossier_claim_refusal` split (pinned equal by the round-4 script test)."""
    if state is None:
        return "ledger_unreadable"
    if not state.get("enabled", True):
        return "disabled"
    if str(state.get("run_day") or "")[:10] == today:
        return "already_ran_today"
    if state.get("claim_at"):
        return "held"
    return "claim_failed"


async def _run_under_claim(
    job: str, run: Callable[[], Awaitable[Any]], *, settles: bool, scope: str,
) -> int:
    """Take `job`'s claim, run `run()` under it and release it with the outcome — the admin
    route's `_start_claimed_benchmark_refresh` + `_run_claimed_benchmark_refresh` for a
    terminal. `settles`: whether a returned summary marks the UTC day done (a full sweep) or
    leaves it open (a `--sector` run)."""
    claimed_at = datetime.now(timezone.utc)
    granted = await asyncio.to_thread(
        notification_jobs.claim_scheduled, job, now=claimed_at,
        stale_seconds=CLAIM_STALE_SECONDS,
    )
    if not granted:
        state = await asyncio.to_thread(notification_jobs.scheduled_job_state, job)
        reason = claim_refusal_reason(state, claimed_at.date().isoformat())
        logger.error(
            "REFUSED (%s): could not take the %s claim — %s. claim_at=%s run_day=%s",
            reason, job, _REFUSAL_COPY[reason],
            (state or {}).get("claim_at"), (state or {}).get("run_day"),
        )
        return EXIT_CLAIM_REFUSED
    logger.info("Took the %s claim at %s (%s)", job, claimed_at.isoformat(), scope)

    success, items, error = False, 0, None
    try:
        result = await run()
        upserted = result.get("rows_upserted") if isinstance(result, dict) else None
        items = upserted if isinstance(upserted, int) and not isinstance(upserted, bool) else 0
        if not isinstance(result, dict):
            error = f"the recompute returned {type(result).__name__}, not a summary"
            logger.error("%s — the %s day is left open", error, job)
            return EXIT_FAILED
        logger.info("Result: %s", result)
        if settles:
            success = True
        else:
            error = f"operator script ({scope}): not a full sweep, so the day is left open"
            logger.info("%s — %s", job, error)
        return EXIT_OK
    except (IndustryBenchmarkRecomputeSkipped, IndustryBenchmarkRecomputeIncomplete) as exc:
        # The service already logged the cause at ERROR; this ties it to the claim.
        error = redact_secrets(str(exc))
        logger.warning(
            "The %s claim is released UNSETTLED (%s) — a same-day retry (or the scheduled "
            "run) recomputes the sectors left behind", job, exc.reason,
        )
        return EXIT_INCOMPLETE
    except asyncio.CancelledError:
        error = "interrupted (cancelled)"
        raise
    except Exception as exc:
        error = redact_secrets(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # SHIELDED, as in the admin route: a Ctrl-C mid-release would otherwise park the
        # claim for its whole stale window, which the scheduled run then waits out.
        await asyncio.shield(
            asyncio.to_thread(
                notification_jobs.finish_scheduled, job,
                success=success, items=items, error=error, now=claimed_at,
            )
        )
        logger.info(
            "Released the %s claim (%s)", job,
            "day settled" if success else f"day left open: {error}",
        )


async def main(args: argparse.Namespace) -> int:
    skip = args.skip_recent_hours if args.skip_recent_hours and args.skip_recent_hours > 0 else None
    sectors = [args.sector] if args.sector else None
    universe_file = resolve_universe_file(args.universe)
    if universe_file:
        logger.warning(
            "Using the universe file %s (%s), not the universe-data bucket copy%s",
            universe_file, "--universe" if args.universe else _ENV_DIR,
            "" if args.dry_run else " — and this is NOT a dry run: production medians will "
            "be written from it",
        )

    async def run() -> Any:
        service = get_industry_benchmark_service()
        # --ttm → the trailing-twelve-month current-snapshot rows (period_type='ttm');
        # default → the fiscal annual/quarterly time-series rows.
        fn = service.recompute_all_ttm if args.ttm else service.recompute_all
        return await fn(
            skip_if_fresh_hours=skip,
            sectors=sectors,
            industries=args.industry,
            dry_run=args.dry_run,
            universe_file=universe_file,
        )

    job = claim_job_for(args)
    try:
        if job is None:
            logger.info("Result: %s", await run())
            return EXIT_OK
        scope = f"--sector {args.sector}" if args.sector else "full sweep"
        return await _run_under_claim(job, run, settles=sectors is None, scope=scope)
    except (IndustryBenchmarkRecomputeSkipped, IndustryBenchmarkRecomputeIncomplete) as exc:
        logger.warning("Not completed (%s) — see the ERROR above", exc.reason)
        return EXIT_INCOMPLETE
    except Exception:
        # Logged here, through the redacting handler, rather than left to Python's own
        # traceback printer, which bypasses logging (an httpx error's text is its URL).
        logger.exception("The recompute crashed")
        return EXIT_FAILED


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(description="Recompute industry + sector benchmarks")
    parser.add_argument("--sector", type=str, default=None, help="Process a single sector (e.g. 'Technology')")
    parser.add_argument(
        "--industry", type=str, action="append", default=None,
        help="Compute ONLY this industry (repeatable); industry rows only, no sector aggregate. For validation.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute + log a sample of the medians and write NOTHING (sanity check before a real run).",
    )
    parser.add_argument("--skip-recent-hours", type=int, default=24, help="Skip sectors computed within N hours (0 = force full)")
    parser.add_argument(
        "--ttm", action="store_true",
        help="Compute the TTM current-snapshot rows (period_type='ttm') instead of the fiscal series. Additive — leaves fiscal rows intact.",
    )
    parser.add_argument(
        "--universe", type=str, default=None, metavar="PATH",
        help=(
            "Compute from THIS benchmark_universe.json instead of the universe-data bucket copy "
            f"(default: the bucket; or the file in ${_ENV_DIR} when that is set). Validate a "
            "freshly built universe with it before uploading."
        ),
    )
    sys.exit(asyncio.run(main(parser.parse_args())))
