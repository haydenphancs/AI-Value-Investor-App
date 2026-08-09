"""The user-scoped watchlist strip on `GET /home/dashboard`.

Until migration 126 this strip read `watchlist_items` directly — 12 newest-added, under a
title hardcoded in Swift. It now follows the user's ACTIVE GROUP, name and all, so this
section, the Updates chips and the Tracking tab always agree. That makes two properties
worth pinning here:

  * ISOLATION — it is still the only user-scoped section on the screen, so it is still the
    only one that can leak between users.
  * THE FALLBACK LADDER — "the user has no tickers", "their group is empty" and "we could
    not read their group" render identically, and only these tests (and the logs) tell them
    apart. A silent collapse to the wrong branch is how a label ends up asserting something
    the data does not back.
"""

import asyncio

import pytest

from app.schemas.home_dashboard import HomeDashboardResponse, MarketPulseItemResponse
from app.services.active_group_service import ActiveGroup, ActiveGroupUnavailable
from app.services.home_dashboard_service import HomeDashboardService

_DEFAULT_TITLE = "Your Watchlist"


def _service(
    rows=None,
    quotes=None,
    read_raises=False,
    quotes_raise=False,
    group=None,
    group_raises=False,
    meta=None,
):
    """Build a service whose Supabase + FMP + active-group reads are all stubbed.

    `group=None` (the default) means "the user has no active group", which exercises the
    master-watchlist fallback that `rows` feeds — i.e. the pre-126 behaviour.
    """
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
        def in_(self, *a): return self
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
    assert hasattr(mod, "get_active_group"), (
        "home_dashboard_service does not import get_active_group at module level — "
        "the strip would never follow the active group"
    )
    mod.get_supabase = lambda: type("S", (), {"table": lambda self, n: _Tbl()})()

    async def _fake_group(_user_id):
        if group_raises:
            raise ActiveGroupUnavailable("supabase down")
        return group

    async def _fake_meta(_user_id, tickers):
        return {t: (meta or {}).get(t, {}) for t in tickers}

    mod.get_active_group = _fake_group
    mod.fetch_ticker_metadata = _fake_meta
    return svc


_Q = lambda sym, price=10.0, pct=1.0, prev=9.9: {
    "symbol": sym, "price": price, "changePercentage": pct, "previousClose": prev
}


def _group(name="Holdings", tickers=("AAPL",)):
    return ActiveGroup(id="g1", name=name, tickers=list(tickers))


# ── the isolation property ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anonymous_caller_gets_no_watchlist():
    """No user id → empty. Never a fallback to someone else's list."""
    svc = _service(rows=[{"ticker": "AAPL"}], quotes=[_Q("AAPL")])
    assert await svc._get_watchlist_guarded(None) == (_DEFAULT_TITLE, False, [])
    assert await svc._get_watchlist_guarded("") == (_DEFAULT_TITLE, False, [])


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
        return (_DEFAULT_TITLE, False, [])

    svc._build_watchlist = _spy
    await svc._get_watchlist_guarded("user-1")
    await svc._get_watchlist_guarded("user-2")
    assert calls == ["user-1", "user-2"]


# ── degradation: this section must never break the screen ────────────────────

@pytest.mark.asyncio
async def test_supabase_failure_degrades_to_empty_not_error():
    svc = _service(read_raises=True)
    assert await svc._get_watchlist_guarded("user-1") == (_DEFAULT_TITLE, False, [])


@pytest.mark.asyncio
async def test_quote_failure_drops_tiles_rather_than_fabricating_prices():
    """A fabricated 0.00 on the user's OWN holdings is worse than no tile."""
    svc = _service(rows=[{"ticker": "AAPL"}], quotes_raise=True)
    assert await svc._get_watchlist_guarded("user-1") == (_DEFAULT_TITLE, False, [])


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
        assert await svc._get_watchlist_guarded("user-1") == (_DEFAULT_TITLE, False, [])
    finally:
        mod._WATCHLIST_BUILD_TIMEOUT_SECONDS = original


