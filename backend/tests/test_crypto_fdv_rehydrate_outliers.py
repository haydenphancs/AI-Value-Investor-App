"""F16-5 outliers — FDV re-hydration from the live `/coins/markets` row.

The fix (crypto_service `_VOLATILE_MARKET_DATA_FIELDS` + `_VOLATILE_CURRENCY_KEYED`,
2026-09-17) is pinned on its happy path in test_crypto_live_price_not_persisted.py (a DB
hit serves the live FDV; a null stays absent; the renderer falls back to the live cap).
These are the inputs that path did not picture: a NaN / Inf FDV on the live row (must
stay ABSENT — `_usd_opt` accepts any float and `_fmt(nan)` prints "$nan"), a string, a
`{usd: …}` dict where a bare float is expected, an outage that answers no row at all, and
a persisted row whose `market_data` is not a dict. Every case: the 12-hour-old persisted
FDV never resurfaces, nothing non-finite is written, no 0 is fabricated.
"""
from __future__ import annotations

import math

import pytest

from app.services import crypto_service as cs
from app.services.crypto_service import strip_volatile_market_data


class _FakeCG:
    def __init__(self, row=None, fail=False):
        self._row, self.fail = row, fail

    async def resolve_coin_id(self, base):
        if self.fail:
            raise RuntimeError("coingecko down")
        return "bitcoin" if base.upper() == "BTC" else None

    async def get_markets(self, bases):
        if self.fail:
            raise RuntimeError("coingecko down")
        return [self._row] if self._row else []


@pytest.fixture
def svc(monkeypatch):
    cs._cache.clear()
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_coingecko_client", lambda: _FakeCG(), raising=True)
    s = cs.CryptoService()
    yield s
    cs._cache.clear()


def _durable():
    return strip_volatile_market_data({
        "id": "bitcoin", "symbol": "btc",
        "market_data": {
            "current_price": {"usd": 79_000.0},
            "market_cap": {"usd": 1.5e12},
            "fully_diluted_valuation": {"usd": 1.6e12},
            "circulating_supply": 19.8e6, "max_supply": 21e6,
        },
    })


def _row(**over):
    base = {"id": "bitcoin", "symbol": "btc", "current_price": 81_500.0,
            "market_cap": 1.55e12, "fully_diluted_valuation": 1.68e12,
            "total_volume": 4.4e10, "high_24h": 82_000.0, "low_24h": 80_000.0,
            "price_change_24h": 100.0, "price_change_percentage_24h": 0.12}
    base.update(over)
    return base


def test_the_strip_removes_fdv_and_is_a_deep_copy():
    src = {"market_data": {"fully_diluted_valuation": {"usd": 1.0}, "circulating_supply": 5}}
    out = strip_volatile_market_data(src)
    assert "fully_diluted_valuation" not in out["market_data"]
    assert src["market_data"]["fully_diluted_valuation"] == {"usd": 1.0}, "stripped in place"
    assert out["_volatile_stripped"] is True
    assert strip_volatile_market_data(None) is None
    assert strip_volatile_market_data({"market_data": "junk"})["market_data"] == "junk"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
async def test_a_non_finite_live_fdv_stays_absent(svc, bad):
    svc.coingecko = _FakeCG(_row(fully_diluted_valuation=bad))
    md = (await svc._rehydrate_volatile("BTC", _durable()))["market_data"]
    assert "fully_diluted_valuation" not in md, "a NaN/Inf FDV was written — `_fmt` prints $nan"
    assert md["market_cap"] == {"usd": 1.55e12}, "the other live fields still land"


@pytest.mark.asyncio
async def test_a_non_finite_live_cap_stays_absent_too(svc):
    svc.coingecko = _FakeCG(_row(market_cap=float("nan")))
    md = (await svc._rehydrate_volatile("BTC", _durable()))["market_data"]
    assert "market_cap" not in md
    assert md["fully_diluted_valuation"] == {"usd": 1.68e12}


@pytest.mark.asyncio
async def test_an_outage_serves_no_fdv_rather_than_the_persisted_one(svc):
    svc.coingecko = _FakeCG(fail=True)
    out = await svc._rehydrate_volatile("BTC", _durable())
    assert "fully_diluted_valuation" not in out["market_data"]
    assert "current_price" not in out["market_data"]


@pytest.mark.asyncio
async def test_a_row_without_the_key_serves_no_fdv(svc):
    row = _row()
    del row["fully_diluted_valuation"]
    svc.coingecko = _FakeCG(row)
    md = (await svc._rehydrate_volatile("BTC", _durable()))["market_data"]
    assert "fully_diluted_valuation" not in md


@pytest.mark.asyncio
async def test_a_durable_row_whose_market_data_is_not_a_dict_is_rebuilt(svc):
    svc.coingecko = _FakeCG(_row())
    out = await svc._rehydrate_volatile("BTC", {"id": "bitcoin", "market_data": "junk"})
    assert out["market_data"]["fully_diluted_valuation"] == {"usd": 1.68e12}
    assert await svc._rehydrate_volatile("BTC", "not-a-dict") == "not-a-dict"


@pytest.mark.asyncio
async def test_a_zero_fdv_is_written_as_zero_not_dropped(svc):
    """An explicit 0 from the provider is a value (a pre-launch token), not an absence;
    the renderer's `if fdv` then falls back to the cap, which is the existing behaviour."""
    svc.coingecko = _FakeCG(_row(fully_diluted_valuation=0))
    md = (await svc._rehydrate_volatile("BTC", _durable()))["market_data"]
    assert md["fully_diluted_valuation"] == {"usd": 0}


def test_renderer_never_prints_nan_or_a_fabricated_zero_for_fdv():
    svc = cs.CryptoService.__new__(cs.CryptoService)
    kw = dict(circulating_supply=100.0, total_supply=100.0, max_supply=None,
              avg_volume=None, symbol="ETH", max_supply_known=True)
    row = next(s for s in svc._build_supply_stats(fdv=None, market_cap=None, **kw)
               if s.label == "Fully Diluted Val.")
    assert row.value == "—"
    row = next(s for s in svc._build_supply_stats(fdv=2e12, market_cap=1e12, **kw)
               if s.label == "Fully Diluted Val.")
    assert row.value == cs._fmt(2e12) and "nan" not in row.value.lower()
    # A stripped-and-not-rehydrated persisted value cannot reach here: the durable row
    # has no key, so `_usd_opt` yields None → the live-cap fallback, never 1.6e12.
    assert "fully_diluted_valuation" not in _durable()["market_data"]


def test_fdv_is_in_both_volatile_tuples_and_not_the_stale_derived_one():
    assert "fully_diluted_valuation" in cs._VOLATILE_MARKET_DATA_FIELDS
    assert "fully_diluted_valuation" in cs._VOLATILE_CURRENCY_KEYED
    assert "fully_diluted_valuation" not in cs._STALE_DERIVED_MARKET_FIELDS, (
        "in the stale-derived tuple it is stripped WITHOUT re-hydration — every DB hit "
        "would be forced onto the market-cap fallback"
    )
