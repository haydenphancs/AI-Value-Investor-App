"""A superseded session restore must never act on the session that replaced it.

`AppState.performRestore` validates the credential it found at launch (or on a heal trigger)
across up to three network awaits: `/users/me`, a token refresh, and `/users/me` again. An
interactive sign-in or a sign-out can land during any of them; both bump `credentialGeneration`
(auth.md §5), which the restore captured before its first await.

THE HOLE: the dead-refresh branch, where the stored refresh token is rejected and the restore
ends the session (Keychain clear, client disarm, `discardDataForEndedSession()`,
`.unauthenticated`), was the one exit that never compared that counter. Cold launch starts a
restore with account A's stale credential; while it waits the user signs in as B; A's refresh
then 401s and the stale restore signed B out, wiping B's fresh session and every device-global
store. The refresh-succeeded retry read had the same gap for a sign-out: it adopted the
ex-user's profile and drove the app back to `.authenticated`.

Source scan (there is no XCTest target), comments stripped and every check scoped to the
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` below breaks each property
once and asserts its guard fails; the same break was also made by hand in the real file.
The dead-refresh branch's `lastAuthenticatedUserId = nil` reset is also pinned by
`test_ios_widget_refresh_lifecycle.py`; here it only has to sit behind the guard.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_APP_STATE = (
    Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Core" / "State" / "AppState.swift"
)

_RESTORE = "private func performRestore(trigger: String) async"
_WINDOW = "private func enterRestoringWindow(generation: UInt64) async"

# Either operand order; the else body must `return` (a log-only `if` is not a guard).
_GEN_GUARD = re.compile(
    r"guard\s+(?:generation\s*==\s*credentialGeneration|credentialGeneration\s*==\s*generation)"
    r"\s+else\s*\{[^{}]*?\breturn\b[^{}]*\}",
    re.S,
)
_DEAD_BRANCH = re.compile(r"if\s+AppError\.from\(error\)\.isAuthError\s*\{")

# Suspension points that wait on the NETWORK, i.e. long enough for a sign-in to land.
_NETWORK_AWAITS = ("try await fetchCurrentUserNoRetry()", "try await authService.refreshToken()")
# What a restore does to the session. After a network await, each one needs a guard first.
_SINKS = (
    "applyProfile(",
    "establishAuthenticatedSession(",
    "discardDataForEndedSession()",
    "authService.clearToken()",
    "auth.status = .unauthenticated",
)
# The dead-refresh teardown, in full. Every one must come after the branch's guard.
_TEARDOWN = (
    "authService.clearToken()",
    "apiClient.setAuthToken(nil)",
    "user = UserState()",
    "lastAuthenticatedUserId = nil",
    "discardDataForEndedSession()",
    "invalidateIdentity(nil)",
    "auth.status = .unauthenticated",
    "cancelRestoreBackoff()",
)
# Must be present, or the branch no longer ends the session and the scan has drifted.
_TEARDOWN_REQUIRED = (
    "authService.clearToken()",
    "lastAuthenticatedUserId = nil",
    "discardDataForEndedSession()",
    "auth.status = .unauthenticated",
)


# ── Scanning helpers ─────────────────────────────────────────────────────────────────

def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fix's own comment names
    `credentialGeneration`, `discardDataForEndedSession` and `clearToken`, so an un-stripped
    scan would pass on prose.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _source() -> str:
    if not _APP_STATE.exists():
        pytest.fail(f"expected file is missing: {_APP_STATE}")
    return _strip(_APP_STATE.read_text(encoding="utf-8"))


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
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    return _block_at(src, start + len(header))


def _dead_refresh_branch(restore: str) -> str:
    refresh = restore.find("try await authService.refreshToken()")
    assert refresh != -1, "the refresh await moved — this scan has drifted"
    m = _DEAD_BRANCH.search(restore, refresh)
    assert m, "the dead-refresh branch moved — this scan has drifted"
    return _block_at(restore, m.start())


# ── The guards ───────────────────────────────────────────────────────────────────────
#
# Each takes the stripped source and raises AssertionError when its property is gone.

def _check_generation_captured_before_first_await(src: str) -> None:
    restore = _block(src, _RESTORE)
    captured = restore.find("let generation = credentialGeneration")
    first_await = re.search(r"\bawait\b", restore)
    assert captured != -1, "performRestore no longer captures `credentialGeneration`"
    assert first_await and captured < first_await.start(), (
        "`generation` is captured after an await — a sign-in that lands before the capture "
        "is invisible to every guard below"
    )


def _check_dead_refresh_branch_is_guarded_first(src: str) -> None:
    dead = _dead_refresh_branch(_block(src, _RESTORE))
    m = _GEN_GUARD.search(dead)
    assert m, (
        "the dead-refresh branch ends the session without `guard generation == "
        "credentialGeneration else { return }` — a restore superseded by a sign-in signs the "
        "NEW account out and wipes its device-global state"
    )
    for token in _TEARDOWN_REQUIRED:
        assert token in dead, f"`{token}` is gone from the dead-refresh branch — this scan has drifted"
    for token in _TEARDOWN:
        idx = dead.find(token)
        if idx != -1:
            assert idx > m.end(), (
                f"`{token}` runs before the dead-refresh branch's generation guard — the "
                "teardown reaches a session the restore no longer owns"
            )


def _check_every_effect_after_a_network_await_is_guarded(src: str) -> None:
    """The general form: no exit of performRestore acts on a session it may not own."""
    restore = _block(src, _RESTORE)
    awaits = sorted(m.start() for tok in _NETWORK_AWAITS for m in re.finditer(re.escape(tok), restore))
    assert len(awaits) >= 3, f"expected fetch, refresh, retry fetch — found {len(awaits)} network awaits"
    checked = 0
    for token in _SINKS:
        for sink in re.finditer(re.escape(token), restore):
            preceding = [a for a in awaits if a < sink.start()]
            if not preceding:
                continue  # the no-token branch: nothing has suspended yet
            segment = restore[preceding[-1] : sink.start()]
            assert _GEN_GUARD.search(segment), (
                f"`{token}` at offset {sink.start()} follows a network await with no generation "
                "guard between them — a sign-in or sign-out that landed during the await is undone"
            )
            checked += 1
    # Success applyProfile + establish, dead-refresh clearToken + discard + .unauthenticated,
    # retry applyProfile + establish. Fewer means the scan matched less than it should.
    assert checked >= 7, f"only {checked} post-await session effects found — this scan has drifted"


def _check_restoring_window_is_guarded(src: str) -> None:
    """The transient exits rely on `enterRestoringWindow` checking the CAPTURED generation."""
    window = _block(src, _WINDOW)
    m = _GEN_GUARD.search(window)
    disarm = window.find("setAuthToken(nil)")
    assert m and disarm != -1 and m.end() < disarm, (
        "enterRestoringWindow disarms the client before checking the generation — a restore "
        "superseded by a sign-in strips the new session's token"
    )
    restore = _block(src, _RESTORE)
    calls = restore.count("enterRestoringWindow(")
    captured = restore.count("enterRestoringWindow(generation: generation)")
    assert calls >= 3 and calls == captured, (
        f"{calls} enterRestoringWindow call(s), {captured} pass the captured `generation` — a "
        "live `credentialGeneration` always compares equal"
    )


GUARDS: dict[str, Callable[[str], None]] = {
    "captured_before_await": _check_generation_captured_before_first_await,
    "dead_refresh_guarded_first": _check_dead_refresh_branch_is_guarded_first,
    "every_effect_guarded": _check_every_effect_after_a_network_await_is_guarded,
    "restoring_window_guarded": _check_restoring_window_is_guarded,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_guard_holds_on_the_real_source(name):
    GUARDS[name](_source())


# ── Mutations: each one breaks a property and must fail its guard ─────────────────────

_G = r"guard\s+generation\s*==\s*credentialGeneration\s+else\s*\{[^{}]*\}"


def _in(header: str, edit: Callable[[str], str]) -> Callable[[str], str]:
    """Apply `edit` to one declaration's body only, so a mutation cannot leak elsewhere."""
    def mutate(src: str) -> str:
        body = _block(src, header)
        start = src.find(body, src.find(header))
        return src[:start] + edit(body) + src[start + len(body):]
    return mutate


