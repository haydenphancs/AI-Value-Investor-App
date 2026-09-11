"""Every per-caller rate limiter is ATTACHED to the route it was written for.

WHY THIS FILE EXISTS
--------------------
`ReportRateLimit`, `ChatRateLimit`, `ProfileRateLimit` and `AvatarRateLimit` appeared in
**zero** test files (`WidgetRateLimit` only in comments). Each one is a keyword default on a
handler — `_rate: None = ReportRateLimit` — so deleting that one parameter is a diff of one
line that the whole suite waves through green. And `ReportRateLimit`'s own docstring says it
is "the ONLY per-caller control on GET /stocks/{ticker}/report — it was previously completely
ungated": ~17 Gemini + ~20 FMP calls per cache miss, keyed (ticker, persona) so a ticker loop
never hits cache. That is the denial-of-wallet surface the limiter closed, and the closure
was unpinned.

`test_public_ai_endpoint_guards.py::_has_rate_limit_dependency` already had the right shape —
inspect the signature for a `Depends` whose `.dependency` is a limiter instance — but its
isinstance tuple named only `RateLimitChecker` and `IdentityRateLimitChecker`.
`WidgetRateLimitChecker` is a SEPARATE class, not a subclass (`issubclass` is False for
both), so that helper answers **False** for the widget route. This file extends the tuple to
every checker class `app/dependencies.py` defines and pins each guarded route.

Four halves, because a guard that only proves presence is half a guard:
  1. the helper itself — recognises every checker class, returns False on a bare handler;
  2. attachment — the handler exists, is mounted, and carries the NAMED limiter instance;
  3. behaviour — each checker class permits up to its cap and then REFUSES with 429, and a
     missing / blank identity falls CLOSED onto the shared bucket instead of skipping;
  4. configuration — every exported identity limiter (scanned, not listed) has a unique
     bucket that is not `guest`/`widget` (the prefixes the other two checker classes key
     on), and the report window is tighter than chat.

Pure: the checkers are driven with explicit kwargs, so no `get_current_user*` dependency runs
and nothing touches Supabase. The process-wide `rate_limiter` is cleared around every test.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import Depends, HTTPException
from fastapi.params import Depends as DependsParam

import app.dependencies as deps
from app.api.v1.endpoints import chat, ticker_report, users, widget
from app.core.security import rate_limiter
from app.dependencies import (
    GUEST_USER_ID,
    AnalyticsRateLimit,
    AvatarRateLimit,
    ChatRateLimit,
    IdentityOnlyRateLimitChecker,
    IdentityRateLimitChecker,
    ProfileRateLimit,
    RateLimitChecker,
    ReportRateLimit,
    WidgetRateLimit,
    WidgetRateLimitChecker,
)

# Every per-caller checker class in app/dependencies.py. `WidgetRateLimitChecker` is listed
# explicitly because it is NOT a subclass of the other two; `IdentityOnlyRateLimitChecker` is
# a subclass of `IdentityRateLimitChecker` and is listed anyway so the intent is legible.
_LIMITER_CLASSES = (
    RateLimitChecker,
    IdentityRateLimitChecker,
    WidgetRateLimitChecker,
    IdentityOnlyRateLimitChecker,
)

# (module, handler name, router attribute on that module, the limiter it must carry).
# The router attribute matters for the widget: `widget.py` keeps TWO routers and the
# market-mover route must live on the client one (auth.md §8a point 3).
GUARDED_ROUTES = [
    (ticker_report, "get_ticker_report", "router", ReportRateLimit),
    (ticker_report, "chat_with_ticker_report", "router", ChatRateLimit),
    (chat, "create_chat_session", "router", ChatRateLimit),
    (chat, "send_chat_message", "router", ChatRateLimit),
    (chat, "stream_chat_message", "router", ChatRateLimit),
    (users, "get_my_investor_profile", "router", ProfileRateLimit),
    (users, "update_my_investor_profile", "router", ProfileRateLimit),
    (users, "upload_my_avatar", "router", AvatarRateLimit),
    (users, "delete_my_avatar", "router", AvatarRateLimit),
    (widget, "get_market_mover", "widget_client_router", WidgetRateLimit),
]
_ROUTE_IDS = [f"{m.__name__.rsplit('.', 1)[-1]}.{h}" for m, h, _r, _l in GUARDED_ROUTES]


@pytest.fixture(autouse=True)
def _reset_limiter():
    """`rate_limiter` is one process-wide instance. Without this, a window filled by one
    test is inherited by the next, and the "permits up to the cap" assertions below would
    fail — or, worse, the "refuses" assertions would pass off someone else's exhaustion."""
    rate_limiter.clear()
    yield
    rate_limiter.clear()


