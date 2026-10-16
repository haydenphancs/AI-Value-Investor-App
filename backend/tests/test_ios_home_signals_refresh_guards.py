"""Home dashboard — the refresh fixes: a hidden tab that kept polling, a previous identity's
answer landing on the next user, a post-purchase reload that joined a pre-purchase poll, and
signal leader rows rebuilt on every refresh.

Four defects, one pass (2026-10-15):
  * `.task(id: isActiveTab)` awaited `loadIfStale()` and then called `startAutoRefresh()`.
    Leaving Home mid-load cancels that run and the next run calls `stopAutoRefresh()` — but the
    load does not respond to cancellation, so the cancelled run resumed and RE-ARMED the poll,
    and the hidden tab fetched `/home/dashboard` every 60 s. A `guard !Task.isCancelled` now
    sits between the await and the start.
  * `handleIdentityChange` cleared `data` and called `load()`, which JOINED a load already in
    flight under the PREVIOUS identity, and `performLoad` assigned its answer unchecked: the
    previous account's dashboard (unlocked signal tickers included) showed to the next user or
    guest until the next poll. Now the identity change bumps `loadGeneration` and drops the
    registration (`loadTask` / `loadTaskID`), `performLoad` drops an answer — success OR
    failure — whose generation moved, and the old task clears the registration only while it
    is still the registered one.
  * `refresh()` (pull-to-refresh, and the `entitlementGeneration` post-purchase reload) joined a
    poll already in flight, which was sent before the purchase and handed back locked rows. It
    now waits that load out and then loads, like `reloadForWatchlistChange`.
  * `SignalLeader` minted `let id = UUID()`, so `ForEach(signal.leaders)` rebuilt every leader
    row once a minute. Its identity is the symbol (unique per card: every backend aggregator
    keys on it), as `MarketPulseItem` and `ExclusiveSignal` already are.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails, each naming the assertion it must fail WITH.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_FILES = {
    "vm": _IOS / "ViewModels/HomeDashboardViewModel.swift",
    "view": _IOS / "Views/Screens/HomeDashboardView.swift",
    "models": _IOS / "Models/HomeDashboardModels.swift",
}

_CLASS = "final class HomeDashboardViewModel: ObservableObject"
_VIEW_STRUCT = "struct HomeDashboardView: View"
_TASK = ".task(id: isActiveTab)"
_IDENTITY = "func handleIdentityChange(isActiveTab: Bool) async"
_LOAD = "func load() async"
_PERFORM = "private func performLoad() async"
_REFRESH = "func refresh() async"
_LEADER = "struct SignalLeader: Identifiable, Hashable"
_GATE = "guard isActiveTab else { return }"
_FETCH = "let fetched = try await repository.fetchHomeDashboard()"
_CAPTURE = "let generation = loadGeneration"
_FENCE = "guard generation == loadGeneration else"
_CANCEL_GUARD = "guard !Task.isCancelled else { return }"


# ── Scanning helpers (copied from the Updates guard file; no shared conftest) ────────────

def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fixes' own comments name
    `loadGeneration`, `stopAutoRefresh()` and `loadTask = nil`, so an un-stripped scan would
    pass on prose.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _sources() -> dict[str, str]:
    out = {}
    for key, path in _FILES.items():
        if not path.exists():
            pytest.fail(f"expected file is missing: {path}")
        out[key] = _strip(path.read_text(encoding="utf-8"))
    return out


def _block_at(src: str, idx: int) -> str:
    """The brace-balanced block opened by the first `{` at or after `idx`."""
    open_brace = src.find("{", idx)
    assert open_brace != -1, f"no block opens after offset {idx}"
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    raise AssertionError(f"unbalanced braces after offset {idx}")


def _block(src: str, header: str) -> str:
    assert src.count(header) == 1, f"expected exactly one {header!r}, found {src.count(header)}"
    start = src.find(header)
    return _block_at(src, start + len(header))


def _idx(src: str, token: str) -> int:
    at = src.find(token)
    assert at != -1, f"{token!r} not found"
    return at


def _norm(src: str) -> str:
    """Whitespace-collapsed, so an exact-expression check survives re-indentation only."""
    return " ".join(src.split())


def _in_order(src: str, *tokens: str) -> None:
    positions = [_idx(src, t) for t in tokens]
    assert positions == sorted(positions), f"out of order: {list(zip(tokens, positions))}"


def _vm(s: dict[str, str]) -> str:
    return _block(s["vm"], _CLASS)


def _fn(s: dict[str, str], header: str) -> str:
    return _block(_vm(s), header)


# ── 1. The cancelled activation run does not re-arm the poll ─────────────────────────────

def _check_cancelled_run_does_not_restart_poll(s):
    view = _block(s["view"], _VIEW_STRUCT)
    task = _block(view, _TASK)
    hidden = _block_at(task, _idx(task, "guard isActiveTab else"))
    assert "viewModel.stopAutoRefresh()" in hidden, "the hidden-tab run no longer stops the poll — scan drifted"
    assert _CANCEL_GUARD in task, (
        "the activation run starts the poll without checking for cancellation — leaving Home "
        "mid-load re-arms the poll on a hidden tab"
    )
    load_at = _idx(task, "await viewModel.loadIfStale()")
    guard_at = _idx(task, _CANCEL_GUARD)
    start_at = _idx(task, "viewModel.startAutoRefresh()")
    assert load_at < guard_at < start_at, (
        "the cancellation check is not between `await viewModel.loadIfStale()` and "
        "`viewModel.startAutoRefresh()` — the run that resumes after a tab-away still starts the poll"
    )
    assert "await" not in task[guard_at + len(_CANCEL_GUARD) : start_at], (
        "an await sits between the cancellation check and startAutoRefresh() — the window reopens"
    )
    assert view.count("viewModel.startAutoRefresh()") == 1, (
        "the poll is started from somewhere other than the guarded activation run"
    )


# ── 2. performLoad drops the previous identity's answer ──────────────────────────────────

def _check_perform_load_drops_previous_identity(s):
    load = _fn(s, _PERFORM)
    assert load.count(_CAPTURE) == 1, "performLoad no longer captures the load generation"
    assert _idx(load, _CAPTURE) < _idx(load, "repository.fetchHomeDashboard()"), (
        "the generation is captured after the request — an identity change mid-flight is missed"
    )
    assert load.count(_FENCE) == 2, "performLoad does not fence both the success and the failure path"
    catch_at = _idx(load, "} catch {")
    success = load[_idx(load, _FETCH) + len(_FETCH) : catch_at]
    assert success.lstrip().startswith(_FENCE), (
        "the success path does not open with the generation fence — it lands the previous "
        "identity's answer (or sets state before dropping it)"
    )
    assert _idx(success, _FENCE) < _idx(success, "data = fetched.data"), (
        "`data = fetched.data` runs before the generation fence — it lands the previous identity's answer"
    )
    dropped = _block_at(success, _idx(success, _FENCE))
    assert "return" in dropped, "the success fence does not return"
    assert not re.search(r"(?<![=!<>])=(?!=)", dropped), (
        "the dropped answer touches state — the new identity's load owns every flag"
    )
    catch = _block_at(load, catch_at + len("} catch "))
    assert _norm(catch[1:]).startswith(_FENCE + " { return }"), (
        "the catch does not open with the generation fence (as its first statement) — the "
        "previous identity's failure raises the banner, the gate or a retry"
    )
    assert _idx(catch, _FENCE) < _idx(catch, "AppError.from(error)"), (
        "the catch classifies the error before the generation fence"
    )


# ── 3. handleIdentityChange fences off the in-flight load above the gate ─────────────────

def _check_identity_change_fences_in_flight_load(s):
    vm = _vm(s)
    handler = _fn(s, _IDENTITY)
    gate = _idx(handler, _GATE)
    bump = handler.find("loadGeneration &+= 1")
    assert bump != -1, "handleIdentityChange never bumps loadGeneration — the old answer still lands"
    assert vm.count("loadGeneration &+= 1") == 1, (
        "loadGeneration is bumped outside handleIdentityChange — a same-identity answer is dropped"
    )
    task_clear = re.search(r"(?<![\w.])loadTask = nil", handler)
    assert task_clear, (
        "handleIdentityChange keeps the old loadTask registered — the new identity's load joins it"
    )
    id_clear = re.search(r"(?<![\w.])loadTaskID = nil", handler)
    assert id_clear, "handleIdentityChange keeps the old loadTaskID registered"
    for name, at in (("loadGeneration &+= 1", bump), ("loadTask = nil", task_clear.start()),
                     ("loadTaskID = nil", id_clear.start())):
        assert at < gate, (
            f"`{name}` sits below the active-tab gate — a hidden tab's in-flight load still "
            "lands the previous identity's answer"
        )


# ── 4. A finished task unregisters only itself ───────────────────────────────────────────

def _check_load_clears_only_its_own_registration(s):
    load = _fn(s, _LOAD)
    _in_order(load, "let id = UUID()", "let task = Task", "loadTask = task", "await task.value")
    after_task = load[_idx(load, "let task = Task"):]
    task = _block_at(after_task, 0)
    registration = after_task[_idx(after_task, task) + len(task):]
    assert re.search(r"(?<![\w.])loadTaskID = id\b", registration[: _idx(registration, "await task.value")]), (
        "load() never registers its id — the fence below can never match"
    )
    fence_at = _idx(task, "if self.loadTaskID == id {")
    assert _idx(task, "await self.performLoad()") < fence_at, "the registration is cleared before the load ran"
    fence = _block_at(task, fence_at)
    assert "self.loadTask = nil" in fence and "self.loadTaskID = nil" in fence, (
        "the id fence no longer clears the registration"
    )
    assert "loadTask = nil" not in task.replace(fence, ""), (
        "the task clears loadTask outside the `if self.loadTaskID == id` fence — the previous "
        "identity's task unregisters the new identity's load"
    )


# ── 5. refresh() waits out a running load, then loads ────────────────────────────────────

def _check_refresh_orders_behind_running_load(s):
    refresh = _fn(s, _REFRESH)
    assert "if let running = loadTask, !running.isCancelled" in refresh, (
        "refresh() joins a load already in flight — a poll sent before the purchase hands back "
        "locked rows"
    )
    wait = _block_at(refresh, _idx(refresh, "if let running = loadTask"))
    assert "await running.value" in wait, "refresh() does not await the running load"
    assert "return" not in wait, "refresh() returns after waiting — that is a join, not a reload"
    tail = refresh[_idx(refresh, wait) + len(wait):]
    assert "await load()" in tail, "refresh() does not load after the running load finishes"


# ── 6. SignalLeader identity is the symbol ───────────────────────────────────────────────

def _check_signal_leader_id_is_symbol(s):
    leader = _block(s["models"], _LEADER)
    assert "UUID()" not in leader, (
        "SignalLeader mints a UUID() — every leader row is rebuilt on each 60 s refresh"
    )
    assert "var id: String { symbol }" in _norm(leader), "SignalLeader's id is not its symbol"


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "cancelled_run_does_not_restart_poll": _check_cancelled_run_does_not_restart_poll,
    "perform_load_drops_previous_identity": _check_perform_load_drops_previous_identity,
    "identity_change_fences_in_flight_load": _check_identity_change_fences_in_flight_load,
    "load_clears_only_its_own_registration": _check_load_clears_only_its_own_registration,
    "refresh_orders_behind_running_load": _check_refresh_orders_behind_running_load,
    "signal_leader_id_is_symbol": _check_signal_leader_id_is_symbol,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_guard_holds_on_the_real_source(name):
    GUARDS[name](_sources())


# ── Mutations: each guard must fail on the bug it names ──────────────────────────────────

def _sub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


def _within(header: str, pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    """Substitute ONCE inside the brace block of `header` (which must be unique), so a token
    that also appears in another function cannot be the one mutated."""
    def mutate(src: str) -> str:
        assert src.count(header) == 1, f"mutation header {header!r} is not unique"
        start = src.find(header) + len(header)
        open_brace = src.find("{", start)
        block = _block_at(src, start)
        new_block, n = re.subn(pattern, repl, block, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match inside {header!r}"
        return src[:open_brace] + new_block + src[open_brace + len(block):]
    return mutate


def _chain(*mutations: Callable[[str], str]) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        for m in mutations:
            src = m(src)
        return src
    return mutate


_E = re.escape

MUTATIONS: list[tuple] = [
    # ── 1. the activation run ──
    ("no-cancel-guard", "cancelled_run_does_not_restart_poll", "view",
     _within(_TASK, r"\n\s*" + _E(_CANCEL_GUARD), ""), "without checking for cancellation"),
    ("cancel-guard-before-load", "cancelled_run_does_not_restart_poll", "view",
     _chain(_within(_TASK, r"\n\s*" + _E(_CANCEL_GUARD), ""),
            _within(_TASK, r"(\n\s*)(await viewModel\.loadIfStale\(\))", r"\1" + _CANCEL_GUARD + r"\1\2")),
     "is not between"),
    ("await-after-cancel-guard", "cancelled_run_does_not_restart_poll", "view",
     _within(_TASK, r"(" + _E(_CANCEL_GUARD) + r"\n)", r"\1            await Task.yield()\n"),
     "the window reopens"),
    ("second-unguarded-start", "cancelled_run_does_not_restart_poll", "view",
     _sub(r"(\.reloadOnIdentityChange \{ isActive in )", r"\1viewModel.startAutoRefresh(); "),
     "somewhere other than the guarded activation run"),

    # ── 2. performLoad's generation fence ──
    ("generation-after-request", "perform_load_drops_previous_identity", "vm",
     _chain(_within(_PERFORM, r"\n\s*" + _E(_CAPTURE), ""),
            _within(_PERFORM, r"(" + _E(_FETCH) + r"\n)", r"\1            " + _CAPTURE + r"\n")),
     "captured after the request"),
    ("no-success-fence", "perform_load_drops_previous_identity", "vm",
     _within(_PERFORM, _E(_FENCE) + r" \{[^{}]*\}\s*", ""), "does not fence both"),
    ("data-before-fence", "perform_load_drops_previous_identity", "vm",
     _chain(_within(_PERFORM, r"\n\s*data = fetched\.data", ""),
            _within(_PERFORM, r"(" + _E(_FETCH) + r"\n)", r"\1            data = fetched.data\n")),
     "does not open with the generation fence"),
    ("dropped-answer-stops-loading", "perform_load_drops_previous_identity", "vm",
     _within(_PERFORM, r"(" + _E(_FENCE) + r" \{[^{}]*?)(\n\s*return\n)", r"\1\n                isLoading = false\2"),
     "the dropped answer touches state"),
    ("no-catch-fence", "perform_load_drops_previous_identity", "vm",
     _within(_PERFORM, r"(\} catch \{\s*)" + _E(_FENCE) + r" \{ return \}\n", r"\1"),
     "does not fence both"),
    ("catch-fence-after-classify", "perform_load_drops_previous_identity", "vm",
     _chain(_within(_PERFORM, r"(\} catch \{\s*)" + _E(_FENCE) + r" \{ return \}\n", r"\1"),
            _within(_PERFORM, r"(let appError = AppError\.from\(error\)\n)",
                    r"\1            " + _FENCE + r" { return }\n")),
     "the catch does not open with the generation fence"),

    # ── 3. handleIdentityChange ──
    ("identity-no-bump", "identity_change_fences_in_flight_load", "vm",
     _within(_IDENTITY, r"\n\s*loadGeneration &\+= 1", ""), "never bumps loadGeneration"),
    ("identity-joins-old-load", "identity_change_fences_in_flight_load", "vm",
     _within(_IDENTITY, r"\n\s*loadTask = nil", ""), "keeps the old loadTask registered"),
    ("identity-keeps-old-id", "identity_change_fences_in_flight_load", "vm",
     _within(_IDENTITY, r"\n\s*loadTaskID = nil", ""), "keeps the old loadTaskID registered"),
    ("bump-below-gate", "identity_change_fences_in_flight_load", "vm",
     _chain(_within(_IDENTITY, r"\n\s*loadGeneration &\+= 1", ""),
            _within(_IDENTITY, r"(" + _E(_GATE) + r"\n)", r"\1        loadGeneration &+= 1\n")),
     "`loadGeneration &+= 1` sits below the active-tab gate"),
    ("bump-elsewhere", "identity_change_fences_in_flight_load", "vm",
     _within(_REFRESH, r"(\n\s*await load\(\))", r"\n        loadGeneration &+= 1\1"),
     "bumped outside handleIdentityChange"),

    # ── 4. load()'s registration ──
    ("unfenced-clear", "load_clears_only_its_own_registration", "vm",
     _within(_LOAD, r"if self\.loadTaskID == id \{\s*self\.loadTask = nil\s*self\.loadTaskID = nil\s*\}",
             "self.loadTask = nil\n            self.loadTaskID = nil"),
     "'if self.loadTaskID == id {' not found"),
    ("extra-clear-outside-fence", "load_clears_only_its_own_registration", "vm",
     _within(_LOAD, r"(await self\.performLoad\(\)\n)", r"\1            self.loadTask = nil\n"),
     "outside the `if self.loadTaskID == id` fence"),
    ("never-registers-id", "load_clears_only_its_own_registration", "vm",
     _within(_LOAD, r"\n\s*loadTaskID = id\n", "\n"), "never registers its id"),

    # ── 5. refresh() ──
    ("refresh-joins", "refresh_orders_behind_running_load", "vm",
     _within(_REFRESH, r"if let running = loadTask, !running\.isCancelled \{\s*await running\.value\s*\}\s*", ""),
     "refresh() joins a load already in flight"),
    ("refresh-returns-after-wait", "refresh_orders_behind_running_load", "vm",
     _within(_REFRESH, r"(await running\.value\n)", r"\1            return\n"),
     "that is a join, not a reload"),
    ("refresh-never-loads", "refresh_orders_behind_running_load", "vm",
     _within(_REFRESH, r"\n\s*await load\(\)", ""), "does not load after the running load"),

    # ── 6. SignalLeader ──
    ("leader-uuid", "signal_leader_id_is_symbol", "models",
     _within(_LEADER, _E("var id: String { symbol }"), "let id = UUID()"), "mints a UUID()"),
    ("leader-id-not-symbol", "signal_leader_id_is_symbol", "models",
     _within(_LEADER, _E("var id: String { symbol }"), "var id: String { stat }"), "id is not its symbol"),
]

_MUTATION_ROWS = [m if len(m) == 5 else (*m, None) for m in MUTATIONS]


@pytest.mark.parametrize("label,guard,key,mutate,message", _MUTATION_ROWS, ids=[m[0] for m in _MUTATION_ROWS])
def test_every_guard_kills_its_mutation(label, guard, key, mutate, message):
    sources = _sources()
    mutated = dict(sources)
    mutated[key] = mutate(sources[key])
    assert mutated[key] != sources[key], f"mutation {label!r} changed nothing"
    # With a message, the guard must fail on THE assertion that names this bug, not trip an
    # unrelated earlier one.
    with pytest.raises(AssertionError, match=re.escape(message) if message else None):
        GUARDS[guard](mutated)


def test_every_guard_has_a_mutation():
    """A guard with no mutation is one nobody has seen fail."""
    covered = {row[1] for row in MUTATIONS}
    assert covered == set(GUARDS), f"guards with no mutation: {sorted(set(GUARDS) - covered)}"


def test_mutation_labels_are_unique():
    labels = [row[0] for row in MUTATIONS]
    assert len(labels) == len(set(labels)), "two mutation rows share a label"


def test_the_comment_stripper_actually_strips():
    """The CONTROL: every guarded token, written only in comments, must vanish."""
    prose = (
        "// guard !Task.isCancelled else { return }  loadGeneration &+= 1\n"
        "/// if self.loadTaskID == id {  var id: String { symbol }\n"
        "/* guard generation == loadGeneration else { return }\n"
        "   if let running = loadTask, !running.isCancelled { */\n"
        'let url = "https://example.com"  // loadTask = nil\n'
    )
    code = _strip(prose)
    for token in ("Task.isCancelled", "loadGeneration", "loadTaskID", "var id", "running", "loadTask"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"


def test_the_block_bound_actually_bounds():
    """The CONTROL for `_block`: a token in the NEXT function must not be read as this one's."""
    src = "func a() {\n    let x = 1\n}\nfunc b() {\n    loadGeneration &+= 1\n}\n"
    assert "loadGeneration" not in _block(src, "func a()")
    assert "loadGeneration" in _block(src, "func b()")
