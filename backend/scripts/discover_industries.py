"""Discovery: enumerate every FMP industry classification we'll ever need.

Run quarterly (or when the weekly industry_dossier job logs an
unrecognized industry) to update `backend/data/industry_universe.json`.
That file is the source of truth for which industries the weekly
`industry_dossier_service.recompute_all()` job processes.

Coverage strategy — canonical industry list:
  1. `/stable/available-industries` returns FMP's complete industry
     classification (~159 distinct strings). This is THE list FMP uses
     when populating company profiles — every ticker FMP tracks falls
     into one of these industries.
  2. For each industry, `/stable/company-screener?industry=X&isActivelyTrading=true`
     returns every active US-listed ticker FMP tracks for that industry
     (not just S&P 500 / Nasdaq / Dow constituents). Each row carries
     `sector` and `marketCap` so we can build the industry → sector
     mapping and seed HHI inputs in the same pass.

Total FMP cost: ~160 calls (1 + 159). Well under the daily quota and
fast (~30s wall clock).

Output:
    backend/data/industry_universe.json:
    {
      "generated_at": "2026-05-22T17:00:00Z",
      "source": "fmp /stable/available-industries + /stable/company-screener",
      "industry_count": 159,
      "ticker_count": 12345,
      "industries": [
        {
          "industry": "Software - Infrastructure",
          "sector": "Technology",
          "tickers": ["MSFT", "ORCL", "PANW", "PLTR", ...]
        },
        ...
      ]
    }

Usage:
    ./backend/venv/bin/python backend/scripts/discover_industries.py

Idempotent — re-running overwrites the universe file with a fresh snapshot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Make `app.*` importable when running from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.integrations.fmp import FMPClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
# Silence the verbose per-request httpx INFO line; we get ~160 of them.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


_OUTPUT_PATH = _REPO_ROOT / "data" / "industry_universe.json"


async def _list_industries(fmp: FMPClient) -> List[str]:
    """Pull FMP's canonical industry classification list."""
    rows = await fmp._make_request("available-industries")
    if not isinstance(rows, list):
        return []
    out: List[str] = []
    for row in rows:
        name = (row.get("industry") if isinstance(row, dict) else "") or ""
        name = name.strip()
        if name:
            out.append(name)
    return sorted(set(out))


async def _screener_for_industry(
    fmp: FMPClient, industry: str,
) -> List[Dict[str, Any]]:
    """Every actively-traded US-listed ticker FMP tracks for an industry."""
    try:
        rows = await fmp._make_request(
            "company-screener",
            params={
                "industry": industry,
                "isActivelyTrading": "true",
                "limit": "1000",
            },
        )
    except Exception as exc:
        logger.warning("screener failed for industry=%r: %s", industry, exc)
        return []
    return rows if isinstance(rows, list) else []


def _aggregate(
    by_industry: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Resolve each (industry → screener rows) into the canonical universe
    entry: {industry, sector, tickers, market_caps}.

    Sector is taken from the modal value across rows — FMP occasionally
    has noise (one mis-labeled ticker shouldn't move the sector).

    `market_caps` is a dict {symbol: cap_usd} captured from the screener
    response. Persisting caps here means the weekly dossier job doesn't
    need to make ~9K individual quote calls (which would rate-limit) —
    it reads caps directly. Caps go stale at the universe-regeneration
    cadence (quarterly), which is fine for relative-share HHI math.
    """
    out: List[Dict[str, Any]] = []
    for industry, rows in by_industry.items():
        if not rows:
            continue
        sector_counts: Dict[str, int] = {}
        caps: Dict[str, float] = {}
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            sec = (r.get("sector") or "").strip()
            cap = r.get("marketCap")
            if sym:
                if isinstance(cap, (int, float)) and cap > 0:
                    caps[sym] = float(cap)
                else:
                    caps[sym] = 0.0
            if sec:
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
        if not caps:
            continue
        sector = (
            max(sector_counts.items(), key=lambda kv: kv[1])[0]
            if sector_counts else "Unknown"
        )
        out.append({
            "industry": industry,
            "sector": sector,
            "tickers": sorted(caps.keys()),
            "market_caps": caps,
        })
    out.sort(key=lambda d: (d["sector"], d["industry"]))
    return out


async def main() -> None:
    fmp = FMPClient()
    try:
        industries = await _list_industries(fmp)
        logger.info("FMP available-industries: %d entries", len(industries))

        sem = asyncio.Semaphore(8)
        by_industry: Dict[str, List[Dict[str, Any]]] = {}

        async def _one(name: str) -> None:
            async with sem:
                rows = await _screener_for_industry(fmp, name)
                by_industry[name] = rows
                logger.info("  %-45s %d tickers", name, len(rows))

        await asyncio.gather(*[_one(i) for i in industries])

        aggregated = _aggregate(by_industry)
        total_tickers = sum(len(e["tickers"]) for e in aggregated)
        logger.info(
            "Industries with constituents: %d  Total tickers: %d",
            len(aggregated), total_tickers,
        )

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "fmp /stable/available-industries + /stable/company-screener",
            "industry_count": len(aggregated),
            "ticker_count": total_tickers,
            "industries": aggregated,
        }
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
        logger.info("Wrote %s", _OUTPUT_PATH)

        # Sanity summary
        per_sector: Dict[str, int] = {}
        for entry in aggregated:
            per_sector[entry["sector"]] = per_sector.get(entry["sector"], 0) + 1
        print("\nIndustries per sector:")
        for sector, n in sorted(per_sector.items()):
            print(f"  {sector:<25} {n}")
    finally:
        await fmp.close()


if __name__ == "__main__":
    asyncio.run(main())