@pytest.mark.asyncio
async def test_empty_watchlist_returns_empty_without_calling_fmp():
    svc = _service(rows=[])

    async def _boom(symbols):
        raise AssertionError("should not quote an empty watchlist")

    svc.fmp.get_batch_quotes_bulk = _boom
    assert await svc._build_watchlist("user-1") == (_DEFAULT_TITLE, False, [])


# ── the active group drives the strip ────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_strip_follows_the_active_group_and_takes_its_name():
    """The whole point: rename "Holdings" → "Tech" on Tracking and Home retitles."""
    svc = _service(
        group=_group(name="Tech", tickers=["ORCL", "PLUG"]),
        meta={"ORCL": {"company_name": "Oracle Corporation", "asset_type": "stock"}},
        quotes=[_Q("ORCL"), _Q("PLUG")],
    )
    title, _is_group, tiles = await svc._build_watchlist("user-1")
    assert title == "Tech"
    assert [t.symbol for t in tiles] == ["ORCL", "PLUG"]
    assert tiles[0].name == "Oracle Corporation"


@pytest.mark.asyncio
async def test_group_order_is_preserved_not_re_sorted():
    """`portfolio_items.position` is the arrangement the user made on Tracking. This strip
    mirrors that tab, so it must not quietly alphabetise or re-sort by recency."""
    svc = _service(
        group=_group(tickers=["ZZZ", "AAA", "MMM"]),
        quotes=[_Q("ZZZ"), _Q("AAA"), _Q("MMM")],
    )
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["ZZZ", "AAA", "MMM"]


@pytest.mark.asyncio
async def test_an_empty_group_keeps_its_name_and_shows_nothing():
    """The user emptied THAT group. Falling back to the master watchlist here would put
    tickers that are not in "Tech" under a heading that says "Tech"."""
    svc = _service(
        group=_group(name="Tech", tickers=[]),
        rows=[{"ticker": "AAPL"}],
        quotes=[_Q("AAPL")],
    )
    assert await svc._build_watchlist("user-1") == ("Tech", True, [])


@pytest.mark.asyncio
async def test_a_group_with_a_blank_name_falls_back_to_the_default_title():
    """A header rendering an empty string is worse than the generic label."""
    svc = _service(group=_group(name="", tickers=["AAPL"]), quotes=[_Q("AAPL")])
    title, _is_group, tiles = await svc._build_watchlist("user-1")
    assert title == _DEFAULT_TITLE
    assert [t.symbol for t in tiles] == ["AAPL"]


@pytest.mark.asyncio
async def test_no_active_group_falls_back_to_the_master_watchlist():
    """Pre-126 behaviour, kept for a user mid-backfill or one who never opened Tracking."""
    svc = _service(group=None, rows=[{"ticker": "AAPL"}], quotes=[_Q("AAPL")])
    title, _is_group, tiles = await svc._build_watchlist("user-1")
    assert title == _DEFAULT_TITLE
    assert [t.symbol for t in tiles] == ["AAPL"]


@pytest.mark.asyncio
async def test_an_unreadable_group_falls_back_rather_than_blanking_the_section():
    """A Supabase blip on the groups table must not read as "you have no tickers"."""
    svc = _service(group_raises=True, rows=[{"ticker": "AAPL"}], quotes=[_Q("AAPL")])
    title, _is_group, tiles = await svc._build_watchlist("user-1")
    assert title == _DEFAULT_TITLE
    assert [t.symbol for t in tiles] == ["AAPL"]


