"""The user_id scoping on portfolio reads/writes — the only wall between two accounts' rows.

WHY THIS FILE EXISTS
--------------------
`_get_portfolio_or_404` and `_name_taken` (portfolios.py) and `_ticker_in_active_group`
(watchlist.py) had **zero direct tests**. Found by mutation: deleting the
`.eq("user_id", user_id)` line from `_get_portfolio_or_404` — so any caller who knows a
portfolio id gets that row, whoever owns it — left the suite green.

That is not a theoretical hole, for two reasons the code itself spells out:

1. `portfolios` and `portfolio_items` are guest-partitioned tables with NO foreign key to
   `public.users` (migrations 108/110/111). Postgres does not know which account a row
   belongs to; the `.eq("user_id", ...)` predicate IS the ownership model.

2. Every `/{portfolio_id}/...` route calls `_get_portfolio_or_404` once and then writes
   keyed on `portfolio_id` ALONE. `set_portfolio_tickers` runs
   `portfolio_items.delete().eq("portfolio_id", ...)`; `set_portfolio_holdings` runs
   `portfolio_items.update(...).eq("portfolio_id", ...)`. No `user_id` in sight. So the
   helper is not defence-in-depth — it is the ONLY check, and if it returns a row it does
   not own, the route wipes or rewrites another account's holdings.

`_name_taken` is the same predicate asked a different question (does this name collide
within MY groups), and `_ticker_in_active_group` is its twin on the watchlist side — the
wrong user's active group answering it 409s an add the user cannot see, or on the mirror
path writes through into a group they do not own.

The fake below holds rows for TWO users. That is the point of it: a single-row fake lets an
unscoped `.eq("id", ...)` pass by accident, because there is no other row to leak. Here an
unscoped query RETURNS the other account's row, and the tests fail on the row itself. The
recorded-predicate assertions are a second, independent tripwire on top.

Pure module: a fake `sb`, no network, no Supabase, no monkeypatching — every helper takes
its client as an argument. Nothing here memoises, so there is no cache to reset.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import portfolios as pf
from app.api.v1.endpoints import watchlist as wl

_USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _R:
    def __init__(self, data):
        self.data = data


class _Q:
    """One PostgREST query against the store. Applies `eq` / `limit` for real AND records
    the predicates, so a test can assert the user_id filter was PRESENT on the wire — not
    merely that the answer happened to come out right."""

    def __init__(self, sb: "_SB", table: str):
        self._sb, self._table = sb, table
        self._filters: list[tuple[str, object]] = []
        self._limit: int | None = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        self._sb.queries.append((self._table, list(self._filters)))
        if self._table in self._sb.raise_on:
            raise RuntimeError(f"supabase down ({self._table})")
        if self._table in self._sb.null_on:
            return _R(None)
        rows = self._sb.store.get(self._table, [])
        matched = [dict(r) for r in rows if all(r.get(c) == v for c, v in self._filters)]
        if self._limit is not None:
            matched = matched[: self._limit]
        return _R(matched)


class _SB:
    """Fake Supabase over an in-memory store.

    `raise_on` models a transport fault on that table; `null_on` models PostgREST's null
    body (`.data is None`) — the shape that becomes a TypeError the moment a helper forgets
    its `or []`."""

    def __init__(self, store=None, *, raise_on=(), null_on=()):
        self.store = store if store is not None else _two_user_store()
        self.queries: list[tuple[str, list[tuple[str, object]]]] = []
        self.raise_on = set(raise_on)
        self.null_on = set(null_on)

    def table(self, name):
        return _Q(self, name)

    def filters_for(self, table):
        """Predicates of the FIRST query issued against `table`."""
        for t, f in self.queries:
            if t == table:
                return f
        raise AssertionError(f"no query was issued against {table!r}")


def _group(pid, user, name, *, active):
    return {"id": pid, "user_id": user, "name": name, "sort_order": 0, "is_active": active}


def _two_user_store():
    """Two accounts, both with a group named "Holdings" (the seed default — the realistic
    collision), A with a second INACTIVE group, and one ticker in every group.

    Laid out so that dropping ANY one predicate returns a wrong row:
      * drop user_id in the 404 helper          → B reading p-a1 gets A's row
      * drop user_id in _name_taken             → B is told "Tech" is taken (only A has it)
      * drop user_id in the active-group check  → A is told NVDA is in their group (it is B's)
      * drop is_active in the active-group check → A is told ORCL is in their group (it is in
        their INACTIVE one)

    ⚠️ ROW ORDER IS LOAD-BEARING for the last two. `_ticker_in_active_group` asks for
    `.limit(1)` and the fake honours it, so the row a dropped predicate would wrongly admit
    must come BEFORE A's real active group — otherwise the helper still lands on the right
    row by luck and the row-level test passes vacuously (measured: with A's active group
    listed first, neither mutation changed a single answer). B's active group goes first,
    then A's inactive one, then A's active one. Do not "tidy" this into per-user order."""
    return {
        "portfolios": [
            _group("p-b1", _USER_B, "Holdings", active=True),   # wrong row if user_id dropped
            _group("p-a2", _USER_A, "Tech", active=False),      # wrong row if is_active dropped
            _group("p-a1", _USER_A, "Holdings", active=True),   # the row A should get
        ],
        "portfolio_items": [
            {"portfolio_id": "p-a1", "ticker": "AAPL", "position": 0},
            {"portfolio_id": "p-a2", "ticker": "ORCL", "position": 0},
            {"portfolio_id": "p-b1", "ticker": "NVDA", "position": 0},
        ],
    }


# ── 1. _get_portfolio_or_404 — the ownership check every /{portfolio_id} route relies on ─

def test_the_owner_gets_their_row():
    row = pf._get_portfolio_or_404(_SB(), _USER_A, "p-a1")
    assert row["id"] == "p-a1" and row["user_id"] == _USER_A


def test_another_user_gets_404_not_the_row():
    """🔴 The money assertion. The store HOLDS p-a1, so an unscoped `.eq("id", ...)` would
    return it and this test would watch A's row come back to B.

    404, not 403: "not yours" and "not there" must be indistinguishable, or the response
    confirms to a stranger that the id exists — and the detail must not carry the row."""
    with pytest.raises(HTTPException) as exc:
        pf._get_portfolio_or_404(_SB(), _USER_B, "p-a1")
    assert exc.value.status_code == 404
    assert _USER_A not in str(exc.value.detail)
    assert "Holdings" not in str(exc.value.detail)


@pytest.mark.parametrize("portfolio_id", ["p-zzz", "", "not-a-uuid", "p-a1 "])
def test_a_missing_or_garbage_id_is_404(portfolio_id):
    """`if not result.data` must be the branch — not `result.data[0]` on an empty list."""
    with pytest.raises(HTTPException) as exc:
        pf._get_portfolio_or_404(_SB(), _USER_A, portfolio_id)
    assert exc.value.status_code == 404


def test_a_missing_user_id_is_not_a_wildcard():
    """A degraded identity dict (`user.get("id")` → None) must match nothing. The fake
    answers "no row"; live PostgREST rejects the literal outright. Either way: not a row."""
    with pytest.raises(HTTPException) as exc:
        pf._get_portfolio_or_404(_SB(), None, "p-a1")
    assert exc.value.status_code == 404


def test_a_null_body_is_404_not_a_crash():
    """PostgREST can answer with a null body. `not None` → 404; indexing into it → TypeError
    → 500, and a 500 on a guard is exactly the kind that tempts a "just catch it" patch —
    which is the fail-open this file exists to prevent."""
    with pytest.raises(HTTPException) as exc:
        pf._get_portfolio_or_404(_SB(null_on={"portfolios"}), _USER_A, "p-a1")
    assert exc.value.status_code == 404


def test_a_transport_fault_propagates_rather_than_yielding_a_row():
    """The one helper in this file that must NOT degrade. Every write after it is keyed on
    `portfolio_id` alone (module docstring); a fault swallowed into `{}` or `None` lets the
    route go on to mutate holdings it never verified it owns."""
    with pytest.raises(RuntimeError):
        pf._get_portfolio_or_404(_SB(raise_on={"portfolios"}), _USER_A, "p-a1")


def test_the_lookup_is_scoped_on_the_wire_not_in_python():
    """Independent of the row-level checks above. Fetching every user's rows and filtering
    client-side would still return the right dict — and pass the two-user tests — while
    shipping another account's data across the wire. Both predicates, on the ONE query."""
    sb = _SB()
    pf._get_portfolio_or_404(sb, _USER_A, "p-a1")
    assert len(sb.queries) == 1
    table, filters = sb.queries[0]
    assert table == "portfolios"
    assert ("user_id", _USER_A) in filters
    assert ("id", "p-a1") in filters


# ── 2. _name_taken — the same predicate asked a different question ───────────

def test_a_name_the_user_already_has_is_taken():
    assert pf._name_taken(_SB(), _USER_A, "Tech") is True


@pytest.mark.parametrize("name", ["tech", "TECH", "tEcH"])
def test_the_collision_is_case_insensitive(name):
    """`portfolios_user_id_name_key` is case-SENSITIVE, so this helper is the only thing
    stopping "tech" landing next to "Tech" as two indistinguishable groups in the picker."""
    assert pf._name_taken(_SB(), _USER_A, name) is True


def test_a_name_only_another_user_holds_is_free():
    """🔴 Scoping. Only A has "Tech". Drop the user_id predicate and B is refused a name
    nobody in their account uses — and, worse, learns that some other account has it."""
    assert pf._name_taken(_SB(), _USER_B, "Tech") is False


def test_a_rename_may_keep_its_own_name():
    """Changing only the case of p-a2 ("Tech" → "TECH") collides with nothing but itself."""
    assert pf._name_taken(_SB(), _USER_A, "TECH", exclude_id="p-a2") is False


def test_exclude_id_exempts_that_one_row_only():
    """Renaming p-a1 to "Tech" still collides with p-a2. `exclude_id` means "ignore the row
    being renamed", not "ignore conflicts"."""
    assert pf._name_taken(_SB(), _USER_A, "Tech", exclude_id="p-a1") is True


def test_excluding_another_users_row_unlocks_nothing():
    """B passing A's id as exclude_id: the scan is already scoped to B, so p-a1 was never a
    candidate and B's own "Holdings" still conflicts."""
    assert pf._name_taken(_SB(), _USER_B, "Holdings", exclude_id="p-a1") is True


@pytest.mark.parametrize(
    "make_sb",
    [lambda: _SB(store={"portfolios": []}), lambda: _SB(null_on={"portfolios"})],
    ids=["empty-list", "null-body"],
)
def test_no_rows_means_no_conflict(make_sb):
    """A brand-new user (empty list) and a null body both mean "nothing to collide with";
    the second must not be a TypeError from iterating None. (Mutation-checked: dropping the
    helper's `or []` turns the null-body case into exactly that TypeError.)"""
    assert pf._name_taken(make_sb(), _USER_A, "Holdings") is False


def test_a_row_without_a_name_key_neither_crashes_nor_matches():
    """`select("id,name")` should always project it, but a projection change must degrade to
    "no match", not `KeyError: 'name'` (mutation-checked: `row["name"]` raises exactly
    that). A `None` VALUE is not a reachable shape — `portfolios.name` is `NOT NULL` in the
    schema — so it is deliberately not pinned here; the helper would `.casefold()` it."""
    sb = _SB(store={"portfolios": [{"id": "p-x", "user_id": _USER_A}]})
    assert pf._name_taken(sb, _USER_A, "Holdings") is False


def test_a_transport_fault_propagates_rather_than_reading_as_free():
    """Swallowed into False, `create_portfolio` inserts blind: an exact duplicate 500s on
    `portfolios_user_id_name_key` (nothing catches that insert) and a case-variant silently
    succeeds."""
    with pytest.raises(RuntimeError):
        pf._name_taken(_SB(raise_on={"portfolios"}), _USER_A, "Tech")


def test_the_name_scan_is_scoped_on_the_wire():
    sb = _SB()
    pf._name_taken(sb, _USER_B, "Tech")
    assert ("user_id", _USER_B) in sb.filters_for("portfolios")


# ── 3. _ticker_in_active_group — the watchlist-side twin ─────────────────────

def test_a_ticker_in_the_users_active_group_is_seen():
    assert wl._ticker_in_active_group(_SB(), _USER_A, "AAPL") is True


def test_a_ticker_only_in_an_inactive_group_is_not_seen():
    """The distinction the helper exists for: ORCL is on A's watchlist (in "Tech") but
    invisible on Home, Updates and Tracking, which all render the ACTIVE group. True here
    is the 409 the user could never clear — the bug the helper was written to fix."""
    assert wl._ticker_in_active_group(_SB(), _USER_A, "ORCL") is False


@pytest.mark.parametrize("user_id, ticker", [(_USER_A, "NVDA"), (_USER_B, "AAPL")])
def test_a_ticker_in_another_users_active_group_is_not_seen(user_id, ticker):
    """🔴 Scoping. NVDA sits in B's active group, AAPL in A's. Drop the user_id predicate
    and whichever active group is FIRST in the store answers for everyone: its owner is
    unaffected, the other user is 409'd for a ticker not in their account — or, on the
    mirror path, written through into a group they do not own. Both directions, so the
    test does not depend on which group happens to be first."""
    assert wl._ticker_in_active_group(_SB(), user_id, ticker) is False


def test_a_user_with_no_groups_is_not_seen():
    sb = _SB(store={"portfolios": [], "portfolio_items": []})
    assert wl._ticker_in_active_group(sb, _USER_A, "AAPL") is False


def test_a_user_with_groups_but_none_active_is_not_seen():
    """No heal here — that belongs to the write-through — so the honest answer is False."""
    store = _two_user_store()
    for r in store["portfolios"]:
        r["is_active"] = False
    assert wl._ticker_in_active_group(_SB(store), _USER_A, "AAPL") is False


def test_an_unknown_ticker_is_not_seen():
    assert wl._ticker_in_active_group(_SB(), _USER_A, "ZZZZ") is False


@pytest.mark.parametrize("table", ["portfolios", "portfolio_items"])
def test_a_transport_fault_on_either_read_falls_closed_to_true(table):
    """The one helper here whose CLOSED answer is True: the production docstring chooses a
    spurious 409 over a write-through against unknown state. Both reads are covered because
    the second is a separate round-trip that can fail on its own."""
    assert wl._ticker_in_active_group(_SB(raise_on={table}), _USER_A, "AAPL") is True


@pytest.mark.parametrize("table", ["portfolios", "portfolio_items"])
def test_a_null_body_on_either_read_is_not_seen(table):
    """`.data is None` is "no rows", not a fault. This pins the BEHAVIOUR (False, not the
    fail-closed True): a null body that tripped a TypeError would land in the `except` and
    surface as a spurious 409. Note the helper's `if not active` / `bool(rows)` already
    absorb `None` on their own — dropping either `or []` is an equivalent mutant here, so
    this test is about the answer, not about which line produces it."""
    assert wl._ticker_in_active_group(_SB(null_on={table}), _USER_A, "AAPL") is False


def test_both_reads_carry_the_right_predicates():
    """The group lookup is scoped by user_id AND is_active; the membership lookup is keyed on
    the group THAT lookup returned. `portfolio_items` has no user_id column, so chaining
    through the first result is the only way the second read inherits the scoping."""
    sb = _SB()
    wl._ticker_in_active_group(sb, _USER_A, "AAPL")
    groups = sb.filters_for("portfolios")
    assert ("user_id", _USER_A) in groups and ("is_active", True) in groups
    items = sb.filters_for("portfolio_items")
    assert ("portfolio_id", "p-a1") in items and ("ticker", "AAPL") in items


# ── 4. Every /{portfolio_id} route actually calls the guard ──────────────────
#
# The tests above prove the helpers are correct. This proves they are REACHED. The writes
# after the 404 check are keyed on portfolio_id alone, so a new `/{portfolio_id}/...` route
# that forgets the call is a cross-account write with no other check in its way.

def _async_defs(module):
    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}