def _sub_once(pattern: str, new: str) -> Callable[[str], str]:
    def edit(body: str) -> str:
        out, n = re.subn(pattern, new, body, count=1, flags=re.S)
        assert n == 1, f"mutation pattern {pattern!r} not found"
        return out
    return edit


def _capture_late(body: str) -> str:
    assert "let generation = credentialGeneration" in body
    body = body.replace("let generation = credentialGeneration", "", 1)
    return body.replace(
        "await apiClient.setAuthToken(token)",
        "await apiClient.setAuthToken(token)\n        let generation = credentialGeneration",
        1,
    )


# `isAuthError\s*\{` skips the first catch's `guard AppError.from(error).isAuthError else {`,
# so the first match is the dead-refresh branch.
MUTATIONS: list[tuple[str, str, Callable[[str], str]]] = [
    ("dead-unguarded", "dead_refresh_guarded_first",
     _in(_RESTORE, _sub_once(r"(isAuthError\s*\{\s*)" + _G, r"\1"))),
    ("dead-unguarded-general", "every_effect_guarded",
     _in(_RESTORE, _sub_once(r"(isAuthError\s*\{\s*)" + _G, r"\1"))),
    ("dead-guard-after-clear", "dead_refresh_guarded_first",
     _in(_RESTORE, _sub_once(r"(isAuthError\s*\{\s*)(" + _G + r")(\s*)authService\.clearToken\(\)",
                             r"\1authService.clearToken()\3\2"))),
    ("dead-live-read", "dead_refresh_guarded_first",
     _in(_RESTORE, _sub_once(r"(isAuthError\s*\{\s*guard\s+)generation(\s*==)", r"\1credentialGeneration\2"))),
    ("dead-log-only", "dead_refresh_guarded_first",
     _in(_RESTORE, _sub_once(r"(isAuthError\s*\{\s*)guard\s+generation\s*==\s*credentialGeneration\s+else"
                             r"\s*\{([^{}]*?)return\s*\}",
                             r"\1if generation != credentialGeneration {\2}"))),
    ("dead-no-reset", "dead_refresh_guarded_first",
     _in(_RESTORE, _sub_once(r"(refreshToken\(\).*?)lastAuthenticatedUserId = nil", r"\1"))),
    ("success-unguarded", "every_effect_guarded",
     _in(_RESTORE, _sub_once(r"(try await fetchCurrentUserNoRetry\(\)\s*)" + _G, r"\1"))),
    ("retry-unguarded", "every_effect_guarded",
     _in(_RESTORE, _sub_once(r"(try await authService\.refreshToken\(\).*try await fetchCurrentUserNoRetry\(\)\s*)"
                             + _G, r"\1"))),
    ("capture-late", "captured_before_await", _in(_RESTORE, _capture_late)),
    ("window-unguarded", "restoring_window_guarded", _in(_WINDOW, _sub_once(_G, ""))),
    ("window-live-arg", "restoring_window_guarded",
     _in(_RESTORE, _sub_once(r"enterRestoringWindow\(generation: generation\)",
                             "enterRestoringWindow(generation: credentialGeneration)"))),
]


@pytest.mark.parametrize("label,guard,mutate", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, mutate):
    source = _source()
    mutated = mutate(source)
    assert mutated != source, f"mutation {label!r} changed nothing — it no longer matches the source"
    with pytest.raises(AssertionError):
        GUARDS[guard](mutated)


def test_every_guard_has_a_mutation():
    """A guard with no mutation is one nobody has seen fail."""
    covered = {guard for _, guard, _ in MUTATIONS}
    assert covered == set(GUARDS), f"guards with no mutation: {sorted(set(GUARDS) - covered)}"


def test_the_comment_stripper_actually_strips():
    """The CONTROL: every guarded token, written only in comments, must vanish."""
    prose = (
        "// guard generation == credentialGeneration else { return }\n"
        "/// discardDataForEndedSession()  authService.clearToken()\n"
        "/* let generation = credentialGeneration\n   lastAuthenticatedUserId = nil */\n"
        'let url = "https://example.com"  // applyProfile(profile)\n'
    )
    code = _strip(prose)
    for token in ("credentialGeneration", "discardDataForEndedSession", "clearToken",
                  "lastAuthenticatedUserId", "applyProfile"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"