@pytest.mark.asyncio
async def test_a_group_ticker_with_no_watchlist_row_still_renders_as_its_symbol():
    """`set_portfolio_tickers` enforces portfolio_items ⊆ watchlist_items, so a metadata
    miss means a repair path failed — degrade to the bare symbol, never drop the tile."""
    svc = _service(group=_group(tickers=["ORCL"]), meta={}, quotes=[_Q("ORCL")])
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert [(t.symbol, t.name, t.type) for t in tiles] == [("ORCL", "ORCL", "stock")]


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
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["GOOD"]


@pytest.mark.asyncio
async def test_nan_quotes_are_dropped_on_the_group_path_too():
    """The guard has to live on BOTH branches — the group path builds its rows from
    metadata rather than the watchlist read, so it does not inherit the fallback's."""
    svc = _service(
        group=_group(tickers=["GOOD", "NANP"]),
        quotes=[_Q("GOOD"), _Q("NANP", price=float("nan"))],
    )
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["GOOD"]


@pytest.mark.asyncio
async def test_tickers_without_a_quote_are_dropped():
    svc = _service(rows=[{"ticker": "AAPL"}, {"ticker": "DELISTED"}],
                   quotes=[_Q("AAPL")])
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["AAPL"]


@pytest.mark.asyncio
async def test_duplicate_tickers_are_deduped():
    svc = _service(rows=[{"ticker": "AAPL"}, {"ticker": "aapl"}], quotes=[_Q("AAPL")])
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert [t.symbol for t in tiles] == ["AAPL"]


@pytest.mark.asyncio
async def test_tile_carries_company_name_and_asset_type():
    svc = _service(
        rows=[{"ticker": "BTC", "company_name": "Bitcoin", "asset_type": "crypto"}],
        quotes=[_Q("BTC", price=64000.0, pct=-1.2, prev=64800.0)],
    )
    t = (await svc._build_watchlist("user-1"))[2][0]
    assert (t.name, t.type, t.price, t.change_percent) == ("Bitcoin", "crypto", 64000.0, -1.2)
    assert t.previous_close == 64800.0
    assert t.spark == []   # no per-ticker intraday call for a glanceable strip


@pytest.mark.asyncio
async def test_tile_count_is_bounded():
    import app.services.home_dashboard_service as mod

    many = [{"ticker": f"T{i}"} for i in range(50)]
    svc = _service(rows=many, quotes=[_Q(f"T{i}") for i in range(50)])
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert len(tiles) <= mod._WATCHLIST_MAX_TILES


@pytest.mark.asyncio
async def test_tile_count_is_bounded_on_the_group_path_too():
    import app.services.home_dashboard_service as mod

    svc = _service(
        group=_group(tickers=[f"T{i}" for i in range(50)]),
        quotes=[_Q(f"T{i}") for i in range(50)],
    )
    _, _is_group, tiles = await svc._build_watchlist("user-1")
    assert len(tiles) <= mod._WATCHLIST_MAX_TILES
    # And it is the FIRST N of the user's own order, not an arbitrary slice.
    assert [t.symbol for t in tiles] == [f"T{i}" for i in range(mod._WATCHLIST_MAX_TILES)]


# ── backend ↔ iOS contract ───────────────────────────────────────────────────

def test_watchlist_is_optional_so_shipped_builds_keep_decoding():
    """Additive + defaulted: a response with no `watchlist` key must still validate,
    and old clients must be able to ignore it."""
    r = HomeDashboardResponse(
        market_status_text="Markets Closed", market_is_open=False, pulse=[]
    )
    assert r.watchlist == []


def test_watchlist_title_defaults_to_the_label_the_section_always_showed():
    """A client that ignores the new key, and a user with no active group, must both land
    on the exact string this section rendered before it was server-supplied."""
    r = HomeDashboardResponse(
        market_status_text="Markets Closed", market_is_open=False, pulse=[]
    )
    assert r.watchlist_title == _DEFAULT_TITLE
    assert "watchlist_title" in r.model_dump()


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
    assert await svc._get_watchlist_guarded(GUEST_USER_ID) == (_DEFAULT_TITLE, False, [])