def _is_router_decorator(dec) -> bool:
    """`@router.<verb>(...)` — the only decorator shape that makes an `async def` a route."""
    func = dec.func if isinstance(dec, ast.Call) else dec
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "router"
    )


def _route_path(dec) -> str | None:
    """The literal path of `@router.put("/x", ...)` OR `@router.put(path="/x", ...)`.
    Mutation-checked: matching only `args[0]` let a route declared with the `path=` keyword
    escape the guard entirely."""
    if not isinstance(dec, ast.Call):
        return None
    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        return dec.args[0].value
    for kw in dec.keywords:
        if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _params(node) -> set[str]:
    a = node.args
    return {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}


def _portfolio_id_routes():
    """Every router-decorated `async def` in portfolios.py that takes a portfolio id.

    Two detectors, OR-ed, so that neither a `path=` keyword nor a non-literal path (an
    f-string, a constant) can hide a route: the literal path contains '{portfolio_id}', or
    the signature declares a `portfolio_id` parameter — FastAPI cannot bind a path param the
    function does not declare, so a real `/{portfolio_id}` route always satisfies the
    second."""
    out = {}
    for name, node in _async_defs(pf).items():
        if not any(_is_router_decorator(d) for d in node.decorator_list):
            continue
        paths = [p for p in map(_route_path, node.decorator_list) if p is not None]
        if any("{portfolio_id}" in p for p in paths) or "portfolio_id" in _params(node):
            out[name] = node
    return out


