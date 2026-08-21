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
    # Task-based rather than Future-based, and in a SUBPACKAGE — which is why it was
    # invisible twice over: the enumeration check below only looked for
    # `create_future()`, and `_source` silently skipped nested paths.
    "agents/ticker_report_data_collector.py",
    "geopolitical_macro_service.py",
    "commodity_service.py",
    # Added 2026-08-21 with the commodity cache pass: N concurrent viewers of the same
    # cold ticker each ran their own 600-day fetch AND their own pandas indicator pass.
    "technical_analysis_service.py",
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
    "widget_movers_service.py",
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
    """Read one listed module.

    `name` may carry a subdirectory (`agents/ticker_report_data_collector.py`). It used
    to be a bare filename joined onto `app/services`, so a module living in a subpackage
    resolved to a path that does not exist and the whole parametrised case SKIPPED —
    silently, and skips are not failures. Anything listed here must therefore exist:
    a typo or a move now fails loudly instead of quietly dropping coverage.
    """
    path = _SERVICES / name
    assert path.exists(), (
        f"{name} is listed in _INFLIGHT_MODULES but does not exist at {path}. "
        f"If it moved, fix the path — do not let it skip."
    )
    return path.read_text()


# A local bound from an in-flight container:  `inflight = self._inflight.get(key)`,
# `fut = _AGENT_INFLIGHT[key]`, `shared = _whale_profile_inflight.get(id)` …
_INFLIGHT_BIND = re.compile(
    r"^\s*(\w+)\s*=\s*(?:await\s+)?[\w\.]*inflight\w*\s*(?:\.get\(|\[)",
    re.IGNORECASE,
)
# The direct-subscript join: `await self._inflight[key]`.
_SUBSCRIPT_JOIN = re.compile(r"await\s+(self\.)?_?\w*inflight\w*\[", re.IGNORECASE)


def _unshielded_joins(src: str) -> list:
    """Line numbers of `await <shared future>` with no `asyncio.shield`.

    Catches BOTH shapes. The original version matched only the direct SUBSCRIPT form,
    which meant it never saw a single real join in this repo: every site binds the
    future to a local first (`inflight = self._inflight.get(k)` … `return await
    inflight`), so the `[` the pattern required never appeared on the await line. All
    81 assertions passed while THIRTEEN unshielded joins across eight of the modules
    listed above were live — the exact defect this file is named for. Bind-then-await is
    the normal way to write it, so the narrow pattern was guaranteed to be vacuous.
    """
    bare = []
    bound = set()
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = _INFLIGHT_BIND.match(line)
        if m:
            bound.add(m.group(1))
            continue
        if "shield" in line:
            continue
        if _SUBSCRIPT_JOIN.search(line):
            bare.append(i)
            continue
        for name in bound:
            if re.search(r"await\s+" + re.escape(name) + r"\b", line):
                bare.append(i)
                break
    return bare


@pytest.mark.parametrize("module", _INFLIGHT_MODULES)
def test_every_join_is_shielded(module):
    bare = _unshielded_joins(_source(module))
    assert not bare, (
        f"{module}: unshielded join at line(s) {bare}. A joiner that gives up would cancel "
        f"the SHARED future and make the leader's set_result raise InvalidStateError."
    )


def test_the_join_detector_is_not_vacuous():
    """Anti-vacuity for `_unshielded_joins` itself.

    The previous detector was green against a codebase where every join was unshielded,
    so the detector — not just the assertions — has to be pinned. Both shapes must be
    caught, and a shielded join of either shape must not be.
    """
    assert _unshielded_joins(
        "inflight = self._inflight.get(k)\nreturn await inflight\n"
    ) == [2], "bind-then-await (the shape every real site uses) must be caught"
    assert _unshielded_joins("return await self._inflight[k]\n") == [1], \
        "direct subscript must still be caught"
    assert _unshielded_joins(
        "inflight = self._inflight.get(k)\nreturn await asyncio.shield(inflight)\n"
    ) == [], "a shielded bind-then-await must pass"
    assert _unshielded_joins("return await asyncio.shield(self._inflight[k])\n") == [], \
        "a shielded subscript must pass"
    assert _unshielded_joins(
        "inflight = self._inflight.get(k)\n# return await inflight\n"
    ) == [], "a commented-out join is not a join"


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
    """A joiner must never be left waiting on something that will never settle.

    There are TWO shapes here and they need different proofs, because the hazard is
    different:

    * **Bare `Future`** — nothing settles it but the leader, so the leader must resolve
      it on EVERY exit including cancellation: an explicit `except asyncio.CancelledError`
      arm, or a tail that resolves it while still pending. Popping the dict is not
      enough — that stops new joiners and strands the existing ones forever.

    * **`Task` / `ensure_future`** — a Task always settles itself (result, exception, or
      cancelled), so a joiner can never hang. What CAN go wrong is the map entry
      outliving nothing or, worse, being removed while the task still runs, so the next
      caller starts a duplicate fan-out. The proof there is that the entry is cleared
      from the task's own completion, not from the leader's frame.

    Asserting the Future proof against a Task module is a category error — that is what
    this arm previously did to `agents/ticker_report_data_collector.py` the moment it
    was listed.
    """
    src = _source(module)
    creates_future = "create_future()" in src
    creates_task = "ensure_future(" in src or "create_task(" in src
    if not (creates_future or creates_task):
        pytest.skip(f"{module} does not create shared awaitables")

    if creates_future:
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

    if creates_task and not creates_future:
        assert "add_done_callback(" in src, (
            f"{module} shares a Task but never clears its in-flight entry from the "
            f"task's own completion. Clearing it in the LEADER's `finally` drops a "
            f"still-running task from the map when the leader is cancelled, so the next "
            f"caller starts a duplicate fan-out."
        )


def test_no_inflight_service_is_missing_from_this_list():
    """Anti-vacuity. A new service adopting the pattern must be added above, or it is
    unguarded and this file silently says nothing about it."""
    found = set()
    for path in _SERVICES.rglob("*.py"):
        src = path.read_text()
        if "inflight" not in src.lower():
            continue
        # A shared awaitable is a shared awaitable: `loop.create_future()` and
        # `asyncio.ensure_future(...)` / `asyncio.create_task(...)` have the SAME
        # cancellation hazard — a joiner that gives up cancels the object everyone else
        # is waiting on. Looking only for `create_future()` is what hid
        # `agents/ticker_report_data_collector.py` from this check entirely.
        if not any(tok in src for tok in
                   ("create_future()", "ensure_future(", "create_task(")):
            continue
        found.add(str(path.relative_to(_SERVICES)))
    missing = found - set(_INFLIGHT_MODULES)
    assert not missing, (
        f"services using the in-flight pattern but absent from _INFLIGHT_MODULES: "
        f"{sorted(missing)}"
    )
