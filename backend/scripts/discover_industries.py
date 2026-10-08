"""Build `backend/data/industry_universe.json`: the benchmark builder at floor 0.

The industry universe is every FMP industry with its US-listed operating companies and
their market caps. It feeds the quarterly industry dossier (HHI, constituent counts), the
moat peer averages (`industry_moat_benchmark_service`) and the report's competitor
candidates (`ticker_report_data_collector._industry_universe_peers`).

Since 2026-10-08 it is built by `scripts.build_benchmark_universe` with NO market-cap floor
— the same rules as the benchmark file, so the same defects cannot come back:

  * US-listed (NYSE / NASDAQ / AMEX) operating common shares only — no ETFs, open-end or
    closed-end funds, `.TO` duplicates, notes, preferreds, warrants or units (the May 2026
    file carried VOO, SPY, 877 CAD-priced `.TO` rows and AT&T's own note);
  * one vote per issuer inside an industry, the hand-checked issuer table, and one vote per
    set of statements (the `ratios-ttm` twin pass);
  * one screener call per industry, fail-closed: a failed request, an industry that fills
    the whole page (never paged — a second page can lose a row) or a fingerprint outage
    fails the build (exit 1) and nothing is written;
  * compared with the file it replaces: a shrink over 10%, a vanished industry, or a file
    built at another floor is REFUSED (exit 3) unless the matching flag is given;
  * written atomically.

The old script sent only `industry` + `isActivelyTrading` + `limit=1000`, logged a failed
industry and wrote it as EMPTY, and wrote non-atomically with no shrink guard.

It is a SEPARATE file from `benchmark_universe.json` ($500M floor, the peer MEDIANS) — never
point one at the other (`--allow-floor-change` exists for a deliberate change only). The log
lines say "benchmark universe" because they are the builder's own.

Output: `industries[]` of `{industry, sector, tickers, market_caps}` plus `generated_at`,
`source`, `market_cap_floor` (0), `industry_count` and `ticker_count` — every key a reader
uses (`universe_data.load_universe` reads `industries` and logs `ticker_count`; the dossier,
moat and collector read `industry`, `sector`, `tickers`, `market_caps`).
It is NOT in git (FMP ToS §2.6.1): upload it to the private `universe-data` bucket, then
redeploy (the app keeps the file in memory for the life of the process).

Cost: ~160 screener calls + one `ratios-ttm` per kept row (~5,500), ~19 min paced. Never run
it Sun 02:00-08:00 UTC or while a benchmark sweep runs (it shares the production FMP key).

Usage (from backend/):
    ./venv/bin/python -m scripts.discover_industries
    ./venv/bin/python -m scripts.discover_industries --allow-shrink 50 \
        --allow-missing "Asset Management - Bonds"
    ./venv/bin/python -m scripts.discover_industries --output /tmp/iu.json   # a dry build

The shrink guard compares with the CURRENT local file: download the live copy from the
bucket into backend/data/ first. Exit codes: as `scripts.build_benchmark_universe` (0
written · 1 nothing written, a request or the build failed · 3 refused by a guard).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.log_redaction import SecretRedactingFilter  # noqa: E402
from scripts import build_benchmark_universe as builder  # noqa: E402

logger = logging.getLogger(__name__)
logger.addFilter(SecretRedactingFilter())

_OUTPUT_PATH = _REPO_ROOT / "data" / "industry_universe.json"
# No floor: the dossier's HHI and constituent counts, and the moat peer averages, need the
# small caps too. Fixed — a different floor would be a different file.
INDUSTRY_UNIVERSE_FLOOR = 0


async def main(
    *,
    output: Path = _OUTPUT_PATH,
    allow_shrink: builder.ShrinkOverride = None,
    allow_missing: Optional[Iterable[str]] = (),
    allow_floor_change: bool = False,
    skip_twin_scan: bool = False,
) -> int:
    """The builder at floor 0, writing `output`. Returns the builder's exit code."""
    logger.info("industry universe: building %s at floor 0 with the benchmark builder",
                output)
    return await builder.main(
        INDUSTRY_UNIVERSE_FLOOR,
        output=output,
        allow_shrink=allow_shrink,
        allow_missing=allow_missing,
        allow_floor_change=allow_floor_change,
        skip_twin_scan=skip_twin_scan,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build industry_universe.json (the benchmark builder at floor 0)")
    parser.add_argument("--allow-shrink", nargs="?", type=builder._shrink_percent_arg,
                        default=None, const=builder._DEFAULT_ALLOW_SHRINK_PERCENT,
                        metavar="PCT",
                        help="Write even when the ticker count drops more than "
                             f"{builder._MAX_SHRINK_PERCENT}%% — up to PCT%% (default "
                             f"{builder._DEFAULT_ALLOW_SHRINK_PERCENT} when given bare)")
    parser.add_argument("--allow-missing", action="append", default=[], metavar="INDUSTRY",
                        help="An industry that may have no constituents now (repeatable)")
    parser.add_argument("--output", type=Path, default=_OUTPUT_PATH,
                        help="Where to write (default backend/data/industry_universe.json); "
                             "the guards compare with this file")
    builder._add_shared_flags(parser)
    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    # Every record that reaches the console, from ANY module, is scrubbed of `apikey=` and
    # the other secrets (as the builder's own `__main__` and app/main.py do).
    for _handler in logging.getLogger().handlers:
        _handler.addFilter(SecretRedactingFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _build_parser().parse_args()
    sys.exit(asyncio.run(main(output=args.output, allow_shrink=args.allow_shrink,
                              allow_missing=args.allow_missing,
                              allow_floor_change=args.allow_floor_change,
                              skip_twin_scan=args.skip_twin_scan)))
