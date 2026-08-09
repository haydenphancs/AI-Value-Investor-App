"""Tickers added to the watchlist reach the tab that renders them.

THE ORIGINAL BUG, exactly as reported: first-run onboarding captured ORCL and PLUG. They
showed up in the Updates tab's filter pills and the Tracking tab said "No tickers yet".

Why. The Assets tab is a GROUP view — `TrackingViewModel.filteredAssets` intersects the
feed with the active portfolio's tickers, and a portfolio is "a user-named subset of the
master watchlist" (portfolios.py module docstring). The design's safety net is
`_seed_default_portfolio`, which populates a default "Holdings" group FROM the watchlist on
the first `GET /portfolios`.

That seed is one-shot and it fires at the wrong moment. `ContentView` mounts every tab at
launch, so `TrackingViewModel.init` issues `GET /portfolios` seconds BEFORE onboarding writes
a single ticker. The seed reads an empty watchlist, creates the group with zero items, and
the `if not portfolios:` guard means it can never run again.

Three fixes, all pinned here:
  * DURABLE — `POST /watchlist` mirrors into the user's ACTIVE group.
  * REPAIR  — `GET /portfolios` backfills a lone empty group from a non-empty watchlist,
    so installs already stuck heal themselves.
  * SCOPE   — the mirror used to fire only for a user with EXACTLY ONE group, because the
    active selection was a device-local UserDefaults string the server never saw. Migration
    126 made it server state, and that narrow scope then became the bug: a user's second
    group was precisely the point at which adds started vanishing from Tracking again. Now
    that Home and Updates ALSO follow the active group, a skipped write-through would hide
    the ticker on all three surfaces at once.

No network / Supabase.
"""
from __future__ import annotations

import pytest

import app.api.v1.endpoints.watchlist as wl


class _Q:
    def __init__(self, store, table, log):
        self.store, self.table, self.log = store, table, log
        self._op = "select"
        self._payload = None
        self._filters = {}
        self._conflict = None
        self._limit = None
        self._in = None

    def select(self, *_a): self._op = "select"; return self
    def insert(self, p): self._op, self._payload = "insert", p; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, c, v): self._filters[c] = v; return self
    def in_(self, c, vals): self._in = (c, list(vals)); return self
    def limit(self, n): self._limit = n; return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        self._op, self._payload, self._conflict = "upsert", payload, on_conflict
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "upsert":
            p = self._payload
            # Model portfolio_items_portfolio_id_ticker_key.
            dupe = any(
                r.get("portfolio_id") == p["portfolio_id"] and r.get("ticker") == p["ticker"]
                for r in rows
            )
            if not dupe:
                rows.append(dict(p))
                self.log.append(("upsert", self.table, p["ticker"]))
            else:
                self.log.append(("upsert-noop", self.table, p["ticker"]))
            return type("R", (), {"data": []})()
        matched = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._in:
            col, vals = self._in
            matched = [r for r in matched if r.get(col) in vals]
        if self._op == "delete":
            for r in matched:
                rows.remove(r)
            self.log.append(("delete", self.table, len(matched)))
            return type("R", (), {"data": [dict(r) for r in matched]})()
        if self._limit is not None:
            matched = matched[: self._limit]
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _SB:
    """Fake Supabase. `rpc` models `ensure_active_portfolio` (migration 126).

    Modelling the RPC matters: without it `_heal_active_group` raises AttributeError,
    gets swallowed by its own except, and every "no active group" test passes for the
    WRONG reason — the heal never ran at all.
    """

    def __init__(self, store, *, rpc_enabled=True):
        self.store, self.log = store, []
        self.rpc_enabled = rpc_enabled

    def table(self, name):
        return _Q(self.store, name, self.log)

    def rpc(self, name, params):
        self.log.append(("rpc", name, params))
        if not self.rpc_enabled or name != "ensure_active_portfolio":
            raise RuntimeError(f"unexpected rpc {name}")
        user_id = params["p_user_id"]
        rows = [r for r in self.store.get("portfolios", []) if r.get("user_id") == user_id]
        active = next((r for r in rows if r.get("is_active")), None)
        if active is None and rows:
            # Same promotion rule the RPC uses: the user's first group.
            active = sorted(rows, key=lambda r: (r.get("sort_order", 0), r["id"]))[0]
            active["is_active"] = True
        data = active["id"] if active else None
        return type("E", (), {"execute": lambda _self: type("R", (), {"data": data})()})()


_USER = "guest-install-A"


def _store(portfolios, items=()):
    return {"portfolios": list(portfolios), "portfolio_items": list(items)}


def _group(pid, *, user=_USER, active=True, name="Holdings"):
    return {"id": pid, "user_id": user, "name": name, "is_active": active}


