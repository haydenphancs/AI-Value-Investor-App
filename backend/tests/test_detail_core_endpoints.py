"""Fast-path + schema-parity guards for the four asset-detail `/core` endpoints.

TestFlight, build 1.0 (6): *"It's very slow at first time open it."* — a screenshot of
`^GSPC` with the whole screen shimmering. Measured against production at the time:

    ^DJI   11.42s cold      ^GSPC   5.63s cold / 0.14s warm / 0.36s rebuild-when-warm
    SCHD    5.89s cold      SOL     1.27s cold      SIUSD  1.34s cold
    DECK  (stock) 7.94s cold FULL   but 0.32s for its `/overview/core`

The stock screen was the proof: its full build is the SLOWEST of the lot and the screen
still feels instant, because a core slice paints first. These four endpoints give the
sibling screens the same thing.

Two invariants, and the second is the one that rots silently:

1. **Schema parity.** Each `*CoreResponse` must serialize exactly the keys its iOS DTO
   decodes, with the same names and container types as the corresponding fields on the
   FULL detail response — the client swaps core -> full in place, so a mismatch is a
   decode crash on the detail screen.

2. **Fast path.** A core build must NEVER reach a slow upstream. Every FMP method except
   the quote is FORBIDDEN by the stub below, so a regression that reintroduces the daily
   history (`_fetch_all_daily`: up to five paged calls for 10-14k rows), the ETF
   fundamentals bundle, or a Gemini call fails loudly here rather than quietly restoring
   the multi-second first paint these endpoints exist to remove.

No network: every client is stubbed and any forbidden call raises.
"""

import asyncio

import pytest

from app.schemas.commodity import CommodityCoreResponse
from app.schemas.crypto import CryptoCoreResponse
from app.schemas.etf import ETFCoreResponse
from app.schemas.etf import MarketStatusResponse as ETFMarketStatusResponse
from app.schemas.index import IndexCoreResponse
from app.schemas.index import MarketStatusResponse as IndexMarketStatusResponse


# The exact snake_case keys each iOS DTO decodes.
_INDEX_KEYS = {
    "symbol", "index_name", "current_price", "price_change",
    "price_change_percent", "market_status", "chart_data",
}
_ETF_KEYS = {
    "symbol", "name", "current_price", "price_change",
    "price_change_percent", "market_status", "chart_data",
}
_COMMODITY_KEYS = set(_ETF_KEYS)
_CRYPTO_KEYS = set(_ETF_KEYS)


# ── Stubs ────────────────────────────────────────────────────────────

_QUOTE = {
    "symbol": "X",
    "name": "Schwab U.S. Dividend Equity ETF",
    "price": 27.31,
    "change": 0.42,
    "changePercentage": 1.56,
    "previousClose": 26.89,
}


class _FakeFMP:
    """Serves the quote and FORBIDS everything else.

    Records the forbidden name BEFORE raising: the core builders gather with
    `return_exceptions=True`, so a forbidden call would otherwise be swallowed and the
    regression would be invisible to the assertions.
    """

    def __init__(self, quote=None):
        self.calls: list[str] = []
        self._quote = _QUOTE if quote is None else quote

    async def get_stock_price_quote(self, symbol: str):
        # A real suspension point. Without one, the first coroutine in an
        # `asyncio.gather` runs to completion before the second starts and every
        # concurrency assertion below becomes vacuous.
        await asyncio.sleep(0)
        self.calls.append("get_stock_price_quote")
        return dict(self._quote)

    def __getattr__(self, name: str):
        async def _forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"a core build must not call fmp.{name}()")
        return _forbidden


def _index_service(fmp=None, tier2=None):
    from app.services.index_service import IndexService, _cache
    _cache.clear()
    svc = IndexService.__new__(IndexService)   # no __init__: no live FMP/Supabase
    svc.fmp = fmp or _FakeFMP()
    svc.supabase = None
    svc._tier2_get = staticmethod(lambda symbol, category: tier2)  # type: ignore[assignment]
    svc._tier2_put = staticmethod(lambda *a, **k: None)            # type: ignore[assignment]
    return svc


