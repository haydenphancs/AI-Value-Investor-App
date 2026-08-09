"""The one resolver that answers "which tickers is this user looking at?".

It exists because that question used to be answered three different ways — Home read the 12
newest `watchlist_items`, Updates read the 30 newest, and only Tracking knew about groups —
so renaming a group changed one screen out of three and a ticker added while two groups
existed was invisible on the tab named "Tracking".

THE PROPERTY THAT MATTERS MOST HERE is that a failed READ and a genuinely absent group are
distinguishable. `tracking_service` once answered a failed watchlist read with an empty 200
that was byte-identical to a genuinely empty watchlist; the client read that as "the user
deleted everything" and propagated the deletion server-side, destroying every portfolio.
This module raises on the former and returns None for the latter, and the tests below hold
that line.

No network / Supabase.
"""
from __future__ import annotations

import pytest

from app.services import active_group_service as ags
from app.services.active_group_service import ActiveGroup, ActiveGroupUnavailable

_USER = "user-1"


class _Q:
    def __init__(self, store, table, raises):
        self.store, self.table, self.raises = store, table, raises
        self._filters = {}
        self._in = None
        self._limit = None

    def select(self, *_a): return self
    def eq(self, c, v): self._filters[c] = v; return self
    def in_(self, c, vals): self._in = (c, list(vals)); return self
    def order(self, *_a, **_kw): return self
    def limit(self, n): self._limit = n; return self

    def execute(self):
        if self.table in self.raises:
            raise RuntimeError(f"{self.table} down")
        rows = [
            r for r in self.store.get(self.table, [])
            if all(r.get(k) == v for k, v in self._filters.items())
        ]
        if self._in:
            col, vals = self._in
            rows = [r for r in rows if r.get(col) in vals]
        if self._limit is not None:
            rows = rows[: self._limit]
        return type("R", (), {"data": [dict(r) for r in rows]})()


def _patch(monkeypatch, store, raises=()):
    sb = type("S", (), {"table": lambda _self, n: _Q(store, n, set(raises))})()
    monkeypatch.setattr(ags, "get_supabase", lambda: sb)


def _store(groups=(), items=(), watchlist=()):
    return {
        "portfolios": list(groups),
        "portfolio_items": list(items),
        "watchlist_items": list(watchlist),
    }


def _g(pid="g1", *, name="Holdings", user=_USER, active=True):
    return {"id": pid, "name": name, "user_id": user, "is_active": active}


def _item(ticker, pid="g1", position=0):
    return {"portfolio_id": pid, "ticker": ticker, "position": position}


# ── resolving the group ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolves_the_active_group_and_its_members(monkeypatch):
    _patch(monkeypatch, _store(
        groups=[_g(name="Tech")],
        items=[_item("ORCL", position=0), _item("PLUG", position=1)],
    ))
    group = await ags.get_active_group(_USER)
    assert (group.id, group.name) == ("g1", "Tech")
    assert group.tickers == ["ORCL", "PLUG"]
    assert group.ticker_count == 2


@pytest.mark.asyncio
async def test_only_the_active_group_is_returned(monkeypatch):
    _patch(monkeypatch, _store(
        groups=[_g("g1", name="Watchlist", active=False), _g("g2", name="Tech", active=True)],
        items=[_item("AAPL", "g1"), _item("ORCL", "g2")],
    ))
    group = await ags.get_active_group(_USER)
    assert (group.name, group.tickers) == ("Tech", ["ORCL"])


@pytest.mark.asyncio
async def test_another_users_group_is_never_returned(monkeypatch):
    _patch(monkeypatch, _store(
        groups=[_g("g-other", user="someone-else")],
        items=[_item("SECRET", "g-other")],
    ))
    assert await ags.get_active_group(_USER) is None


@pytest.mark.asyncio
async def test_no_active_group_returns_none_not_an_empty_group(monkeypatch):
    """None means "fall back"; an empty group means "the user emptied this one". Home
    renders those two differently — a fallback keeps the generic title, an empty group
    keeps the group's name — so collapsing them puts the wrong label on the screen."""
    _patch(monkeypatch, _store(groups=[_g(active=False)]))
    assert await ags.get_active_group(_USER) is None


@pytest.mark.asyncio
async def test_an_active_but_empty_group_is_a_group_with_no_tickers(monkeypatch):
    _patch(monkeypatch, _store(groups=[_g(name="Tech")], items=[]))
    group = await ags.get_active_group(_USER)
    assert group is not None and group.name == "Tech" and group.tickers == []


@pytest.mark.asyncio
async def test_a_missing_user_id_returns_none_without_touching_the_database(monkeypatch):
    _patch(monkeypatch, _store(), raises={"portfolios"})
    assert await ags.get_active_group("") is None
    assert await ags.get_active_group(None) is None


# ── the read/absence distinction ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_group_read_raises_rather_than_looking_empty(monkeypatch):
    _patch(monkeypatch, _store(groups=[_g()]), raises={"portfolios"})
    with pytest.raises(ActiveGroupUnavailable):
        await ags.get_active_group(_USER)


