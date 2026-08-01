"""The user-scoped watchlist strip on `GET /home/dashboard`.

Until now the dashboard took no user id at all, so a day-1 user and a 40-ticker power
user saw a byte-identical Home. This adds the one user-scoped section — which makes it
the one section that can leak between users, so the isolation and degradation
properties are pinned here.
"""

import asyncio

import pytest

from app.schemas.home_dashboard import HomeDashboardResponse, MarketPulseItemResponse
from app.services.home_dashboard_service import HomeDashboardService


def _service(rows=None, quotes=None, read_raises=False, quotes_raise=False):
    svc = object.__new__(HomeDashboardService)

    class _FakeFMP:
        async def get_batch_quotes_bulk(self, symbols):
            if quotes_raise:
                raise RuntimeError("fmp down")
            return [q for q in (quotes or []) if q.get("symbol") in symbols]

    svc.fmp = _FakeFMP()

    class _Tbl:
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, n): self._n = n; return self

        def execute(self):
            if read_raises:
                raise RuntimeError("supabase down")
            return type("R", (), {"data": (rows or [])[: getattr(self, "_n", 99)]})()

    import app.services.home_dashboard_service as mod

    # Assert BEFORE overriding. Without this the monkeypatch below would INJECT the
    # name into the module and every test here would pass even if the service never
    # imported it — which is exactly what happened: the module-level import was
    # missing, these tests were green, and the strip returned empty in production.
    assert hasattr(mod, "get_supabase"), (
        "home_dashboard_service does not import get_supabase at module level — "
        "_build_watchlist would raise NameError at runtime"
    )
    mod.get_supabase = lambda: type("S", (), {"table": lambda self, n: _Tbl()})()
    return svc


_Q = lambda sym, price=10.0, pct=1.0, prev=9.9: {
    "symbol": sym, "price": price, "changePercentage": pct, "previousClose": prev
}


# ── the isolation property ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anonymous_caller_gets_no_watchlist():
    """No user id → empty. Never a fallback to someone else's list."""
    svc = _service(rows=[{"ticker": "AAPL"}], quotes=[_Q("AAPL")])
    assert await svc._get_watchlist_guarded(None) == []
    assert await svc._get_watchlist_guarded("") == []


@pytest.mark.asyncio
async def test_the_strip_is_never_cached_across_users():
    """Every other section here uses a CLASS-level cache shared by all callers.
    Caching this one by user is how one person's holdings get served to another —
    a bug class this codebase has already been burned by. Asserts the builder is
    re-invoked per user rather than memoised."""
    svc = _service(rows=[{"ticker": "AAPL"}], quotes=[_Q("AAPL")])
    calls = []

    async def _spy(user_id):
        calls.append(user_id)
        return []

    svc._build_watchlist = _spy
    await svc._get_watchlist_guarded("user-1")
    await svc._get_watchlist_guarded("user-2")
    assert calls == ["user-1", "user-2"]


# ── degradation: this section must never break the screen ────────────────────

@pytest.mark.asyncio
async def test_supabase_failure_degrades_to_empty_not_error():
    svc = _service(read_raises=True)
    assert await svc._get_watchlist_guarded("user-1") == []


@pytest.mark.asyncio
async def test_quote_failure_drops_tiles_rather_than_fabricating_prices():
    """A fabricated 0.00 on the user's OWN holdings is worse than no tile."""
    svc = _service(rows=[{"ticker": "AAPL"}], quotes_raise=True)
    assert await svc._get_watchlist_guarded("user-1") == []


@pytest.mark.asyncio
async def test_a_slow_build_times_out_into_an_empty_section():
    svc = _service()

    async def _hang(user_id):
        await asyncio.sleep(30)

    svc._build_watchlist = _hang
    import app.services.home_dashboard_service as mod
    original = mod._WATCHLIST_BUILD_TIMEOUT_SECONDS
    mod._WATCHLIST_BUILD_TIMEOUT_SECONDS = 0.05
    try:
        assert await svc._get_watchlist_guarded("user-1") == []
    finally:
        mod._WATCHLIST_BUILD_TIMEOUT_SECONDS = original


