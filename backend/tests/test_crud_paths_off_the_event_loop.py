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
    # ⚠️ ADDED 2026-09-12 (post-deploy review). This file was NEITHER swept NOR listed, so
    # its nine blocking round trips — including the `_search_filing_chunks` /
    # `_search_all_chunks` pgvector similarity searches that freeze the worker for the
    # length of a vector query on every RAG-enabled chat turn — were invisible to both
    # halves of this guard. Same work stream as chat.py.
    "services/chat_service.py",
}


_AUTH_VERBS = {
    "sign_in_with_password", "sign_up", "sign_out", "reset_password_for_email",
    "verify_otp", "refresh_session", "update_user", "delete_user",
    "update_user_by_id", "generate_link", "list_users", "create_user",
    "exchange_code_for_session", "sign_in_with_id_token",
}
_STORAGE_VERBS = {"upload", "download", "remove", "create_signed_url", "move", "copy"}


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


class _SyncHelperFinder(ast.NodeVisitor):
    """A DIRECT call, from an `async def`, to a plain `def` in the same module that runs a
    Supabase round trip.

    THE GAP THIS EXISTS FOR. `_AsyncExecFinder` only sees `.execute()` whose innermost
    enclosing scope is an `async def`, on the stated assumption that a `.execute()` inside a
    plain `def` is "almost always the payload handed to `asyncio.to_thread`". In this
    codebase that assumption was wrong for 66 call sites / ~85 round trips: the plain def is
    called SYNCHRONOUSLY instead. `GET /portfolios` alone did 28 of them, so the endpoint
    every client hits on launch was fully blocking while this file reported the path clean
    (found 2026-09-12, after the first sweep shipped).

    ⚠️ THE EXEMPTION IS PER-SITE, NOT PER-NAME. An earlier version skipped any helper whose
    name appeared as a `to_thread` argument ANYWHERE in the file, which made the guard blind
    at 41 of the 59 swept sites: a helper threaded in one handler and called directly in
    another was clean by association. Measured — reverting exactly one of the two
    `_fetch_portfolio_items` sites in `portfolios.py` produced zero hits from BOTH finders.
    It was worse for additions than reversions: a brand-new handler appended to
    `portfolios.py` calling `_get_portfolio_or_404` and `_fetch_portfolio_items` directly
    would ship two blocking round trips and be green on day one.

    No exemption is needed at all. A real `to_thread(_helper, ...)` passes the helper as a
    bare `Name`, never as a Call, so it is not a Call node to this visitor; and the
    `to_thread(lambda: _helper(sb))` form is already excluded because a Lambda pushes a
    non-async scope. Verified app-wide: zero false positives.
    """

    def __init__(self, tree):
        self.blocking = self._sync_defs_with_execute(tree)
        self.stack, self.hits = [], []

    @staticmethod
    def _sync_defs_with_execute(tree):
        """Sync helpers that make ANY blocking Supabase round trip.

        ⚠️ Not just `.execute()`. `_purge_research_pdfs` / `_purge_avatars` are
        `for page in range(...)` loops of blocking Storage `bucket.list()` +
        `bucket.remove()` — hundreds of synchronous HTTP calls, the most expensive
        blocking work in any handler — and they contain no `.execute()` at all, so an
        execute-only key could not see them even when an `async def` called them directly.
        """
        out = {}
        for n in ast.walk(tree):
            if not isinstance(n, ast.FunctionDef):
                continue
            for sub in ast.walk(n):
                if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                    continue
                attr = sub.func.attr
                if attr == "execute":
                    out[n.name] = out.get(n.name, 0) + 1
                    continue
                chain, cur = [], sub.func
                while isinstance(cur, ast.Attribute):
                    chain.append(cur.attr)
                    cur = cur.value
                chain_set = set(chain)
                # ⚠️ Key on ACQUIRING the bucket, not on the verb. `_purge_research_pdfs`
                # does `bucket = supabase.storage.from_(...)` and then calls
                # `bucket.list(...)` / `bucket.remove(...)` on a LOCAL NAME, so the verb's
                # own attribute chain is just `["list"]` and a chain test never matches.
                # Any helper that reaches into Storage at all is doing blocking HTTP.
                if attr == "from_" and "storage" in chain_set:
                    out[n.name] = out.get(n.name, 0) + 1
                elif attr in _AUTH_VERBS and {"auth", "admin"} & chain_set:
                    out[n.name] = out.get(n.name, 0) + 1
                elif attr in _STORAGE_VERBS and {"storage", "from_"} & chain_set:
                    out[n.name] = out.get(n.name, 0) + 1
        return out

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
        """A direct call to a blocking helper. No `to_thread` exemption — see the class
        docstring: the exemption was per-NAME and blinded the guard at 41 of 59 sites."""
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name in self.blocking and self.stack and self.stack[-1]:
            self.hits.append((n.lineno, name))
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