@pytest.mark.asyncio
async def test_a_failed_membership_read_raises_too(monkeypatch):
    """Half a group is worse than no group: the surfaces would render a confident
    subset of the user's tickers under the right name, with nothing to signal it."""
    _patch(monkeypatch, _store(groups=[_g()], items=[_item("ORCL")]),
           raises={"portfolio_items"})
    with pytest.raises(ActiveGroupUnavailable):
        await ags.get_active_group(_USER)


@pytest.mark.asyncio
async def test_the_raised_error_names_the_underlying_cause(monkeypatch):
    """Diagnosed later from logs alone, with no repro — the type and message have to survive."""
    _patch(monkeypatch, _store(groups=[_g()]), raises={"portfolios"})
    with pytest.raises(ActiveGroupUnavailable) as exc:
        await ags.get_active_group(_USER)
    assert "RuntimeError" in str(exc.value)


# ── membership hygiene ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tickers_are_uppercased_and_deduped_preserving_position_order(monkeypatch):
    _patch(monkeypatch, _store(
        groups=[_g()],
        items=[_item("orcl", position=0), _item("ORCL", position=1),
               _item("plug", position=2)],
    ))
    group = await ags.get_active_group(_USER)
    assert group.tickers == ["ORCL", "PLUG"]


@pytest.mark.asyncio
async def test_blank_and_non_string_tickers_are_dropped(monkeypatch):
    """A blank symbol would become a nameless pill and an FMP quote lookup for "".”"""
    _patch(monkeypatch, _store(
        groups=[_g()],
        items=[{"portfolio_id": "g1", "ticker": "", "position": 0},
               {"portfolio_id": "g1", "ticker": "   ", "position": 1},
               {"portfolio_id": "g1", "ticker": None, "position": 2},
               {"portfolio_id": "g1", "position": 3},
               _item("ORCL", position=4)],
    ))
    group = await ags.get_active_group(_USER)
    assert group.tickers == ["ORCL"]


@pytest.mark.asyncio
async def test_a_group_name_that_is_null_becomes_an_empty_string_not_a_crash(monkeypatch):
    """`portfolios.name` is NOT NULL, so this is defence against a degraded row rather than
    a reachable state — but the callers treat "" as "use the default title", and None would
    instead put the literal "None" on the Home header."""
    _patch(monkeypatch, _store(groups=[{"id": "g1", "name": None, "user_id": _USER,
                                        "is_active": True}]))
    group = await ags.get_active_group(_USER)
    assert group.name == ""


@pytest.mark.asyncio
async def test_membership_is_bounded(monkeypatch):
    _patch(monkeypatch, _store(
        groups=[_g()],
        items=[_item(f"T{i}", position=i) for i in range(ags.MAX_GROUP_TICKERS + 50)],
    ))
    group = await ags.get_active_group(_USER)
    assert len(group.tickers) <= ags.MAX_GROUP_TICKERS


# ── metadata lookup ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metadata_is_keyed_by_uppercase_ticker(monkeypatch):
    _patch(monkeypatch, _store(watchlist=[
        {"user_id": _USER, "ticker": "ORCL", "company_name": "Oracle Corporation",
         "logo_url": "u", "asset_type": "stock"},
    ]))
    meta = await ags.fetch_ticker_metadata(_USER, ["ORCL"])
    assert meta["ORCL"]["company_name"] == "Oracle Corporation"


@pytest.mark.asyncio
async def test_metadata_never_crosses_users(monkeypatch):
    _patch(monkeypatch, _store(watchlist=[
        {"user_id": "someone-else", "ticker": "ORCL", "company_name": "Not Yours"},
    ]))
    assert await ags.fetch_ticker_metadata(_USER, ["ORCL"]) == {}


@pytest.mark.asyncio
async def test_a_metadata_failure_degrades_to_empty_rather_than_raising(monkeypatch):
    """Unlike the group read, this one is cosmetic: the callers fall back to the bare
    symbol. A missing display name must never cost the user their tab bar."""
    _patch(monkeypatch, _store(), raises={"watchlist_items"})
    assert await ags.fetch_ticker_metadata(_USER, ["ORCL"]) == {}


@pytest.mark.asyncio
async def test_metadata_short_circuits_on_empty_input(monkeypatch):
    _patch(monkeypatch, _store(), raises={"watchlist_items"})
    assert await ags.fetch_ticker_metadata(_USER, []) == {}
    assert await ags.fetch_ticker_metadata("", ["ORCL"]) == {}


@pytest.mark.asyncio
async def test_a_ticker_with_no_watchlist_row_is_simply_absent(monkeypatch):
    """`set_portfolio_tickers` enforces portfolio_items ⊆ watchlist_items, so this means a
    repair path failed. Callers render the bare symbol rather than dropping the chip."""
    _patch(monkeypatch, _store(watchlist=[{"user_id": _USER, "ticker": "ORCL"}]))
    meta = await ags.fetch_ticker_metadata(_USER, ["ORCL", "GHOST"])
    assert "ORCL" in meta and "GHOST" not in meta


# ── the frozen dataclass contract ────────────────────────────────────────────

def test_active_group_is_immutable():
    """Two surfaces share one resolver result per request; a caller slicing or re-sorting
    for its own limit must not mutate what the other one sees."""
    group = ActiveGroup(id="g1", name="Tech", tickers=["A", "B"])
    with pytest.raises(Exception):
        group.name = "Other"   # frozen dataclass