def _etf_service(fmp=None, tier2=None):
    from app.services.etf_service import ETFService, _cache
    _cache.clear()
    svc = ETFService.__new__(ETFService)
    svc.fmp = fmp or _FakeFMP()
    svc.supabase = None
    svc._tier2_get = staticmethod(lambda symbol, category: tier2)  # type: ignore[assignment]
    svc._tier2_put = staticmethod(lambda *a, **k: None)            # type: ignore[assignment]
    return svc


def _commodity_service(fmp=None, tier2=None):
    from app.services.commodity_service import CommodityService, _cache
    _cache.clear()
    svc = CommodityService.__new__(CommodityService)
    svc.fmp = fmp or _FakeFMP()
    svc._tier2_get = staticmethod(lambda key: tier2)               # type: ignore[assignment]
    svc._tier2_put = staticmethod(lambda *a, **k: None)            # type: ignore[assignment]
    return svc


# ── 1. Schema parity ─────────────────────────────────────────────────


def test_index_core_keys_match_ios_dto():
    from app.services.index_service import _get_market_status
    resp = IndexCoreResponse(
        symbol="^GSPC", index_name="S&P 500", current_price=6001.2,
        price_change=12.4, price_change_percent=0.21,
        market_status=_get_market_status(),
        chart_data=[{"date": "2026-01-02", "close": 6001.2}],
    )
    dumped = resp.model_dump(mode="json")
    assert set(dumped.keys()) == _INDEX_KEYS
    # market_status is a nested object (iOS MarketStatusDTO), never a scalar — the
    # index/ETF DTOs decode an object here while commodity/crypto decode a string.
    assert isinstance(dumped["market_status"], dict)
    assert "close" in dumped["chart_data"][0]
    # `app.schemas.index` and `app.schemas.etf` each define their OWN
    # MarketStatusResponse — same shape, different classes. Assert against the one
    # this response actually uses, or the check is trivially false.
    assert isinstance(resp.market_status, IndexMarketStatusResponse)


def test_etf_core_keys_match_ios_dto():
    from app.services.etf_service import _get_market_status
    resp = ETFCoreResponse(
        symbol="SCHD", name="Schwab U.S. Dividend Equity ETF", current_price=27.31,
        price_change=0.42, price_change_percent=1.56,
        market_status=_get_market_status(), chart_data=[],
    )
    dumped = resp.model_dump(mode="json")
    assert set(dumped.keys()) == _ETF_KEYS
    assert isinstance(dumped["market_status"], dict)
    assert isinstance(resp.market_status, ETFMarketStatusResponse)


def test_commodity_core_keys_match_ios_dto():
    resp = CommodityCoreResponse(
        symbol="SI", name="Silver", current_price=31.02,
        price_change=-0.11, price_change_percent=-0.35,
        market_status="Open", chart_data=[],
    )
    dumped = resp.model_dump(mode="json")
    assert set(dumped.keys()) == _COMMODITY_KEYS
    # Commodity + crypto market_status is a plain STRING on the full response, so the
    # core must not "upgrade" it to an object — iOS decodes a String here.
    assert isinstance(dumped["market_status"], str)


def test_crypto_core_keys_match_ios_dto():
    resp = CryptoCoreResponse(
        symbol="SOL", name="Solana", current_price=142.11,
        price_change=3.4, price_change_percent=2.45,
        market_status="24/7 Trading", chart_data=[],
    )
    dumped = resp.model_dump(mode="json")
    assert set(dumped.keys()) == _CRYPTO_KEYS
    assert isinstance(dumped["market_status"], str)


def test_core_field_names_match_the_full_detail_response():
    """The client renders `full ?? core`, so every shared field must be the SAME name
    and type on both. A rename on one side alone is a silent decode failure the moment
    the core lands first."""
    from app.schemas.commodity import CommodityDetailResponse
    from app.schemas.crypto import CryptoDetailResponse
    from app.schemas.etf import ETFDetailResponse
    from app.schemas.index import IndexDetailResponse

    for core, full in (
        (IndexCoreResponse, IndexDetailResponse),
        (ETFCoreResponse, ETFDetailResponse),
        (CommodityCoreResponse, CommodityDetailResponse),
        (CryptoCoreResponse, CryptoDetailResponse),
    ):
        for name, field in core.model_fields.items():
            assert name in full.model_fields, (
                f"{core.__name__}.{name} has no counterpart on {full.__name__} — the "
                f"client cannot swap core -> full without it"
            )
            assert field.annotation == full.model_fields[name].annotation, (
                f"{core.__name__}.{name} is {field.annotation} but "
                f"{full.__name__}.{name} is {full.model_fields[name].annotation}"
            )


