"""Build the broad benchmark universe for INDUSTRY + SECTOR medians.

Per FMP industry, pulls every actively-traded US-listed ticker ABOVE a market-cap
floor (default $500M — excludes micro/penny-stock noise while keeping small+mid+large
caps, so medians are fair to small-cap companies). Groups by industry + modal parent
sector, capturing each ticker's market cap.

This is a SEPARATE file from `industry_universe.json` (which has NO floor and feeds the
moat/dossier jobs) — do not conflate them. Output: backend/data/benchmark_universe.json.
~160 FMP calls (1 available-industries + ~159 screener calls), ~30s.

Usage:
    cd backend
    python -m scripts.build_benchmark_universe                 # $500M floor
    python -m scripts.build_benchmark_universe --floor 1000000000   # $1B floor

Idempotent — re-running overwrites the file with a fresh snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.integrations.fmp import FMPClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_OUTPUT_PATH = _REPO_ROOT / "data" / "benchmark_universe.json"
_DEFAULT_FLOOR = 500_000_000  # $500M — small-cap inclusive, micro/penny excluded


async def _list_industries(fmp: FMPClient) -> List[str]:
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
    fmp: FMPClient, industry: str, floor: int,
) -> List[Dict[str, Any]]:
    """Every actively-traded US-listed ticker above the market-cap floor."""
    try:
        rows = await fmp._make_request(
            "company-screener",
            params={
                "industry": industry,
                "isActivelyTrading": "true",
                "marketCapMoreThan": str(floor),
                "limit": "1000",
            },
        )
    except Exception as exc:
        logger.warning("screener failed for industry=%r: %s", industry, exc)
        return []
    return rows if isinstance(rows, list) else []


def _aggregate(by_industry: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Resolve (industry → screener rows) → {industry, sector(modal), tickers, market_caps}."""
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
            if sym and isinstance(cap, (int, float)) and cap > 0:
                caps[sym] = float(cap)
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


async def main(floor: int) -> None:
    fmp = FMPClient()
    try:
        industries = await _list_industries(fmp)
        logger.info("FMP available-industries: %d entries (floor=$%.0fM)", len(industries), floor / 1e6)

        sem = asyncio.Semaphore(8)
        by_industry: Dict[str, List[Dict[str, Any]]] = {}

        async def _one(name: str) -> None:
            async with sem:
                rows = await _screener_for_industry(fmp, name, floor)
                by_industry[name] = rows
                logger.info("  %-45s %d tickers", name, len(rows))

        await asyncio.gather(*[_one(i) for i in industries])

        aggregated = _aggregate(by_industry)
        total = sum(len(e["tickers"]) for e in aggregated)
        logger.info("Industries with constituents: %d  Total tickers: %d", len(aggregated), total)

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "fmp /stable/available-industries + /stable/company-screener (marketCapMoreThan)",
            "market_cap_floor": floor,
            "industry_count": len(aggregated),
            "ticker_count": total,
            "industries": aggregated,
        }
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
        logger.info("Wrote %s", _OUTPUT_PATH)

        per_sector: Dict[str, int] = {}
        for e in aggregated:
            per_sector[e["sector"]] = per_sector.get(e["sector"], 0) + 1
        print("\nIndustries per sector:")
        for sector, n in sorted(per_sector.items()):
            print(f"  {sector:<25} {n}")
    finally:
        await fmp.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the broad benchmark universe")
    parser.add_argument("--floor", type=int, default=_DEFAULT_FLOOR, help="Market-cap floor in USD (default 500M)")
    asyncio.run(main(parser.parse_args().floor))
