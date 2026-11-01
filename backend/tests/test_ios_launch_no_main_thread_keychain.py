"""Cold launch makes NO synchronous Keychain read, and no App Group call, on the main thread.

`AppState.configure`'s launch `Task` runs on the MAIN actor. Until 2026-11-01 it called the
synchronous `SecItemCopyMatching` (a round trip to securityd) five times for the same
`access_token` before Home's first frame: `primeStoredCredential`, `authService.hasStoredToken`
(the widget session gate), `storedCredentialSubject()`, the
`getStoredToken().flatMap` owner handed to Home's prime, and `performRestore`. It also made its
App Group `UserDefaults(suiteName:)` calls there (`WidgetAPIConfig.publishBaseURL`,
`WidgetSnapshotStore.migrateLegacyIfNeeded`, and `hasAnyState` on a signed-out launch), each a
cfprefsd round trip. Sentry CAYDEX-APPLE-IOS-S (release 1.02 (13)) was a 7 s "App Hang Fully
Blocked" from the same Task, caused by a stalled `stat()`. A stalled securityd or cfprefsd would freeze the
splash the same way: no frame drawn for as long as the stall lasts.

The fix, pinned here:
  * the launch reads the token ONCE, through `AuthService.loadStoredToken()` (a `Task.detached`
    read through the `nonisolated`, `Sendable` `KeychainService`), with `credentialGeneration`
    captured before it, and every launch consumer takes that one value — the arm, the widget
    gate, the snapshot owner and Home's prime;
  * the arm is fenced on that generation: a sign-in that landed during the off-main read
    installed a newer credential, and arming the launch copy over it would strip that session's
    token off the client (auth.md §8);
  * `performRestore` keeps its OWN read — a refresh rotates the access token without bumping
    `credentialGeneration`, so re-arming the launch copy could put a superseded token back —
    but reads off main too, and re-checks the generation BEFORE either exit acts on it;
  * the App Group calls run inside an awaited `Task.detached` that ends before the widget
    session opener, the credential gate and the seed (order unchanged), and the signed-out
    restore's `hasAnyState` read likewise, with a generation re-check after it. The orphan
    CLEAR (`clearOrphanedState`, once per device, the pinned sign-out teardown) stays on main
    by decision.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails WITH the assertion that names the bug (rule 3, kept in-suite so a
guard that goes vacuous later fails the build too).

MUTATION_LOG (in-suite, every run; first run 2026-11-01 — all killed).
CONTROL: `test_the_comment_stripper_actually_strips`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_FILES = {
    "app": _IOS / "Core/State/AppState.swift",
    "auth": _IOS / "Core/Services/AuthService.swift",
}

_CONFIGURE = "func configure(apiClient: APIClient, authService: AuthService)"
_RESTORE = "private func performRestore(trigger: String) async"
_RESTORE_SESSION = "func restoreSession(trigger: String) async"
_PRIME_HELPER = "private func primeStoredCredential(_ token: String?, readUnder generation: UInt64) async"
_SUBJECT_HELPER = "private func storedCredentialSubject(of token: String?) -> String?"
_LOAD = "func loadStoredToken() async -> String?"
_KEYCHAIN = "nonisolated final class KeychainService: Sendable"

# A synchronous Keychain read, spelled any way the app spells one.
_SYNC_KEYCHAIN = (
    "getStoredToken(",
    "hasStoredToken",
    "getStoredRefreshToken(",
    "hasUnusedStoredCredential",
    "keychain.",
    "KeychainService",
    "SecItemCopyMatching",
)
# An App Group call (every one is a cfprefsd round trip).
_APP_GROUP = ("WidgetSnapshotStore.", "WidgetAPIConfig.", "WidgetSharedDefaults", "UserDefaults(suiteName")
# A returning guard on the generation captured before the suspension (a log-only `if` is not one).
_GEN_GUARD = re.compile(
    r"guard\s+(?:generation\s*==\s*credentialGeneration|credentialGeneration\s*==\s*generation)"
    r"\s+else\s*\{[^{}]*?\breturn\b[^{}]*\}",
    re.S,
)
_DETACHED = re.compile(r"await\s+Task\.detached\b[^{]*\{")


# ── Scanning helpers (testing.md: no shared conftest, so each guard file carries its own) ──

def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fix's own comments name
    `getStoredToken()`, `hasStoredToken` and `migrateLegacyIfNeeded`, so an un-stripped scan
    would pass (or fail) on prose.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


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


def _idx(src: str, token: str, start: int = 0) -> int:
    i = src.find(token, start)
    assert i != -1, f"{token!r} not found"
    return i


@lru_cache(maxsize=1)
def _real_sources() -> tuple[tuple[str, str], ...]:
    return tuple((key, _strip(_read(path))) for key, path in _FILES.items())


def _sources() -> dict[str, str]:
    return dict(_real_sources())


def _launch_task(app: str) -> str:
    """The launch `Task { … }` in `configure`: the one whose body arms the credential.

    The two notification observers spawn `Task { @MainActor … }` and are skipped by the regex.
    """
    cfg = _block(app, _CONFIGURE)
    for m in re.finditer(r"\bTask\s*\{\s*\n", cfg):
        body = _block_at(cfg, m.start())
        if "primeStoredCredential(" in body:
            return body
    raise AssertionError("the launch Task that arms the stored credential is gone — this scan has drifted")


def _detached_spans(body: str) -> list[tuple[int, int]]:
    """(start, end) of every `await Task.detached { … }.value` closure in `body`.

    Only an AWAITED detached task counts: a fire-and-forget one loses its order against the
    seed, and one that is not awaited for its `.value` reads nothing back.
    """
    spans = []
    for m in _DETACHED.finditer(body):
        closure = _block_at(body, m.start())
        start = body.find(closure, m.start())
        end = start + len(closure)
        if re.match(r"\s*\.value\b", body[end:]):
            spans.append((m.start(), end))
    return spans


def _outside(body: str, spans: list[tuple[int, int]]) -> str:
    """`body` with every span blanked (same length, so offsets stay valid)."""
    out = body
    for start, end in spans:
        out = out[:start] + " " * (end - start) + out[end:]
    return out


def _no_sync_keychain(where: str, body: str) -> None:
    for tok in _SYNC_KEYCHAIN:
        assert tok not in body, (
            f"{where} reads the Keychain synchronously on the main actor (`{tok}`) — a stalled "
            "securityd freezes the cold-launch splash; read through `await authService.loadStoredToken()`"
        )


# ── The guards ───────────────────────────────────────────────────────────────────────

def _check_launch_reads_once_off_main(s: dict[str, str]) -> None:
    launch = _launch_task(s["app"])
    _no_sync_keychain("the launch Task", launch)
    reads = launch.count("loadStoredToken(")
    m = re.search(r"let\s+(\w+)\s*=\s*await\s+authService\.loadStoredToken\(\)", launch)
    assert m and reads == 1, (
        f"the launch Task no longer reads the stored token exactly once through "
        f"`let … = await authService.loadStoredToken()` ({reads} read(s))"
    )
    # Everything the launch does with the credential follows the read.
    first_use = min(_idx(launch, tok) for tok in ("primeStoredCredential(", "openSession()",
                                                    "storedCredentialSubject(", "restoreSession("))
    assert m.end() <= first_use, "the launch Task uses the stored credential before it has read it"


def _check_launch_consumers_take_the_one_read(s: dict[str, str]) -> None:
    launch = _launch_task(s["app"])
    m = re.search(r"let\s+(\w+)\s*=\s*await\s+authService\.loadStoredToken\(\)", launch)
    assert m, "the launch Task's one Keychain read is gone — this scan has drifted"
    tok = m.group(1)
    gen = re.search(r"let\s+(\w+)\s*=\s*credentialGeneration\b", launch)
    assert gen and gen.end() <= m.start(), (
        "the launch Task no longer captures `credentialGeneration` BEFORE its Keychain read — a "
        "sign-in during the read is laundered into the capture, and the arm overwrites it"
    )
    arm = re.search(rf"await\s+primeStoredCredential\(\s*{tok}\s*,\s*readUnder:\s*{gen.group(1)}\s*\)", launch)
    assert arm, (
        f"the launch arm is not `primeStoredCredential({tok}, readUnder: {gen.group(1)})` — it "
        "must take the launch read AND the generation captured before it"
    )
    opener = re.search(rf"if\s+{tok}\s*!=\s*nil\s*\{{", launch)
    assert opener and "WidgetRefreshService.shared.openSession()" in _block_at(launch, opener.start()), (
        f"the widget session gate is not opened on the launch read (`if {tok} != nil {{ … openSession() }}`)"
    )
    assert re.search(rf"storedCredentialSubject\(of:\s*{tok}\)", launch), (
        f"the account-snapshot owner is not derived from the launch read (`storedCredentialSubject(of: {tok})`)"
    )
    prime = launch[_idx(launch, "HomeDashboardSnapshotStore.shared.prime("):]
    prime = prime[: _idx(prime, "apiClient:")]
    assert re.search(rf"{tok}\.flatMap\s*\{{\s*WidgetJWT\.subject\(of:\s*\$0\)\s*\}}", prime), (
        f"Home's prime owner is not derived from the launch read (`{tok}.flatMap {{ WidgetJWT.subject(of: $0) }}`)"
    )


def _check_launch_helpers_read_no_keychain(s: dict[str, str]) -> None:
    app = s["app"]
    prime = _block(app, _PRIME_HELPER)
    subject = _block(app, _SUBJECT_HELPER)
    for name, body in (("primeStoredCredential", prime), ("storedCredentialSubject", subject),
                       ("restoreSession", _block(app, _RESTORE_SESSION))):
        _no_sync_keychain(name, body)
    fence = re.search(r"guard\s+let\s+token\s*,\s*generation\s*==\s*credentialGeneration\s+else\s*\{\s*return\s*\}", prime)
    arm = prime.find("setAuthToken(")
    assert fence and arm != -1 and fence.end() <= arm, (
        "primeStoredCredential arms the launch read without checking the credential generation — "
        "a sign-in that landed during the off-main read would have its token overwritten (auth.md §8)"
    )


def _check_load_is_detached(s: dict[str, str]) -> None:
    auth = s["auth"]
    body = _block(auth, _LOAD)
    spans = _detached_spans(body)
    assert len(spans) == 1, (
        "loadStoredToken no longer reads inside ONE awaited `Task.detached { … }.value` — the "
        "Keychain read runs on the caller's actor, the main one"
    )
    start, end = spans[0]
    closure = body[start:end]
    assert closure.count("keychain.get(") == 1 and _outside(body, spans).count("keychain.") == 0, (
        "loadStoredToken reads the Keychain outside its detached closure"
    )
    for hop in ("@MainActor", "MainActor.run", "MainActor.assumeIsolated"):
        assert hop not in body, f"loadStoredToken's detached read hops back to the main actor (`{hop}`)"
    assert _KEYCHAIN in auth and "func get(_ key: String) -> String?" in _block(auth, _KEYCHAIN), (
        "KeychainService is no longer `nonisolated` + `Sendable` with its `get` — the detached read "
        "would have to hop back to the main actor for it"
    )


def _check_restore_reads_off_main_and_rechecks(s: dict[str, str]) -> None:
    restore = _block(s["app"], _RESTORE)
    _no_sync_keychain("performRestore", restore)
    read = re.search(r"let\s+(\w+)\s*=\s*await\s+authService\.loadStoredToken\(\)", restore)
    captured = restore.find("let generation = credentialGeneration")
    assert read and captured != -1 and captured < read.start(), (
        "performRestore no longer reads the stored token off main (`let … = await "
        "authService.loadStoredToken()`) after capturing `credentialGeneration`"
    )
    unwrap = re.search(rf"guard\s+let\s+token\s*=\s*{read.group(1)}\s+else\s*\{{", restore)
    assert unwrap, "performRestore no longer unwraps the read it just made — this scan has drifted"
    between = restore[read.end() : unwrap.start()]
    assert _GEN_GUARD.search(between), (
        "performRestore acts on its Keychain read without re-checking the generation — a sign-in "
        "or sign-out that landed during the off-main read is undone (arm, `.restoring`, `.unauthenticated`)"
    )
    no_token = _block_at(restore, unwrap.start())
    spans = _detached_spans(no_token)
    assert not any(tok in _outside(no_token, spans) for tok in _APP_GROUP), (
        "the signed-out restore reads the App Group on the main actor — `WidgetSnapshotStore.hasAnyState` "
        "belongs inside an awaited `Task.detached`"
    )
    reads = [(a, b) for a, b in spans if "WidgetSnapshotStore.hasAnyState" in no_token[a:b]]
    assert reads, "the signed-out restore no longer checks `WidgetSnapshotStore.hasAnyState` — this scan has drifted"
    after = no_token[reads[0][1]:]
    effects = [after.find(tok) for tok in ("clearOrphanedState(", "resolveIdentity(", "auth.status", "cancelRestoreBackoff(")]
    first_effect = min(i for i in effects if i != -1)
    assert _GEN_GUARD.search(after[:first_effect]), (
        "the signed-out restore acts after its widget-state read without re-checking the generation — "
        "a sign-in that landed during it is driven back to `.unauthenticated`"
    )


def _check_app_group_off_main_before_seed(s: dict[str, str]) -> None:
    launch = _launch_task(s["app"])
    spans = _detached_spans(launch)
    stray = [tok for tok in _APP_GROUP if tok in _outside(launch, spans)]
    assert not stray, (
        f"the launch Task makes an App Group call outside an awaited `Task.detached` ({stray}) — on "
        "the main actor, or unordered against the seed when the task is not awaited"
    )
    for call in ("WidgetAPIConfig.publishBaseURL(", "WidgetSnapshotStore.migrateLegacyIfNeeded()"):
        hosts = [(a, b) for a, b in spans if call in launch[a:b]]
        assert len(hosts) == 1 and launch.count(call) == 1, (
            f"`{call}` no longer runs exactly once, inside an awaited `Task.detached` in the launch Task"
        )
        start, end = hosts[0]
        for hop in ("@MainActor", "MainActor.run", "MainActor.assumeIsolated"):
            assert hop not in launch[start:end], f"the launch's App Group task hops back to the main actor (`{hop}`)"
        for later in ("WidgetRefreshService.shared.openSession()", "WidgetRefreshService.shared.markCredentialReady()",
                      "WidgetRefreshService.shared.refresh(identity:"):
            assert end <= _idx(launch, later), (
                f"`{call}` no longer finishes before `{later}` — the seed must land in the migrated "
                "v2 slots, against the published base URL"
            )


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "launch_reads_once": _check_launch_reads_once_off_main,
    "launch_consumers": _check_launch_consumers_take_the_one_read,
    "launch_helpers": _check_launch_helpers_read_no_keychain,
    "load_detached": _check_load_is_detached,
    "restore_off_main": _check_restore_reads_off_main_and_rechecks,
    "app_group_off_main": _check_app_group_off_main_before_seed,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_guard_holds_on_the_real_source(name):
    GUARDS[name](_sources())


# ── Mutations: each breaks one property and must fail the assertion that names it ─────

def _sub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


_READ_LINE = r"(let launchToken = await authService\.loadStoredToken\(\)\n)"
_APP_GROUP_TASK = (r"await Task\.detached\(priority: \.userInitiated\) \{\n"
                   r"(\s*)WidgetAPIConfig\.publishBaseURL\(widgetBaseURL\)\n"
                   r"(\s*\n)*\s*WidgetSnapshotStore\.migrateLegacyIfNeeded\(\)\n\s*\}\.value\n")

MUTATIONS: list[tuple] = [
    # (label, guard, file key, mutate, message the failing assertion must carry)
    ("sync-read-in-launch", "launch_reads_once", "app",
     _sub(_READ_LINE, r"\1            _ = authService.getStoredToken()\n"),
     "the launch Task reads the Keychain synchronously"),
    ("sync-opener", "launch_reads_once", "app",
     _sub(r"if launchToken != nil \{", "if authService.hasStoredToken {"),
     "the launch Task reads the Keychain synchronously"),
    ("sync-home-owner", "launch_reads_once", "app",
     _sub(r"ownerUserId: launchToken\.flatMap", "ownerUserId: authService.getStoredToken().flatMap"),
     "the launch Task reads the Keychain synchronously"),
    ("second-read", "launch_reads_once", "app",
     _sub(r"storedCredentialSubject\(of: launchToken\)", "storedCredentialSubject(of: await authService.loadStoredToken())"),
     "exactly once"),
    ("arm-before-read", "launch_reads_once", "app",
     _sub(_READ_LINE + r"([ \t]*await primeStoredCredential\(launchToken, readUnder: launchGeneration\)\n)", r"\2\1"),
     "uses the stored credential before it has read it"),
    ("generation-after-read", "launch_consumers", "app",
     _sub(r"(let launchGeneration = credentialGeneration\n)(\s*)" + _READ_LINE, r"\3\2\1"),
     "BEFORE its Keychain read"),
    ("arm-without-generation", "launch_consumers", "app",
     _sub(r"primeStoredCredential\(launchToken, readUnder: launchGeneration\)",
          "primeStoredCredential(launchToken, readUnder: credentialGeneration)"),
     "the launch arm is not"),
    ("opener-on-other-value", "launch_consumers", "app",
     _sub(r"if launchToken != nil \{", "if accountSnapshotOwner != nil {"),
     "the widget session gate is not opened on the launch read"),
    ("owner-from-elsewhere", "launch_consumers", "app",
     _sub(r"storedCredentialSubject\(of: launchToken\)", "storedCredentialSubject(of: nil)"),
     "the account-snapshot owner is not derived from the launch read"),
    ("home-owner-dropped", "launch_consumers", "app",
     _sub(r"ownerUserId: launchToken\.flatMap \{ WidgetJWT\.subject\(of: \$0\) \}", "ownerUserId: nil"),
     "Home's prime owner is not derived from the launch read"),
    ("helper-reads", "launch_helpers", "app",
     _sub(r"guard let token else \{ return nil \}\n(\s*)return WidgetJWT",
          r"guard let token = token ?? authService.getStoredToken() else { return nil }\n\1return WidgetJWT"),
     "storedCredentialSubject reads the Keychain synchronously"),
    ("prime-unfenced", "launch_helpers", "app",
     _sub(r"guard let token, generation == credentialGeneration else \{ return \}", "guard let token else { return }"),
     "arms the launch read without checking the credential generation"),
    ("load-on-caller", "load_detached", "auth",
     _sub(r"return await Task\.detached\(priority: \.userInitiated\) \{\n\s*keychain\.get\(key\)\n\s*\}\.value",
          "return keychain.get(key)"),
     "loadStoredToken no longer reads inside ONE awaited"),
    ("load-fire-and-forget", "load_detached", "auth",
     _sub(r"(\{\n\s*keychain\.get\(key\)\n\s*\})\.value", r"\1.result.get()"),
     "loadStoredToken no longer reads inside ONE awaited"),
    ("load-main-hop", "load_detached", "auth",
     _sub(r"(Task\.detached\(priority: \.userInitiated\) \{)", r"\1 @MainActor in"),
     "hops back to the main actor"),
    ("keychain-isolated", "load_detached", "auth",
     _sub(r"nonisolated final class KeychainService: Sendable", "final class KeychainService: Sendable"),
     "KeychainService is no longer `nonisolated`"),
    ("restore-sync-read", "restore_off_main", "app",
     _sub(r"let storedToken = await authService\.loadStoredToken\(\)", "let storedToken = authService.getStoredToken()"),
     "performRestore reads the Keychain synchronously"),
    ("restore-read-unfenced", "restore_off_main", "app",
     _sub(r"(let storedToken = await authService\.loadStoredToken\(\)\n)\s*guard generation == credentialGeneration else \{[^{}]*\}\n",
          r"\1"),
     "acts on its Keychain read without re-checking the generation"),
    ("restore-read-log-only", "restore_off_main", "app",
     _sub(r"(let storedToken = await authService\.loadStoredToken\(\)\n\s*)guard generation == credentialGeneration else \{([^{}]*?)return\n(\s*)\}",
          r"\1if generation != credentialGeneration {\2\3}"),
     "acts on its Keychain read without re-checking the generation"),
    ("orphan-check-on-main", "restore_off_main", "app",
     _sub(r"await Task\.detached\(priority: \.userInitiated\) \{\n\s*WidgetSnapshotStore\.hasAnyState\n\s*\}\.value",
          "WidgetSnapshotStore.hasAnyState"),
     "reads the App Group on the main actor"),
    ("orphan-unfenced", "restore_off_main", "app",
     _sub(r"(WidgetSnapshotStore\.hasAnyState\n\s*\}\.value\n)\s*guard generation == credentialGeneration else \{[^{}]*\}\n",
          r"\1"),
     "acts after its widget-state read without re-checking the generation"),
    ("publish-on-main", "app_group_off_main", "app",
     _sub(r"(await Task\.detached\(priority: \.userInitiated\) \{\n)(\s*)(WidgetAPIConfig\.publishBaseURL\(widgetBaseURL\)\n)",
          r"\3\2\1"),
     "makes an App Group call outside an awaited"),
    ("migrate-on-main", "app_group_off_main", "app",
     _sub(r"(\n\s*WidgetSnapshotStore\.migrateLegacyIfNeeded\(\))(\n\s*\}\.value)", r"\2\1"),
     "makes an App Group call outside an awaited"),
    ("app-group-not-awaited", "app_group_off_main", "app",
     _sub(r"await (Task\.detached\(priority: \.userInitiated\) \{\n\s*WidgetAPIConfig\.publishBaseURL.*?\})\.value",
          r"\1"),
     "makes an App Group call outside an awaited"),
    ("app-group-main-hop", "app_group_off_main", "app",
     _sub(r"(await Task\.detached\(priority: \.userInitiated\) \{)(\n\s*WidgetAPIConfig\.publishBaseURL)", r"\1 @MainActor in\2"),
     "hops back to the main actor"),
    ("app-group-after-seed", "app_group_off_main", "app",
     _sub(r"([ \t]*let widgetBaseURL = [^\n]*\n[ \t]*" + _APP_GROUP_TASK + r")(.*?)"
          r"([ \t]*WidgetRefreshService\.shared\.refresh\(identity: identityGeneration\)\n)", r"\4\3\1"),
     "no longer finishes before"),
]


@pytest.mark.parametrize("label,guard,key,mutate,message", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, key, mutate, message):
    sources = _sources()
    mutated = dict(sources)
    mutated[key] = mutate(sources[key])
    assert mutated[key] != sources[key], f"mutation {label!r} changed nothing"
    # The guard must fail on THE assertion that names this bug, not trip an unrelated earlier one.
    with pytest.raises(AssertionError, match=re.escape(message)):
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
        "// let token = authService.getStoredToken()\n"
        "/// if authService.hasStoredToken { WidgetRefreshService.shared.openSession() }\n"
        "/* WidgetSnapshotStore.migrateLegacyIfNeeded()\n"
        "   SecItemCopyMatching(query as CFDictionary, &result) */\n"
        'let url = "https://example.com"  // WidgetAPIConfig.publishBaseURL(url)\n'
    )
    code = _strip(prose)
    for token in ("getStoredToken", "hasStoredToken", "migrateLegacyIfNeeded", "SecItemCopyMatching",
                  "WidgetAPIConfig"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"
