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
from app.services.entitlements import (
    UPDATES_TICKER_LIMITS,
    select_visible_tickers,
    updates_ticker_limit,
)
from _price_fakes import PriceFromFMPFake

_USER = "user-1"


def _user(tier="free", uid=_USER):
    return {"id": uid, "email": "a@b.c", "tier": tier}


def _patch(monkeypatch, *, group=None, group_raises=False, watchlist=(),
           quotes=None, quotes_raise=False, watchlist_raises=False, etfs=()):
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

    async def _fake_etfs(tickers):
        return set(etfs) & set(tickers)

    monkeypatch.setattr(up, "get_active_group", _fake_group)
    monkeypatch.setattr(up, "fetch_ticker_metadata", _fake_meta)
    monkeypatch.setattr(up, "fetch_etf_tickers", _fake_etfs)
    monkeypatch.setattr(up, "get_supabase",
                        lambda: type("S", (), {"table": lambda _s, n: _Tbl()})())
    monkeypatch.setattr(up, "get_fmp_client", lambda: _FMP())
    monkeypatch.setattr(up, "price_source",
                        lambda owner=None: PriceFromFMPFake(_FMP()))


def _tabs(resp):
    """EVERY ticker scope the response carries, locked or not, in the GROUP's own order.

    This is the Manage-Assets sheet's source. The plan gates which feeds you can OPEN, not
    which of your own watchlist tickers you may see and remove — feeding that sheet the
    gated list showed a Free user 1 of their 20 tickers with no way to remove the other 19.
    """
    return [t.scope for t in resp.tabs if not t.is_market_tab]


def _openable(resp):
    """The scopes the chip strip renders — what this caller's plan lets them open."""
    return [t.scope for t in resp.tabs if not t.is_market_tab and not t.is_locked]


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
    assert _openable(resp) == ["BDU"]
    # The other two are still SENT, just locked — the manage sheet needs them. The wire
    # keeps the GROUP's order (portfolio_items.position); only the plan SELECTION is
    # alphabetical, so the sheet lists tickers the way the user arranged them.
    assert _tabs(resp) == ["BDU", "DIF", "BDX"]
    assert resp.locked_count == 2
    assert resp.tier_required == "pro"


@pytest.mark.asyncio
async def test_the_free_pick_does_not_depend_on_group_order(monkeypatch):
    for order in (["BDU", "DIF", "BDX"], ["BDX", "DIF", "BDU"], ["DIF", "BDU", "BDX"]):
        _patch(monkeypatch, group=_group(order))
        resp = await up.get_updates_tabs(user=_user("free"))
        assert _openable(resp) == ["BDU"], f"order {order} changed the visible chip"


@pytest.mark.asyncio
async def test_pro_and_max_see_more(monkeypatch):
    tickers = [f"T{i:02d}" for i in range(30)]
    _patch(monkeypatch, group=_group(tickers))

    pro = await up.get_updates_tabs(user=_user("pro"))
    assert len(_openable(pro)) == 15
    assert len(_tabs(pro)) == 30, "locked tickers must still be sent for the manage sheet"
    assert pro.locked_count == 15
    assert pro.tier_required == "premium"

    mx = await up.get_updates_tabs(user=_user("premium"))
    assert len(_openable(mx)) == 30
    assert mx.locked_count == 0
    assert mx.tier_required is None


