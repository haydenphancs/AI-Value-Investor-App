"""
Load test for POST /research/generate — verifies the Phase-0 scaling fixes.

What it exercises:
  * Global admission gate  → 409 SYSTEM_BUSY ratio under burst.
  * Per-user cap           → 409 TOO_MANY_CONCURRENT_REPORTS (if one user fires
                              more than MAX_CONCURRENT_REPORTS_PER_USER).
  * Agent-run semaphore    → accepted reports drain at bounded concurrency.
  * Same-(ticker,persona)  → a hot-ticker burst collapses to one agent run
    dedup                    (watch the server logs: one "Shared cache MISS"
                              then "HIT"s / one FMP fan-out).
  * Event-loop health      → latency of parallel GET /health during the burst.
  * Narrative quality      → optional: follow accepted reports to completion and
                              count real vs. sentinel ("unavailable") narratives.

⚠️  COST: every ACCEPTED report charges 5 credits and burns real Gemini + FMP
    quota. Point this at a dev/staging user with plenty of credits, keep
    --concurrency modest, or rely on the global cap to reject the overflow.
    The REJECTED (409) requests are free — they fast-fail pre-charge.

Usage:
    export CAYDEX_BASE_URL=http://localhost:8000
    export CAYDEX_TOKEN=<a valid bearer token>           # from a logged-in user
    ./venv/bin/python scripts/load_test_reports.py \
        --concurrency 60 --hot-ticker AAPL --hot-share 0.5 --follow 5

    # Diverse-ticker burst (stresses FMP), no completion-follow:
    ./venv/bin/python scripts/load_test_reports.py --concurrency 100 --hot-share 0.0
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import httpx

# A spread of liquid large-caps for the "diverse ticker" portion of the burst.
_DIVERSE_TICKERS = [
    "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "JPM", "V", "MA",
    "UNH", "HD", "PG", "COST", "XOM", "CVX", "LLY", "ABBV", "KO", "PEP",
    "WMT", "DIS", "NFLX", "CRM", "AMD", "INTC", "ORCL", "ADBE", "QCOM", "TXN",
]
_PERSONAS = ["warren_buffett", "cathie_wood", "peter_lynch", "bill_ackman"]


def _build_requests(
    n: int, hot_ticker: str, hot_share: float, persona: str,
) -> List[Tuple[str, str]]:
    """Return n (ticker, persona) request specs. `hot_share` fraction go to
    `hot_ticker` (simulating a post-earnings rush); the rest spread across the
    diverse universe (simulating broad traffic)."""
    out: List[Tuple[str, str]] = []
    hot_count = int(round(n * hot_share))
    for _ in range(hot_count):
        out.append((hot_ticker, persona))
    for i in range(n - hot_count):
        out.append((_DIVERSE_TICKERS[i % len(_DIVERSE_TICKERS)], persona))
    return out


async def _fire_one(
    client: httpx.AsyncClient, ticker: str, persona: str,
) -> Dict[str, Any]:
    """Fire one /research/generate and return a result row."""
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            "/api/v1/research/generate",
            json={"stock_id": ticker, "investor_persona": persona},
        )
        elapsed = time.perf_counter() - t0
        body: Dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:
            pass
        return {
            "ticker": ticker,
            "status": resp.status_code,
            "error_code": body.get("error_code"),
            "report_id": body.get("report_id"),
            "elapsed": elapsed,
        }
    except Exception as e:
        return {
            "ticker": ticker,
            "status": -1,
            "error_code": f"{type(e).__name__}",
            "report_id": None,
            "elapsed": time.perf_counter() - t0,
        }


async def _health_probe(
    client: httpx.AsyncClient, stop: asyncio.Event,
) -> List[float]:
    """Poll GET /health every 0.5s during the burst; return latencies (s).
    A spiking tail = the report work is starving the event loop."""
    latencies: List[float] = []
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            await client.get("/health")
            latencies.append(time.perf_counter() - t0)
        except Exception:
            latencies.append(float("nan"))
        await asyncio.sleep(0.5)
    return latencies


async def _follow_to_completion(
    client: httpx.AsyncClient, report_id: str, timeout_s: float = 240.0,
) -> Dict[str, Any]:
    """Poll a report's ticker-report until completed/failed; classify whether
    the narratives are real or fell back to honest sentinels."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        r = await client.get(f"/api/v1/research/reports/{report_id}/status")
        status = (r.json() or {}).get("status") if r.status_code == 200 else None
        if status == "completed":
            tr = await client.get(
                f"/api/v1/research/reports/{report_id}/ticker-report"
            )
            data = tr.json() if tr.status_code == 200 else {}
            return {"report_id": report_id, "status": "completed",
                    "sentinel": _looks_sentinel(data)}
        if status == "failed":
            return {"report_id": report_id, "status": "failed", "sentinel": None}
        await asyncio.sleep(3)
    return {"report_id": report_id, "status": "timeout", "sentinel": None}