def _has_rate_limit_dependency(func) -> bool:
    """True when any parameter's default is a FastAPI `Depends` wrapping one of the
    per-caller checkers. Same shape as `test_public_ai_endpoint_guards.py:38`, with the
    tuple extended to every checker class."""
    for param in inspect.signature(func).parameters.values():
        dep = getattr(param.default, "dependency", None)
        if isinstance(dep, _LIMITER_CLASSES):
            return True
    return False


def _limiter_instances_on(func):
    """Every limiter instance a handler carries, for the identity assertions."""
    return [
        getattr(p.default, "dependency", None)
        for p in inspect.signature(func).parameters.values()
        if isinstance(getattr(p.default, "dependency", None), _LIMITER_CLASSES)
    ]


# ── 1. The helper itself ─────────────────────────────────────────────────────
#
# Anti-vacuity for the attachment tests: if the helper answered True for everything, every
# route below would pass with its limiter deleted.

@pytest.mark.parametrize(
    "checker",
    [
        RateLimitChecker(1, 60),
        IdentityRateLimitChecker("t-identity", 1, 60),
        WidgetRateLimitChecker(1, 60),
        IdentityOnlyRateLimitChecker("t-identity-only", 1, 60),
    ],
    ids=lambda c: type(c).__name__,
)
def test_helper_recognises_every_checker_class(checker):
    async def handler(_rate: None = Depends(checker)) -> None:  # pragma: no cover - shape only
        return None

    assert _has_rate_limit_dependency(handler) is True


def test_helper_returns_false_for_a_bare_handler():
    """The refused case for the helper: a handler with no `Depends` at all."""

    async def handler(ticker: str) -> None:  # pragma: no cover - shape only
        return None

    assert _has_rate_limit_dependency(handler) is False
    assert _limiter_instances_on(handler) == []


def test_helper_returns_false_for_a_non_limiter_depends():
    """A `Depends` on something that is not a checker (an auth dependency, say) must not
    count — otherwise swapping `ReportRateLimit` for `Depends(get_current_user)` would still
    read as rate-limited."""

    async def _not_a_limiter() -> None:  # pragma: no cover - shape only
        return None

    async def handler(user: None = Depends(_not_a_limiter)) -> None:  # pragma: no cover
        return None

    assert _has_rate_limit_dependency(handler) is False


def test_the_two_class_tuple_would_have_missed_the_widget_route():
    """Documents WHY the tuple was extended. `WidgetRateLimitChecker` is a standalone class,
    so the older helper's `(RateLimitChecker, IdentityRateLimitChecker)` tuple does not
    match it — and would report the widget route as unguarded (or, inverted, would let it
    lose its limiter unnoticed once someone "fixed" the false negative by skipping it)."""
    assert not issubclass(WidgetRateLimitChecker, (RateLimitChecker, IdentityRateLimitChecker))
    assert isinstance(WidgetRateLimit.dependency, WidgetRateLimitChecker)


