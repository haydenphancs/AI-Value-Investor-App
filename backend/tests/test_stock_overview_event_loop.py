"""The stock-overview path must not touch Supabase on the event loop.

Why this exists
---------------
`app/database.py` builds the SYNCHRONOUS supabase-py client, and Railway runs a single
uvicorn worker (`railway.toml`), so every `.execute()` blocks the whole loop for its
round trip. `main.py` already records a measured **18.2s contiguous event-loop stall**
from exactly this — "byte-for-byte identical at concurrency 1, 3 and 56".

`stock_overview_service` was the only service on the detail-screen path with no
`asyncio.to_thread` at all (20+ siblings use it), and it had three blocking call sites:

  * `_check_fundamentals_db`      — a SELECT on every tier-1 miss
  * `_upsert_fundamentals_db`     — serialize + POST of `response_json`, which carries
                                    `stock_historical` AND `spy_historical`, up to
                                    5,000 rows each
  * `_upsert_company_profile_db`  — ran on EVERY /overview cache miss, from inside the
                                    *synchronous* `_build_full_response`

The user-visible symptom was that `GET /stocks/{t}/overview/core` — the "fast core"
that exists to paint price+chart in ~0.5s while the full overview finishes — could not
be SCHEDULED while a cold `/overview` held the loop, so the detail screen sat on a
shimmer skeleton for 5-7s and the fast-core design bought nothing. Reported from
TestFlight on AVGO.

Why it asserts thread identity rather than grepping for `to_thread`
-------------------------------------------------------------------
A source scan for the literal token passes on a COMMENT — this repo has been bitten by
vacuous guards repeatedly (`.claude/rules/testing.md` §3). Recording
`threading.get_ident()` inside the stubbed DB method and comparing it to the loop's own
thread cannot pass unless the call really was dispatched to a worker thread.
"""

import asyncio
import threading

import pytest

from app.schemas.stock_overview import StockOverviewResponse
from app.services import stock_overview_service as sos
from app.services.stock_overview_service import StockOverviewService, _cache


def _service() -> StockOverviewService:
    """A real service with a cold module cache. Constructing the Supabase client is
    fine — the hermeticity guard only trips on an actual connect, and every DB method
    is stubbed below."""
    _cache.clear()
    return StockOverviewService()


class _StubBenchmarkLookup:
    """`get_sector_benchmark_lookup()` is a module SINGLETON that builds a real Supabase
    client in __init__, and `_build_snapshots` calls it on the fallback Price card."""

    def get_current_benchmark_values(self, industry, sector, metrics):
        return {}


def _neutralise_upstreams(monkeypatch, svc):
    """Stub everything the build path would otherwise reach over the network.

    `stock_overview_service` imports `get_sector_benchmark_lookup` and `get_short_interest`
    at MODULE level, but `get_overview` re-imports `get_short_interest` inside the function
    body — a function-scoped import resolves from the SOURCE module on every call. So the
    same function needs BOTH bindings patched; patching one leaves live calls behind
    (`.claude/rules/testing.md`, "Patch the binding the caller actually uses").
    """
    async def _no_short_interest(ticker):
        return {}

    monkeypatch.setattr(sos, "get_sector_benchmark_lookup", lambda: _StubBenchmarkLookup())
    monkeypatch.setattr(sos, "get_short_interest", _no_short_interest)
    monkeypatch.setattr(
        "app.integrations.finra_short_interest.get_short_interest", _no_short_interest
    )


# ── 1. Tier-2 fundamentals READ ───────────────────────────────────────


@pytest.mark.asyncio
async def test_tier2_fundamentals_read_runs_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def _fake_check(ticker):
        seen["thread"] = threading.get_ident()
        # A usable tier-2 hit short-circuits the FMP fan-out entirely.
        return {"profile": {"companyName": "Broadcom"}, "key_metrics": [{}], "stock_historical": [{}]}

    svc = _service()
    monkeypatch.setattr(svc, "_check_fundamentals_db", _fake_check)

    out = await svc._get_fundamentals("AVGO")

    assert out["profile"]["companyName"] == "Broadcom", "the tier-2 hit must still be returned"
    assert "thread" in seen, "VACUOUS: _check_fundamentals_db was never reached"
    assert seen["thread"] != loop_thread, (
        "_check_fundamentals_db ran ON the event loop thread — a blocking Supabase "
        "SELECT that stalls every other in-flight request, including /overview/core"
    )


# ── 2. Tier-2 fundamentals WRITE (the multi-MB one) ───────────────────