def _called_names(node):
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _calls_to(node, fn_name):
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == fn_name
    ]


def _arg(call, position, keyword):
    """The AST node passed at `position` (or as `keyword=`) — None if neither is present."""
    if len(call.args) > position:
        return call.args[position]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _gating_calls_to(node, fn_name):
    """Calls to `fn_name` whose RESULT decides a branch — i.e. that sit inside the `test` of
    an `if`. Mutation-checked: `_ticker_in_active_group(...)` on its own line followed by
    `if True:` satisfied a call-exists check while gating nothing."""
    return [
        c
        for n in ast.walk(node)
        if isinstance(n, ast.If)
        for c in _calls_to(n.test, fn_name)
    ]


def _is_the_route_portfolio_id(arg) -> bool:
    return isinstance(arg, ast.Name) and arg.id == "portfolio_id"


def _is_the_callers_identity(arg) -> bool:
    """`user["id"]` (every current call site) or a bare `user_id` local — the two spellings
    a refactor could plausibly land on. A literal, another dict, or a request-body field is
    not the caller."""
    if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) and arg.value.id == "user":
        return isinstance(arg.slice, ast.Constant) and arg.slice.value == "id"
    return isinstance(arg, ast.Name) and arg.id == "user_id"


def test_the_unscoped_writers_are_among_the_guarded_routes():
    """Anti-vacuity, and the two routes this file is really about: after the 404 check their
    `portfolio_items` writes carry no user_id. If they are renamed, point this at the new
    names rather than letting the guard below find nothing to check."""
    routes = _portfolio_id_routes()
    assert routes, "no /{portfolio_id} routes found in portfolios.py — guard gone vacuous"
    assert {"set_portfolio_tickers", "set_portfolio_holdings"} <= set(routes)