def _sync_helper_sites():
    out = {}
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                     # pragma: no cover
            continue
        finder = _SyncHelperFinder(tree)
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
    helpers = _sync_helper_sites()
    stale = [f for f in sorted(_NOT_YET) if f not in sites and f not in helpers]
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


def test_the_sync_helper_finder_is_not_vacuous():
    """It must flag a direct call and clear a to_thread'd one — otherwise the sweep below
    is the same silent pass that let 85 round trips through."""
    bad = ast.parse(
        "def _rows(sb):\n"
        "    return sb.table('x').select('*').execute().data\n"
        "async def h(sb):\n"
        "    return _rows(sb)\n"
    )
    good = ast.parse(
        "import asyncio\n"
        "def _rows(sb):\n"
        "    return sb.table('x').select('*').execute().data\n"
        "async def h(sb):\n"
        "    return await asyncio.to_thread(_rows, sb)\n"
    )
    sync_caller = ast.parse(
        "def _rows(sb):\n"
        "    return sb.table('x').select('*').execute().data\n"
        "def plain(sb):\n"
        "    return _rows(sb)\n"
    )
    # THE CASE THE OLD PER-NAME EXEMPTION MISSED: the same helper threaded in one handler
    # and called directly in another. Only the direct call may be flagged.
    mixed = ast.parse(
        "import asyncio\n"
        "def _rows(sb):\n"
        "    return sb.table('x').select('*').execute().data\n"
        "async def threaded(sb):\n"
        "    return await asyncio.to_thread(_rows, sb)\n"
        "async def direct(sb):\n"
        "    return _rows(sb)\n"
    )
    lam = ast.parse(
        "import asyncio\n"
        "def _rows(sb):\n"
        "    return sb.table('x').select('*').execute().data\n"
        "async def h(sb):\n"
        "    return await asyncio.to_thread(lambda: _rows(sb))\n"
    )
    f1 = _SyncHelperFinder(bad); f1.visit(bad)
    f2 = _SyncHelperFinder(good); f2.visit(good)
    f3 = _SyncHelperFinder(sync_caller); f3.visit(sync_caller)
    f4 = _SyncHelperFinder(mixed); f4.visit(mixed)
    f5 = _SyncHelperFinder(lam); f5.visit(lam)
    assert [h[1] for h in f1.hits] == ["_rows"], "the finder cannot see a direct call"
    assert f2.hits == [], "the finder flags the to_thread FIX as the bug"
    assert f3.hits == [], "a sync caller is not on the event loop and must not be flagged"
    assert [h[1] for h in f4.hits] == ["_rows"], (
        "a helper threaded ELSEWHERE in the file vouched for a direct call — the per-name "
        "exemption blinded the guard at 41 of 59 sites"
    )
    assert f4.hits[0][0] == 7, f4.hits
    assert f5.hits == [], "the to_thread(lambda: ...) form must not be flagged"


def test_no_async_handler_calls_a_blocking_sync_helper_directly():
    """The half `_AsyncExecFinder` structurally cannot see.

    `GET /portfolios` did 28 Supabase round trips inside plain `def` helpers it called
    synchronously, while this file reported the CRUD paths clean — a green guard actively
    preventing anyone from re-noticing them.
    """
    live = {f: hits for f, hits in _sync_helper_sites().items() if f not in _NOT_YET}
    assert not live, (
        "an async handler calls a blocking Supabase helper directly — on the single "
        "Railway worker this stalls EVERY in-flight request for the round trip:\n  "
        + "\n  ".join(
            f"app/{f}: " + ", ".join(f"{name}() at :{ln}" for ln, name in hits)
            for f, hits in sorted(live.items())
        )
    )


# ── the blocking calls that are NOT `.execute()` ────────────────────────────────────

#: GoTrue (`auth.*`) and Storage (`bucket.*`) round trips are synchronous httpx calls with
#: no `.execute()`, so both finders above are structurally blind to them — and they are the
#: HEAVIEST blocking calls in the app (a sign-in is a server-side bcrypt).

