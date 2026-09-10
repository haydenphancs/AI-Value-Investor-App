"""The Pro/Max gate on whale TRADE GROUPS — the one paid surface nothing was pinning.

WHY THIS FILE EXISTS
--------------------
`whale_detail_allowed` and `_trade_groups_locked` had **zero test coverage**. Found by
mutation: rewriting the gate's body to a bare `return True` — handing every Free account
full position-level detail for every whale — left the entire 10,248-test suite green.

That is not a hypothetical leak. `whales.py` says so in its own comment above the routes:

    "Both routes below took NO auth dependency at all until the tier gate landed. That made
     the profile's Recent Trades redaction bypassable by anyone who knew a whale id — the
     withheld trade groups were served in full from here."

So the gate is the FIX for a shipped paywall bypass, and the fix was unguarded. Redaction
of the profile is covered (`test_whale_entitlement.py`); this is the other door to the same
data, which is exactly the shape of the original bug — one door closed, one left open.

Pure module: a fake `sb`, no network, no Supabase.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import whales as whales_endpoint
from app.services import whale_service as wsvc
from app.services.entitlements import (
    FREE_TIER_WHALE_NAME,
    TIER_FREE,
    TIER_MAX,
    TIER_PRO,
)

_FREE_WHALE_ID = "11111111-1111-1111-1111-111111111111"
_OTHER_WHALE_ID = "22222222-2222-2222-2222-222222222222"


class _FakeSupabase:
    """Answers the one `whales` lookup `free_tier_whale_id` makes.

    `rows=None` models the cold-database case the production docstring calls out (the
    registry sync has not run), and `raises=True` the transport failure. Both must fail
    CLOSED.
    """

    def __init__(self, rows=None, raises=False):
        self._rows = rows if rows is not None else [{"id": _FREE_WHALE_ID, "name": FREE_TIER_WHALE_NAME}]
        self._raises = raises

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def ilike(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._raises:
            raise RuntimeError("supabase down")
        return type("R", (), {"data": list(self._rows)})()


@pytest.fixture(autouse=True)
def _reset_memo():
    """`free_tier_whale_id` memoizes into a module global for the process lifetime, so
    without this the first test to resolve it decides the answer for every later one —
    and the fail-closed cases below would pass vacuously off a warm cache."""
    wsvc.reset_free_whale_cache()
    yield
    wsvc.reset_free_whale_cache()


# ── 1. The gate itself ───────────────────────────────────────────────────────

@pytest.mark.parametrize("tier", [TIER_PRO, TIER_MAX])
def test_a_paid_tier_sees_any_whale(tier):
    assert wsvc.whale_detail_allowed(tier, _OTHER_WHALE_ID, _FakeSupabase()) is True


def test_free_sees_the_designated_whale():
    """The packaging argument: one investor in full, so the feature demonstrates itself
    instead of every row dead-ending in a paywall."""
    assert wsvc.whale_detail_allowed(TIER_FREE, _FREE_WHALE_ID, _FakeSupabase()) is True


def test_free_is_refused_any_other_whale():
    """🔴 The money assertion. A bare `return True` here passed the whole suite."""
    assert wsvc.whale_detail_allowed(TIER_FREE, _OTHER_WHALE_ID, _FakeSupabase()) is False


@pytest.mark.parametrize("tier", [None, "", "guest", "nonsense", "FREE_TRIAL", "max"])
def test_an_unrecognised_tier_falls_closed(tier):
    """An unknown tier must land on the PAID surface, not through it. Note "max" is not
    the Max tier — that is spelled "premium" (`TIER_MAX = "premium"`), so a plausible
    misspelling must not unlock anything."""
    assert wsvc.whale_detail_allowed(tier, _OTHER_WHALE_ID, _FakeSupabase()) is False


@pytest.mark.parametrize("sb", [_FakeSupabase(rows=[]), _FakeSupabase(raises=True)])
def test_an_unresolvable_free_whale_fails_closed(sb):
    """`free_tier_whale_id` returns None on a cold database or a transport fault. Its
    docstring requires the caller fail CLOSED — `None == whale_id` must never be the
    comparison that decides this."""
    assert wsvc.whale_detail_allowed(TIER_FREE, _OTHER_WHALE_ID, sb) is False
    assert wsvc.whale_detail_allowed(TIER_FREE, _FREE_WHALE_ID, sb) is False


def test_the_id_comparison_is_string_based_not_identity():
    """Supabase returns the uuid as `str`; a caller may hold it as something else. The
    production code stringifies both sides, and this pins that rather than the accident
    of two equal `str`s."""

    class _UUIDish:
        def __init__(self, v):
            self._v = v

        def __str__(self):
            return self._v

    assert wsvc.whale_detail_allowed(
        TIER_FREE, _UUIDish(_FREE_WHALE_ID), _FakeSupabase()
    ) is True


# ── 2. The endpoint helper both routes share ─────────────────────────────────

def test_locked_helper_returns_none_when_allowed(monkeypatch):
    """`None` is the "carry on" signal — the routes use `if (locked := ...) is not None`.

    Patched on `whales_endpoint`, not on the source module: `whales.py` binds
    `get_supabase` with a MODULE-LEVEL import, so the name is resolved once at import time
    and only the endpoint module's own binding is live at call time."""
    monkeypatch.setattr(whales_endpoint, "get_supabase", _FakeSupabase)
    assert whales_endpoint._trade_groups_locked({"tier": TIER_PRO}, _OTHER_WHALE_ID) is None


