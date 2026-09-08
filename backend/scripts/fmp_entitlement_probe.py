#!/usr/bin/env python3
"""Probe the LIVE FMP entitlement boundary. Read-only. The oracle for the rebuild.

WHY THIS IS A REPO SCRIPT AND NOT A SCRATCH FILE
------------------------------------------------
FMP enforced its Data Packages on 2026-09-03: anything outside the nine on the Order Form
answers `402 Restricted Endpoint`. Before that, every path returned 200 regardless of
entitlement, so "it works" proved nothing and the boundary had to be inferred from FMP's
product names — which is wrong in both directions (Search reads unbought but serves; the
Analyst section reads bought but only one endpoint is).

Enforcement means the API is now the authoritative answer, and every remaining phase of
the rebuild depends on being able to re-ask it. The original lived in a session scratchpad
and was lost with the session while the plan still cited it — hence this file.

USAGE
    ./backend/venv/bin/python backend/scripts/fmp_entitlement_probe.py
    ./backend/venv/bin/python backend/scripts/fmp_entitlement_probe.py --all

By default it probes only what the app calls plus the disputed cases. `--all` adds the
"owned but unused" opportunities.

READING THE OUTPUT
    200 + rows   ENTITLED
    200 + []     ambiguous — usually a non-trading `date` or a bad param, NOT a block.
                 Check before concluding anything: sector-performance-snapshot returns []
                 on a Saturday and that once looked like a revocation.
    402          BLOCKED. The reason string says so explicitly.
    404          retired path, NOT an entitlement problem.

⚠️ Blocking also happens per SYMBOL on endpoints we own: `historical-price-eod/full`
answers 200 for AAPL and SHOP.TO and 402 for ^GSPC / GCUSD / BTCUSD / EURUSD. The
SYMBOL-CLASS group below exists to keep that visible.

Never prints the API key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
BASE = "https://financialmodelingprep.com/stable"


def _api_key() -> str:
    for line in (BACKEND / ".env").read_text().splitlines():
        if line.strip().startswith("FMP_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("FMP_API_KEY not found in backend/.env")


EQ, ETF = "AAPL", "SPY"
TRADING_DAY = "2026-09-04"   # a known US session; a weekend/holiday returns [] not 402

CORE: list[tuple[str, str, dict]] = [
    # ── controls: known-unbought. If these 200, enforcement is off again. ──────────
    ("CONTROL", "funds/disclosure", {"symbol": "VTSAX", "year": 2026, "quarter": 2}),
    ("CONTROL", "earning-call-transcript", {"symbol": EQ, "year": 2026, "quarter": 2}),
    ("CONTROL", "esg-disclosures", {"symbol": EQ}),
    # ── quote family — the Phase 1 loss ───────────────────────────────────────────
    ("QUOTE", "quote", {"symbol": EQ}),
    ("QUOTE", "batch-quote", {"symbols": "AAPL,MSFT"}),
    ("QUOTE", "stock-price-change", {"symbol": EQ}),
    # ── market performance — the Phase 2 loss ─────────────────────────────────────
    ("MARKET-PERF", "biggest-gainers", {}),
    ("MARKET-PERF", "biggest-losers", {}),
    ("MARKET-PERF", "most-actives", {}),
    ("MARKET-PERF", "sector-performance-snapshot", {"date": TRADING_DAY}),
    ("MARKET-PERF", "industry-performance-snapshot", {"date": TRADING_DAY}),
    # ── analyst + calendar — Phase 3 ──────────────────────────────────────────────
    ("PHASE3", "analyst-estimates", {"symbol": EQ, "period": "annual", "limit": 2}),
    ("PHASE3", "grades", {"symbol": EQ}),
    ("PHASE3", "price-target-consensus", {"symbol": EQ}),
    ("PHASE3", "ratings-snapshot", {"symbol": EQ}),
    ("PHASE3", "dividends", {"symbol": EQ}),
    ("PHASE3", "splits", {"symbol": "NVDA"}),
    # ── symbol-class blocking on endpoints we DO own — Phase 4/5 ──────────────────
    ("SYMBOL-CLASS", "historical-price-eod/full", {"symbol": EQ}),
    ("SYMBOL-CLASS", "historical-price-eod/full", {"symbol": "SHOP.TO"}),
    ("SYMBOL-CLASS", "historical-price-eod/full", {"symbol": "^GSPC"}),
    ("SYMBOL-CLASS", "historical-price-eod/full", {"symbol": "GCUSD"}),
    ("SYMBOL-CLASS", "historical-price-eod/full", {"symbol": "BTCUSD"}),
    ("SYMBOL-CLASS", "sp500-constituent", {}),
    # ── the load-bearing replacements. A regression here breaks the app. ──────────
    ("REPLACEMENTS", "profile", {"symbol": EQ}),
    ("REPLACEMENTS", "company-screener", {"marketCapMoreThan": 2_000_000_000, "limit": 5}),
    ("REPLACEMENTS", "batch-eod", {"date": TRADING_DAY}),
    ("REPLACEMENTS", "market-capitalization-batch", {"symbols": "AAPL,MSFT"}),
    ("REPLACEMENTS", "historical-price-eod/non-split-adjusted", {"symbol": "NVDA"}),
    ("REPLACEMENTS", "search-symbol", {"query": "AAPL"}),
    ("REPLACEMENTS", "exchange-market-hours", {"exchange": "NASDAQ"}),
    # ── purchased packages, one probe each ────────────────────────────────────────
    ("PACKAGES", "income-statement", {"symbol": EQ, "limit": 1}),
    ("PACKAGES", "ratios-ttm", {"symbol": EQ}),
    ("PACKAGES", "earnings", {"symbol": EQ}),
    ("PACKAGES", "stock-peers", {"symbol": EQ}),
    ("PACKAGES", "etf/holdings", {"symbol": ETF}),
    ("PACKAGES", "institutional-ownership/symbol-positions-summary",
     {"symbol": EQ, "year": 2026, "quarter": 2}),
    ("PACKAGES", "historical-chart/5min", {"symbol": EQ}),
    ("PACKAGES", "news/stock", {"symbols": EQ}),
    ("PACKAGES", "insider-trading/search", {"symbol": EQ}),
    ("PACKAGES", "senate-trades", {"symbol": EQ}),
]

EXTRA: list[tuple[str, str, dict]] = [
    ("OPPORTUNITY", "owner-earnings", {"symbol": EQ}),
    ("OPPORTUNITY", "enterprise-values", {"symbol": EQ, "limit": 1}),
    ("OPPORTUNITY", "revenue-geographic-segmentation", {"symbol": EQ}),
    ("OPPORTUNITY", "employee-count", {"symbol": EQ}),
    ("OPPORTUNITY", "etf/asset-exposure", {"symbol": EQ}),
    ("OPPORTUNITY", "mergers-acquisitions-latest", {"page": 0, "limit": 5}),
    ("OPPORTUNITY", "news/press-releases", {"symbols": EQ}),
    ("OPPORTUNITY", "insider-trading/statistics", {"symbol": EQ}),
    ("OPPORTUNITY", "discounted-cash-flow", {"symbol": EQ}),
    ("OPPORTUNITY", "stock-list", {}),
]


def _shape(text: str) -> str:
    try:
        d = json.loads(text)
    except Exception:
        return f"non-JSON: {text[:70]}"
    if isinstance(d, dict) and "Error Message" in d:
        return f"ERROR: {str(d['Error Message'])[:80]}"
    if isinstance(d, list):
        return f"list[{len(d)}]" + ("  ⚠️ EMPTY" if not d else "")
    return f"dict({','.join(list(d)[:3])})"


async def _one(client: httpx.AsyncClient, key: str, group: str, path: str,
               params: dict, sem: asyncio.Semaphore):
    async with sem:
        p = dict(params)
        p["apikey"] = key
        try:
            r = await client.get(f"{BASE}/{path}", params=p)
        except Exception as e:
            return group, path, params, "EXC", f"{type(e).__name__}: {e}"[:70]
        return group, path, params, r.status_code, _shape(r.text)


async def main(probe_all: bool) -> int:
    key = _api_key()
    probes = CORE + (EXTRA if probe_all else [])
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=90.0) as c:
        rows = await asyncio.gather(
            *[_one(c, key, g, p, q, sem) for g, p, q in probes]
        )

    blocked, empty = [], []
    seen_groups: list[str] = []
    for g, *_ in rows:
        if g not in seen_groups:
            seen_groups.append(g)

    for g in seen_groups:
        print(f"\n{'=' * 108}\n{g}\n{'=' * 108}")
        for group, path, params, status, shape in rows:
            if group != g:
                continue
            sym = params.get("symbol") or params.get("symbols") or ""
            label = f"{path} {sym}".strip()
            flag = ""
            if status in (401, 402, 403):
                flag = "  🔴 BLOCKED"
                blocked.append((group, label, status))
            elif status == 404:
                flag = "  ⚫ 404 (retired path, not a licence issue)"
            elif "EMPTY" in shape or shape.startswith("ERROR"):
                flag = "  ⚠️ inspect — usually a param/date issue, not a block"
                empty.append((group, label, shape))
            print(f"  {str(status):<5} {label:<58} {shape}{flag}")

    print(f"\n{'#' * 108}\nSUMMARY: {len(blocked)} blocked, {len(empty)} empty/error\n{'#' * 108}")
    for group, label, status in blocked:
        print(f"  🔴 [{group}] {label}  ({status})")
    for group, label, shape in empty:
        print(f"  ⚠️  [{group}] {label}  {shape}")
    print(
        "\nCross-check anything surprising against "
        "backend/app/integrations/fmp_entitlements.py, which is the manifest the runtime "
        "guard and tests read. If this script and the manifest disagree, the API wins — "
        "update the manifest and the debt list in "
        "tests/test_fmp_entitlement_parity.py together."
    )
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true",
                    help="also probe the owned-but-unused opportunities")
    sys.exit(asyncio.run(main(ap.parse_args().all)))