# ── 2. Fast path: the quote and nothing else ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("rng,interval", [("3M", "daily"), ("1Y", "daily"),
                                          ("5Y", "weekly"), ("ALL", "monthly")])
async def test_index_core_never_pulls_the_daily_history(rng, interval):
    """Cold chart section on ANY daily-or-coarser range → empty bars, one FMP call.

    `_fetch_all_daily` is up to five paged pulls of 10-14k rows and it is the single
    biggest item in a cold index build. Reintroducing it here would restore the
    multi-second first paint with no other visible symptom.
    """
    svc = _index_service()
    resp = await svc.get_index_core("^GSPC", chart_range=rng, interval=interval)
    assert svc.fmp.calls == ["get_stock_price_quote"]
    assert resp.chart_data == []
    assert resp.current_price == 27.31
    assert resp.symbol == "^GSPC"
    assert resp.index_name == "S&P 500"          # from the static profile, no fetch


@pytest.mark.asyncio
async def test_index_core_serves_a_warm_tier2_chart():
    """The other half of the rule: bars that are ALREADY cached are free, so core
    ships them. On the default 3M/daily open this is the common case."""
    svc = _index_service(tier2=[{"date": "2026-01-02", "close": 6001.2, "open": 5990.0}])
    resp = await svc.get_index_core("^GSPC", chart_range="3M", interval="daily")
    assert svc.fmp.calls == ["get_stock_price_quote"]
    assert len(resp.chart_data) == 1
    assert resp.chart_data[0].close == 6001.2


@pytest.mark.asyncio
@pytest.mark.parametrize("rng,interval", [("3M", "daily"), ("5Y", "weekly")])
async def test_etf_core_never_pulls_history_or_fundamentals(rng, interval):
    svc = _etf_service()
    resp = await svc.get_etf_core("SCHD", chart_range=rng, interval=interval)
    assert svc.fmp.calls == ["get_stock_price_quote"]
    assert resp.chart_data == []
    # The name comes from the QUOTE payload, not the 30 kB fundamentals section.
    assert resp.name == "Schwab U.S. Dividend Equity ETF"


@pytest.mark.asyncio
async def test_etf_core_falls_back_to_the_symbol_when_the_quote_has_no_name():
    svc = _etf_service(fmp=_FakeFMP({**_QUOTE, "name": None}))
    resp = await svc.get_etf_core("SCHD", chart_range="3M", interval="daily")
    assert resp.name == "SCHD"


@pytest.mark.asyncio
@pytest.mark.parametrize("rng,interval", [("3M", "daily"), ("ALL", "monthly")])
async def test_commodity_core_never_pulls_the_daily_history(rng, interval):
    svc = _commodity_service()
    resp = await svc.get_commodity_core("SIUSD", chart_range=rng, interval=interval)
    assert svc.fmp.calls == ["get_stock_price_quote"]
    assert resp.chart_data == []
    # The trailing "USD" pair suffix is stripped exactly as the full build strips it —
    # a core that resolved the symbol differently would be a different asset.
    assert resp.symbol == "SI"


# ── 3. Degradation ───────────────────────────────────────────────────


class _DeadQuoteFMP(_FakeFMP):
    async def get_stock_price_quote(self, symbol: str):
        await asyncio.sleep(0)
        self.calls.append("get_stock_price_quote")
        raise RuntimeError("quote upstream down")


@pytest.mark.asyncio
@pytest.mark.parametrize("builder,method,sym", [
    (_index_service, "get_index_core", "^GSPC"),
    (_etf_service, "get_etf_core", "SCHD"),
    (_commodity_service, "get_commodity_core", "SIUSD"),
])
async def test_core_refuses_rather_than_painting_a_zero_price(builder, method, sym):
    """A core response is the FIRST thing on screen, so a `$0.00` header there reads as
    authoritative. The client fetches core with `try?`, so raising simply leaves the
    skeleton up until the full response lands — strictly better than a wrong number."""
    svc = builder(fmp=_DeadQuoteFMP())
    with pytest.raises(Exception):
        await getattr(svc, method)(sym, chart_range="3M", interval="daily")