def _looks_sentinel(data: Dict[str, Any]) -> bool:
    """Heuristic: a degraded (quota-starved) report leaves narrative fields on
    their 'unavailable' fallback. Sample the executive summary + overall read."""
    if not isinstance(data, dict):
        return True
    text = " ".join(str(data.get(k, "")) for k in (
        "executive_summary_text",
    )).lower()
    oa = data.get("overall_assessment")
    if isinstance(oa, dict):
        text += " " + str(oa.get("text", "")).lower()
    return ("unavailable" in text) or (not text.strip())


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--concurrency", type=int, default=60)
    ap.add_argument("--hot-ticker", default="AAPL")
    ap.add_argument("--hot-share", type=float, default=0.5,
                    help="fraction of the burst aimed at the hot ticker (0..1)")
    ap.add_argument("--persona", default="warren_buffett", choices=_PERSONAS)
    ap.add_argument("--follow", type=int, default=0,
                    help="poll this many accepted reports to completion + "
                         "classify real vs. sentinel narratives")
    args = ap.parse_args()

    base_url = os.environ.get("CAYDEX_BASE_URL", "http://localhost:8000")
    token = os.environ.get("CAYDEX_TOKEN")
    if not token:
        raise SystemExit("Set CAYDEX_TOKEN to a valid bearer token first.")

    headers = {"Authorization": f"Bearer {token}"}
    specs = _build_requests(
        args.concurrency, args.hot_ticker, args.hot_share, args.persona
    )

    print(f"Firing {len(specs)} concurrent /research/generate "
          f"({int(args.hot_share*100)}% on {args.hot_ticker}, rest diverse) "
          f"at {base_url}")

    async with httpx.AsyncClient(
        base_url=base_url, headers=headers, timeout=60.0
    ) as client:
        stop = asyncio.Event()
        health_task = asyncio.create_task(_health_probe(client, stop))

        t0 = time.perf_counter()
        rows = await asyncio.gather(*(_fire_one(client, t, p) for t, p in specs))
        burst_elapsed = time.perf_counter() - t0

        stop.set()
        health = await health_task

        # ── Admission summary ──
        by_code = Counter(
            r["error_code"] or f"HTTP_{r['status']}" for r in rows
        )
        accepted = [r for r in rows if r["status"] in (200, 201) and r["report_id"]]
        lat = sorted(r["elapsed"] for r in rows)
        p50 = lat[len(lat)//2] if lat else 0.0
        p95 = lat[min(len(lat)-1, int(len(lat)*0.95))] if lat else 0.0

        print("\n── Admission results ──")
        print(f"  burst wall-clock:   {burst_elapsed:.2f}s")
        print(f"  accepted (queued):  {len(accepted)}")
        for code, c in by_code.most_common():
            print(f"  {code:<32} {c}")
        print(f"  request latency:    p50 {p50*1000:.0f}ms  p95 {p95*1000:.0f}ms")

        ok_health = [h for h in health if h == h]  # drop NaN
        if ok_health:
            hp95 = sorted(ok_health)[min(len(ok_health)-1, int(len(ok_health)*0.95))]
            print(f"  /health p95 during burst: {hp95*1000:.0f}ms  "
                  f"(low = event loop NOT starved)")

        # ── Optional: follow some accepted reports to completion ──
        if args.follow and accepted:
            sample = accepted[: args.follow]
            print(f"\n── Following {len(sample)} reports to completion ──")
            done = await asyncio.gather(*(
                _follow_to_completion(client, r["report_id"]) for r in sample
            ))
            statuses = Counter(d["status"] for d in done)
            real = sum(1 for d in done if d["status"] == "completed" and not d["sentinel"])
            sentinel = sum(1 for d in done if d["status"] == "completed" and d["sentinel"])
            print(f"  outcomes: {dict(statuses)}")
            print(f"  real narratives: {real}   sentinel/degraded: {sentinel}")
            if sentinel:
                print("  ⚠️  sentinel reports = Gemini quota was hit; raise the "
                      "tier or lower MAX_CONCURRENT_AGENT_RUNS.")


if __name__ == "__main__":
    asyncio.run(main())
