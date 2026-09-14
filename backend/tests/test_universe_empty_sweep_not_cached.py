"""An EMPTY screener sweep is a failure, never a cached universe.

`_get_universe` memoised `{}` for `_UNIVERSE_TTL` when `company-screener` answered `[]`, and
the fan-out guard in `get_quotes` then skipped the per-symbol fallback because the universe
was empty — so one `[]` blip blanked every equity quote (Home tiles, Tracking, the widget,
the alert sweep) for a full minute, with the same cached `{}` served to every caller.
"""
import pytest

from app.integrations.fmp import FMPUnavailableException
from app.services import price_service as ps
from app.services.price_service import PriceService, _cache


@pytest.fixture(autouse=True)
def _clean():
    _cache.clear()
    ps._inflight.clear()
    yield
    _cache.clear()
    ps._inflight.clear()


def _svc(pages):
    svc = PriceService.__new__(PriceService)
    calls = {"n": 0}

    async def _fetch():
        calls["n"] += 1
        page = pages[min(calls["n"] - 1, len(pages) - 1)]
        if isinstance(page, Exception):
            raise page
        return page
    svc._fetch_universe_pages = _fetch
    return svc, calls


@pytest.mark.asyncio
async def test_an_empty_sweep_is_not_memoised_as_the_universe():
    svc, calls = _svc([[], [{"symbol": "AAPL", "price": 100.0}]])
    with pytest.raises(FMPUnavailableException):
        await svc._get_universe()
    assert _cache.get("price:universe") is None, "an empty universe was cached"
    # Degraded state is memoised briefly: the very next call does NOT hit upstream…
    with pytest.raises(FMPUnavailableException):
        await svc._get_universe()
    assert calls["n"] == 1
    # …but once the memo expires the sweep retries and a real answer is cached.
    _cache.pop(ps._UNIVERSE_DEGRADED_KEY, None)
    universe = await svc._get_universe()
    assert set(universe) == {"AAPL"}
    assert calls["n"] == 2
    assert _cache.get(ps._UNIVERSE_DEGRADED_KEY) is None


@pytest.mark.asyncio
async def test_rows_that_are_all_blocked_symbols_count_as_empty():
    svc, calls = _svc([[{"symbol": "BTCUSD", "price": 1.0}, {"symbol": "", "price": 2.0}]])
    with pytest.raises(FMPUnavailableException):
        await svc._get_universe()
    assert _cache.get("price:universe") is None


@pytest.mark.asyncio
async def test_an_upstream_exception_is_memoised_as_degraded_not_as_a_universe():
    svc, calls = _svc([RuntimeError("edge 520"), [{"symbol": "MSFT", "price": 1.0}]])
    with pytest.raises(RuntimeError):
        await svc._get_universe()
    assert _cache.get("price:universe") is None
    with pytest.raises(FMPUnavailableException):
        await svc._get_universe()          # memoised failure, no second upstream call
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_get_quotes_degrades_to_no_equities_but_the_next_window_recovers(monkeypatch):
    svc, calls = _svc([[], [{"symbol": "AAPL", "companyName": "Apple", "price": 100.0,
                             "marketCap": 3e12}]])

    async def _closes(symbols):
        return {}
    svc.get_close_snapshots = _closes
    monkeypatch.setattr(PriceService, "_crypto_quotes_enabled", lambda self: False)
    first = await svc.get_quotes(["AAPL"])
    assert first == {}                      # this request degrades honestly
    _cache.pop(ps._UNIVERSE_DEGRADED_KEY, None)
    second = await svc.get_quotes(["AAPL"])
    assert "AAPL" in second                 # the blip did not poison the next window
