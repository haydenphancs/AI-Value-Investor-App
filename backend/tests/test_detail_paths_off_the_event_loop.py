"""More of the `project_overview_event_loop_blocking` class, found 2026-09-12.

Railway runs ONE uvicorn worker and the Supabase SDK is SYNCHRONOUS, so a `.execute()` on
the loop suspends every other in-flight request for its round trip — `main.py` records a
measured 18.2 s contiguous stall from exactly this. The 2026-08-27 fix threaded the
services it knew about; these three were missed because they sit in their OWN helpers:

* the three ETF side-endpoints (`/etfs/{s}/dividends`, `/profile`, `/holdings-risk`) each
  did a blocking SELECT and, on a Tier-2 miss, a blocking UPSERT;
* `tracking_service`'s `whale_trades` read sits INSIDE the feed's `asyncio.gather`, so it
  stalled every sibling in the fan-out too;
* `index_service._refresh_ai_stories` wrote the macro cache synchronously, twenty lines
  from a `_tier2_put` that was already threaded.

Thread IDENTITY, not a grep for `to_thread` — a source scan passes on a comment.
"""

from __future__ import annotations

import threading

import pytest

import app.services.etf_service as etf


@pytest.mark.asyncio
@pytest.mark.parametrize("method,category", [
    ("get_dividend_history", "dividends"),
    ("get_profile", "profile"),
    ("get_holdings_risk", "holdings_risk"),
])
async def test_the_etf_side_endpoints_read_their_cache_off_the_loop(monkeypatch, method, category):
    threads = []
    svc = etf.ETFService.__new__(etf.ETFService)
    etf._cache.clear()

    def _check(symbol, cat):
        threads.append(threading.get_ident())
        # A cached payload short-circuits the rest of the handler, which is all this test
        # needs: the read itself is the thing that must not block.
        return None

    monkeypatch.setattr(svc, "_check_snapshot_cache", _check, raising=False)
    monkeypatch.setattr(svc, "_upsert_snapshot_cache", lambda *a, **k: None, raising=False)

    async def _fundamentals(symbol):
        return {"profile": {}, "etf_info": {}, "holders": [], "sector_weights": [],
                "dividends": []}

    monkeypatch.setattr(svc, "_get_fundamentals", _fundamentals, raising=False)

    async def _quote(symbol):
        return {"symbol": symbol, "price": 100.0}

    monkeypatch.setattr(svc, "_get_quote", _quote, raising=False)

    async def _history(symbol):
        return []

    monkeypatch.setattr(svc, "_get_history", _history, raising=False)
    loop_ident = threading.get_ident()
    try:
        await getattr(svc, method)("SPY")
    except Exception:
        # The handler may still fail further down on these stubs — irrelevant. What is
        # being asserted is WHICH THREAD the cache read ran on, and it already ran.
        pass
    assert threads, f"{method} never consulted its cache — the test would be vacuous"
    assert all(t != loop_ident for t in threads), (
        f"{method} blocked the event loop on a PostgREST round trip"
    )


def test_the_threaded_sites_are_awaited_not_merely_wrapped():
    """`asyncio.to_thread(...)` without an `await` returns a coroutine and runs NOTHING —
    a silent no-op that would look like a fix and delete the cache write."""
    import ast
    import inspect

    for module in (etf, __import__("app.services.tracking_service", fromlist=["x"]),
                   __import__("app.services.index_service", fromlist=["x"])):
        src = inspect.getsource(module)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_thread"):
                parent_is_await = any(
                    isinstance(p, ast.Await) and p.value is node
                    for p in ast.walk(tree)
                )
                assert parent_is_await, (
                    f"{module.__name__}: an asyncio.to_thread(...) call is not awaited — "
                    "it returns a coroutine and performs no work"
                )
