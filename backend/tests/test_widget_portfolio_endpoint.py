"""`GET /widget/portfolio-mover` — every branch of the handler, against stubs.

WHY THIS FILE EXISTS
--------------------
Until 2026-09-30 nothing drove this handler's body. The only coverage was auth (a widget
token is refused, the route sits on the strict router), so three defects shipped green
behind a Holdings tile the user cannot interrogate:

* an EMPTY group answered with the MARKET payload, which the iOS snapshot store refuses
  for the portfolio slot — so emptying a group froze the old holdings on the Home Screen;
* a watchlist READ FAILURE was logged and became `[]`, i.e. "no holdings" — the
  outage-equals-empty conflation `active_group_service` says must never happen;
* `ActiveGroupUnavailable` fell back to the master watchlist, silently swapping a "Tech"
  tile for every holding the user owns.

The contract now (schemas/widget.py, `WidgetMoverPayload`):

* every branch answers `mode="portfolio"` — the market fallback is gone;
* `holdings_count` is N (the valid tickers BEFORE the `_MAX_HOLDINGS` cap), 0 for an authoritative
  empty group, None for a DEGRADED answer the client keeps its last good snapshot over;
* `group_name` is applied AFTER the 60 s service cache, on a COPY: the cache key carries
  neither the name nor the group id, so a rename shows at once, and the cached payload —
  returned by reference to every caller in the TTL — is never mutated.

Hermetic: `get_active_group` and `get_widget_movers_service` are patched on the ENDPOINT
module (module-level imports there), and the watchlist read's function-local
`from app.database import get_supabase` is patched on its SOURCE module (testing.md trap 1).
The handler coroutine is called directly, so no auth dependency runs.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional

import pytest
from fastapi.params import Depends as DependsParam

import app.database as database
from app.api.v1.endpoints import widget
from app.schemas.widget import WidgetMoverPayload
from app.services.active_group_service import ActiveGroup, ActiveGroupUnavailable
from app.services.widget_movers_service import WidgetMoversService

UID = "11111111-2222-3333-4444-555555555555"


# ── stubs ─────────────────────────────────────────────────────────────


def _payload(holdings_count: Optional[int] = 0, **extra: Any) -> WidgetMoverPayload:
    """What the SERVICE returns: a portfolio build, no name (it never knows the name)."""
    return WidgetMoverPayload(
        mode="portfolio",
        as_of="2026-09-29T20:05:00Z",
        market_session="closed",
        session_date="2026-09-29",
        session_label="Tue close",
        scope_label="Your holdings",
        holdings_count=holdings_count,
        **extra,
    )


class _FakeMovers:
    """Records what the handler asked for. `get_market_mover` must never be reached."""

    def __init__(self, result: Any = None, raises: Optional[BaseException] = None):
        self.portfolio_calls: List[tuple] = []
        self.market_calls = 0
        self._result = result
        self._raises = raises

    async def get_portfolio_mover(self, user_id: str, tickers):
        self.portfolio_calls.append((user_id, list(tickers)))
        if self._raises is not None:
            raise self._raises
        if self._result is not None:
            return self._result
        # Default: a healthy build whose count is what the SERVICE was handed (the cap).
        return _payload(holdings_count=len(tickers))

    async def get_market_mover(self):
        self.market_calls += 1
        raise AssertionError("the portfolio route must never fall back to the market payload")


def _group(name: Optional[str], tickers: List[str]) -> ActiveGroup:
    return ActiveGroup(id="g-1", name=name, tickers=list(tickers))


def _install(monkeypatch, *, group=None, group_raises=None, movers=None) -> _FakeMovers:
    async def _get_active_group(user_id: str):
        assert user_id == UID
        if group_raises is not None:
            raise group_raises
        return group

    movers = movers or _FakeMovers()
    monkeypatch.setattr(widget, "get_active_group", _get_active_group)
    monkeypatch.setattr(widget, "get_widget_movers_service", lambda: movers)
    return movers


class _Query:
    """Chainable stand-in for the supabase-py query builder used by `_watchlist_tickers`."""

    def __init__(self, rows=None, raises: Optional[BaseException] = None, log=None):
        self._rows = rows or []
        self._raises = raises
        self._log = log if log is not None else []

    def table(self, name):
        self._log.append(("table", name))
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._log.append(("eq", col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._log.append(("limit", n))
        return self

    def execute(self):
        if self._raises is not None:
            raise self._raises

        class _Res:
            data = self._rows

        return _Res()


def _supabase(monkeypatch, *, rows=None, raises=None) -> list:
    log: list = []
    fake = _Query(rows=rows, raises=raises, log=log)
    monkeypatch.setattr(database, "get_supabase", lambda: fake)
    return log


def _no_supabase(monkeypatch) -> list:
    """The watchlist must NOT be read on this branch — record any attempt."""
    calls: list = []

    def _get():
        calls.append("get_supabase")
        raise RuntimeError("the watchlist was read on a branch that must not read it")

    monkeypatch.setattr(database, "get_supabase", _get)
    return calls


async def _call() -> WidgetMoverPayload:
    return await widget.get_portfolio_mover(user={"id": UID}, _rate_limit=None)


# ── a non-empty group ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_group_answers_in_portfolio_mode_with_its_name_and_count(monkeypatch):
    movers = _install(monkeypatch, group=_group("Tech", ["aapl", "MSFT", "NVDA"]))
    _no_supabase(monkeypatch)

    p = await _call()

    assert p.mode == "portfolio"
    assert p.group_name == "Tech"
    assert p.holdings_count == 3
    assert movers.portfolio_calls == [(UID, ["AAPL", "MSFT", "NVDA"])]
    assert movers.market_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("raw, expected", [
    ("", None),
    ("   ", None),
    (None, None),
    ("  Long Term  ", "Long Term"),
    ("Tech", "Tech"),
])
async def test_a_blank_group_name_is_none_so_the_client_says_my_holdings(monkeypatch, raw, expected):
    """"" is NO name: a header reading " · 3 holdings" with nothing before the dot is the
    bug the None → "My Holdings" fallback exists to prevent."""
    _install(monkeypatch, group=_group(raw, ["AAPL", "MSFT", "NVDA"]))
    p = await _call()
    assert p.group_name == expected
    assert p.holdings_count == 3


@pytest.mark.asyncio
async def test_invalid_and_duplicate_tickers_are_filtered_before_counting(monkeypatch):
    """`_valid_ticker` allows alnum and `.-^=`, at most 32 chars. The COUNT is taken after
    the filter and the dedup, so "N holdings" never counts a row the tile can never show."""
    raw = ["aapl", "AAPL", "BRK.B", "^GSPC", "", "BRK/B", "X" * 33, "msft"]
    movers = _install(monkeypatch, group=_group("Tech", raw))
    p = await _call()
    assert movers.portfolio_calls == [(UID, ["AAPL", "BRK.B", "^GSPC", "MSFT"])]
    assert p.holdings_count == 4


@pytest.mark.asyncio
async def test_more_than_the_cap_ranks_the_cap_but_reports_the_real_count(monkeypatch):
    """The header says how many holdings the user HAS. The ranking cap is a cost bound, not
    a fact about the portfolio — reporting the cap for a larger group is a count the user
    can see is wrong."""
    tickers = [f"T{i:03d}" for i in range(650)]
    movers = _install(monkeypatch, group=_group("Everything", tickers))

    p = await _call()

    (uid, sent), = movers.portfolio_calls
    assert sent == tickers[:widget._MAX_HOLDINGS] and len(sent) == 500, (
        "the first _MAX_HOLDINGS in the user's own order must be ranked"
    )
    assert p.holdings_count == 650
    assert p.group_name == "Everything"


@pytest.mark.asyncio
async def test_exactly_one_holding_is_served(monkeypatch):
    movers = _install(monkeypatch, group=_group("Solo", ["TSLA"]))
    p = await _call()
    assert movers.portfolio_calls == [(UID, ["TSLA"])]
    assert p.holdings_count == 1 and p.group_name == "Solo"


@pytest.mark.asyncio
async def test_a_degraded_build_keeps_its_count_withheld_but_still_names_the_group(monkeypatch):
    """The service sets `holdings_count=None` when the QUOTE leg failed. The endpoint must not
    paint the pre-cap N over it: that would turn an outage into "No prices for your 3
    holdings today", and the client would replace a good snapshot with it."""
    movers = _FakeMovers(result=_payload(holdings_count=None))
    _install(monkeypatch, group=_group("Tech", ["AAPL", "MSFT", "NVDA"]), movers=movers)

    p = await _call()

    assert p.holdings_count is None
    assert p.group_name == "Tech"
    assert p.mode == "portfolio"