@pytest.mark.asyncio
@pytest.mark.parametrize("builder,method,sym", [
    (_index_service, "get_index_core", "^GSPC"),
    (_etf_service, "get_etf_core", "SCHD"),
    (_commodity_service, "get_commodity_core", "SIUSD"),
])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
async def test_core_rejects_a_non_finite_price(builder, method, sym, bad):
    """FMP emits bare NaN / Infinity JSON tokens and `json.loads` parses them. A NaN is
    TRUTHY, so it sails past `or 0` and lands in a REQUIRED response float — Starlette
    then renders with allow_nan=False and 500s the screen from INSIDE the renderer,
    where the caller cannot catch it."""
    svc = builder(fmp=_FakeFMP({**_QUOTE, "price": bad}))
    with pytest.raises(Exception):
        await getattr(svc, method)(sym, chart_range="3M", interval="daily")


@pytest.mark.asyncio
async def test_index_core_survives_a_non_finite_change_percent():
    """A bad `change`/`changePercentage` must degrade to 0, not take the response with
    it — unlike `price`, a zero move is a legitimate value and not a masquerade."""
    svc = _index_service(fmp=_FakeFMP({
        **_QUOTE, "change": float("nan"), "changePercentage": float("nan"),
        "previousClose": float("nan"),
    }))
    resp = await svc.get_index_core("^GSPC", chart_range="3M", interval="daily")
    assert resp.current_price == 27.31
    assert resp.price_change == 0
    assert resp.price_change_percent == 0


@pytest.mark.asyncio
async def test_index_core_reads_the_stable_singular_change_percentage():
    """`changePercentage` is the /stable spelling; `changesPercentage` is the dead
    /api/v3 one. Reading only the plural pinned "+0.00%" on every index for a release,
    so the core must keep both — in that order."""
    svc = _index_service(fmp=_FakeFMP(
        {**_QUOTE, "changePercentage": None, "changesPercentage": 1.56}))
    resp = await svc.get_index_core("^GSPC", chart_range="3M", interval="daily")
    assert resp.price_change_percent == 1.56


@pytest.mark.asyncio
async def test_index_core_derives_change_percent_from_previous_close():
    svc = _index_service(fmp=_FakeFMP({
        **_QUOTE, "changePercentage": None, "changesPercentage": None,
        "change": 1.0, "previousClose": 100.0,
    }))
    resp = await svc.get_index_core("^GSPC", chart_range="3M", interval="daily")
    assert resp.price_change_percent == 1.0


@pytest.mark.asyncio
async def test_index_core_drops_unrenderable_tier2_chart_rows():
    """A Tier-2 row is JSON a PREVIOUS deploy wrote. iOS declares `close` non-optional,
    so ONE bad bar fails the whole decode — filter on the way OUT, not only on the way
    in."""
    svc = _index_service(tier2=[
        {"date": "2026-01-02", "close": 6001.2},
        {"date": "2026-01-03", "close": float("nan")},   # non-finite
        {"date": "2026-01-04", "close": 0},              # non-positive
        {"date": "2026-01-05"},                          # missing close
        "not-a-dict",
    ])
    resp = await svc.get_index_core("^GSPC", chart_range="3M", interval="daily")
    assert [p.close for p in resp.chart_data] == [6001.2]


@pytest.mark.asyncio
async def test_commodity_core_drops_unrenderable_tier2_chart_rows():
    svc = _commodity_service(tier2=[
        {"date": "2026-01-02", "close": 31.02},
        {"date": "2026-01-03", "close": float("inf")},
        {"close": 30.0},          # missing date
        None,
    ])
    resp = await svc.get_commodity_core("SIUSD", chart_range="3M", interval="daily")
    assert [p.close for p in resp.chart_data] == [31.02]


# ── 4. Anti-vacuity ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_forbidden_stub_actually_fires():
    """If `_FakeFMP.__getattr__` ever stopped raising, every fast-path assertion above
    would pass against a service making unlimited slow calls."""
    fake = _FakeFMP()
    with pytest.raises(AssertionError, match="must not call fmp.get_historical_prices"):
        await fake.get_historical_prices("X", "2020-01-01", "2026-01-01")
    assert "get_historical_prices" in fake.calls