@pytest.mark.asyncio
async def test_tier2_fundamentals_write_runs_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    # Every "essential" slice must be non-empty or _get_fundamentals deliberately
    # refuses to persist (a transient FMP blip must not pin an empty 24h bundle).
    fetched = {
        "profile": {"companyName": "Broadcom"},
        "stock_historical": [{"date": "2026-08-26", "close": 300.0}],
        "key_metrics": [{"peRatio": 30.0}],
    }

    async def _fake_fetch(ticker):
        return fetched

    def _fake_upsert(ticker, data):
        seen["thread"] = threading.get_ident()

    svc = _service()
    monkeypatch.setattr(svc, "_check_fundamentals_db", lambda ticker: None)
    monkeypatch.setattr(svc, "_fetch_fundamentals", _fake_fetch)
    monkeypatch.setattr(svc, "_upsert_fundamentals_db", _fake_upsert)

    await svc._get_fundamentals("AVGO")

    assert "thread" in seen, "VACUOUS: the upsert was never reached (an essential slice was empty?)"
    assert seen["thread"] != loop_thread, (
        "_upsert_fundamentals_db ran ON the event loop thread — this write serializes "
        "stock_historical + spy_historical (up to 5,000 rows each) and POSTs them"
    )


# ── 3. The synchronous builder must not write at all ──────────────────


def test_the_synchronous_builder_does_not_write_to_supabase(monkeypatch):
    """`_build_full_response` is a plain `def`, so ANY Supabase call inside it is
    necessarily on the loop. The company-profile write was moved out to `get_overview`,
    which can await it in a thread. Mutation: put the call back → this fails."""
    called: list[str] = []

    svc = _service()
    _neutralise_upstreams(monkeypatch, svc)
    monkeypatch.setattr(
        svc, "_upsert_company_profile_db",
        lambda ticker, payload: called.append(ticker),
    )

    response = svc._build_full_response(
        "AVGO",
        {"profile": {"companyName": "Broadcom", "sector": "Technology"}},
        {"quote": {"price": 300.0}, "chart_data": []},
        "1D", "5min", False,
    )

    assert isinstance(response, StockOverviewResponse)
    assert called == [], (
        "_build_full_response wrote to Supabase. It is synchronous, so that write "
        "blocks the event loop on every /overview cache miss — move it to get_overview "
        "and dispatch it with asyncio.to_thread."
    )


# ── 4. …and the caller does write it, off the loop ────────────────────


@pytest.mark.asyncio
async def test_company_profile_write_runs_off_the_event_loop(monkeypatch):
    """Drives the real `get_overview` with every upstream stubbed, so this proves the
    write still HAPPENS (moving it must not silently drop the chat-context cache) and
    that it happens on a worker thread."""
    loop_thread = threading.get_ident()
    seen: dict[str, object] = {}

    def _fake_profile_upsert(ticker, payload):
        seen["thread"] = threading.get_ident()
        seen["payload"] = payload

    async def _fake_fundamentals(ticker):
        return {"profile": {"companyName": "Broadcom", "sector": "Technology"}}

    async def _fake_volatile(ticker, chart_range, interval, extended_hours, **kwargs):
        return {"quote": {"price": 300.0}, "chart_data": []}

    async def _empty_list():
        return []

    class _NoSnapshot:
        async def _none(self, ticker):
            return None
        get_profitability_snapshot = _none
        get_growth_snapshot = _none
        get_valuation_snapshot = _none
        get_health_snapshot = _none
        get_ownership_snapshot = _none

    for module_name, factory in (
        ("profitability_snapshot_service", "get_profitability_snapshot_service"),
        ("growth_snapshot_service", "get_growth_snapshot_service"),
        ("valuation_snapshot_service", "get_valuation_snapshot_service"),
        ("health_snapshot_service", "get_health_snapshot_service"),
        ("ownership_snapshot_service", "get_ownership_snapshot_service"),
    ):
        module = __import__(f"app.services.{module_name}", fromlist=[factory])
        # Function-scoped imports inside get_overview resolve from the SOURCE module on
        # every call, so patching the source module is the correct target here.
        monkeypatch.setattr(module, factory, lambda: _NoSnapshot())

    svc = _service()
    _neutralise_upstreams(monkeypatch, svc)
    monkeypatch.setattr(svc, "_get_fundamentals", _fake_fundamentals)
    monkeypatch.setattr(svc, "_get_volatile", _fake_volatile)
    monkeypatch.setattr(svc, "_upsert_company_profile_db", _fake_profile_upsert)
    monkeypatch.setattr(svc.fmp, "get_sector_performance", _empty_list)
    monkeypatch.setattr(svc.fmp, "get_industry_performance", _empty_list)

    async def _no_related(ticker):
        return []
    monkeypatch.setattr(svc, "_build_related_tickers", _no_related)

    await svc.get_overview("AVGO", "1D", "5min", False)

    assert "thread" in seen, (
        "VACUOUS: the company-profile write never happened. Moving it out of "
        "_build_full_response must not drop it — chat AI context reads this row."
    )
    assert seen["thread"] != loop_thread, (
        "_upsert_company_profile_db ran ON the event loop thread"
    )
    assert set(seen["payload"]) == {
        "description", "ceo", "founded", "employees", "headquarters",
        "website", "sector", "industry", "sector_performance", "industry_rank",
    }, "the payload shape changed — app/services/chat_context_resolver reads these keys"
