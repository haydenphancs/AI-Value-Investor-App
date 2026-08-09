"""`GET /updates/tabs` — the ticker pills follow the active group, capped by plan.

Two behaviours meet in this endpoint and each has a way of going quietly wrong:

  * SOURCE — the pills used to read `watchlist_items` directly. They now follow the active
    group, so the Updates strip, Home's watchlist section and the Tracking tab always show
    the same tickers under the same name. If this silently reverts to the watchlist, the
    three screens disagree again and nothing errors.
  * PLAN — this is the app's first tier gate. Everything monetised before it is metered in
    credits, where an unknown tier just means "no allocation". A feature gate fails the
    other way: get it wrong and the paid surface is free for everyone, with no error.

The gate TRUNCATES and never 402s. An over-limit group is the ordinary state after a
downgrade, and after adding tickers on Tracking — which is free and must stay free.

No network / Supabase.
"""
from __future__ import annotations

import asyncio

import pytest

import app.api.v1.endpoints.updates as up
from app.services.active_group_service import ActiveGroup, ActiveGroupUnavailable

_USER = "user-1"


def _user(tier="free", uid=_USER):
    return {"id": uid, "email": "a@b.c", "tier": tier}


def _patch(monkeypatch, *, group=None, group_raises=False, watchlist=(),
           quotes=None, quotes_raise=False, watchlist_raises=False):
    async def _fake_group(_uid):
        if group_raises:
            raise ActiveGroupUnavailable("supabase down")
        return group

    async def _fake_meta(_uid, tickers):
        return {t: {"company_name": f"{t} Inc.", "logo_url": None} for t in tickers}

    class _Tbl:
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, n): return self

        def execute(self):
            if watchlist_raises:
                raise RuntimeError("supabase down")
            return type("R", (), {"data": list(watchlist)})()

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            if quotes_raise:
                raise RuntimeError("fmp down")
            if quotes is not None:
                return [q for q in quotes if q.get("symbol") in symbols]
            return [{"symbol": s, "changePercentage": 1.0} for s in symbols]

    monkeypatch.setattr(up, "get_active_group", _fake_group)
    monkeypatch.setattr(up, "fetch_ticker_metadata", _fake_meta)
    monkeypatch.setattr(up, "get_supabase",
                        lambda: type("S", (), {"table": lambda _s, n: _Tbl()})())
    monkeypatch.setattr(up, "get_fmp_client", lambda: _FMP())


def _tabs(resp):
    """Ticker scopes only — the Market pill is always present and never gated."""
    return [t.scope for t in resp.tabs if not t.is_market_tab]


def _group(tickers, name="Holdings"):
    return ActiveGroup(id="g1", name=name, tickers=list(tickers))