def test_the_tuple_covers_every_checker_class_dependencies_defines():
    """A fifth checker class added to `app/dependencies.py` must be added here too, or a
    route guarded by it would read as unguarded — and the natural "fix" is to drop the
    route from the list. Fail on the class instead."""
    checkers = {
        name: obj
        for name, obj in vars(deps).items()
        if inspect.isclass(obj)
        and name.endswith("RateLimitChecker")
        and obj.__module__ == deps.__name__
    }
    assert len(checkers) >= 4, f"expected the four known checker classes, found {sorted(checkers)}"
    missing = [n for n, c in checkers.items() if not issubclass(c, _LIMITER_CLASSES)]
    assert not missing, (
        f"checker class(es) {missing} are not in _LIMITER_CLASSES — a route guarded by one of "
        "them would be reported as unguarded by this file's helper"
    )


# ── 2. Attachment — the half that was missing ────────────────────────────────

@pytest.mark.parametrize("module,handler,router_attr,expected", GUARDED_ROUTES, ids=_ROUTE_IDS)
def test_guarded_handler_still_exists(module, handler, router_attr, expected):
    """Fails LOUDLY by name if a handler is renamed or removed. Without this, the tests
    below would be pointed at nothing — a renamed handler must update this list, not
    silently drop out of it."""
    assert getattr(module, handler, None) is not None, (
        f"{module.__name__}.{handler} no longer exists — rename it in GUARDED_ROUTES, or if "
        "the route was removed, delete its row here deliberately"
    )
    assert getattr(module, router_attr, None) is not None, (
        f"{module.__name__}.{router_attr} no longer exists"
    )


@pytest.mark.parametrize("module,handler,router_attr,expected", GUARDED_ROUTES, ids=_ROUTE_IDS)
def test_guarded_handler_is_rate_limited(module, handler, router_attr, expected):
    """🔴 The money assertion. Deleting `_rate: None = XRateLimit` from any of these handlers
    passed the whole suite before this file."""
    func = getattr(module, handler)
    assert _has_rate_limit_dependency(func), (
        f"{module.__name__}.{handler} lost its rate limit — the per-caller window that bounds "
        f"this route is gone and nothing else throttles it"
    )


@pytest.mark.parametrize("module,handler,router_attr,expected", GUARDED_ROUTES, ids=_ROUTE_IDS)
def test_guarded_handler_carries_its_named_limiter(module, handler, router_attr, expected):
    """Presence is not enough: the widget route must NOT carry `StandardRateLimit` (it would
    bucket every widget on Earth on one shared guest key — see `WidgetRateLimitChecker`'s
    docstring), and the chat routes must share ONE `ChatRateLimit` instance so a caller
    cannot dodge the window by alternating endpoints. Identity, not equality — the same
    object the dependencies module exports."""
    func = getattr(module, handler)
    carried = _limiter_instances_on(func)
    assert any(dep is expected.dependency for dep in carried), (
        f"{module.__name__}.{handler} carries {[type(d).__name__ for d in carried]} but not the "
        f"{type(expected.dependency).__name__} instance exported as the route's named limiter"
    )


@pytest.mark.parametrize("module,handler,router_attr,expected", GUARDED_ROUTES, ids=_ROUTE_IDS)
def test_guarded_handler_is_mounted_on_its_router(module, handler, router_attr, expected):
    """A limited handler nobody routes to is dead code, not a guard. Pins the ROUTER too:
    the widget route must sit on `widget_client_router`, the one that accepts the 90-day
    widget token; on the strict `router` it would 401 every WidgetKit wake."""
    func = getattr(module, handler)
    router = getattr(module, router_attr)
    mounted = [r for r in router.routes if getattr(r, "endpoint", None) is func]
    assert mounted, (
        f"{module.__name__}.{handler} is not mounted on {module.__name__}.{router_attr} — "
        "either the decorator was removed or the route moved routers"
    )