#: ⚠️ DELIBERATELY NOT THREADED — do not "fix" these without changing the client model
#: first.
#:
#: `database.get_auth_client()` returns a PROCESS-WIDE SINGLETON and calls
#: `_reset_to_service_role(...)` on every resolution, so each request starts from
#: service_role. That reset-then-use pattern is safe only because the event loop
#: SERIALISES it. supabase-py's auth-state listener rewrites the shared
#: `options.headers["Authorization"]` on every sign-in verb, so two sign-ins running
#: concurrently in threads would interleave reset and rewrite on one client — which is
#: exactly the cross-user demotion `database.py` records as having broken account
#: deletion, change-password and reset-password in production
#: (`project_supabase_admin_client_demotion`).
#:
#: Threading these therefore requires a per-request auth client (or a lock) FIRST. Listed
#: here so the cost is visible and nobody threads them believing it is mechanical.
#: ⚠️ Scoped to the KIND, not the whole file. A file-level exclusion also excused the
#: STORAGE calls in `users.py`, so reverting the account-deletion purge back onto the loop
#: was invisible — the exemption silently covered a defect it was never meant to.
_BLOCKING_BY_DESIGN = {
    # `auth.py` LEFT this list on 2026-09-17: every GoTrue verb there now runs through
    # `database.run_gotrue`, which keeps the serialisation the loop used to provide (a
    # process-wide asyncio.Lock, service_role re-asserted INSIDE it) while the verb itself
    # runs in a worker thread. A flood of wrong-password logins now queues LOGINS, not the
    # whole process. Re-adding the entry re-opens that stall.
    "api/v1/endpoints/users.py": {"auth"},    # auth.admin.delete_user on the same client
}


class _NonExecuteFinder(ast.NodeVisitor):
    """Blocking GoTrue / Storage calls whose innermost scope is an `async def`."""

    def __init__(self):
        self.stack, self.hits = [], []

    def _scope(self, node, is_async):
        self.stack.append(is_async)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, n):      self._scope(n, False)
    def visit_AsyncFunctionDef(self, n): self._scope(n, True)
    def visit_Lambda(self, n):           self._scope(n, False)

    def visit_Call(self, n):
        f = n.func
        if isinstance(f, ast.Attribute) and self.stack and self.stack[-1]:
            chain, cur = [], f
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            chain = list(reversed(chain))
            if f.attr in _AUTH_VERBS and ("auth" in chain or "admin" in chain):
                self.hits.append((n.lineno, "auth", ".".join(chain)))
            elif f.attr in _STORAGE_VERBS and ("storage" in chain or "from_" in chain):
                self.hits.append((n.lineno, "storage", ".".join(chain)))
        self.generic_visit(n)


def _non_execute_sites():
    out = {}
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                     # pragma: no cover
            continue
        finder = _NonExecuteFinder()
        finder.visit(tree)
        if finder.hits:
            out[rel] = finder.hits
    return out


def test_the_non_execute_finder_is_not_vacuous():
    bad = ast.parse(
        "async def h(sb):\n"
        "    return sb.auth.sign_in_with_password({'email': e})\n"
    )
    good = ast.parse(
        "import asyncio\n"
        "async def h(sb):\n"
        "    return await asyncio.to_thread(lambda: sb.auth.sign_in_with_password({}))\n"
    )
    f1 = _NonExecuteFinder(); f1.visit(bad)
    f2 = _NonExecuteFinder(); f2.visit(good)
    assert [h[1] for h in f1.hits] == ["auth"], "the finder cannot see a GoTrue call"
    assert f2.hits == [], "the finder flags the to_thread fix as the bug"


def test_no_new_blocking_auth_or_storage_call_appears_on_the_loop():
    """Everything outside the two recorded files must stay off the loop.

    The account-deletion Storage purges used to be here — each a `for page in range(...)`
    of blocking `bucket.list()` + `bucket.remove()`, hundreds of synchronous round trips in
    one handler — while the cheap `.execute()`-shaped purge three lines below them was
    threaded. They are now `asyncio.to_thread`-ed.
    """
    live = {}
    for f, hits in _non_execute_sites().items():
        if f in _NOT_YET:
            continue
        allowed = _BLOCKING_BY_DESIGN.get(f, set())
        remaining = [h for h in hits if h[1] not in allowed]
        if remaining:
            live[f] = remaining
    assert not live, (
        "a blocking GoTrue/Storage round trip runs on the event loop — these are the "
        "HEAVIEST blocking calls in the app:\n  "
        + "\n  ".join(
            f"app/{f}: " + ", ".join(f"{t} at :{ln}" for ln, _k, t in hits)
            for f, hits in sorted(live.items())
        )
    )


def test_the_by_design_blocking_files_still_have_the_calls_they_name():
    """An exclusion that outlives its reason is how a fix rots — same discipline as
    `_NOT_YET`."""
    sites = _non_execute_sites()
    stale = [
        f for f, kinds in sorted(_BLOCKING_BY_DESIGN.items())
        if not any(h[1] in kinds for h in sites.get(f, []))
    ]
    assert not stale, (
        "these files no longer hold a blocking auth/storage call — delete them from "
        "_BLOCKING_BY_DESIGN: " + ", ".join(stale)
    )

