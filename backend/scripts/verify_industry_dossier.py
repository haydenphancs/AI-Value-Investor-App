"""Verification: run the dossier compute for every industry in the
universe and dump the results so the operator can spot-check.

This is what `industry_dossier_service.recompute_all()` does for the
weekly batch, but here we:
  - Don't write to Supabase (read-only verification)
  - Print a markdown table per row so anomalies are easy to scan
  - Emit a per-tier summary (industry / sector / all_industry counts)
  - Flag rows that look suspicious (0 TAM, null CAGR, missing concentration)

Usage:
    ./backend/venv/bin/python backend/scripts/verify_industry_dossier.py
    ./backend/venv/bin/python backend/scripts/verify_industry_dossier.py --csv > dossier.csv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.ERROR)

from app.services.industry_dossier_service import (  # noqa: E402
    IndustryDossier,
    get_industry_dossier_service,
)


_UNIVERSE_PATH = _REPO_ROOT / "data" / "industry_universe.json"


def _fmt_tam(b: Optional[float]) -> str:
    if b is None or b <= 0:
        return "—"
    if b >= 1000:
        return f"${b/1000:.1f}T"
    return f"${b:.0f}B"


def _fmt_cagr(p: Optional[float]) -> str:
    if p is None:
        return "—"
    sign = "+" if p >= 0 else ""
    return f"{sign}{p:.1f}%"


def _row_warnings(d: IndustryDossier) -> List[str]:
    """Flag values that look broken so a human can investigate."""
    w: List[str] = []
    if d.current_tam <= 0:
        w.append("0_TAM")
    if d.cagr_5y_pct is None:
        w.append("NULL_CAGR")
    if d.concentration_label is None:
        w.append("NULL_CONC")
    # Sanity: future TAM should be >= current * (clamped CAGR)
    if d.current_tam > 0 and d.future_tam < d.current_tam * 0.5:
        w.append("FUTURE_BELOW_HALF_OF_CURRENT")
    return w


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", action="store_true", help="Emit CSV instead of markdown")
    parser.add_argument("--anomalies-only", action="store_true",
                        help="Print only rows with warnings")
    args = parser.parse_args()

    universe = json.loads(_UNIVERSE_PATH.read_text())
    entries = universe.get("industries", [])
    if not entries:
        print("Universe file is empty — run discover_industries.py first.")
        return

    total_tickers = sum(len(e.get("tickers", [])) for e in entries)
    print(f"# Verification — {len(entries)} industries / {total_tickers} tickers", file=sys.stderr)
    print(f"# Using pre-captured market caps from universe file", file=sys.stderr)
    print(f"# Computing dossiers (FRED + Census calls per industry)...", file=sys.stderr)

    svc = get_industry_dossier_service()

    sem = asyncio.Semaphore(6)
    results: List[tuple[Dict[str, Any], IndustryDossier]] = []

    async def _compute_entry(entry: Dict[str, Any]) -> None:
        async with sem:
            try:
                dossier = await svc._compute_one(
                    industry=entry["industry"],
                    sector=entry["sector"],
                    tickers=entry.get("tickers", []),
                    caps_by_ticker=entry.get("market_caps") or {},
                )
                results.append((entry, dossier))
            except Exception as exc:
                print(f"# FAIL {entry['industry']!r}: {exc}", file=sys.stderr)

    await asyncio.gather(*[_compute_entry(e) for e in entries])

    # Sort: sector asc, then industry asc.
    results.sort(key=lambda r: (r[0]["sector"], r[0]["industry"]))

    # Summary stats first
    tier_counts: Dict[str, int] = {}
    warning_count = 0
    for _, d in results:
        tier_counts[d.source_grain] = tier_counts.get(d.source_grain, 0) + 1
        if _row_warnings(d):
            warning_count += 1

    print(f"# Computed {len(results)} dossiers", file=sys.stderr)
    print(f"# Tier breakdown: {tier_counts}", file=sys.stderr)
    print(f"# Rows with warnings: {warning_count}", file=sys.stderr)

    # Emit table
    if args.csv:
        cols = [
            "sector", "industry", "source_grain", "current_tam_b", "future_tam_b",
            "cagr_5y_pct", "lifecycle_phase", "concentration_label",
            "hhi", "top1_share_pct", "constituent_count", "warnings", "source_label",
        ]
        print(",".join(cols))
        for entry, d in results:
            w = "|".join(_row_warnings(d))
            row = [
                entry["sector"], d.industry, d.source_grain,
                str(d.current_tam), str(d.future_tam),
                str(d.cagr_5y_pct or ""), d.lifecycle_phase,
                d.concentration_label or "",
                str(d.hhi or ""), str(d.top1_share_pct or ""),
                str(d.constituent_count or 0),
                w, d.source_label.replace(",", ";"),
            ]
            print(",".join(row))
    else:
        # Markdown — source_label included so research-overridden rows
        # are visible at a glance (they'll cite SIA/IQVIA/etc. while
        # Phase A rows cite Census/FRED).
        print(f"\n| Sector | Industry | Grain | TAM (now → 5y) | CAGR | Lifecycle | Concentration | # Cos | Source | Warnings |")
        print(f"| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for entry, d in results:
            warns = _row_warnings(d)
            if args.anomalies_only and not warns:
                continue
            tam = f"{_fmt_tam(d.current_tam)} → {_fmt_tam(d.future_tam)}"
            # Truncate long source labels so the markdown table stays readable
            src = (d.source_label or "—")[:60]
            print(
                f"| {entry['sector']} | {d.industry} | {d.source_grain} | "
                f"{tam} | {_fmt_cagr(d.cagr_5y_pct)} | {d.lifecycle_phase} | "
                f"{d.concentration_label or '—'} | {d.constituent_count or 0} | "
                f"{src} | {','.join(warns) if warns else ''} |"
            )


if __name__ == "__main__":
    asyncio.run(main())
