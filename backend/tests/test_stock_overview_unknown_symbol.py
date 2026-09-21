"""The full `/overview` path for a symbol FMP cannot serve: a typed, retryable refusal,
and NOTHING pinned in the 120 s response cache.

Drives the real `get_overview` with every upstream stubbed to answer empty — the shape a
bare coin ticker (DOGE) or any unknown symbol produces on the equity pipeline — and pins
that the builder's new price guard propagates through the gather/re-raise path and that
`_cache_set(overview_key, …)` is never reached (a cached refusal would be a cached $0.00).
The harness mirrors `test_stock_overview_event_loop.py::test_company_profile_write_runs_off_the_event_loop`.
"""

import pytest

from app.integrations.fmp import FMPUnavailableException
from app.services import stock_overview_service as sos
from app.services.stock_overview_service import _cache, _cache_get

from test_stock_overview_event_loop import _neutralise_upstreams, _service


def _stub_everything_empty(monkeypatch, svc):
    async def _fake_fundamentals(ticker):
        return {"profile": {}}          # FMP answered [] for the profile → folded to {}

    async def _fake_volatile(ticker, chart_range, interval, extended_hours, **kwargs):
        return {"quote": {}, "chart_data": []}

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
        monkeypatch.setattr(module, factory, lambda: _NoSnapshot())

    _neutralise_upstreams(monkeypatch, svc)
    monkeypatch.setattr(svc, "_get_fundamentals", _fake_fundamentals)
    monkeypatch.setattr(svc, "_get_volatile", _fake_volatile)

    writes = []
    monkeypatch.setattr(svc, "_upsert_company_profile_db", lambda t, payload: writes.append(t))
    movers = sos.get_market_movers_service()
    monkeypatch.setattr(movers, "get_sector_performance", _empty_list)
    monkeypatch.setattr(movers, "get_industry_performance", _empty_list)

    async def _no_related(ticker):
        return []
    monkeypatch.setattr(svc, "_build_related_tickers", _no_related)

    # `_get_session_ohl` reaches FMP on its own (the Open / Day High / Day Low merge); an
    # unservable symbol has no session either.
    async def _no_ohl(ticker, volatile=None, **kwargs):
        return {}
    monkeypatch.setattr(svc, "_get_session_ohl", _no_ohl)
    return writes


@pytest.mark.asyncio
async def test_an_unservable_symbol_is_a_typed_refusal_and_is_never_cached(monkeypatch):
    svc = _service()
    writes = _stub_everything_empty(monkeypatch, svc)

    with pytest.raises(FMPUnavailableException):
        await svc.get_overview("DOGE", "3M", "1day", False)

    # Nothing pinned under any key: the raise precedes `_cache_set`. The exact key is the
    # one `get_overview` builds for these arguments (interval "1day", not "default" — a
    # first version probed a key that could never exist and was always true).
    assert not [k for k in _cache if "DOGE" in str(k)], "a refusal must not be cached"
    assert _cache_get("stock_overview:DOGE:3M:1day:False", ttl=sos._VOLATILE_TTL) is None
    # And no company-profile row was written for a symbol that has none.
    assert writes == [], "the profile write must not run for a refused symbol"


@pytest.mark.asyncio
async def test_a_priced_symbol_still_serves_through_the_same_path(monkeypatch):
    """Control: the same harness with a real quote price serves normally, so the refusal
    above is the guard and not the stubbing."""
    svc = _service()
    writes = _stub_everything_empty(monkeypatch, svc)

    async def _priced_volatile(ticker, chart_range, interval, extended_hours, **kwargs):
        return {"quote": {"price": 300.0}, "chart_data": []}
    monkeypatch.setattr(svc, "_get_volatile", _priced_volatile)

    resp = await svc.get_overview("AVGO", "3M", "1day", False)
    assert resp.current_price == 300.0
    assert writes == ["AVGO"]
    assert _cache_get("stock_overview:AVGO:3M:1day:False", ttl=sos._VOLATILE_TTL) is resp, (
        "the key format the refusal test probes must be the one a served overview is pinned under"
    )