def test_the_route_detector_is_not_over_matching():
    """The three routes that take no portfolio id must not be swept in — an over-broad
    detector would make the guard below fail on `create_portfolio`, and the "fix" would be
    to loosen the guard. Pins the current split explicitly."""
    routes = set(_portfolio_id_routes())
    assert routes == {
        "rename_portfolio", "delete_portfolio", "activate_portfolio",
        "set_portfolio_tickers", "set_portfolio_holdings", "get_portfolio_insights",
    }, f"the /{{portfolio_id}} route set changed: {sorted(routes)} — update this and audit the new route"
    assert not routes & {"list_portfolios", "create_portfolio", "reorder_portfolios"}


def test_every_portfolio_id_route_calls_the_ownership_guard():
    """AST-bounded to each route's own body — the brace-bounding rule from testing.md, so a
    mention in a neighbouring function or in the helper's definition cannot satisfy it."""
    offenders = [
        name
        for name, node in _portfolio_id_routes().items()
        if "_get_portfolio_or_404" not in _called_names(node)
    ]
    assert not offenders, (
        f"/{{portfolio_id}} route(s) {offenders} do not call `_get_portfolio_or_404` — "
        "every write after that check is keyed on portfolio_id alone, so this is a "
        "cross-account mutation with nothing else in its way"
    )


