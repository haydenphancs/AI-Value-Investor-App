"""The one unauthenticated route in the app must not be an unbounded CPU faucet.

`POST /billing/app-store-notifications` takes no credential (Apple calls it) and the work
behind it is JWS certificate-chain verification — the most CPU-expensive operation in the
process — on a single uvicorn worker. It had no rate limiter and no body cap of its own
(found 2026-09-12).

A ceiling is safe precisely because Apple retries a non-2xx over DAYS: a 429 defers a
genuine notification, it never loses one.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import app.api.v1.endpoints.billing as billing


@pytest.fixture(autouse=True)
def _clear():
    billing._webhook_hits.clear()
    billing._webhook_global.clear()
    yield
    billing._webhook_hits.clear()
    billing._webhook_global.clear()


def _req(ip="203.0.113.9"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def test_traffic_under_the_ceiling_passes():
    for _ in range(billing._WEBHOOK_MAX_PER_MINUTE):
        assert billing._webhook_rate_limited(_req()) is False


def test_the_ceiling_bites_and_is_per_ip():
    for _ in range(billing._WEBHOOK_MAX_PER_MINUTE):
        billing._webhook_rate_limited(_req("1.1.1.1"))
    assert billing._webhook_rate_limited(_req("1.1.1.1")) is True
    assert billing._webhook_rate_limited(_req("2.2.2.2")) is False, \
        "one noisy caller must not lock Apple out"


def test_the_window_rolls(monkeypatch):
    for _ in range(billing._WEBHOOK_MAX_PER_MINUTE):
        billing._webhook_rate_limited(_req())
    assert billing._webhook_rate_limited(_req()) is True
    base = time.monotonic()
    monkeypatch.setattr(billing.time, "monotonic", lambda: base + 61)
    assert billing._webhook_rate_limited(_req()) is False, "a minute later must be allowed"


@pytest.mark.parametrize("req", [
    SimpleNamespace(client=None),
    SimpleNamespace(),                                  # no `.client` attribute at all
    SimpleNamespace(client=SimpleNamespace()),          # client without `.host`
])
def test_a_missing_client_does_not_raise(req):
    """A limiter that can raise turns a flood into an outage — and Apple retries a 500
    for days."""
    assert billing._webhook_rate_limited(req) is False


def _fill_keyspace(n, monkeypatch):
    """Mint `n` distinct per-IP buckets with the GLOBAL ceiling lifted.

    The global ceiling would otherwise short-circuit at 600 and the per-IP map would never
    grow — which is correct in production but makes the prune path untestable.
    """
    monkeypatch.setattr(billing, "_WEBHOOK_MAX_PER_MINUTE_GLOBAL", 10 ** 9)
    for i in range(n):
        billing._webhook_rate_limited(_req(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}"))


def test_the_bucket_dict_is_bounded(monkeypatch):
    """The asserted bound must be the CODE's, not the number of keys inserted.

    This test read `<= 10_600` after inserting 10,600 keys — an assertion that could not
    fail whatever the production code did, so deleting the prune block entirely left it
    green. The bound below is `_WEBHOOK_MAX_KEYS` plus one prune batch's worth of headroom,
    which is what the implementation actually promises.
    """
    _fill_keyspace(billing._WEBHOOK_MAX_KEYS + 600, monkeypatch)
    assert len(billing._webhook_hits) <= billing._WEBHOOK_MAX_KEYS + billing._WEBHOOK_PRUNE_BATCH, \
        "the keyspace must not grow without bound"


def test_the_ceiling_still_bites_after_the_keyspace_is_pruned(monkeypatch):
    """THE REGRESSION. The prune used to run AFTER `_webhook_hits[ip]` had minted this
    caller's bucket — and it selected keys "whose list is empty", so it popped the very key
    just created. `hits.append(now)` then appended to a list no longer in the dict, every
    bucket was orphaned on arrival, and past 10,000 keys the limiter never bit again for
    anyone, for the life of the process. Measured before the fix: 2,000 requests from ONE
    ip after the fill → 0 blocked.
    """
    _fill_keyspace(billing._WEBHOOK_MAX_KEYS + 600, monkeypatch)
    attacker = "203.0.113.77"
    allowed = sum(
        0 if billing._webhook_rate_limited(_req(attacker)) else 1
        for _ in range(billing._WEBHOOK_MAX_PER_MINUTE * 3)
    )
    assert allowed <= billing._WEBHOOK_MAX_PER_MINUTE, (
        f"{allowed} requests passed the per-IP ceiling of "
        f"{billing._WEBHOOK_MAX_PER_MINUTE} — the limiter is disarmed"
    )
    assert attacker in billing._webhook_hits, \
        "the caller's own bucket must survive the prune, or its hits are never counted"


def test_a_saturated_route_still_accepts_a_QUIET_caller_like_apple():
    """THE PROPERTY THAT PROTECTS SUBSCRIPTION STATE.

    The global ceiling used to be keyless, so Apple shared one bucket with everyone: five
    noisy sources at the per-IP limit saturated it and every genuine notification was then
    429'd. Apple retries a non-2xx about five times over ~3 days and nothing here monitors
    that budget — `expire_stale_subscriptions` self-heals a lost EXPIRED, but NOTHING
    recovers a lost REFUND, and a lost REFUND is purchased credits never clawed back.
    """
    # Saturate the route with noisy callers (each at its own per-IP ceiling).
    noisy = billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL // billing._WEBHOOK_MAX_PER_MINUTE + 1
    for n in range(noisy):
        for _ in range(billing._WEBHOOK_MAX_PER_MINUTE):
            billing._webhook_rate_limited(_req(f"10.9.0.{n}"))
    assert len(billing._webhook_global) >= billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL, \
        "the fixture did not actually saturate the global bucket"

    # Apple now arrives, at its real rate.
    apple = "17.0.0.1"
    blocked = sum(
        1 for _ in range(billing._WEBHOOK_QUIET_CALLER_PER_MINUTE)
        if billing._webhook_rate_limited(_req(apple))
    )
    assert blocked == 0, (
        f"{blocked} of Apple's notifications were 429'd because OTHER callers had filled "
        "a shared bucket — a dropped REFUND is money that is never clawed back"
    )


def test_a_saturated_route_still_rejects_a_NOISY_caller():
    """Control. The exemption must not disarm the ceiling for the traffic it exists to
    shed — otherwise the quiet-caller carve-out is just 'no global ceiling'."""
    noisy = billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL // billing._WEBHOOK_MAX_PER_MINUTE + 1
    for n in range(noisy):
        for _ in range(billing._WEBHOOK_MAX_PER_MINUTE):
            billing._webhook_rate_limited(_req(f"10.9.0.{n}"))

    attacker = "203.0.113.50"
    for _ in range(billing._WEBHOOK_QUIET_CALLER_PER_MINUTE):
        billing._webhook_rate_limited(_req(attacker))
    assert billing._webhook_rate_limited(_req(attacker)) is True, (
        "a caller above the quiet threshold is exempt from the global ceiling too — the "
        "ceiling now sheds nothing"
    )


def test_a_DISTRIBUTED_flood_of_quiet_callers_is_still_bounded():
    """The hard backstop. A thousand sources at nine a minute are all individually
    'quiet', so the exemption alone would leave the route unbounded."""
    allowed = 0
    for i in range(billing._WEBHOOK_HARD_GLOBAL_PER_MINUTE * 2):
        # A fresh source every few requests, each staying under the quiet threshold.
        ip = f"10.{i // 65536}.{(i // 250) % 256}.{i % 250}"
        if not billing._webhook_rate_limited(_req(ip)):
            allowed += 1
    assert allowed <= billing._WEBHOOK_HARD_GLOBAL_PER_MINUTE, (
        f"{allowed} requests got through a distributed flood of individually-quiet "
        "callers — the hard backstop is not bounding anything"
    )


def test_the_global_window_rolls(monkeypatch):
    """A saturated minute must not become a permanently closed route."""
    noisy = billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL // billing._WEBHOOK_MAX_PER_MINUTE + 1
    for n in range(noisy):
        for _ in range(billing._WEBHOOK_MAX_PER_MINUTE):
            billing._webhook_rate_limited(_req(f"10.9.0.{n}"))
    attacker = "203.0.113.50"
    for _ in range(billing._WEBHOOK_QUIET_CALLER_PER_MINUTE + 1):
        billing._webhook_rate_limited(_req(attacker))
    assert billing._webhook_rate_limited(_req(attacker)) is True

    base = time.monotonic()
    monkeypatch.setattr(billing.time, "monotonic", lambda: base + 61)
    assert billing._webhook_rate_limited(_req(attacker)) is False, \
        "a minute later the route must accept everyone again"


def test_the_ceilings_are_ordered_so_the_quiet_exemption_is_reachable():
    """Replaces two constant-vs-constant assertions that observed nothing about Apple.

    The old pair read `600 >= 10 * 120 / 2` (i.e. `600 >= 600`, true only by exact
    equality) and `120 >= 60` — neither encoded any fact about Apple's notification rate
    while both were NAMED as if they did. `.claude/rules/testing.md` §3: a vacuous guard is
    worse than no guard. What actually has to hold is the ORDERING that makes the
    quiet-caller carve-out meaningful.
    """
    assert (
        billing._WEBHOOK_QUIET_CALLER_PER_MINUTE
        < billing._WEBHOOK_MAX_PER_MINUTE
        <= billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL
        < billing._WEBHOOK_HARD_GLOBAL_PER_MINUTE
    ), (
        "the four ceilings are out of order — the quiet exemption is either unreachable "
        "or swallows the per-IP ceiling"
    )
    # A caller at Apple's real rate must be BELOW the quiet threshold with room to spare.
    assert billing._WEBHOOK_QUIET_CALLER_PER_MINUTE >= 5, (
        "the quiet threshold is so low that an ordinary Apple burst counts as noisy"
    )


def test_the_ceiling_is_checked_before_the_body_is_parsed():
    """A flood must cost a dict lookup, not a JSON parse."""
    import ast
    import inspect
    import re

    src = inspect.getsource(billing)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "app_store_notifications")
    body = ast.get_source_segment(src, fn)
    code = "\n".join(re.sub(r"#.*$", "", l) for l in body.splitlines())
    assert "_webhook_rate_limited(request)" in code, "the webhook has no rate limiter"
    assert code.index("_webhook_rate_limited") < code.index("await request.json()")
    assert "429" in code


def test_the_ceiling_is_generous_enough_for_real_apple_traffic():
    assert billing._WEBHOOK_MAX_PER_MINUTE >= 60