def test_locked_helper_returns_the_typed_error_for_free(monkeypatch):
    monkeypatch.setattr(whales_endpoint, "get_supabase", _FakeSupabase)
    resp = whales_endpoint._trade_groups_locked({"tier": TIER_FREE}, _OTHER_WHALE_ID)

    assert resp is not None, "a Free caller was allowed through the trade-group gate"
    body = resp.body.decode()
    assert ErrorCode.WHALE_FOLLOW_LOCKED.value in body
    # invariant #3: the iOS decoder needs these keys or it cannot render an actionable error
    assert '"error_code"' in body and '"user_message"' in body


def test_a_missing_tier_key_is_treated_as_free(monkeypatch):
    """`user.get("tier")` yields None for a degraded identity dict. That must lock."""
    monkeypatch.setattr(whales_endpoint, "get_supabase", _FakeSupabase)
    assert whales_endpoint._trade_groups_locked({}, _OTHER_WHALE_ID) is not None


# ── 3. Every trade-group route actually calls the gate ───────────────────────
#
# The tests above prove the gate is correct. This proves it is REACHED — which is the half
# that was missing when both routes shipped with no auth dependency at all. A correct gate
# nothing calls is precisely the bug this file was written for.

def _route_functions_touching_trade_groups():
    """Every `async def` in whales.py whose route path contains 'trade-groups'."""
    src = Path(inspect.getfile(whales_endpoint)).read_text()
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            # @router.get("/{whale_id}/trade-groups", ...) — first positional arg is the path
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            first = dec.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if "trade-groups" in first.value:
                    out[node.name] = node
    return out


def test_there_is_at_least_one_trade_group_route():
    """Anti-vacuity: if the routes are renamed or moved, the guard below would pass by
    finding nothing to check."""
    assert _route_functions_touching_trade_groups(), (
        "no trade-group routes found in whales.py — this guard has gone vacuous, "
        "point it at wherever those routes live now"
    )


def test_every_trade_group_route_consults_the_gate():
    """AST-bounded to each route's own body, so the check cannot be satisfied by the
    helper's definition or by a mention in a neighbouring function — the brace-bounding
    rule from .claude/rules/testing.md, expressed as a node walk rather than a grep."""
    offenders = []
    for name, node in _route_functions_touching_trade_groups().items():
        calls = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if "_trade_groups_locked" not in calls:
            offenders.append(name)

    assert not offenders, (
        f"trade-group route(s) {offenders} do not call `_trade_groups_locked` — the paid "
        "trade data is served unguarded, which is the exact bypass the gate was added to "
        "close (see the comment above the routes in whales.py)"
    )
