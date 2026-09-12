"""The user-CRUD half of `project_overview_event_loop_blocking`, closed 2026-09-12.

Railway runs exactly ONE uvicorn worker — pinned by `test_deploy_command_parity.py`,
because every lifespan job in `app/main.py` is unclaimed and safe only under that
assumption — and `supabase-py` is SYNCHRONOUS. So a bare `.execute()` inside an `async def`
does not merely slow its own request: it suspends the whole process for the round trip and
every other in-flight request waits behind it. `main.py` records a measured 18.2 s
contiguous stall from exactly this shape.

The 2026-08-27 pass fixed three call sites on `/stocks/{t}/overview` and declared that path
clean. **92 more were still on the loop** when this file was written — the watchlist,
portfolio, tracking, auth, admin, whale, home and insights paths, i.e. the CRUD a signed-in
user touches on every screen. They now go through `app.utils.supabase_async.sb_exec`.

Two guards, because either alone is weak:
  * a THREAD-IDENTITY test — the helper must really leave the loop thread. A grep for
    `to_thread` passes on a comment.
  * a SOURCE scan with a NAMED, justified exclusion list — thread identity can only be
    asserted where a test can drive the handler, and there are 92 sites.
"""

from __future__ import annotations

import ast
import pathlib
import threading

import pytest

from app.utils.supabase_async import sb_exec

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: Files still holding a blocking `.execute()` inside an `async def`, and why.
#:
#: These are the AI-feature area, owned by a separate work stream as of 2026-09-12. They
#: carry the same defect and the same fix applies; they are excluded here so this guard
#: does not fight a concurrent edit, NOT because they are correct.
_NOT_YET = {
    "api/v1/endpoints/chat.py",
    "api/v1/endpoints/research.py",
    "services/research_service.py",
}


class _AsyncExecFinder(ast.NodeVisitor):
    """`.execute()` whose INNERMOST enclosing scope is an `async def`.

    The nesting matters: the same call inside a `lambda` or a plain `def` is almost always
    the payload handed to `asyncio.to_thread`, which is the fix, not the bug.
    """

    def __init__(self):
        self.stack, self.hits = [], []

    def _scope(self, node, is_async):
        self.stack.append(is_async)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, n):      self._scope(n, False)
    def visit_AsyncFunctionDef(self, n): self._scope(n, True)
    def visit_Lambda(self, n):           self._scope(n, False)
    def visit_ListComp(self, n):         self._scope(n, False)
    def visit_SetComp(self, n):          self._scope(n, False)
    def visit_DictComp(self, n):         self._scope(n, False)
    def visit_GeneratorExp(self, n):     self._scope(n, False)

    def visit_Call(self, n):
        f = n.func
        if (isinstance(f, ast.Attribute) and f.attr == "execute"
                and self.stack and self.stack[-1]):
            self.hits.append(n.lineno)
        self.generic_visit(n)


def _blocking_sites():
    out = {}
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                     # pragma: no cover
            continue
        finder = _AsyncExecFinder()
        finder.visit(tree)
        if finder.hits:
            out[rel] = finder.hits
    return out


# ── thread identity ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sb_exec_really_leaves_the_loop_thread():
    """The whole point. A helper that awaited a synchronous call in place would satisfy
    every source scan in this file and change nothing."""
    seen = {}

    class _Query:
        def execute(self):
            seen["thread"] = threading.get_ident()
            return {"data": [{"ok": True}]}

    loop_thread = threading.get_ident()
    result = await sb_exec(_Query())
    assert result == {"data": [{"ok": True}]}, "the result must pass through untouched"
    assert seen["thread"] != loop_thread, (
        "the Supabase round trip ran ON the event-loop thread — on the single Railway "
        "worker that suspends every other in-flight request for its duration"
    )


@pytest.mark.asyncio
async def test_sb_exec_propagates_the_exception_unchanged():
    """Every call site keeps its existing `except` clauses only if the type survives the
    thread hop — `is_transient_supabase_error` and the 23505/42501 handlers all match on
    type and attributes."""
    class _Boom(RuntimeError):
        pass

    class _Query:
        def execute(self):
            exc = _Boom("Error 520: ")
            exc.code = 520
            raise exc

    with pytest.raises(_Boom) as info:
        await sb_exec(_Query())
    assert getattr(info.value, "code", None) == 520, "the attributes must survive too"


