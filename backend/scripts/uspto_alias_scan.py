"""USPTO assignee-name alias scanner.

Walks the top-N tickers by market cap, queries USPTO with the current
normalizer, and for any ticker whose hit-count looks too low for its
employee count / mkt cap, probes plausible alternates (first word,
ticker symbol, two-word prefix) to find the legal entity that USPTO
actually files patents under.

Output: /tmp/uspto_alias_scan_results.json with full results, plus
a printed summary of recommended additions to
backend/data/uspto_assignee_aliases.json. The recommendations are NOT
auto-applied — the operator reviews and adds them manually.

Run:
    backend/venv/bin/python backend/scripts/uspto_alias_scan.py [TOP_N]

Default TOP_N = 500. Throttled to ~30 USPTO + ~30 FMP calls/min.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from dotenv import load_dotenv

load_dotenv(REPO / "backend" / ".env")

import httpx

from app.integrations.fmp import get_fmp_client
from app.services.ip_intel_service import _normalize_assignee, _load_assignee_aliases

USPTO_URL = "https://api.uspto.gov/api/v1/patent/applications/search"
USPTO_KEY = os.environ.get("USPTO_API_KEY") or ""
SINCE_YEAR = 2021

THROTTLE_SECONDS = 2.0      # ~30 calls/min, comfortably under USPTO's limit
SUSPICIOUS_HIT_THRESHOLD = 20
MIN_EMPLOYEES_FOR_PROBE = 1000
MIN_MARKET_CAP_FOR_PROBE = 5_000_000_000.0   # $5B floor — below this we don't care
RECOMMENDATION_MULTIPLIER = 2.0  # alternate must beat baseline by 2× to recommend


async def query_uspto(client: httpx.AsyncClient, assignee: str) -> int:
    body = {
        "q": (
            f'assignmentBag.assigneeBag.assigneeNameText:"{assignee}" '
            f'AND applicationMetaData.filingDate:[{SINCE_YEAR}-01-01 TO 2099-12-31]'
        ),
        "pagination": {"offset": 0, "limit": 1},
    }
    try:
        r = await client.post(
            USPTO_URL,
            headers={"X-API-KEY": USPTO_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
    except Exception as exc:
        print(f"  [warn] USPTO request failed for {assignee!r}: {exc}")
        return -1
    if r.status_code == 404:
        return 0
    if r.status_code != 200:
        return -1
    try:
        return int((r.json() or {}).get("count") or 0)
    except Exception:
        return -1


def candidate_alternates(name: str, ticker: str) -> List[str]:
    """Produce 3-4 plausible alternates worth probing for a low-hit baseline."""
    seen: List[str] = []
    for alt in (
        name.split()[0] if name else "",
        ticker,
        " ".join(name.split()[:2]) if len(name.split()) >= 2 else "",
        name.replace(",", "").replace(".", "").strip(),
    ):
        alt = (alt or "").strip()
        if alt and alt not in seen:
            seen.append(alt)
    return seen


def load_universe_top_n(top_n: int) -> List[Tuple[str, float]]:
    path = REPO / "backend" / "data" / "industry_universe.json"
    data = json.loads(path.read_text())
    rows: List[Tuple[str, float]] = []
    for ind in data.get("industries", []) or []:
        for tkr, cap in (ind.get("market_caps") or {}).items():
            if cap and tkr:
                rows.append((tkr.upper(), float(cap)))
    rows.sort(key=lambda r: r[1], reverse=True)
    # Dedup while preserving order (some tickers appear in multiple industries)
    seen: set = set()
    unique: List[Tuple[str, float]] = []
    for tkr, cap in rows:
        if tkr in seen:
            continue
        seen.add(tkr)
        unique.append((tkr, cap))
    return unique[:top_n]


async def main(top_n: int) -> None:
    if not USPTO_KEY:
        sys.exit("USPTO_API_KEY missing from backend/.env")

    existing_aliases = _load_assignee_aliases() or {}
    universe = load_universe_top_n(top_n)
    print(
        f"Scanning top {len(universe)} tickers by market cap "
        f"(existing aliases: {len(existing_aliases)}). "
        f"Throttle: 1 call every {THROTTLE_SECONDS}s."
    )

    fmp = get_fmp_client()
    findings: List[Dict[str, Any]] = []
    recommendations: List[Dict[str, Any]] = []
    started = time.time()

    async with httpx.AsyncClient() as client:
        for idx, (ticker, mkt_cap) in enumerate(universe, start=1):
            try:
                profile = await fmp.get_company_profile(ticker)
            except Exception as exc:
                print(f"  [{idx:>3}/{top_n}] {ticker}  [skip] FMP profile failed: {exc}")
                await asyncio.sleep(THROTTLE_SECONDS)
                continue
            name = (profile or {}).get("companyName") or ""
            employees = (profile or {}).get("fullTimeEmployees")
            try:
                employees_int = int(employees) if employees else 0
            except (TypeError, ValueError):
                employees_int = 0

            normalized = _normalize_assignee(name, ticker=ticker)
            baseline = await query_uspto(client, normalized) if normalized else 0
            await asyncio.sleep(THROTTLE_SECONDS)

            is_aliased = ticker in existing_aliases
            verdict = "ok"
            best_alt: Optional[Tuple[str, int]] = None

            # Only probe alternates when the company is large enough to
            # plausibly have patents and the baseline came back low.
            if (
                not is_aliased
                and baseline < SUSPICIOUS_HIT_THRESHOLD
                and employees_int >= MIN_EMPLOYEES_FOR_PROBE
                and mkt_cap >= MIN_MARKET_CAP_FOR_PROBE
            ):
                verdict = "suspicious"
                for alt in candidate_alternates(name, ticker):
                    if alt == normalized:
                        continue
                    hits = await query_uspto(client, alt)
                    await asyncio.sleep(THROTTLE_SECONDS)
                    if hits > (best_alt[1] if best_alt else baseline):
                        best_alt = (alt, hits)
                if best_alt and best_alt[1] >= baseline * RECOMMENDATION_MULTIPLIER:
                    verdict = "recommend"
                    recommendations.append({
                        "ticker": ticker, "fmp_name": name,
                        "normalized": normalized, "baseline_hits": baseline,
                        "alias": best_alt[0], "alias_hits": best_alt[1],
                        "mkt_cap_b": round(mkt_cap / 1e9, 1),
                        "employees": employees_int,
                    })

            findings.append({
                "ticker": ticker, "fmp_name": name,
                "normalized": normalized, "baseline_hits": baseline,
                "mkt_cap_b": round(mkt_cap / 1e9, 1),
                "employees": employees_int, "is_aliased": is_aliased,
                "verdict": verdict,
                "best_alt": best_alt,
            })

            mark = "★" if verdict == "recommend" else (
                "?" if verdict == "suspicious" else ("·" if is_aliased else " ")
            )
            print(
                f"  [{idx:>3}/{top_n}] {mark} {ticker:6s} {name[:30]:30s} "
                f"norm={normalized[:20]:20s} hits={baseline:>6} "
                f"alt={best_alt[0] if best_alt else '-':<20s} alt_hits={best_alt[1] if best_alt else '-'}"
            )

            # Save incrementally so a crash doesn't lose progress.
            if idx % 25 == 0:
                Path("/tmp/uspto_alias_scan_results.json").write_text(
                    json.dumps({"scanned": idx, "findings": findings, "recommendations": recommendations}, indent=2)
                )

    Path("/tmp/uspto_alias_scan_results.json").write_text(
        json.dumps({
            "scanned": len(findings),
            "elapsed_seconds": round(time.time() - started, 1),
            "findings": findings,
            "recommendations": recommendations,
        }, indent=2)
    )

    print()
    print(f"Done in {round(time.time() - started, 1)}s. Scanned {len(findings)} tickers.")
    print(f"Already aliased: {sum(1 for f in findings if f['is_aliased'])}")
    print(f"Suspicious (probed alternates): {sum(1 for f in findings if f['verdict'] in ('suspicious','recommend'))}")
    print(f"Recommended new aliases: {len(recommendations)}")
    if recommendations:
        recommendations.sort(key=lambda r: r["mkt_cap_b"], reverse=True)
        print()
        print("Top recommendations (sorted by market cap):")
        print(f"  {'TICKER':<8} {'NORMALIZED':<25} {'BASELINE':>8} -> {'ALIAS':<25} {'NEW HITS':>10}  MKT_CAP")
        for r in recommendations[:30]:
            print(
                f"  {r['ticker']:<8} {r['normalized'][:25]:<25} {r['baseline_hits']:>8} -> "
                f"{r['alias'][:25]:<25} {r['alias_hits']:>10}  ${r['mkt_cap_b']}B"
            )
        print()
        print("Full results in /tmp/uspto_alias_scan_results.json")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    asyncio.run(main(n))