# ---------------------------------------------------------------------------
# The durable write-through
# ---------------------------------------------------------------------------


def test_a_watchlist_add_reaches_the_users_active_group():
    """THE reported symptom: onboarding adds ORCL, Assets stays empty."""
    store = _store([_group("p1")])
    sb = _SB(store)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")

    assert [r["ticker"] for r in store["portfolio_items"]] == ["ORCL"]
    assert store["portfolio_items"][0]["portfolio_id"] == "p1"


def test_positions_increment_rather_than_collide():
    store = _store([_group("p1")])
    sb = _SB(store)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")
    wl._write_through_to_active_portfolio(sb, _USER, "PLUG")

    assert [(r["ticker"], r["position"]) for r in store["portfolio_items"]] == [
        ("ORCL", 0), ("PLUG", 1)
    ]


def test_re_adding_an_existing_ticker_is_a_no_op():
    """UNIQUE(portfolio_id, ticker) — a re-add must not raise, and must not reorder."""
    store = _store([_group("p1")],
                   [{"portfolio_id": "p1", "ticker": "ORCL", "position": 0}])
    sb = _SB(store)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")

    assert len(store["portfolio_items"]) == 1
    assert store["portfolio_items"][0]["position"] == 0
    assert ("upsert-noop", "portfolio_items", "ORCL") in sb.log


def test_a_user_with_several_groups_gets_the_ACTIVE_one():
    """The behaviour that INVERTED with migration 126.

    This case used to be skipped entirely — the server could not tell which group the user
    meant, so it did nothing and the ticker was invisible on Tracking. Now the active group
    is server state, so there is no ambiguity left to be careful about, and skipping would
    hide the ticker on Home and Updates too (both follow the same group).
    """
    store = _store([
        _group("p1", active=False, name="Watchlist"),
        _group("p2", active=True, name="Tech"),
        _group("p3", active=False, name="Dividends"),
    ])
    sb = _SB(store)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")

    assert [(r["portfolio_id"], r["ticker"]) for r in store["portfolio_items"]] == [
        ("p2", "ORCL")
    ], "the ticker must land in the ACTIVE group, not the first one"


def test_a_user_with_groups_but_none_active_is_HEALED_then_mirrored():
    """The state that loses tickers permanently if this path just gives up.

    Reachable whenever `_ensure_active_portfolio` fails transiently after a delete — that
    endpoint logs a warning and still returns 200, and its `other_count == 0` guard means
    a group always survives. NO repair path writes `portfolio_items` for the ticker after
    that: `ensure_active_portfolio` only flips a boolean, `_seed_default_portfolio`
    requires zero groups, and `_backfill_lone_empty_portfolio` requires exactly one group
    holding zero items. So the row would sit in `watchlist_items` while Home, Updates AND
    Tracking all read the active group — invisible everywhere at once.

    Healing first is safe rather than a guess: the RPC promotes by
    (sort_order, created_at, id), the same group `GET /portfolios` would pick.
    """
    store = _store([_group("p1", active=False), _group("p2", active=False)])
    sb = _SB(store)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")

    assert ("rpc", "ensure_active_portfolio", {"p_user_id": _USER}) in sb.log
    assert [(r["portfolio_id"], r["ticker"]) for r in store["portfolio_items"]] == [
        ("p1", "ORCL")
    ], "the ticker must land in the promoted group, not be dropped"


def test_the_heal_failing_still_never_breaks_the_add():
    """The watchlist row is committed and IS the source of truth."""
    store = _store([_group("p1", active=False)])
    sb = _SB(store, rpc_enabled=False)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")  # must not raise

    assert store["portfolio_items"] == []


def test_a_user_with_no_portfolio_is_left_to_the_seed():
    """`GET /portfolios` seeds one from the watchlist; inventing a second here would race it."""
    store = _store([])
    sb = _SB(store)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")

    assert store["portfolio_items"] == []
    assert store["portfolios"] == []


def test_another_users_active_group_is_never_targeted():
    """The user_id filter, not just the is_active one, is what scopes this."""
    store = _store([_group("p-other", user="someone-else", active=True)])
    sb = _SB(store)

    wl._write_through_to_active_portfolio(sb, _USER, "ORCL")

    assert store["portfolio_items"] == [], "wrote into another user's group"


def test_a_mirror_failure_never_breaks_the_add():
    """The watchlist row is already committed and IS the source of truth. Raising here would
    turn a successful add into a 500."""
    class _Boom:
        def table(self, _n):
            raise RuntimeError("supabase down")

    wl._write_through_to_active_portfolio(_Boom(), _USER, "ORCL")  # must not raise


