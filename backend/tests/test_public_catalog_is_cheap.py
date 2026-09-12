"""The two `.public` storefront routes must not be an event-loop denial primitive.

`GET /billing/plans` and `GET /billing/credit-packs` are two of the TEN routes reachable
with no credential at all (auth.md §1a), and neither has a rate limiter. Each one ran its
Supabase SELECT per request, SYNCHRONOUSLY, on the single Railway uvicorn worker — measured
2026-09-12 as 12 requests → 12 `plan_credits` SELECTs at 92-281 ms each, every one of them
suspending the whole process. Plain anonymous GETs were enough to hold the loop down.

Two independent properties fix it and BOTH are pinned here: the read is off the loop, and a
successful read is memoised so the common case does no I/O at all.

⚠️ A FALLBACK IS NEVER MEMOISED. The seeded catalogue is what we serve when the read failed
or came back empty; pinning it for the TTL would make one Supabase blip look like a pricing
change for two minutes — the "a cached failure is byte-identical to a real answer" trap.
"""

from __future__ import annotations

import ast
import inspect
import re
import threading

import pytest

import app.services.subscription_service as ss


@pytest.fixture(autouse=True)
def _clear():
    ss.reset_catalog_cache()
    yield
    ss.reset_catalog_cache()


class _Table:
    def __init__(self, rows, counter, boom=False):
        self._rows, self._c, self._boom = rows, counter, boom

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self

    def execute(self):
        self._c.append(threading.get_ident())
        if self._boom:
            raise RuntimeError("supabase is down")
        return type("R", (), {"data": list(self._rows)})()


def _svc(rows, counter, boom=False):
    svc = ss.SubscriptionService.__new__(ss.SubscriptionService)
    svc.supabase = type("S", (), {"table": lambda _s, _n: _Table(rows, counter, boom)})()
    return svc


_PLAN_ROWS = [
    {"tier": "free", "monthly_credits": 50, "price_cents": 0, "display_name": "Free"},
    {"tier": "pro", "monthly_credits": 1200, "price_cents": 999, "display_name": "Pro"},
]


def test_a_successful_catalog_read_happens_once_not_once_per_request():
    calls = []
    svc = _svc(_PLAN_ROWS, calls)
    first = svc.get_plan_catalog()
    for _ in range(11):
        svc.get_plan_catalog()
    assert len(calls) == 1, (
        f"{len(calls)} Supabase round trips for 12 anonymous requests — the public "
        "catalogue is still one blocking query per caller"
    )
    assert svc.get_plan_catalog() == first


def test_the_cached_rows_cannot_be_mutated_by_a_caller():
    """Callers build Pydantic models from these dicts; handing out the stored objects
    would let one request poison every later one."""
    calls = []
    svc = _svc(_PLAN_ROWS, calls)
    a = svc.get_plan_catalog()
    a[0]["display_name"] = "POISONED"
    b = svc.get_plan_catalog()
    assert b[0]["display_name"] != "POISONED"


@pytest.mark.parametrize("rows,boom", [([], False), (_PLAN_ROWS, True)])
def test_a_fallback_answer_is_never_memoised(rows, boom):
    """An empty table and a failed read both serve the seeded catalogue — and both must
    retry on the very next request, not pin the fallback for the TTL."""
    calls = []
    svc = _svc(rows, calls, boom=boom)
    svc.get_plan_catalog()
    svc.get_plan_catalog()
    svc.get_plan_catalog()
    assert len(calls) == 3, (
        "a degraded catalogue was cached — one Supabase blip would look like a pricing "
        "change for the whole TTL"
    )


def test_the_ttl_expires():
    calls = []
    svc = _svc(_PLAN_ROWS, calls)
    svc.get_plan_catalog()
    key, (stamp, rows) = "plans", ss._catalog_cache["plans"]
    ss._catalog_cache[key] = (stamp - ss._CATALOG_TTL_SECONDS - 1, rows)
    svc.get_plan_catalog()
    assert len(calls) == 2, "the memo never expires — a pricing edit would never surface"


def test_the_packs_catalog_has_the_same_two_properties():
    calls = []
    svc = _svc([{"product_id": "p1", "credits": 100, "price_cents": 499,
                 "display_name": "Small", "sort_order": 1}], calls)
    for _ in range(5):
        svc.get_credit_pack_catalog()
    assert len(calls) == 1
    assert "packs" in ss._catalog_cache


@pytest.mark.parametrize("handler", ["get_plans", "get_credit_packs"])
def test_the_public_handlers_do_not_call_the_catalog_on_the_loop(handler):
    """Source-level, comment-stripped and function-bound: the handler must hand the
    synchronous service method to a thread, not call it inline."""
    import app.api.v1.endpoints.billing as billing

    src = inspect.getsource(billing)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == handler)
    body = ast.get_source_segment(src, fn)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())
    assert "asyncio.to_thread" in code, (
        f"{handler} runs a blocking Supabase read on the event loop — and it is a "
        "`.public` route, so anyone can hold the single worker down with plain GETs"
    )
    assert not re.search(r"SubscriptionService\(\)\.get_\w+_catalog\(\)", code), (
        "the catalogue is still invoked inline"
    )