# ── the empty group: a STATE, never the market ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("tickers", [[], ["", "BRK/B", "Y" * 40]])
async def test_an_empty_group_is_an_authoritative_zero_not_the_market(monkeypatch, tickers):
    """Empty — or only unshowable tickers — is "No holdings in Tech yet": `mode="portfolio"`,
    `holdings_count=0`, the name, and an honest session anchor. NOT the market payload,
    which the client refuses for the Holdings slot and so used to freeze the old group."""
    movers = _install(monkeypatch, group=_group("Tech", tickers))
    _no_supabase(monkeypatch)

    p = await _call()

    assert p.mode == "portfolio"
    assert p.holdings_count == 0, "0 is the authoritative empty; None would read as degraded"
    assert p.group_name == "Tech"
    assert p.headline_mover is None and p.runners_up == []
    assert p.market_assets == []
    assert p.session_date and p.session_label, "even the empty state carries a time anchor"
    assert movers.portfolio_calls == [] and movers.market_calls == 0


@pytest.mark.asyncio
async def test_an_empty_watchlist_with_no_group_is_also_an_authoritative_zero(monkeypatch):
    movers = _install(monkeypatch, group=None)
    _supabase(monkeypatch, rows=[])

    p = await _call()

    assert p.mode == "portfolio"
    assert p.holdings_count == 0
    assert p.group_name is None
    assert movers.market_calls == 0


