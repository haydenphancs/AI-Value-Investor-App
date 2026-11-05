"""The on-device snapshot stores make NO file-system call on the main thread.

Sentry CAYDEX-APPLE-IOS-S (2026-11-01, release 1.02 (13), one real device on iOS 27.0.1): "App
Hang Fully Blocked — App hanging between 7.0 and 7.8 seconds" at cold launch. The hang
tracker's one main-thread stack sat in a `stat()` syscall:

    AppState.configure's launch Task -> UpdatesFeedSnapshotStore.shared (a lazy @MainActor
    global, built on first access) -> AccountSnapshotConfig.defaultDirectory ->
    FileManager.default.urls(for: .cachesDirectory, ...) -> String.homeDirectoryPath -> stat

The OS did not answer a file-system lookup for seconds, and because the lookup ran while the
store was BUILT on the main actor, no frame was drawn. Tracking's store and Home's
`static let shared` did the same a line later. The fix: a store holds an unresolved file
location (`AccountSnapshotFileLocation` / `HomeDashboardSnapshotFile`), and only the `*Disk`
read / write / delete — which run solely on each store's detached disk tail — resolve it.

What this file pins, for the Updates, Tracking and Home stores:
  * each `.shared` instance is built with `persistsToDisk: true` and nothing that touches the
    file system; the payload files never touch it at all;
  * neither store's `init`, nor anything in its `@MainActor` class body, makes a file-system call;
  * the stores hold the location type, and that type is a `nonisolated` Sendable struct;
  * `.resolve()` runs only inside the `*Disk` read / write / delete, once each, and the snapshot
    directories are read nowhere in the app but inside `resolve()`;
  * each `resolve()` traps on the main queue in DEBUG only (`dispatchPrecondition` traps in -O
    builds too);
  * `AppState.configure` itself makes no file-system call.

The existing text pins (owner fence, epoch, the one serial tail) stay in
`test_ios_account_snapshot_guards.py` and `test_ios_home_instant_paint_guards.py`.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails with the assertion that names the bug.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Callable

import pytest

_IOS_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_IOS = _IOS_ROOT / "ios"

_FILES = {
    "store": _IOS / "Core/Repositories/AccountSnapshotStore.swift",
    "updates_payload": _IOS / "Core/Repositories/UpdatesFeedSnapshot.swift",
    "tracking_payload": _IOS / "Core/Repositories/TrackingSnapshot.swift",
    "home_store": _IOS / "Core/Repositories/HomeDashboardSnapshotStore.swift",
    "app": _IOS / "Core/State/AppState.swift",
}

_ACCOUNT_CLASS = "final class AccountSnapshotStore<Payload: AccountSnapshotPayload>"
_HOME_CLASS = "final class HomeDashboardSnapshotStore"
_ACCOUNT_INIT = "init(config: AccountSnapshotConfig, persistsToDisk: Bool)"
_HOME_INIT = "init(persistsToDisk: Bool)"
_ACCOUNT_DISK = "nonisolated enum AccountSnapshotDisk"
_HOME_DISK = "nonisolated enum HomeDashboardSnapshotDisk"
_ACCOUNT_LOCATION = "nonisolated struct AccountSnapshotFileLocation: Sendable"
_HOME_LOCATION = "nonisolated struct HomeDashboardSnapshotFile: Sendable"
_RESOLVE = "func resolve() -> URL?"
_HOME_DIR_DECL = "nonisolated static var defaultDirectory: URL?"
_CONFIGURE = "func configure(apiClient: APIClient, authService: AuthService)"

_UPDATES_GLOBAL = "@MainActor private let updatesFeedSnapshotStoreInstance = "
_TRACKING_GLOBAL = "@MainActor private let trackingSnapshotStoreInstance = "
_HOME_SHARED = "static let shared = "

# Anything that reaches the file system (or the sandbox's path lookup) synchronously.
_FS_TOKENS = (
    "FileManager",
    "defaultDirectory",
    "cachesDirectory",
    ".resolve(",
    "fileExists",
    "Data(contentsOf:",
    ".write(to:",
    "NSHomeDirectory",
    "NSSearchPathForDirectoriesInDomains",
    "containerURL(",
)

# A read of a snapshot store's directory, anywhere in the app.
_SNAPSHOT_DIR_READ = re.compile(
    r"(?:AccountSnapshotConfig|HomeDashboardSnapshotStore|\.updatesFeed|\.tracking|config)\s*\.\s*defaultDirectory\b"
)


# ── Scanning helpers (copied from the snapshot guard files; testing.md: no shared conftest) ──

def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fix's own comments name
    `FileManager`, `defaultDirectory` and `resolve()`, so an un-stripped scan would pass or fail
    on prose.
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


@lru_cache(maxsize=1)
def _other_swift_sources() -> tuple[tuple[str, str], ...]:
    """Every other Swift file of the app, the Shared folder and the widget, comment-stripped."""
    pinned = {p.resolve() for p in _FILES.values()}
    out = []
    for path in sorted(_IOS_ROOT.rglob("*.swift")):
        if path.resolve() in pinned or "DerivedData" in path.parts or ".build" in path.parts:
            continue
        out.append((str(path.relative_to(_IOS_ROOT)), _strip(path.read_text(encoding="utf-8"))))
    assert len(out) > 100, f"only {len(out)} Swift files found under {_IOS_ROOT} — this scan has drifted"
    return tuple(out)


def _span_at(src: str, idx: int, open_ch: str = "{", close_ch: str = "}") -> tuple[int, int]:
    """[start, end) of the balanced block opened by the first `open_ch` at or after `idx`."""
    start = src.find(open_ch, idx)
    assert start != -1, f"no {open_ch!r} opens after offset {idx}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise AssertionError(f"unbalanced {open_ch}{close_ch} after offset {idx}")


def _span(src: str, header: str) -> tuple[int, int]:
    assert src.count(header) == 1, f"expected exactly one {header!r}, found {src.count(header)}"
    return _span_at(src, src.find(header) + len(header))


def _block(src: str, header: str) -> str:
    start, end = _span(src, header)
    return src[start:end]


def _norm(src: str) -> str:
    """Whitespace-collapsed, so an exact-expression check survives re-indentation only."""
    return " ".join(src.split())


def _fs_hits(src: str) -> list[str]:
    return [t for t in _FS_TOKENS if t in src]


def _initializer(src: str, header: str) -> str:
    """`header` plus the constructor call that follows it, through its balanced `( … )`."""
    assert src.count(header) == 1, f"expected exactly one {header!r}, found {src.count(header)}"
    at = src.find(header)
    _, end = _span_at(src, at + len(header), "(", ")")
    return src[at:end]


def _disk_ops(src: str, disk_header: str) -> dict[str, str]:
    disk = _block(src, disk_header)
    return {op: _block(disk, f"static func {op}(") for op in ("read", "write", "delete")}


def _without(src: str, header: str) -> str:
    """`src` minus the declaration `header` and its block."""
    start, end = _span(src, header)
    return src[: src.find(header)] + src[end:]


# ── Guards ───────────────────────────────────────────────────────────────────────────

def _check_shared_initialisers(s):
    for key, header, want in (
        ("updates_payload", _UPDATES_GLOBAL, "UpdatesFeedSnapshotStore( config: .updatesFeed, persistsToDisk: true )"),
        ("tracking_payload", _TRACKING_GLOBAL, "TrackingSnapshotStore( config: .tracking, persistsToDisk: true )"),
    ):
        name = header.split("let ")[1].split(" =")[0]
        expr = _norm(_initializer(s[key], header)[len(header):])
        assert expr == want, (
            f"{name} is no longer built as `{want}` — a `.shared` initializer runs on the main "
            f"actor at launch, so it must not resolve a directory (Sentry CAYDEX-APPLE-IOS-S); got `{expr}`"
        )
        hits = _fs_hits(s[key])
        assert not hits, f"{_FILES[key].name} touches the file system ({hits}) — a payload file never should"
    home = _block(s["home_store"], _HOME_CLASS)
    shared = _norm(_initializer(home, _HOME_SHARED))
    assert shared == "static let shared = HomeDashboardSnapshotStore(persistsToDisk: true)", (
        f"Home's `shared` is no longer built with `persistsToDisk: true` alone — it resolves its "
        f"directory on the main actor at launch (Sentry CAYDEX-APPLE-IOS-S); got `{shared}`"
    )


def _check_store_inits(s):
    for key, cls, init in (("store", _ACCOUNT_CLASS, _ACCOUNT_INIT), ("home_store", _HOME_CLASS, _HOME_INIT)):
        body = _block(_block(s[key], cls), init)
        hits = _fs_hits(body) + (["appendingPathComponent"] if "appendingPathComponent" in body else [])
        assert not hits, f"{_FILES[key].name}'s `{init}` touches the file system ({hits}) — it runs on the main actor"


def _check_class_bodies(s):
    account = _block(s["store"], _ACCOUNT_CLASS)
    hits = _fs_hits(account)
    assert not hits, (
        f"AccountSnapshotStore's @MainActor body makes a file-system call ({hits}) — every disk "
        "operation, the Caches lookup included, belongs on the detached disk tail"
    )
    home_cls = _block(s["home_store"], _HOME_CLASS)
    home = _without(home_cls, _HOME_DIR_DECL)
    hits = _fs_hits(home)
    assert not hits, (
        f"HomeDashboardSnapshotStore's @MainActor body makes a file-system call ({hits}) — every "
        "disk operation, the Caches lookup included, belongs on the detached disk tail"
    )


def _check_holds_location(s):
    account = _block(s["store"], _ACCOUNT_CLASS)
    assert "private let fileURL: AccountSnapshotFileLocation?" in account, (
        "AccountSnapshotStore holds a resolved URL again — resolving it means a file-system call at construction"
    )
    home = _block(s["home_store"], _HOME_CLASS)
    assert "private let fileURL: HomeDashboardSnapshotFile?" in home, (
        "HomeDashboardSnapshotStore holds a resolved URL again — resolving it means a file-system call at construction"
    )
    for key, decl in (("store", _ACCOUNT_LOCATION), ("home_store", _HOME_LOCATION)):
        assert s[key].count(decl) == 1, (
            f"`{decl}` is gone or no longer nonisolated — under default MainActor isolation its "
            "`resolve()` would be main-actor and could not run on the detached tail"
        )
    home_dir = _block(s["home_store"], _HOME_DIR_DECL)
    assert ".cachesDirectory" in home_dir, "Home's directory is no longer Library/Caches — this scan has drifted"


def _check_tail_only(s):
    for key, disk_header in (("store", _ACCOUNT_DISK), ("home_store", _HOME_DISK)):
        ops = _disk_ops(s[key], disk_header)
        for op, body in ops.items():
            assert body.count(".resolve()") == 1, (
                f"{_FILES[key].name}: `{op}` resolves its file {body.count('.resolve()')} times, expected once"
            )
        assert s[key].count(".resolve()") == 3, (
            f"{_FILES[key].name} resolves the file outside the disk read/write/delete — a "
            "resolve() anywhere else can run on the main actor"
        )
    allowed = {
        "store": _span(s["store"], _RESOLVE),
        "home_store": _span(s["home_store"], _RESOLVE),
    }
    for key, src in s.items():
        for m in _SNAPSHOT_DIR_READ.finditer(src):
            span = allowed.get(key)
            inside = span is not None and span[0] <= m.start() < span[1]
            assert inside, (
                f"{_FILES[key].name} reads a snapshot defaultDirectory outside resolve() "
                f"(`{m.group(0)}`) — that is a file-system call wherever it runs"
            )
    for rel, src in _other_swift_sources():
        m = _SNAPSHOT_DIR_READ.search(src)
        assert m is None, (
            f"{rel} reads a snapshot defaultDirectory (`{m.group(0)}`) — only resolve(), on the "
            "disk tail, may"
        )


def _check_debug_trap(s):
    for key, decl in (("store", _ACCOUNT_LOCATION), ("home_store", _HOME_LOCATION)):
        resolve = _norm(_block(_block(s[key], decl), _RESOLVE))
        assert resolve.startswith(
            "{ #if DEBUG dispatchPrecondition(condition: .notOnQueue(.main)) #endif return "
        ), (
            f"{_FILES[key].name}: resolve() no longer opens with the DEBUG-only main-queue trap "
            "(outside `#if DEBUG` it would trap in Release too); got "
            f"`{resolve[:120]}`"
        )


def _check_configure_free(s):
    configure = _block(s["app"], _CONFIGURE)
    hits = _fs_hits(configure)
    assert not hits, (
        f"AppState.configure makes a file-system call ({hits}) — its launch Task runs on the main "
        "actor before Home's first frame"
    )


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "shared_initialisers": _check_shared_initialisers,
    "store_inits": _check_store_inits,
    "class_bodies": _check_class_bodies,
    "holds_location": _check_holds_location,
    "tail_only": _check_tail_only,
    "debug_trap": _check_debug_trap,
    "configure_free": _check_configure_free,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_guard_holds_on_the_real_source(name):
    GUARDS[name](_sources())


# ── Mutations: each guard must fail on the bug it names ──────────────────────────────

def _rm(token: str) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        assert src.count(token) >= 1, f"mutation anchor {token!r} not in source"
        return src.replace(token, "", 1)
    return mutate


def _sub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


_BEFORE_EXT = r"(\nextension AccountSnapshotStore)"

MUTATIONS: list[tuple] = [
    # (label, guard, file key, mutate, message the failing assertion must carry)
    ("updates-shared-eager", "shared_initialisers", "updates_payload",
     _sub(r"config: \.updatesFeed, persistsToDisk: true",
          "config: .updatesFeed, directory: AccountSnapshotConfig.updatesFeed.defaultDirectory"),
     "updatesFeedSnapshotStoreInstance is no longer built"),
    ("tracking-shared-eager", "shared_initialisers", "tracking_payload",
     _sub(r"config: \.tracking, persistsToDisk: true",
          "config: .tracking, directory: AccountSnapshotConfig.tracking.defaultDirectory"),
     "trackingSnapshotStoreInstance is no longer built"),
    ("home-shared-eager", "shared_initialisers", "home_store",
     _sub(r"static let shared = HomeDashboardSnapshotStore\(persistsToDisk: true\)",
          "static let shared = HomeDashboardSnapshotStore(directory: HomeDashboardSnapshotStore.defaultDirectory)"),
     "Home's `shared` is no longer built"),
    ("payload-filemanager", "shared_initialisers", "tracking_payload",
     _sub(_BEFORE_EXT, r"\nlet rogueCaches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)\1"),
     "TrackingSnapshot.swift touches the file system"),
    ("store-init-resolves", "store_inits", "store",
     _sub(r"(self\.config = config\n)", r"\1        _ = AccountSnapshotFileLocation(config: config).resolve()\n"),
     "AccountSnapshotStore.swift's `init(config: AccountSnapshotConfig, persistsToDisk: Bool)` touches"),
    ("home-init-resolves", "store_inits", "home_store",
     _sub(r"(fileURL = persistsToDisk \? HomeDashboardSnapshotFile\(\) : nil\n)",
          r"\1        _ = HomeDashboardSnapshotStore.fileName.isEmpty ? nil : FileManager.default\n"),
     "HomeDashboardSnapshotStore.swift's `init(persistsToDisk: Bool)` touches"),
    ("launch-bind-resolves", "class_bodies", "store",
     _sub(r"(func bindLaunchOwner\(ownerUserId: String\?\) \{\n)", r"\1        _ = fileURL?.resolve()\n"),
     "AccountSnapshotStore's @MainActor body"),
    ("home-prime-filemanager", "class_bodies", "home_store",
     _sub(r"(func prime\(ownerUserId: String\?, apiClient: APIClient = \.shared\) async \{\n)",
          r'\1        _ = FileManager.default.fileExists(atPath: "/")\n'),
     "HomeDashboardSnapshotStore's @MainActor body"),
    ("store-holds-url", "holds_location", "store",
     _sub(r"private let fileURL: AccountSnapshotFileLocation\?", "private let fileURL: URL?"),
     "AccountSnapshotStore holds a resolved URL"),
    ("home-holds-url", "holds_location", "home_store",
     _sub(r"private let fileURL: HomeDashboardSnapshotFile\?", "private let fileURL: URL?"),
     "HomeDashboardSnapshotStore holds a resolved URL"),
    ("location-isolated", "holds_location", "store",
     _sub(r"nonisolated struct AccountSnapshotFileLocation: Sendable", "struct AccountSnapshotFileLocation: Sendable"),
     "no longer nonisolated"),
    ("resolve-before-detach", "tail_only", "store",
     _sub(r"(private func readOnDiskTail\(_ url: AccountSnapshotFileLocation\) async -> AccountSnapshotReadOutcome \{\n)",
          r"\1        _ = url.resolve()\n"),
     "resolves the file outside the disk read/write/delete"),
    ("disk-op-resolves-twice", "tail_only", "home_store",
     _sub(r"(static func delete\(at file: HomeDashboardSnapshotFile\) \{\n)", r"\1        _ = file.resolve()\n"),
     "`delete` resolves its file 2 times"),
    ("dir-read-elsewhere", "tail_only", "updates_payload",
     _sub(_BEFORE_EXT, r"\n@MainActor private let rogueDir = AccountSnapshotConfig.updatesFeed.defaultDirectory\1"),
     "UpdatesFeedSnapshot.swift reads a snapshot defaultDirectory outside resolve()"),
    ("home-dir-read-in-app", "tail_only", "app",
     _sub(r"(let accountSnapshotOwner = storedCredentialSubject\(of: launchToken\)\n)",
          r"\1            _ = HomeDashboardSnapshotStore.defaultDirectory\n"),
     "AppState.swift reads a snapshot defaultDirectory outside resolve()"),
    ("debug-trap-removed", "debug_trap", "store",
     _sub(r"(nonisolated struct AccountSnapshotFileLocation: Sendable \{.*?)[ \t]*dispatchPrecondition\(condition: \.notOnQueue\(\.main\)\)\n",
          r"\1"),
     "AccountSnapshotStore.swift: resolve() no longer opens"),
    ("debug-trap-in-release", "debug_trap", "home_store",
     _sub(r"#if DEBUG\n(\s*dispatchPrecondition\(condition: \.notOnQueue\(\.main\)\)\n)\s*#endif\n", r"\1"),
     "HomeDashboardSnapshotStore.swift: resolve() no longer opens"),
    ("configure-filemanager", "configure_free", "app",
     _sub(r"(\n[ \t]*let accountSnapshotOwner = storedCredentialSubject\(of: launchToken\)\n)",
          r"\n            _ = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)\1"),
     "AppState.configure makes a file-system call"),
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
        "// FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)\n"
        "/// AccountSnapshotConfig.updatesFeed.defaultDirectory\n"
        "/* _ = fileURL?.resolve()\n"
        "   Data(contentsOf: url) */\n"
        'let url = "https://example.com"  // fileExists(atPath:)\n'
    )
    code = _strip(prose)
    for token in _FS_TOKENS:
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"
