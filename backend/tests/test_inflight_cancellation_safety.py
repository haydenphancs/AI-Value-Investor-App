"""Every `_inflight` leader must resolve its shared future on EVERY exit, including cancellation.

Two distinct defects live in this pattern, and the codebase has had both:

1. **The leader is cancelled.** `asyncio.CancelledError` is a `BaseException`, so it skips
   `except Exception` entirely. A `finally` that merely pops the dict stops NEW joiners while
   leaving every joiner already parked on `await inflight` hanging for the life of the process.

2. **A JOINER is cancelled.** Awaiting the shared future directly means the joiner's
   cancellation propagates INTO the future — cancelling it. The leader's later `set_result`
   then raises `InvalidStateError`, so a request whose data loaded perfectly 500s, and the
   other joiners get a `CancelledError` they never asked for. `asyncio.shield` is the fix.

Defect 2 is demonstrated below rather than asserted, because it is counter-intuitive enough
that a source-only guard would not survive someone "simplifying" the shield away.

`profit_power_service.py` is the reference implementation for both.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parents[1] / "app/services"

# Every module using the shared-future dedup pattern. Adding one without adding it here is
# what let three of these drift — `test_no_inflight_service_is_missing_from_this_list` catches it.
_INFLIGHT_MODULES = [
    "profit_power_service.py",
    "growth_service.py",
    "earnings_service.py",
    "holders_service.py",
    "news_cache_service.py",
    "whale_service.py",
    "news_insight_service.py",
    "ticker_data_cache.py",
    "research_service.py",
    # The 2026-08-07 audit named only the six above. The anti-vacuity check at the bottom of
    # this file found sixteen more already using the same shared-future dedup, which is the
    # whole reason that check exists.
    "competitor_intel_service.py",
    "growth_snapshot_service.py",
    "health_check_service.py",
    "health_snapshot_service.py",
    "home_dashboard_service.py",
    "ip_intel_service.py",
    "journey_content_service.py",
    "moat_scoring_service.py",
    "money_moves_content_service.py",
    "ownership_snapshot_service.py",
    "price_catalyst_service.py",
    "profitability_snapshot_service.py",
    "revenue_breakdown_service.py",
    "signal_of_confidence_service.py",
    "signals_service.py",
    "valuation_snapshot_service.py",
]


# ── Defect 2, demonstrated ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unshielded_joiner_cancellation_breaks_the_leader():
    """The behaviour the shield exists to prevent. If this ever stops holding, asyncio has
    changed and the shields can be revisited."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    async def joiner():
        return await fut          # UNSHIELDED — the bug

    t = asyncio.create_task(joiner())
    await asyncio.sleep(0)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await asyncio.sleep(0)

    assert fut.cancelled(), "the joiner's cancellation propagated into the SHARED future"
    with pytest.raises(asyncio.InvalidStateError):
        fut.set_result("data loaded fine")   # the leader, 500ing for no reason


@pytest.mark.asyncio
async def test_a_shielded_joiner_cancellation_leaves_the_leader_alone():
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    async def joiner():
        return await asyncio.shield(fut)

    t = asyncio.create_task(joiner())
    await asyncio.sleep(0)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await asyncio.sleep(0)

    assert not fut.cancelled()
    fut.set_result("data loaded fine")       # must not raise
    assert fut.result() == "data loaded fine"


# ── Defect 1 + 2, pinned in source ───────────────────────────────────────────

def _source(name: str) -> str:
    path = _SERVICES / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    return path.read_text()


@pytest.mark.parametrize("module", _INFLIGHT_MODULES)
def test_every_join_is_shielded(module):
    src = _source(module)
    bare = []
    for i, line in enumerate(src.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        # `await <something>_inflight[...]` / `await inflight` without a shield.
        if re.search(r"await\s+(self\.)?_?\w*inflight\w*\[", line) and "shield" not in line:
            bare.append(i)
    assert not bare, (
        f"{module}: unshielded join at line(s) {bare}. A joiner that gives up would cancel "
        f"the SHARED future and make the leader's set_result raise InvalidStateError."
    )


@pytest.mark.parametrize("module", _INFLIGHT_MODULES)
def test_every_resolve_is_guarded(module):
    """A bare `set_result` / `set_exception` raises InvalidStateError if anything already
    resolved the future — which, before the shields, a cancelled joiner could do."""
    src = _source(module)
    offenders = []
    lines = src.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"\b\w*fut\w*\.set_(result|exception)\(", stripped):
            # 10 lines back, not 3: these sites routinely put a multi-line comment between
            # the `if not fut.done():` guard and the call it guards (explaining the
            # `_has_waiters` split), and a tight window reads those as unguarded.
            window = "\n".join(lines[max(0, i - 10): i + 1])
            if ".done()" not in window:
                offenders.append(i + 1)
    assert not offenders, (
        f"{module}: unguarded future resolution at line(s) {offenders} — wrap in "
        f"`if not <fut>.done():`"
    )


@pytest.mark.parametrize("module", _INFLIGHT_MODULES)
def test_cancellation_cannot_leave_a_future_pending(module):
    """Every leader must SETTLE the future on a cancellation path — either an explicit
    `except asyncio.CancelledError` arm, or a `finally` that resolves when still pending.
    Popping the dict is not enough: it stops new joiners and strands the existing ones."""
    src = _source(module)
    if "create_future()" not in src:
        pytest.skip(f"{module} does not create shared futures")

    settles = (
        "except asyncio.CancelledError" in src
        or "except BaseException" in src
        # A `finally` (or any tail) that resolves a still-pending future covers it too.
        or re.search(r"if not \w*fut\w*\.done\(\):\s*\n\s*\w*fut\w*\.(set_exception|set_result|cancel)\(", src)
    )
    assert settles, (
        f"{module} creates a shared future but has no path that resolves it on "
        f"CancelledError — joiners hang for the life of the process"
    )


def test_no_inflight_service_is_missing_from_this_list():
    """Anti-vacuity. A new service adopting the pattern must be added above, or it is
    unguarded and this file silently says nothing about it."""
    found = set()
    for path in _SERVICES.rglob("*.py"):
        src = path.read_text()
        if "create_future()" in src and "inflight" in src.lower():
            found.add(path.name)
    missing = found - set(_INFLIGHT_MODULES)
    assert not missing, (
        f"services using the in-flight pattern but absent from _INFLIGHT_MODULES: "
        f"{sorted(missing)}"
    )