@pytest.mark.asyncio
async def test_the_loop_keeps_running_while_the_query_blocks():
    """Behavioural proof, not just a thread id: a sibling coroutine must make progress
    while the 'round trip' is in flight."""
    import asyncio

    started = threading.Event()
    release = threading.Event()
    ticks = 0

    class _Slow:
        def execute(self):
            started.set()
            release.wait(5)
            return "done"

    async def _sibling():
        nonlocal ticks
        while not started.is_set():
            await asyncio.sleep(0.001)
        for _ in range(5):
            ticks += 1
            await asyncio.sleep(0.001)
        release.set()

    result, _ = await asyncio.gather(sb_exec(_Slow()), _sibling())
    assert result == "done"
    assert ticks == 5, (
        "the event loop was frozen for the whole query — nothing else could run"
    )


# ── the sweep ───────────────────────────────────────────────────────────────────────


def test_the_finder_is_not_vacuous():
    """It must flag a real blocking call and clear the threaded form. Without this, an
    empty sweep below would read as 'everything is clean'."""
    bad = ast.parse(
        "async def h():\n"
        "    rows = sb.table('x').select('*').execute().data\n"
    )
    good = ast.parse(
        "async def h():\n"
        "    rows = (await sb_exec(sb.table('x').select('*'))).data\n"
        "    other = await asyncio.to_thread(lambda: sb.table('y').select('*').execute())\n"
    )
    f1, f2 = _AsyncExecFinder(), _AsyncExecFinder()
    f1.visit(bad); f2.visit(good)
    assert f1.hits == [2], "the finder cannot see a blocking call"
    assert f2.hits == [], "the finder flags the FIX as the bug"


def test_no_supabase_round_trip_runs_on_the_event_loop():
    live = {f: lines for f, lines in _blocking_sites().items() if f not in _NOT_YET}
    assert not live, (
        "a synchronous Supabase round trip is back on the event loop — on the single "
        "Railway worker this stalls EVERY in-flight request for its duration:\n  "
        + "\n  ".join(f"app/{f}: lines {lines}" for f, lines in sorted(live.items()))
    )


def test_the_excluded_files_are_still_the_ones_named():
    """An exclusion list that silently outlives its reason is how a fix rots.

    Every entry must STILL have a blocking call — otherwise it was fixed and the entry
    belongs deleted, so this goes red and forces that. It also fails if an excluded file
    disappears entirely.
    """
    sites = _blocking_sites()
    stale = [f for f in sorted(_NOT_YET) if f not in sites]
    assert not stale, (
        "these files no longer block the loop — delete them from `_NOT_YET`: "
        + ", ".join(stale)
    )


# ── the backfill that could never succeed ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_sector_backfill_skips_assets_that_can_never_have_one(monkeypatch):
    """A coin, an index or a commodity has no `sector` in `company_profile_cache` and never
    will, so an unfiltered "missing sector" list kept every one of them missing forever —
    the same symbols re-queried on EVERY tracking request, each lookup coming back empty.
    """
    import app.services.tracking_service as ts

    svc = ts.TrackingService.__new__(ts.TrackingService)
    asked = []

    class _Q:
        def table(self, _n): return self
        def select(self, *a, **k): return self
        def in_(self, _col, syms):
            asked.append(list(syms))
            return self
        def execute(self):
            return type("R", (), {"data": []})()

    monkeypatch.setattr(ts, "get_supabase", lambda: _Q())
    watchlist = [
        {"ticker": "AAPL", "asset_type": "Stock", "sector": None},
        {"ticker": "BTCUSD", "asset_type": "Crypto", "sector": None},
        {"ticker": "^GSPC", "asset_type": "Index", "sector": None},
        {"ticker": "GCUSD", "asset_type": "Commodity", "sector": None},
    ]
    await svc._backfill_classification("u1", watchlist)
    assert asked, "the backfill made no lookup at all — the equity row must still heal"
    assert asked[0] == ["AAPL"], (
        f"non-equity symbols were looked up in company_profile_cache: {asked[0]}"
    )


@pytest.mark.asyncio
async def test_an_all_non_equity_watchlist_makes_no_query_at_all(monkeypatch):
    import app.services.tracking_service as ts

    svc = ts.TrackingService.__new__(ts.TrackingService)
    calls = []

    def _boom():
        calls.append(1)
        raise AssertionError("a crypto-only watchlist must not query the profile cache")

    monkeypatch.setattr(ts, "get_supabase", _boom)
    await svc._backfill_classification("u1", [
        {"ticker": "BTCUSD", "asset_type": "Crypto", "sector": None},
        {"ticker": "ETHUSD", "asset_type": "Crypto", "sector": None},
    ])
    assert calls == []