@pytest.mark.asyncio
async def test_nothing_is_locked_when_the_group_fits(monkeypatch):
    """`locked_count == 0` is what suppresses the upsell chip — a Free user with one
    ticker must not be told they are missing something."""
    _patch(monkeypatch, group=_group(["ORCL"]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert (_openable(resp), resp.locked_count, resp.tier_required) == (["ORCL"], 0, None)


@pytest.mark.asyncio
async def test_an_unknown_tier_is_gated_as_free_not_ungated(monkeypatch):
    """Guests and every degraded identity dict carry `tier: "free"`, but a missing or
    unrecognised value must not fall open."""
    _patch(monkeypatch, group=_group(["BDU", "DIF"]))
    for tier in (None, "", "enterprise", "max"):
        resp = await up.get_updates_tabs(user={"id": _USER, "tier": tier})
        assert _openable(resp) == ["BDU"], f"tier {tier!r} was not gated as free"

    missing = await up.get_updates_tabs(user={"id": _USER})
    assert _openable(missing) == ["BDU"]


@pytest.mark.asyncio
async def test_a_downgrade_truncates_rather_than_erroring(monkeypatch):
    """Pro → Free with 20 tickers in the group. A 402 here would take away a screen the
    user can still legitimately use, over a state they did not create in this request."""
    _patch(monkeypatch, group=_group([f"T{i:02d}" for i in range(20)]))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert len(_openable(resp)) == 1
    assert resp.locked_count == 19


@pytest.mark.asyncio
async def test_the_gate_also_applies_on_the_watchlist_fallback(monkeypatch):
    """A user with no active group must not get an unmetered strip — that would make the
    gate skippable by simply never opening the Tracking tab."""
    _patch(monkeypatch, group=None,
           watchlist=[{"ticker": "BDU"}, {"ticker": "DIF"}, {"ticker": "BDX"}])
    resp = await up.get_updates_tabs(user=_user("free"))
    assert _openable(resp) == ["BDU"]
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
    # And the strict path, which is now the ONLY path. The account-only redesign removed the
    # four hand-built guest identity dicts this used to count `"tier": "free"` in; every
    # identity is a real `public.users` row today, so `get_current_user` is where a narrowed
    # column list would do the damage. Asserting the old count would now pass vacuously at
    # zero — the wrong half of the invariant survived the refactor.
    strict_src = inspect.getsource(deps.get_current_user)
    assert 'table("users").select("*")' in strict_src, (
        "get_current_user no longer selects every users column — if `tier` is not among "
        "them, the Updates plan gate silently demotes every paying user to free"
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


# ── sort keys: the strip orders by Tracking's "Sort By" (2026-10-27) ──────────

@pytest.mark.asyncio
async def test_pills_carry_price_and_market_cap_from_the_same_quote(monkeypatch):
    """iOS sorts the chips by Tracking's Price / Market Cap choice, so each pill carries both —
    read from the quote `change_percent` already comes from, not from a second call."""
    _patch(monkeypatch, group=_group(["AAPL", "MSFT"]),
           quotes=[{"symbol": "AAPL", "changePercentage": 1.5, "price": 190.25,
                    "marketCap": 2.9e12},
                   {"symbol": "MSFT", "changePercentage": -0.5, "price": 410.0,
                    "marketCap": 3.1e12}])
    resp = await up.get_updates_tabs(user=_user("premium"))
    by = {t.scope: t for t in resp.tabs}
    assert (by["AAPL"].price, by["AAPL"].market_cap) == (190.25, 2.9e12)
    assert (by["MSFT"].price, by["MSFT"].market_cap) == (410.0, 3.1e12)


@pytest.mark.asyncio
async def test_the_market_pill_carries_no_sort_keys(monkeypatch):
    _patch(monkeypatch, group=_group(["AAPL"]))
    resp = await up.get_updates_tabs(user=_user("pro"))
    market = resp.tabs[0]
    assert market.is_market_tab
    assert market.price is None and market.market_cap is None


@pytest.mark.asyncio
async def test_unusable_sort_keys_are_nulled_never_zeroed(monkeypatch):
    """NaN / inf 500 the strip under allow_nan=False; a missing, string, bool, zero or
    negative market cap must read as MISSING — the Tracking feed's rule — so both screens
    put that ticker in the same place."""
    _patch(monkeypatch, group=_group(["NANP", "INFP", "ZERO", "NEGC", "STRP", "BOOL", "NONE"]),
           quotes=[{"symbol": "NANP", "price": float("nan"), "marketCap": float("nan")},
                   {"symbol": "INFP", "price": float("inf"), "marketCap": float("-inf")},
                   {"symbol": "ZERO", "price": 0.0, "marketCap": 0},
                   {"symbol": "NEGC", "price": 5.0, "marketCap": -10.0},
                   {"symbol": "STRP", "price": "n/a", "marketCap": "big"},
                   {"symbol": "BOOL", "price": True, "marketCap": True},
                   {"symbol": "NONE"}])
    resp = await up.get_updates_tabs(user=_user("premium"))
    by = {t.scope: t for t in resp.tabs}
    for sym in ("NANP", "INFP", "STRP", "BOOL", "NONE"):
        assert by[sym].price is None, sym
    for sym in ("NANP", "INFP", "ZERO", "NEGC", "STRP", "BOOL", "NONE"):
        assert by[sym].market_cap is None, sym
    # A zero price is a real (if odd) number, not missing — Tracking sorts it as 0 too.
    assert by["ZERO"].price == 0.0
    assert by["NEGC"].price == 5.0
    # And the whole response still serializes strictly.
    import json
    json.dumps(resp.model_dump(), allow_nan=False)


@pytest.mark.asyncio
async def test_a_quote_failure_leaves_the_sort_keys_missing(monkeypatch):
    _patch(monkeypatch, group=_group(["ORCL"]), quotes_raise=True)
    resp = await up.get_updates_tabs(user=_user("pro"))
    assert resp.tabs[1].price is None and resp.tabs[1].market_cap is None


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

    assert len(pills) == up._MAX_TABS, "the wire stays bounded by _MAX_TABS"
    missing = [p.scope for p in pills if not p.company_name]
    assert not missing, f"pills lost their display name: {missing}"


@pytest.mark.asyncio
async def test_metadata_is_one_bounded_query_for_the_whole_strip(monkeypatch):
    """Locked tickers ship too — the manage sheet needs them — so metadata covers the whole
    bounded slice. What must hold is that it stays ONE query and never exceeds `_MAX_TABS`
    symbols however large the group, which is why sending the locked ones costs nothing."""
    asked = []

    async def _spy(_uid, tickers):
        asked.append(list(tickers))
        return {}

    _patch(monkeypatch, group=_group([f"T{i:03d}" for i in range(120)]))
    monkeypatch.setattr(up, "fetch_ticker_metadata", _spy)
    await up.get_updates_tabs(user=_user("free"))

    assert len(asked) == 1, "metadata must be a single query, never per-ticker"
    assert len(asked[0]) == up._MAX_TABS


@pytest.mark.asyncio
async def test_quotes_are_one_bounded_call_for_the_whole_strip(monkeypatch):
    """`/tabs` is one chunked batch-quote call regardless of symbol count — the reason the
    plan ladder is packaging rather than cost recovery. Bounded all the same."""
    asked = []

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            asked.append(list(symbols))
            return []

    _patch(monkeypatch, group=_group([f"T{i:03d}" for i in range(120)]))
    monkeypatch.setattr(up, "get_fmp_client", lambda: _FMP())
    monkeypatch.setattr(up, "price_source",
                        lambda owner=None: PriceFromFMPFake(_FMP()))
    await up.get_updates_tabs(user=_user("free"))

    assert len(asked) == 1
    assert len(asked[0]) == up._MAX_TABS


# ── groups larger than `_MAX_TABS`: every visible ticker ships (2026-10-31) ───────────
#
# The plan gate picks the visible tickers alphabetically over the WHOLE group, while the
# wire used to be `all_tickers[:_MAX_TABS]` — a POSITION slice. In a group past the cap,
# an unlocked ticker stored past position 30 was cut from the response (the user paid for
# a chip they never got), and which ones were cut moved every time they reordered the group.

# 35 tickers stored in REVERSE alphabetical order, so the alphabetically-first ones — the
# ones every plan unlocks first — sit at positions 30-34, past the old cut.
_BIG_GROUP = [f"T{i:02d}" for i in range(34, -1, -1)]
_TIERS = [("free", 1), ("pro", 15), ("premium", 30)]


def _expected_wire(group, limit):
    """The rule, restated independently: the visible set, then locked tickers in group
    order up to `_MAX_TABS`, all emitted in group order."""
    visible = set(select_visible_tickers(group, limit))
    locked_fill = [t for t in group if t not in visible][: up._MAX_TABS - len(visible)]
    keep = visible | set(locked_fill)
    return [t for t in group if t in keep]


def test_no_plan_unlocks_more_tickers_than_the_wire_can_carry():
    """The guarantee "every visible ticker is emitted" rests on this: a tier whose limit
    exceeded `_MAX_TABS` would unlock chips the response has no room for."""
    assert max(UPDATES_TICKER_LIMITS.values()) <= up._MAX_TABS


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,limit", _TIERS)
async def test_every_visible_ticker_ships_even_when_stored_past_the_cap(monkeypatch, tier, limit):
    _patch(monkeypatch, group=_group(_BIG_GROUP))
    resp = await up.get_updates_tabs(user=_user(tier))

    visible = select_visible_tickers(_BIG_GROUP, limit)
    assert updates_ticker_limit(tier) == limit
    assert sorted(_openable(resp)) == visible, "a ticker the plan unlocks was cut from the wire"
    assert "T00" in _openable(resp), "T00 sorts first and is stored LAST (position 34)"
    assert len(_tabs(resp)) == up._MAX_TABS
    assert _tabs(resp) == _expected_wire(_BIG_GROUP, limit)
    # `is_locked` keeps its meaning: locked exactly when the plan hides the ticker's feed.
    for tab in resp.tabs[1:]:
        assert tab.is_locked is (tab.scope not in visible), tab.scope
    # The upsell counts the WHOLE group, not just what fit on the wire.
    assert resp.locked_count == len(_BIG_GROUP) - limit


@pytest.mark.asyncio
async def test_the_locked_filler_follows_group_order(monkeypatch):
    """Free: T00 claims its slot, then the 29 locked tickers stored FIRST fill the rest —
    T34..T06. T05..T01 (locked, stored last) are the ones left off."""
    _patch(monkeypatch, group=_group(_BIG_GROUP))
    resp = await up.get_updates_tabs(user=_user("free"))
    assert _tabs(resp) == [f"T{i:02d}" for i in range(34, 5, -1)] + ["T00"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,limit", _TIERS)
async def test_reordering_the_group_never_changes_the_visible_set(monkeypatch, tier, limit):
    """Manage Tickers lets the user drag the Custom order freely. The plan's pick is
    alphabetical, so no reorder may add, drop or relock a visible chip — and the wire
    keeps following the NEW order, which iOS reads as the Custom position."""
    import random

    visible = select_visible_tickers(_BIG_GROUP, limit)
    rng = random.Random(20261031)
    orders = [list(_BIG_GROUP), sorted(_BIG_GROUP)]
    for _ in range(6):
        order = list(_BIG_GROUP)
        rng.shuffle(order)
        orders.append(order)

    for order in orders:
        _patch(monkeypatch, group=_group(order))
        resp = await up.get_updates_tabs(user=_user(tier))
        assert sorted(_openable(resp)) == visible, f"order {order} changed the visible chips"
        assert _tabs(resp) == _expected_wire(order, limit), f"order {order}"
        assert resp.locked_count == len(order) - limit


@pytest.mark.asyncio
async def test_metadata_and_quotes_cover_the_visible_tickers_past_the_cap(monkeypatch):
    """Both reads ask about the EMITTED set — still one call each, still `_MAX_TABS` at
    most — so a visible ticker stored past position 30 gets its name and its quote."""
    meta_asked, quote_asked = [], []

    async def _spy_meta(_uid, tickers):
        meta_asked.append(list(tickers))
        return {t: {"company_name": f"{t} Inc."} for t in tickers}

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            quote_asked.append(list(symbols))
            return [{"symbol": s, "changePercentage": 1.0} for s in symbols]

    _patch(monkeypatch, group=_group(_BIG_GROUP))
    monkeypatch.setattr(up, "fetch_ticker_metadata", _spy_meta)
    monkeypatch.setattr(up, "get_fmp_client", lambda: _FMP())
    monkeypatch.setattr(up, "price_source",
                        lambda owner=None: PriceFromFMPFake(_FMP()))
    resp = await up.get_updates_tabs(user=_user("premium"))

    assert len(meta_asked) == 1 and len(quote_asked) == 1
    assert meta_asked[0] == quote_asked[0] == _tabs(resp)
    assert len(meta_asked[0]) == up._MAX_TABS
    visible = select_visible_tickers(_BIG_GROUP, 30)
    assert set(visible) <= set(meta_asked[0]), "a visible ticker was never read"
    assert len(_openable(resp)) == len(visible)
    for tab in resp.tabs[1:]:
        if not tab.is_locked:
            assert tab.company_name == f"{tab.scope} Inc.", tab.scope
            assert tab.change_percent == 1.0, tab.scope


# The helper on its own — the degenerate inputs the endpoint never builds today.

def test_emitted_tickers_is_the_identity_when_the_group_fits():
    group = ["ORCL", "AAPL", "PLUG"]
    assert up._emitted_tickers(group, ["AAPL"], up._MAX_TABS) == group


@pytest.mark.parametrize("cap", [0, -1])
def test_emitted_tickers_with_no_room_sends_nothing(cap):
    assert up._emitted_tickers(["AAPL", "ORCL"], ["AAPL"], cap) == []


def test_emitted_tickers_handles_an_empty_group_and_no_visible_tickers():
    assert up._emitted_tickers([], [], up._MAX_TABS) == []
    assert up._emitted_tickers(["B", "A", "C"], [], 2) == ["B", "A"]


def test_emitted_tickers_ignores_a_visible_ticker_not_in_the_group():
    """A visible name the group does not hold must not take a slot from a real ticker."""
    assert up._emitted_tickers(["B", "A", "C"], ["ZZZ", "C"], 2) == ["B", "C"]


def test_emitted_tickers_counts_a_duplicated_visible_ticker_once():
    assert up._emitted_tickers(["B", "A", "C"], ["C", "C"], 2) == ["B", "C"]


def test_emitted_tickers_keeps_the_alphabetical_head_if_a_plan_outgrew_the_cap():
    """Not reachable today (pinned above), but must still stay bounded: the gate's own
    alphabetical order decides which visible tickers fit, and no locked one does."""
    group = ["D", "C", "B", "A"]
    visible = select_visible_tickers(group, 4)
    assert up._emitted_tickers(group, visible, 2) == ["B", "A"]



# ── the ETF class for "Ask Cay AI" (2026-09-27 deep-check) ─────────────────────────

@pytest.mark.asyncio
async def test_a_legacy_stock_row_that_is_an_etf_is_declared_an_etf(monkeypatch):
    """ETFs watched before 2026-09-11 are stored 'Stock', so the chat reference had no |ETF
    and Cay AI answered about SPY as an operating company."""
    _patch(monkeypatch, group=_group(["SPY", "ORCL", "BTCUSD"]), etfs={"SPY"})
    resp = await up.get_updates_tabs(user=_user("max"))
    classes = {t.scope: t.asset_type for t in resp.tabs if not t.is_market_tab}
    assert classes == {"SPY": "etf", "ORCL": "stock", "BTCUSD": "crypto"}


@pytest.mark.asyncio
async def test_the_etf_lookup_only_asks_about_stock_like_pills(monkeypatch):
    asked = []
    _patch(monkeypatch, group=_group(["QQQ", "ETHUSD"]))

    async def _spy(tickers):
        asked.extend(tickers)
        return set()

    monkeypatch.setattr(up, "fetch_etf_tickers", _spy)
    await up.get_updates_tabs(user=_user("max"))
    assert asked == ["QQQ"], "a coin is never looked up"


@pytest.mark.asyncio
async def test_fetch_etf_tickers_reads_only_a_definite_isetf(monkeypatch):
    import app.services.active_group_service as ags

    rows = [
        {"ticker": "spy", "profile_json": {"isEtf": True}},
        {"ticker": "PDI", "profile_json": {"isEtf": False, "isFund": True}},   # a CEF: not an ETF
        {"ticker": "ORCL", "profile_json": {"description": "formatted, no flags"}},
        {"ticker": "BAD", "profile_json": "not a dict"},
    ]

    class _Q:
        def __init__(self):
            self.args = None

        def table(self, name):
            assert name == "company_profile_cache"
            return self

        def select(self, *_a):
            return self

        def in_(self, col, vals):
            self.args = (col, list(vals))
            return self

        def execute(self):
            return type("R", (), {"data": rows})()

    q = _Q()
    monkeypatch.setattr(ags, "get_supabase", lambda: q)
    assert await ags.fetch_etf_tickers(["spy", "SPY", "PDI", "ORCL", "BAD", ""]) == {"SPY"}
    assert q.args == ("ticker", ["SPY", "PDI", "ORCL", "BAD"])

    def _boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(ags, "get_supabase", _boom)
    assert await ags.fetch_etf_tickers(["SPY"]) == set(), "a failed lookup keeps the stored class"


@pytest.mark.parametrize("raw,expected", [
    ({"isEtf": True, "isFund": False}, {"isEtf": True, "isFund": False}),
    ({"isEtf": False}, {"isEtf": False}),
    ({}, {}),                                   # a failed profile fetch: write nothing
    ({"isEtf": None, "isFund": "yes"}, {}),     # never a non-bool
    (None, {}), ([], {}),
])
def test_the_formatted_profile_write_keeps_the_fund_flags(raw, expected):
    """stock_overview's formatted write replaced company_profile_cache WHOLE without the
    flags, so any detail view of SPY undid the Updates tab's ETF upgrade for a week."""
    from app.services.stock_overview_service import fund_flags

    assert fund_flags(raw) == expected


def test_the_overview_write_merges_the_fund_flags():
    import inspect

    import app.services.stock_overview_service as sos

    src = inspect.getsource(sos.StockOverviewService)
    write = src[src.index("self._upsert_company_profile_db,"):]
    write = write[:write.index("        )\n")]
    assert '**fund_flags(fundamentals.get("profile")),' in write


# ── the three reads overlap (2026-10-08 first-paint pass) ────────────────────────
#
# Metadata, quotes and the ETF lookup each need only the ticker list, so they run in ONE
# gather. The ETF lookup is asked about a shape-based SUPERSET of the final stock set and its
# answer is intersected with that set, so a stored non-stock class is never relabelled.

def _overlap_probe():
    state = {"live": 0, "max": 0}

    async def _enter():
        state["live"] += 1
        state["max"] = max(state["max"], state["live"])
        await asyncio.sleep(0.02)
        state["live"] -= 1

    return state, _enter


@pytest.mark.asyncio
async def test_metadata_quotes_and_the_etf_lookup_run_together(monkeypatch):
    """Serial awaits peak at one live read; the gather peaks at three."""
    state, _enter = _overlap_probe()

    async def _meta(_uid, tickers):
        await _enter()
        return {t: {"company_name": f"{t} Inc."} for t in tickers}

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            await _enter()
            return [{"symbol": s, "changePercentage": 1.0} for s in symbols]

    async def _etfs(tickers):
        await _enter()
        return set()

    _patch(monkeypatch, group=_group(["ORCL", "SPY"]))
    monkeypatch.setattr(up, "fetch_ticker_metadata", _meta)
    monkeypatch.setattr(up, "fetch_etf_tickers", _etfs)
    monkeypatch.setattr(up, "price_source", lambda owner=None: PriceFromFMPFake(_FMP()))

    resp = await up.get_updates_tabs(user=_user("pro"))

    assert state["max"] == 3, f"the three reads did not overlap (peak {state['max']})"
    assert [t.company_name for t in resp.tabs[1:]] == ["ORCL Inc.", "SPY Inc."]
    assert all(t.change_percent == 1.0 for t in resp.tabs[1:])


@pytest.mark.asyncio
async def test_a_superset_etf_answer_never_relabels_a_stored_non_stock_class(monkeypatch):
    """XYZ is stock-SHAPED, so the overlapped lookup asks about it before its stored class is
    known. The profile cache says ETF; the stored 'index' must still win."""
    asked = []

    async def _meta(_uid, tickers):
        return {
            "SPY": {"company_name": "SPDR S&P 500", "asset_type": "Stock"},   # legacy default
            "XYZ": {"company_name": "Some index", "asset_type": "index"},
        }

    async def _etfs(tickers):
        asked.extend(tickers)
        return {"SPY", "XYZ"} & set(tickers)

    _patch(monkeypatch, group=_group(["SPY", "XYZ", "ORCL"]))
    monkeypatch.setattr(up, "fetch_ticker_metadata", _meta)
    monkeypatch.setattr(up, "fetch_etf_tickers", _etfs)

    resp = await up.get_updates_tabs(user=_user("max"))
    classes = {t.scope: t.asset_type for t in resp.tabs if not t.is_market_tab}

    assert "XYZ" in asked, "anti-vacuity: the superset really did ask about XYZ"
    assert classes == {"SPY": "etf", "XYZ": "index", "ORCL": "stock"}


@pytest.mark.asyncio
async def test_the_watchlist_fallback_asks_only_about_stored_stock_rows(monkeypatch):
    """On the fallback path the stored classes are known before the gather: a trusted
    'crypto' row is never looked up, even though the bare symbol is stock-shaped."""
    asked = []

    async def _etfs(tickers):
        asked.extend(tickers)
        return set()

    _patch(monkeypatch, group=None,
           watchlist=[{"ticker": "SPY", "asset_type": "Stock"},
                      {"ticker": "BTC", "asset_type": "crypto"}])
    monkeypatch.setattr(up, "fetch_etf_tickers", _etfs)

    resp = await up.get_updates_tabs(user=_user("max"))

    assert asked == ["SPY"]
    classes = {t.scope: t.asset_type for t in resp.tabs if not t.is_market_tab}
    assert classes == {"SPY": "stock", "BTC": "crypto"}


@pytest.mark.asyncio
async def test_a_raising_metadata_leg_still_renders_every_pill(monkeypatch, caplog):
    """`fetch_ticker_metadata` never raises today; if it ever does, the pills still render as
    bare symbols (as on a failed read) with their change %, and the leg is named in the log."""
    async def _boom(_uid, tickers):
        raise RuntimeError("metadata exploded")

    _patch(monkeypatch, group=_group(["ORCL", "PLUG"]))
    monkeypatch.setattr(up, "fetch_ticker_metadata", _boom)

    with caplog.at_level("WARNING", logger=up.logger.name):
        resp = await up.get_updates_tabs(user=_user("pro"))

    pills = [t for t in resp.tabs if not t.is_market_tab]
    assert [p.scope for p in pills] == ["ORCL", "PLUG"]
    assert all(p.company_name is None for p in pills)
    assert all(p.change_percent == 1.0 for p in pills)
    assert "metadata leg raised" in caplog.text and _USER in caplog.text


@pytest.mark.asyncio
async def test_a_raising_etf_leg_keeps_the_stored_classes(monkeypatch, caplog):
    async def _boom(tickers):
        raise RuntimeError("profile cache exploded")

    async def _meta(_uid, tickers):
        return {"GLD": {"asset_type": "etf"}, "SPY": {"asset_type": "Stock"}}

    _patch(monkeypatch, group=_group(["SPY", "GLD", "ETHUSD"]))
    monkeypatch.setattr(up, "fetch_ticker_metadata", _meta)
    monkeypatch.setattr(up, "fetch_etf_tickers", _boom)

    with caplog.at_level("WARNING", logger=up.logger.name):
        resp = await up.get_updates_tabs(user=_user("max"))

    classes = {t.scope: t.asset_type for t in resp.tabs if not t.is_market_tab}
    assert classes == {"SPY": "stock", "GLD": "etf", "ETHUSD": "crypto"}
    assert all(t.change_percent == 1.0 for t in resp.tabs[1:])
    assert "ETF lookup leg raised" in caplog.text


@pytest.mark.asyncio
async def test_a_cancelled_leg_is_re_raised_never_degraded(monkeypatch):
    """A leg that ends cancelled comes back from the gather as a CancelledError VALUE; turning
    it into "no ETFs" would answer a request that is being torn down."""
    async def _cancelled(tickers):
        raise asyncio.CancelledError()

    _patch(monkeypatch, group=_group(["SPY"]))
    monkeypatch.setattr(up, "fetch_etf_tickers", _cancelled)

    with pytest.raises(asyncio.CancelledError):
        await up.get_updates_tabs(user=_user("max"))


@pytest.mark.asyncio
async def test_no_stock_like_pill_means_no_etf_lookup_at_all(monkeypatch):
    called = []

    async def _etfs(tickers):
        called.append(list(tickers))
        return set()

    _patch(monkeypatch, group=_group(["BTCUSD", "^GSPC"]))
    monkeypatch.setattr(up, "fetch_etf_tickers", _etfs)
    resp = await up.get_updates_tabs(user=_user("max"))
    assert called == []
    assert {t.scope: t.asset_type for t in resp.tabs[1:]} == {"BTCUSD": "crypto",
                                                              "^GSPC": "index"}