# ── 3. Behaviour — the limiter permits, then REFUSES ─────────────────────────
#
# Every checker is driven through its own `__call__` with explicit kwargs, so the auth
# sub-dependencies never run and nothing touches Supabase. Gutting `rate_limiter.is_allowed`
# to `return True`, or any `__call__` to `return None`, fails these.

async def _drive(call, n: int) -> list:
    """`n` calls; each entry is "ok" or the (status, Retry-After) of the refusal."""
    out = []
    for _ in range(n):
        try:
            await call()
            out.append("ok")
        except HTTPException as exc:
            out.append((exc.status_code, (exc.headers or {}).get("Retry-After")))
    return out


@pytest.mark.asyncio
async def test_identity_limiter_permits_to_the_cap_then_refuses():
    checker = IdentityRateLimitChecker("t-bucket", 2, 60)
    seen = await _drive(lambda: checker(user={"id": "u1"}, x_guest_id=None), 3)
    assert seen == ["ok", "ok", (429, "60")]


@pytest.mark.asyncio
async def test_identity_only_limiter_refuses_too():
    """The subclass OVERRIDES `__call__` (to resolve identity without a DB read), so a
    gutted override would be invisible to the base-class test above."""
    checker = IdentityOnlyRateLimitChecker("t-identity-only", 1, 60)
    seen = await _drive(lambda: checker(user={"id": "u1"}, x_guest_id=None), 2)
    assert seen == ["ok", (429, "60")]


@pytest.mark.asyncio
async def test_plain_limiter_refuses_with_no_identity_at_all():
    """`RateLimitChecker` with neither a user id nor a guest header must still bucket (on
    the shared guest key) and refuse — "no identity" is not "no limit"."""
    checker = RateLimitChecker(1, 60)
    seen = await _drive(lambda: checker(user_id=None, x_guest_id=None), 2)
    assert seen == ["ok", (429, "60")]


@pytest.mark.asyncio
async def test_widget_limiter_refuses_per_caller_not_globally():
    """Per-caller is the entire reason the class exists: `a` exhausting its window must
    not touch `b`'s. (The inverse — one shared window for every widget on Earth — is the
    `StandardRateLimit` failure its docstring describes.)"""
    checker = WidgetRateLimitChecker(1, 60)
    assert await _drive(lambda: checker(caller_id="a"), 2) == ["ok", (429, "60")]
    assert await _drive(lambda: checker(caller_id="b"), 1) == ["ok"]


@pytest.mark.asyncio
async def test_a_missing_identity_falls_closed_onto_the_shared_bucket():
    """A degraded identity dict (no `id`) with no `X-Guest-Id` must still be throttled.
    The failure this prevents: a limiter that reads "unknown caller" as "skip"."""
    checker = IdentityRateLimitChecker("t-missing", 2, 60)
    seen = await _drive(lambda: checker(user={}, x_guest_id=None), 3)
    assert seen == ["ok", "ok", (429, "60")]


@pytest.mark.asyncio
async def test_a_blank_guest_header_cannot_mint_a_fresh_window():
    """Garbage input falls CLOSED: a whitespace `X-Guest-Id` resolves to the SAME shared
    guest bucket as no header at all (`guest_user_id_for` strips and falls back), so
    exhausting one exhausts the other. A blank header is not a new identity."""
    checker = IdentityRateLimitChecker("t-blank", 1, 60)
    guest = {"id": GUEST_USER_ID}
    assert await _drive(lambda: checker(user=guest, x_guest_id=None), 1) == ["ok"]
    assert await _drive(lambda: checker(user=guest, x_guest_id="   "), 1) == [(429, "60")]


