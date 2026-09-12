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


def test_the_global_ceiling_cannot_be_evaded_by_varying_the_client_ip():
    """`--forwarded-allow-ips='*'` means `request.client.host` IS the `X-Forwarded-For`
    header — attacker-chosen. A per-IP ceiling alone therefore bounds nothing: a fresh
    header is a fresh allowance. The global ceiling takes no key at all.
    """
    allowed = sum(
        0 if billing._webhook_rate_limited(_req(f"198.51.100.{i % 256}")) else 1
        for i in range(billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL * 2)
    )
    assert allowed <= billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL, (
        f"{allowed} requests got through by rotating the client IP"
    )


def test_the_global_window_rolls(monkeypatch):
    for i in range(billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL):
        billing._webhook_rate_limited(_req(f"198.51.100.{i % 256}"))
    assert billing._webhook_rate_limited(_req("198.51.100.250")) is True
    base = time.monotonic()
    monkeypatch.setattr(billing.time, "monotonic", lambda: base + 61)
    assert billing._webhook_rate_limited(_req("198.51.100.250")) is False, \
        "a minute later the route must accept Apple again"


def test_the_global_ceiling_leaves_real_apple_traffic_untouched():
    """Apple sends single-digit notifications a minute even for a busy product."""
    assert billing._WEBHOOK_MAX_PER_MINUTE_GLOBAL >= 10 * billing._WEBHOOK_MAX_PER_MINUTE / 2


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