# ── degraded: unreadable holdings ─────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unreadable_active_group_is_degraded_not_a_scope_swap(monkeypatch):
    """`ActiveGroupUnavailable` must NOT fall back to the master watchlist: a "Tech" tile
    rebuilt from every holding the user owns, labelled as their holdings, is a silent scope
    swap. The degraded payload lets the client keep its last good Tech snapshot."""
    movers = _install(monkeypatch, group_raises=ActiveGroupUnavailable("db down"))
    watchlist_reads = _no_supabase(monkeypatch)

    p = await _call()

    assert p.mode == "portfolio"
    assert p.holdings_count is None
    assert p.group_name is None
    assert p.headline_mover is None
    assert watchlist_reads == [], "the master watchlist was read instead of degrading"
    assert movers.portfolio_calls == [] and movers.market_calls == 0


@pytest.mark.asyncio
async def test_no_group_reads_the_master_watchlist(monkeypatch):
    movers = _install(monkeypatch, group=None)
    log = _supabase(monkeypatch, rows=[
        {"ticker": "aapl"}, {"ticker": "NVDA"}, {"ticker": None}, {"ticker": ""},
        {"ticker": "BAD/T"}, {"ticker": "AAPL"}, {"ticker": 7203},
    ])

    p = await _call()

    assert ("table", "watchlist_items") in log
    assert ("eq", "user_id", UID) in log, "the watchlist read must be scoped to the caller"
    assert ("limit", widget._MAX_HOLDINGS) in log
    # Non-string ticker 7203 is stringified (a TSE-style code is a valid symbol); blanks,
    # None and the invalid "BAD/T" are dropped; "aapl"/"AAPL" collapse to one.
    assert movers.portfolio_calls == [(UID, ["AAPL", "NVDA", "7203"])]
    assert p.holdings_count == 3
    assert p.group_name is None, "the master watchlist is not a named group"


@pytest.mark.asyncio
async def test_a_watchlist_read_failure_is_degraded_not_no_holdings(monkeypatch):
    """It used to log and return `[]`, which the route served as "no holdings" — so a
    database blip told the user their holdings were gone, and replaced their snapshot."""
    movers = _install(monkeypatch, group=None)
    _supabase(monkeypatch, raises=RuntimeError("connection reset"))

    p = await _call()

    assert p.mode == "portfolio"
    assert p.holdings_count is None, "an outage must never read as an authoritative empty"
    assert movers.portfolio_calls == [] and movers.market_calls == 0


