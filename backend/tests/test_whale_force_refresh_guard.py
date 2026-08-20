"""
force_refresh auth guard on GET /whales/{id}/profile.

`force_refresh` DESTRUCTIVELY deletes the durable `whale_filing_snapshots` store and
drives an unbounded FMP rebuild that is deliberately EXCLUDED from the
`_whale_profile_inflight` coalescing. Without a working guard an unauthenticated caller
could loop `?force_refresh=true` over every whale id to wipe snapshots and drain the
shared FMP quota — and, once FMP was exhausted, the degraded empty-holdings build got
written back into the 24-hour cache, poisoning the profile for PAYING users too.

⚠️ THIS FILE PREVIOUSLY PASSED WHILE THE GUARD WAS DEAD.

The old version called `get_whale_profile(user_id=None)` directly. But the route
resolves through `get_watchlist_identity`, which NEVER yields a None id: a signed-out
caller gets the shared `GUEST_USER_ID` sentinel, and one sending any `X-Guest-Id`
header gets a per-install uuid5. Both are truthy, so the real `user_id is None` guard
never fired for any request the endpoint could actually produce — while this test kept
reporting green against a value the endpoint cannot generate.

The guard now keys on `is_guest` (defaulting to True = deny), and these tests drive the
three identity shapes the dependency ACTUALLY returns.

Pure logic with a chainable fake Supabase shim — no network, no real DB. Run via
`python -m pytest` from backend/.
"""

import asyncio
import inspect
import uuid

import pytest

from app.services import whale_service as wsvc
from app.services.whale_service import WhaleService, _whale_profile_cache

# The shared signed-out sentinel and the per-install synthetic id are BOTH truthy.
# Bound to the real constants so a change over there fails here rather than silently
# re-opening the hole.
from app.dependencies import GUEST_USER_ID, guest_user_id_for


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    """Records delete() calls; every chained op returns self; execute() -> []."""

    def __init__(self, name, recorder):
        self.name = name
        self._rec = recorder

    def delete(self):
        self._rec.append((self.name, "delete"))
        return self

    def __getattr__(self, _name):
        def _chain(*args, **kwargs):
            return self
        return _chain

    def execute(self):
        return _Result([])  # whale not found -> build short-circuits to None


class _FakeSB:
    def __init__(self, recorder):
        self._rec = recorder

    def table(self, name):
        return _Table(name, self._rec)


@pytest.fixture(autouse=True)
def _clear_cache():
    _whale_profile_cache.clear()
    yield
    _whale_profile_cache.clear()


def _run(monkeypatch, *, user_id, is_guest, force_refresh=True):
    recorder: list = []
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _FakeSB(recorder))
    svc = WhaleService.__new__(WhaleService)  # skip __init__ (no FMP client needed)
    result = asyncio.run(
        svc.get_whale_profile(
            "w1", user_id=user_id, force_refresh=force_refresh, is_guest=is_guest
        )
    )
    return result, recorder


def _destructive(recorder):
    return [
        e for e in recorder
        if e in (("whale_profile_cache", "delete"), ("whale_filing_snapshots", "delete"))
    ]


# ── The three identity shapes get_watchlist_identity can actually return ──────


def test_anonymous_guest_sentinel_cannot_force_refresh(monkeypatch):
    """Signed out, no X-Guest-Id: a TRUTHY sentinel id. This is the shape the old
    `user_id is None` guard let straight through."""
    result, recorder = _run(monkeypatch, user_id=GUEST_USER_ID, is_guest=True)
    assert result is None
    assert _destructive(recorder) == [], (
        f"the shared guest sentinel must not delete, got {recorder}"
    )


def test_per_install_guest_cannot_force_refresh(monkeypatch):
    """Signed out WITH a client-chosen X-Guest-Id: a per-install uuid5 that never
    equals the sentinel — so an `== GUEST_USER_ID` check would ALSO be wrong here.
    See .claude/rules/auth.md §1a."""
    per_install = guest_user_id_for(str(uuid.uuid4()))
    assert str(per_install) != str(GUEST_USER_ID), "premise: these must differ"
    result, recorder = _run(monkeypatch, user_id=per_install, is_guest=True)
    assert result is None
    assert _destructive(recorder) == [], (
        f"a per-install guest must not delete, got {recorder}"
    )


def test_authenticated_account_may_force_refresh(monkeypatch):
    """A real account IS allowed to force a rebuild — the positive control that stops
    this file from passing by simply refusing everything."""
    result, recorder = _run(monkeypatch, user_id="u1", is_guest=False)
    assert result is None
    assert ("whale_profile_cache", "delete") in recorder
    assert ("whale_filing_snapshots", "delete") in recorder


def test_no_force_refresh_never_deletes(monkeypatch):
    """Control isolating the query parameter as the trigger."""
    _, recorder = _run(monkeypatch, user_id="u1", is_guest=False, force_refresh=False)
    assert _destructive(recorder) == []


# ── Fail-closed by construction ───────────────────────────────────────────────


def test_is_guest_defaults_to_deny():
    """An omitted `is_guest` must DISARM the destructive lever, not enable it.

    Asserted on the signature rather than on behaviour so that adding a new call site
    which forgets the argument cannot quietly re-open the hole.
    """
    for fn in (WhaleService.get_whale_profile, WhaleService._get_whale_profile_ungated):
        param = inspect.signature(fn).parameters["is_guest"]
        assert param.default is True, (
            f"{fn.__qualname__}.is_guest must default to True (deny); "
            f"got {param.default!r}"
        )


def test_omitting_is_guest_does_not_delete(monkeypatch):
    """Behavioural half of the above: the defaulted call refuses."""
    recorder: list = []
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _FakeSB(recorder))
    svc = WhaleService.__new__(WhaleService)
    asyncio.run(svc.get_whale_profile("w1", user_id="u1", force_refresh=True))
    assert _destructive(recorder) == []


def test_endpoint_passes_is_guest_through():
    """The route must forward the flag; the service default only protects forgetful
    callers, it cannot protect a caller that passes the WRONG thing.

    Source-scanned because there is no DB to drive the real dependency here. Comments
    are stripped first — the explanation next to this call names every token the
    assertion looks for, so an un-stripped scan would pass on prose alone.
    """
    import re
    from pathlib import Path

    src = Path("app/api/v1/endpoints/whales.py").read_text(encoding="utf-8")
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#.*$", "", src)

    def _paren_balanced(text: str, start: int) -> str:
        """Slice from `start` to the paren that closes the FIRST '(' after it."""
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        raise AssertionError("unbalanced parentheses from index %d" % start)

    call = _paren_balanced(src, src.index("service.get_whale_profile("))
    # Mutation-check the EXTRACTOR, not just the assertion: a truncating extractor is
    # how a guard goes vacuous without anyone noticing. `bool(user.get(...))` contains
    # nested parens, so a naive `index(")")` cut the call short and hid the argument.
    assert call.count("(") == call.count(")"), f"unbalanced extraction:\n{call}"
    assert "get_whale_profile" in call
    assert "is_guest=" in call, (
        "GET /whales/{id}/profile must forward is_guest to the service; "
        f"got:\n{call}"
    )
    assert "user_id is None" not in src, (
        "the dead `user_id is None` predicate must not come back — this dependency "
        "never yields a None id"
    )