# ── the Market pill is unconditional ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_market_pill_is_always_present_and_never_gated(monkeypatch):
    """Free users get a working screen. The Updates tab exists to be useful before you
    pay, and per-ticker news is free on every detail screen anyway — what the plan gates
    is the aggregated feed, not the content."""
    _patch(monkeypatch, group=_group([]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert resp.tabs[0].scope == up.MARKET_SCOPE
    assert resp.tabs[0].is_market_tab is True


# ── source: the active group ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pills_come_from_the_active_group_and_carry_its_name(monkeypatch):
    _patch(monkeypatch, group=_group(["ORCL", "PLUG"], name="Tech"))
    resp = await up.get_updates_tabs(user=_user("pro"))
    assert _tabs(resp) == ["ORCL", "PLUG"]
    assert resp.group_name == "Tech"


@pytest.mark.asyncio
async def test_no_active_group_falls_back_to_the_master_watchlist(monkeypatch):
    _patch(monkeypatch, group=None, watchlist=[{"ticker": "AAPL"}])
    resp = await up.get_updates_tabs(user=_user("pro"))
    assert _tabs(resp) == ["AAPL"]
    assert resp.group_name is None


@pytest.mark.asyncio
async def test_an_unreadable_group_falls_back_rather_than_losing_the_tab_bar(monkeypatch):
    """The pills are this screen's navigation. Showing a superset for one request beats
    collapsing to Market-only."""
    _patch(monkeypatch, group_raises=True, watchlist=[{"ticker": "AAPL"}])
    resp = await up.get_updates_tabs(user=_user("pro"))
    assert _tabs(resp) == ["AAPL"]


@pytest.mark.asyncio
async def test_a_total_read_failure_still_returns_a_usable_market_tab(monkeypatch):
    _patch(monkeypatch, group_raises=True, watchlist_raises=True)
    resp = await up.get_updates_tabs(user=_user("pro"))
    assert _tabs(resp) == []
    assert resp.tabs[0].is_market_tab is True


@pytest.mark.asyncio
async def test_malformed_scopes_are_dropped_before_the_plan_limit_is_applied(monkeypatch):
    """Otherwise a junk symbol consumes a Free user's single chip and they see nothing."""
    _patch(monkeypatch, group=_group(["BAD SYMBOL!", "ORCL"]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert _tabs(resp) == ["ORCL"]


# ── the plan gate ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_free_sees_the_alphabetically_first_ticker_only(monkeypatch):
    """The stated product rule, end to end: BDU, DIF, BDX → BDU."""
    _patch(monkeypatch, group=_group(["BDU", "DIF", "BDX"]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert _tabs(resp) == ["BDU"]
    assert resp.locked_count == 2
    assert resp.tier_required == "pro"


@pytest.mark.asyncio
async def test_the_free_pick_does_not_depend_on_group_order(monkeypatch):
    for order in (["BDU", "DIF", "BDX"], ["BDX", "DIF", "BDU"], ["DIF", "BDU", "BDX"]):
        _patch(monkeypatch, group=_group(order))
        resp = await up.get_updates_tabs(user=_user("free"))
        assert _tabs(resp) == ["BDU"], f"order {order} changed the visible chip"


@pytest.mark.asyncio
async def test_pro_and_max_see_more(monkeypatch):
    tickers = [f"T{i:02d}" for i in range(30)]
    _patch(monkeypatch, group=_group(tickers))

    pro = await up.get_updates_tabs(user=_user("pro"))
    assert len(_tabs(pro)) == 15
    assert pro.locked_count == 15
    assert pro.tier_required == "premium"

    mx = await up.get_updates_tabs(user=_user("premium"))
    assert len(_tabs(mx)) == 30
    assert mx.locked_count == 0
    assert mx.tier_required is None


@pytest.mark.asyncio
async def test_nothing_is_locked_when_the_group_fits(monkeypatch):
    """`locked_count == 0` is what suppresses the upsell chip — a Free user with one
    ticker must not be told they are missing something."""
    _patch(monkeypatch, group=_group(["ORCL"]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert (_tabs(resp), resp.locked_count, resp.tier_required) == (["ORCL"], 0, None)


@pytest.mark.asyncio
async def test_an_unknown_tier_is_gated_as_free_not_ungated(monkeypatch):
    """Guests and every degraded identity dict carry `tier: "free"`, but a missing or
    unrecognised value must not fall open."""
    _patch(monkeypatch, group=_group(["BDU", "DIF"]))
    for tier in (None, "", "enterprise", "max"):
        resp = await up.get_updates_tabs(user={"id": _USER, "tier": tier})
        assert _tabs(resp) == ["BDU"], f"tier {tier!r} was not gated as free"

    missing = await up.get_updates_tabs(user={"id": _USER})
    assert _tabs(missing) == ["BDU"]


@pytest.mark.asyncio
async def test_a_downgrade_truncates_rather_than_erroring(monkeypatch):
    """Pro → Free with 20 tickers in the group. A 402 here would take away a screen the
    user can still legitimately use, over a state they did not create in this request."""
    _patch(monkeypatch, group=_group([f"T{i:02d}" for i in range(20)]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert len(_tabs(resp)) == 1
    assert resp.locked_count == 19


@pytest.mark.asyncio
async def test_the_gate_also_applies_on_the_watchlist_fallback(monkeypatch):
    """A user with no active group must not get an unmetered strip — that would make the
    gate skippable by simply never opening the Tracking tab."""
    _patch(monkeypatch, group=None,
           watchlist=[{"ticker": "BDU"}, {"ticker": "DIF"}, {"ticker": "BDX"}])
    resp = await up.get_updates_tabs(user=_user("free"))
    assert _tabs(resp) == ["BDU"]
    assert resp.locked_count == 2


@pytest.mark.asyncio
async def test_locked_count_counts_distinct_tickers_not_raw_rows(monkeypatch):
    """A duplicate would inflate the "+N more" number, promising chips that do not exist."""
    _patch(monkeypatch, group=_group(["BDU", "BDU", "DIF"]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert resp.locked_count == 1


@pytest.mark.asyncio
async def test_an_empty_group_locks_nothing(monkeypatch):
    _patch(monkeypatch, group=_group([], name="Tech"))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert _tabs(resp) == []
    assert (resp.locked_count, resp.tier_required) == (0, None)
    assert resp.group_name == "Tech"


# ── the assumption the whole gate rests on ───────────────────────────────────

def test_the_identity_dependency_actually_carries_a_tier():
    """`user.get("tier")` is the ONLY input to the limit, and it is read off the dict
    `get_watchlist_identity` returns. That dict is either a `public.users` row (from
    `select("*")`, and `users.tier` is NOT NULL DEFAULT 'free') or a hand-built guest dict.

    If a refactor ever drops the key — or narrows that `select("*")` to a column list that
    omits `tier` — `normalize_tier` falls back to free and EVERY PAYING USER silently drops
    to one chip. No error, no log, nothing to notice: it looks exactly like the free tier
    working correctly. This pins both halves so that refactor fails here instead."""
    import inspect

    from app import dependencies as deps

    src = inspect.getsource(deps.get_current_user_or_guest)
    assert 'table("users").select("*")' in src, (
        "get_current_user_or_guest no longer selects every users column — if `tier` is not "
        "among them, the Updates plan gate silently demotes every paying user to free"
    )
    # Every hand-built identity dict in the module states a tier explicitly.
    module_src = inspect.getsource(deps)
    assert module_src.count('"tier": "free"') >= 4, (
        "an identity dict lost its explicit tier — the gate would read None and, while that "
        "still fails CLOSED, it would do so for reasons nobody chose"
    )


def test_a_signed_in_users_row_flows_through_the_guest_wrapper_with_its_tier():
    """`get_watchlist_identity` spreads the users row (`{**user, "is_guest": False}`), so a
    Pro user must arrive at the endpoint still carrying tier="pro"."""
    import inspect

    from app import dependencies as deps

    src = inspect.getsource(deps.get_watchlist_identity)
    assert '{**user, "is_guest": False}' in src, (
        "get_watchlist_identity no longer forwards the whole users row — tier would be lost "
        "and every paying user would be gated as free"
    )


# ── quote hygiene on the gated set ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_quote_failure_renders_pills_without_a_change_label(monkeypatch):
    """A fabricated 0.0% sits inches from pills where the number means one exact thing."""
    _patch(monkeypatch, group=_group(["ORCL"]), quotes_raise=True)
    resp = await up.get_updates_tabs(user=_user("pro"))
    assert _tabs(resp) == ["ORCL"]
    assert resp.tabs[1].change_percent is None


@pytest.mark.asyncio
async def test_non_finite_change_percentages_are_nulled(monkeypatch):
    """FMP emits NaN/Infinity for thin or just-listed symbols; those serialize to invalid
    JSON under allow_nan=False and 500 the whole tab bar."""
    _patch(monkeypatch, group=_group(["NANP", "INFP"]),
           quotes=[{"symbol": "NANP", "changePercentage": float("nan")},
                   {"symbol": "INFP", "changePercentage": float("inf")}])
    resp = await up.get_updates_tabs(user=_user("pro"))
    assert all(t.change_percent is None for t in resp.tabs)


@pytest.mark.asyncio
async def test_visible_pills_carry_their_company_name(monkeypatch):
    """The bug this pins: metadata used to be resolved for the first `_MAX_TABS` of the
    group by POSITION, while the plan gate selects ALPHABETICALLY. Those are different
    sets, and every ticker in the difference rendered with no company name and no logo.

    Reproducing it needs all three conditions, which is why a small group missed it:
      * the group is LARGER than `_MAX_TABS` (otherwise the position slice covers
        everything and the two sets coincide),
      * position order disagrees with alphabetical order — here, deliberately reversed,
      * so the alphabetically-first names sit PAST the position cut.

    With 40 reverse-ordered tickers, T00–T09 live at positions 30–39 and were exactly the
    ones the old slice dropped — while being the first ones a plan shows.
    """
    reverse_ordered = [f"T{i:02d}" for i in range(39, -1, -1)]
    _patch(monkeypatch, group=_group(reverse_ordered))

    resp = await up.get_updates_tabs(user=_user("premium"))
    pills = [t for t in resp.tabs if not t.is_market_tab]

    assert [p.scope for p in pills] == [f"T{i:02d}" for i in range(30)]
    missing = [p.scope for p in pills if not p.company_name]
    assert not missing, f"visible pills lost their display name: {missing}"


@pytest.mark.asyncio
async def test_only_visible_tickers_have_metadata_resolved(monkeypatch):
    """A Free user must not cost a metadata lookup over 30 symbols to render one pill."""
    asked = []

    async def _spy(_uid, tickers):
        asked.append(list(tickers))
        return {}

    _patch(monkeypatch, group=_group([f"T{i:02d}" for i in range(30)]))
    monkeypatch.setattr(up, "fetch_ticker_metadata", _spy)
    await up.get_updates_tabs(user=_user("free"))
    assert asked == [["T00"]]


@pytest.mark.asyncio
async def test_only_visible_tickers_are_quoted(monkeypatch):
    """A Free user must not cost an FMP fan-out over 30 symbols to render one pill."""
    asked = []

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            asked.extend(symbols)
            return []

    _patch(monkeypatch, group=_group([f"T{i:02d}" for i in range(30)]))
    monkeypatch.setattr(up, "get_fmp_client", lambda: _FMP())
    await up.get_updates_tabs(user=_user("free"))
    assert len(asked) == 1