def test_every_portfolio_id_route_guards_its_own_id_for_the_caller():
    """The call must be on the RIGHT arguments. Mutation-checked: a route that called
    `_get_portfolio_or_404(supabase, user["id"], "<some other id>")` — or guarded the id
    from a request body instead of the path — passed the name-only guard above and then
    wrote to `portfolio_id` unchecked. At least one call in each route must pass the
    caller's identity AND the route's own `portfolio_id` parameter."""
    for name, node in _portfolio_id_routes().items():
        assert "portfolio_id" in _params(node), f"{name} takes no `portfolio_id` parameter"
        calls = _calls_to(node, "_get_portfolio_or_404")
        assert calls, f"{name} does not call _get_portfolio_or_404"
        well_formed = [
            c for c in calls
            if _is_the_callers_identity(_arg(c, 1, "user_id"))
            and _is_the_route_portfolio_id(_arg(c, 2, "portfolio_id"))
        ]
        assert well_formed, (
            f"{name} calls _get_portfolio_or_404 but never as "
            f"(supabase, user[\"id\"], portfolio_id) — got "
            f"{[ast.unparse(c) for c in calls]}"
        )


def test_the_name_writers_consult_name_taken():
    """`create_portfolio` and `rename_portfolio` are the two routes that write `name`, and
    both must ask on behalf of the CALLER. Rename must also pass `exclude_id=portfolio_id`:
    without it a case-only rename ("Tech" → "TECH") collides with the row being renamed and
    409s — `test_a_rename_may_keep_its_own_name` above is only meaningful if the route
    actually uses that parameter."""
    fns = _async_defs(pf)
    for name in ("create_portfolio", "rename_portfolio"):
        assert name in fns, f"{name} not found — point this guard at the route that replaced it"
        calls = _gating_calls_to(fns[name], "_name_taken")
        assert calls, f"{name} no longer gates on _name_taken"
        assert any(_is_the_callers_identity(_arg(c, 1, "user_id")) for c in calls), (
            f"{name} calls _name_taken but not for the caller: {[ast.unparse(c) for c in calls]}"
        )
    rename_calls = _gating_calls_to(fns["rename_portfolio"], "_name_taken")
    assert any(
        _is_the_route_portfolio_id(_arg(c, 3, "exclude_id")) for c in rename_calls
    ), f"rename_portfolio must pass exclude_id=portfolio_id: {[ast.unparse(c) for c in rename_calls]}"


def test_the_watchlist_add_route_consults_the_active_group_check():
    """A helper nobody calls is the original bug in a new place. Bounded to
    `add_to_watchlist`'s own body, and on the caller's identity — `add_to_watchlist` binds
    `user_id = user["id"]` first, so either spelling is accepted."""
    fns = _async_defs(wl)
    assert "add_to_watchlist" in fns, "add_to_watchlist not found — point this at the add route"
    calls = _gating_calls_to(fns["add_to_watchlist"], "_ticker_in_active_group")
    assert calls, "add_to_watchlist no longer gates on _ticker_in_active_group"
    assert any(_is_the_callers_identity(_arg(c, 1, "user_id")) for c in calls), (
        f"add_to_watchlist asks about someone else: {[ast.unparse(c) for c in calls]}"
    )