@pytest.mark.asyncio
async def test_empty_watchlist_returns_empty_without_calling_fmp():
    svc = _service(rows=[])

    async def _boom(symbols):
        raise AssertionError("should not quote an empty watchlist")

    svc.fmp.get_batch_quotes_bulk = _boom
    assert await svc._build_watchlist("user-1") == []


# ── data hygiene ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nan_and_infinite_quotes_are_dropped():
    """FMP emits NaN/Infinity for thin or just-listed symbols; those serialize to
    invalid JSON under allow_nan=False and 500 the whole screen."""
    svc = _service(
        rows=[{"ticker": "GOOD"}, {"ticker": "NANP"}, {"ticker": "INFP"}],
        quotes=[
            _Q("GOOD"),
            _Q("NANP", price=float("nan")),
            _Q("INFP", pct=float("inf")),
        ],
    )
    tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["GOOD"]


@pytest.mark.asyncio
async def test_tickers_without_a_quote_are_dropped():
    svc = _service(rows=[{"ticker": "AAPL"}, {"ticker": "DELISTED"}],
                   quotes=[_Q("AAPL")])
    tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["AAPL"]


@pytest.mark.asyncio
async def test_duplicate_tickers_are_deduped():
    svc = _service(rows=[{"ticker": "AAPL"}, {"ticker": "aapl"}], quotes=[_Q("AAPL")])
    tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["AAPL"]


@pytest.mark.asyncio
async def test_tile_carries_company_name_and_asset_type():
    svc = _service(
        rows=[{"ticker": "BTC", "company_name": "Bitcoin", "asset_type": "crypto"}],
        quotes=[_Q("BTC", price=64000.0, pct=-1.2, prev=64800.0)],
    )
    t = (await svc._build_watchlist("user-1"))[0]
    assert (t.name, t.type, t.price, t.change_percent) == ("Bitcoin", "crypto", 64000.0, -1.2)
    assert t.previous_close == 64800.0
    assert t.spark == []   # no per-ticker intraday call for a glanceable strip


@pytest.mark.asyncio
async def test_tile_count_is_bounded():
    import app.services.home_dashboard_service as mod

    many = [{"ticker": f"T{i}"} for i in range(50)]
    svc = _service(rows=many, quotes=[_Q(f"T{i}") for i in range(50)])
    tiles = await svc._build_watchlist("user-1")
    assert len(tiles) <= mod._WATCHLIST_MAX_TILES


# ── backend ↔ iOS contract ───────────────────────────────────────────────────

def test_watchlist_is_optional_so_shipped_builds_keep_decoding():
    """Additive + defaulted: a response with no `watchlist` key must still validate,
    and old clients must be able to ignore it."""
    r = HomeDashboardResponse(
        market_status_text="Markets Closed", market_is_open=False, pulse=[]
    )
    assert r.watchlist == []


def test_watchlist_serializes_under_the_snake_case_wire_name():
    """iOS decodes with explicit CodingKeys and does NOT use convertFromSnakeCase,
    so the literal key on the wire is the contract."""
    r = HomeDashboardResponse(
        market_status_text="Markets Open", market_is_open=True, pulse=[],
        watchlist=[MarketPulseItemResponse(
            symbol="AAPL", name="Apple Inc.", type="stock",
            price=1.0, change_percent=2.0, previous_close=0.9, spark=[],
        )],
    )
    dumped = r.model_dump()
    assert "watchlist" in dumped
    item = dumped["watchlist"][0]
    for key in ("symbol", "name", "type", "price", "change_percent",
                "previous_close", "spark"):
        assert key in item, f"iOS expects `{key}` on a watchlist tile"


@pytest.mark.asyncio
async def test_the_shared_guest_sentinel_is_not_treated_as_a_user():
    """Caught live, not by the original test: the endpoint passes the resolved
    identity, and for a header-less caller that is the SHARED GUEST_USER_ID — whose
    rows are the pre-migration-108 pool every signed-out install wrote into. Rendering
    them puts strangers' tickers on Home. The `not user_id` guard alone never fired
    for this, because the sentinel is a perfectly non-empty uuid."""
    from app.dependencies import GUEST_USER_ID

    svc = _service(rows=[{"ticker": "SOMEONE_ELSES"}], quotes=[_Q("SOMEONE_ELSES")])
    assert await svc._get_watchlist_guarded(GUEST_USER_ID) == []