@pytest.mark.asyncio
async def test_watchlist_tickers_raises_on_a_read_failure(monkeypatch):
    """The helper itself, not just the route: a swallowed failure here is the bug."""
    _supabase(monkeypatch, raises=RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await widget._watchlist_tickers(UID)


@pytest.mark.asyncio
async def test_a_service_failure_is_the_degraded_portfolio_payload(monkeypatch):
    movers = _FakeMovers(raises=RuntimeError("quote source down"))
    _install(monkeypatch, group=_group("Tech", ["AAPL"]), movers=movers)

    p = await _call()

    assert p.mode == "portfolio"
    assert p.holdings_count is None
    assert p.group_name is None
    assert p.headline_mover is None
    assert p.session_label, "a blank tile that cannot say WHEN looks broken"


# ── the name is applied AFTER the cache, on a copy ────────────────────


def _real_service(monkeypatch, built: Dict[str, int]) -> WidgetMoversService:
    """A real service — so `_cached` and its TTL are exercised — with only the build
    stubbed. Counts builds so a cache hit is observable."""
    svc = WidgetMoversService()

    async def _build_portfolio(user_id, tickers):
        built["n"] += 1
        return _payload(holdings_count=len(tickers))

    monkeypatch.setattr(svc, "_build_portfolio", _build_portfolio)
    monkeypatch.setattr(widget, "get_widget_movers_service", lambda: svc)
    return svc


@pytest.mark.asyncio
async def test_a_rename_within_the_cache_ttl_shows_the_new_name(monkeypatch):
    built = {"n": 0}
    _real_service(monkeypatch, built)
    tickers = ["AAPL", "MSFT", "NVDA"]
    state = {"group": _group("Tech", tickers)}

    async def _get_active_group(user_id):
        return state["group"]

    monkeypatch.setattr(widget, "get_active_group", _get_active_group)

    first = await _call()
    state["group"] = _group("Growth", tickers)   # renamed, same membership
    second = await _call()

    assert built["n"] == 1, "the second call must be a cache hit — the test is otherwise vacuous"
    assert first.group_name == "Tech"
    assert second.group_name == "Growth", "a rename inside the 60 s TTL served the old name"


@pytest.mark.asyncio
async def test_the_cached_payload_is_never_mutated(monkeypatch):
    """The cached object is returned BY REFERENCE to every caller in the TTL. Writing the
    name or the pre-cap count onto it would serve one request's values to the next — and
    the market entry is shared by every user on the platform."""
    built = {"n": 0}
    svc = _real_service(monkeypatch, built)
    tickers = [f"T{i:03d}" for i in range(650)]
    group = _group("Tech", tickers)

    async def _get_active_group(user_id):
        return group

    monkeypatch.setattr(widget, "get_active_group", _get_active_group)

    p = await _call()

    ((key, (stamp, cached)),) = svc._cache.items()
    before = cached.model_dump()
    assert p is not cached, "the handler returned the shared cached object itself"
    assert p.group_name == "Tech" and p.holdings_count == 650

    p2 = await _call()
    assert built["n"] == 1
    assert cached.model_dump() == before, "the cached payload was mutated by the handler"
    assert cached.group_name is None
    assert cached.holdings_count == widget._MAX_HOLDINGS, "the service's own count (the ranked cap) must survive"
    assert p2.holdings_count == 650


# ── its own rate-limit bucket ─────────────────────────────────────────


def test_the_route_carries_its_own_rate_limiter_not_the_shared_standard_one():
    """`StandardRateLimit` is shared with a dozen browsing routes, so a burst elsewhere
    429'd the widget refresh — and a 429 is the one answer here that bypasses "degrade,
    never error"."""
    from app.dependencies import StandardRateLimit, UserIdRateLimitChecker

    params = inspect.signature(widget.get_portfolio_mover).parameters
    limiter = params["_rate_limit"].default
    assert limiter is widget.WidgetPortfolioRateLimit
    assert limiter is not StandardRateLimit
    checker = limiter.dependency
    assert isinstance(checker, UserIdRateLimitChecker)
    assert checker.bucket == "widget_portfolio"
    # The app asks at most ~2 times per foreground behind a 60 s throttle.
    assert checker.max_requests >= 10 and checker.window_seconds == 60


def test_the_widget_portfolio_bucket_is_unique_across_every_limiter():
    """Two limiter instances with one bucket silently share a counter. `dependencies.py`
    is scanned by `test_rate_limiter_attachment.py`, but this limiter lives in the endpoint
    module, so scan every endpoint module too."""
    import importlib
    import pkgutil

    import app.api.v1.endpoints as endpoints_pkg
    import app.dependencies as deps
    from app.dependencies import IdentityRateLimitChecker

    modules = [deps] + [
        importlib.import_module(f"{endpoints_pkg.__name__}.{m.name}")
        for m in pkgutil.iter_modules(endpoints_pkg.__path__)
    ]
    owners: Dict[str, set] = {}
    for mod in modules:
        for name, obj in vars(mod).items():
            if isinstance(obj, DependsParam) and isinstance(obj.dependency, IdentityRateLimitChecker):
                owners.setdefault(obj.dependency.bucket, set()).add(id(obj.dependency))
    assert "widget_portfolio" in owners, "the scan did not find the widget limiter — it has rotted"
    assert len(owners["widget_portfolio"]) == 1, "two limiter instances share 'widget_portfolio'"
    assert "widget" not in owners and "guest" not in owners, (
        "an identity limiter writes into a key space another checker class owns"
    )


def test_the_ranking_cap_covers_every_holding_a_user_can_have():
    """The tile states counts ("▲ 8 ▼ 5", "N no price"), so every holding the product
    allows must actually be ranked — below the watchlist cap, a 250-holding group reported
    its 50 unranked names as unpriced."""
    from app.config import settings

    assert widget._MAX_HOLDINGS >= int(settings.WATCHLIST_MAX_ITEMS)