@pytest.mark.asyncio
async def test_the_production_report_limiter_refuses_on_request_n_plus_one():
    """Drives the very instance `get_ticker_report` carries, at its configured cap, so this
    fails if `ReportRateLimit` is re-pointed at a permissive checker as well as if the
    limiter core is gutted. Reads the cap off the instance, so a config change re-tunes
    the loop rather than breaking it."""
    checker = ReportRateLimit.dependency
    cap = checker.max_requests
    assert 0 < cap <= 60, f"REPORT_RATE_LIMIT_PER_MINUTE={cap} is outside any sane per-minute window"
    seen = await _drive(lambda: checker(user={"id": "acct"}, x_guest_id=None), cap + 1)
    assert seen[:cap] == ["ok"] * cap
    assert seen[cap] == (429, str(checker.window_seconds))


# ── 4. Configuration invariants the docstrings promise ───────────────────────

def _exported_identity_limiters() -> dict[str, IdentityRateLimitChecker]:
    """Every `Depends(...)` in `app/dependencies.py` that wraps an identity checker, by
    export name. Scanned, not listed: a SIXTH instance added with a copy-pasted bucket is
    exactly the regression the uniqueness test exists for, and a hand-written list would
    not know about it."""
    return {
        name: obj.dependency
        for name, obj in vars(deps).items()
        if isinstance(obj, DependsParam)
        and isinstance(obj.dependency, IdentityRateLimitChecker)
    }


# Key-space prefixes the OTHER checker classes already own. `RateLimitChecker` keys an
# unauthenticated caller as `guest:{guest_user_id_for(x_guest_id)}`, and `identity_key`
# resolves the same caller to the same uuid5 — so `IdentityRateLimitChecker("guest", …)`
# would produce byte-identical keys and share `StandardRateLimit`'s window. Likewise
# `WidgetRateLimitChecker` keys `widget:{user_id}` and `get_widget_caller` returns the
# account's user id, which is what `identity_key` returns for a signed-in account.
_RESERVED_BUCKET_PREFIXES = {"guest", "widget"}


def test_every_identity_bucket_is_unique_per_limiter_instance():
    """`AvatarRateLimit`'s docstring: "The bucket string must stay unique across limiters or
    two features share a counter." Chat's routes deliberately share ONE instance; that is
    fine — it is two INSTANCES with one bucket that silently merges two features."""
    instances = _exported_identity_limiters()
    known = {
        "ChatRateLimit": ChatRateLimit.dependency,
        "ReportRateLimit": ReportRateLimit.dependency,
        "AnalyticsRateLimit": AnalyticsRateLimit.dependency,
        "ProfileRateLimit": ProfileRateLimit.dependency,
        "AvatarRateLimit": AvatarRateLimit.dependency,
    }
    # Anti-vacuity for the scan: it must find at least the five this file imports, and find
    # the SAME objects — otherwise an empty/mis-typed scan would pass the uniqueness check
    # on nothing.
    for name, inst in known.items():
        assert instances.get(name) is inst, (
            f"scan of app.dependencies did not find {name} (found {sorted(instances)})"
        )
    buckets = {name: inst.bucket for name, inst in instances.items()}
    assert all(buckets.values()), f"a limiter has an empty bucket: {buckets}"
    assert len(set(buckets.values())) == len(buckets), f"two limiters share a bucket: {buckets}"
    reserved = {n: b for n, b in buckets.items() if b in _RESERVED_BUCKET_PREFIXES}
    assert not reserved, (
        f"{reserved} would collide with the key space RateLimitChecker / "
        "WidgetRateLimitChecker already write into"
    )


def test_the_report_window_is_tighter_than_chat():
    """`config.py` next to `REPORT_RATE_LIMIT_PER_MINUTE`: "this sits far below
    CHAT_RATE_LIMIT_PER_MINUTE" — a report is ~20x the cost of a chat turn. A settings
    edit that inverts that is a cost regression, not a tuning choice."""
    report, chat_ = ReportRateLimit.dependency, ChatRateLimit.dependency
    assert report.window_seconds == chat_.window_seconds == 60
    assert report.max_requests < chat_.max_requests, (
        f"report window {report.max_requests}/min is not tighter than chat's {chat_.max_requests}/min"
    )