def test_the_add_endpoint_actually_calls_the_mirror():
    """A helper nobody calls is the original bug in a new place."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "app" / "api" / "v1" / "endpoints" / "watchlist.py").read_text(encoding="utf-8")
    assert "_write_through_to_active_portfolio(supabase, user_id, ticker)" in src


# ---------------------------------------------------------------------------
# The delete-through (mirror image of the add)
# ---------------------------------------------------------------------------


def test_removing_a_ticker_clears_it_from_every_group():
    """`portfolio_items.ticker` is bare TEXT with no FK to `watchlist_items`, so deleting
    the watchlist row leaves the group row behind.

    That orphan used to be invisible — Home and the Updates chips read `watchlist_items`
    directly, so a removal took effect everywhere at once. Now BOTH read `portfolio_items`
    via the active group and neither intersects back against the watchlist, so the orphan
    RENDERS: the user removes a ticker, watches it vanish from Tracking, and finds it
    still on Home and in the Updates strip with a live quote and no company name.

    Every group, not just the active one: the ticker is gone from the master watchlist, so
    it cannot legitimately survive in any subset of it.
    """
    store = _store(
        [_group("p1", active=True, name="Holdings"),
         _group("p2", active=False, name="Tech")],
        [{"portfolio_id": "p1", "ticker": "ORCL", "position": 0},
         {"portfolio_id": "p2", "ticker": "ORCL", "position": 0},
         {"portfolio_id": "p1", "ticker": "PLUG", "position": 1}],
    )
    sb = _SB(store)

    wl._delete_through_from_groups(sb, _USER, "ORCL")

    assert [(r["portfolio_id"], r["ticker"]) for r in store["portfolio_items"]] == [
        ("p1", "PLUG")
    ]


def test_delete_through_never_touches_another_users_groups():
    store = _store(
        [_group("p-mine", user=_USER), _group("p-other", user="someone-else")],
        [{"portfolio_id": "p-other", "ticker": "ORCL", "position": 0}],
    )
    sb = _SB(store)

    wl._delete_through_from_groups(sb, _USER, "ORCL")

    assert len(store["portfolio_items"]) == 1, "deleted from another user's group"


def test_delete_through_is_a_no_op_for_a_user_with_no_groups():
    store = _store([])
    sb = _SB(store)
    wl._delete_through_from_groups(sb, _USER, "ORCL")  # must not raise
    assert store["portfolio_items"] == []


def test_a_delete_through_failure_never_breaks_the_removal():
    """The watchlist row is already deleted and IS the source of truth. Raising here would
    report a removal that DID happen as an error."""
    class _Boom:
        def table(self, _n):
            raise RuntimeError("supabase down")

    wl._delete_through_from_groups(_Boom(), _USER, "ORCL")  # must not raise


def test_the_delete_endpoint_actually_calls_the_delete_through():
    """A helper nobody calls is the original bug in a new place."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "app" / "api" / "v1" / "endpoints" / "watchlist.py").read_text(encoding="utf-8")
    assert "_delete_through_from_groups(supabase, user_id, ticker)" in src


# ---------------------------------------------------------------------------
# The repair path
# ---------------------------------------------------------------------------


def test_backfill_is_wired_into_the_portfolio_list():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "app" / "api" / "v1" / "endpoints" / "portfolios.py").read_text(encoding="utf-8")
    assert "_backfill_lone_empty_portfolio(supabase, user[\"id\"], portfolios)" in src, (
        "installs already stuck with an empty portfolio never heal"
    )


def test_backfill_only_touches_a_lone_empty_portfolio():
    """Narrow on purpose: a user with several portfolios, or one deliberately emptied among
    several, must not be re-populated on every list call."""
    import app.api.v1.endpoints.portfolios as pf

    class _P:
        def __init__(self, pid, tickers):
            self.id, self.tickers = pid, tickers

    sb = _SB(_store([]))
    two = [_P("p1", []), _P("p2", ["AAPL"])]
    assert pf._backfill_lone_empty_portfolio(sb, _USER, two) is two, "touched a multi-portfolio user"

    non_empty = [_P("p1", ["AAPL"])]
    assert pf._backfill_lone_empty_portfolio(sb, _USER, non_empty) is non_empty


def test_the_claim_path_clears_is_active_when_moving_a_guest_group():
    """`idx_portfolios_one_active_per_user` is a partial UNIQUE index. A guest who used the
    Assets tab always has an active group, so re-pointing it at an account that also has one
    raises 23505 — and `_claim()` shares ONE try, so that exception silently skips Learn
    progress, research reports and chat sessions. This exact class of failure already shipped
    once on the portfolios name collision."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "app" / "api" / "v1" / "endpoints" / "users.py").read_text(encoding="utf-8")
    assert '{"user_id": user_id, "is_active": False}' in src, (
        "claiming a guest portfolio must clear is_active in the SAME update that moves it"
    )
