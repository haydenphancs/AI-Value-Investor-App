"""Mounting the tabs at cold launch reads no Learn bundle and calls no audio server on main.

Follow-up to Sentry CAYDEX-APPLE-IOS-S (2026-11-01, release 1.02 (13): a 7 s "App Hang Fully
Blocked" at cold launch, caused by synchronous file-system work on the main thread; the snapshot
stores' half is pinned by `test_ios_snapshot_stores_no_main_thread_io.py`). When `auth.status`
becomes `.restoring`, `ContentView` mounts all five tabs in one main-thread pass, and that pass
also ran, in front of Home's first content frame:

  * `LearnViewModel.init` -> `JourneyContentStore.shared` -> `loadBundled()`: read + decode the
    824 KB `journey_lessons.json`;
  * ~0.3 s later, `MoneyMovesContentStore.shared` -> `loadBundled()`: the 431 KB `money_moves.json`;
  * `RootContainerView`'s `@StateObject` -> `AudioManager.init` -> `setCategory(...)`: a
    synchronous XPC to the audio server.

Under a system I/O or audio-server stall each of those freezes the launch. The fix:

  * each store's `init` hands the read + decode to a `Task.detached` that calls a
    `nonisolated static func decodeBundledFile()` (a DEBUG `dispatchPrecondition` traps it on
    main); only the cheap mapping comes back to the main actor;
  * the decoded DTOs (and the helpers their `init(from:)` calls) are `nonisolated`, because the
    app defaults every declaration to the main actor and a main-actor Decodable conformance
    cannot be used off it;
  * `prefetch()` waits for the bundle first, so the offline fallback is in place for every caller
    that awaits a prefetch (remote content still wins in every reader);
  * the Wiser row's first paint waits for the bundle, so it shows the authored cards rather than
    only the sample placeholders;
  * `AudioManager.configureAudioSession()` (still called from `init`, as
    `test_ios_background_modes_justified.py` requires) hands `setCategory` to a `Task.detached`
    calling a `nonisolated static` setter. The only other `setCategory` is
    `activateAudioSession()`, which runs on a user's play tap and re-asserts the category.

Source scans (there is no XCTest target): comments stripped and every check scoped to its
brace-bounded declaration (.claude/rules/testing.md §3). `MUTATIONS` breaks each property once
and asserts its guard fails with the assertion that names the bug.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_FILES = {
    "journey": _IOS / "Services/JourneyContentStore.swift",
    "money": _IOS / "Services/MoneyMovesContentStore.swift",
    "money_models": _IOS / "Models/MoneyMovesContentModels.swift",
    "read_along": _IOS / "Models/ReadAlongModels.swift",
    "audio": _IOS / "Services/AudioManager.swift",
    "learn_vm": _IOS / "ViewModels/LearnViewModel.swift",
}

# (file key, class header) for the two bundle-backed stores.
_STORES = (
    ("journey", "final class JourneyContentStore"),
    ("money", "final class MoneyMovesContentStore"),
)
_INIT = "private init()"
_DECODE = "nonisolated private static func decodeBundledFile() ->"
_PREFETCH = "func prefetch() async"
# Anything that reads the bundle synchronously.
_FILE_WORK = ("Data(contentsOf:", "JSONDecoder(", "Bundle.main.url(")
_DEBUG_TRAP = "dispatchPrecondition(condition: .notOnQueue(.main))"

# Every type decoded on the detached task, and every helper its `init(from:)` reaches.
_NONISOLATED = {
    "journey": (
        "struct FailableDecodable", "struct DroppedElementIdentity",
        "struct JourneyBundleFile", "struct JourneyBundleLesson", "struct JourneyBundleCard",
    ),
    "money_models": (
        "struct FailableDecodable", "struct DroppedElementIdentity", "struct FlexibleStringElement",
        "func _stringifyDouble", "extension KeyedDecodingContainer",
        "struct MoneyMovesContentFile", "struct MoneyMoveArticleDTO", "extension MoneyMoveArticleDTO",
        "struct ArticleAuthorDTO", "struct ArticleHighlightDTO", "struct ArticleSectionDTO",
        "struct ArticleSectionContentDTO", "extension ArticleSectionContentDTO",
        "struct ArticleStatisticDTO", "struct ArticleCommentDTO", "struct RelatedArticleDTO",
    ),
    "read_along": ("struct ReadAlongSentence", "extension ReadAlongSentence"),
}

_AUDIO_CLASS = "final class AudioManager"
_CONFIGURE = "private func configureAudioSession()"
_SETTER = "nonisolated private static func setPlaybackCategory()"
_ACTIVATE = "private func activateAudioSession()"
_LOAD_MOCK = "func loadMockData()"


def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. The fix's own comments name
    `Task.detached`, `setCategory` and `decodeBundledFile`, so an un-stripped scan would pass on
    prose after the code is reverted.
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


def _span_at(src: str, idx: int) -> tuple[int, int]:
    """[start, end) of the balanced `{ … }` opened by the first `{` at or after `idx`."""
    start = src.find("{", idx)
    assert start != -1, f"no '{{' opens after offset {idx}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise AssertionError(f"unbalanced braces after offset {idx}")


def _span(src: str, header: str) -> tuple[int, int]:
    assert src.count(header) == 1, f"expected exactly one {header!r}, found {src.count(header)}"
    return _span_at(src, src.find(header) + len(header))


def _block(src: str, header: str) -> str:
    start, end = _span(src, header)
    return src[start:end]


def _detached_spans(src: str) -> list[tuple[int, int]]:
    """The closure body of every `Task.detached(…) { … }` in `src`."""
    return [_span_at(src, m.end()) for m in re.finditer(r"\bTask\.detached\b", src)]


def _inside(idx: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= idx < end for start, end in spans)


def _call_sites(src: str, name: str) -> list[int]:
    """Offsets of `name(` used as a CALL, not as its own `func name(` declaration."""
    return [m.start() for m in re.finditer(rf"(?<!func )\b{re.escape(name)}\(", src)]


# ── Guards ───────────────────────────────────────────────────────────────────────────

def _check_store_init(s):
    for key, cls in _STORES:
        init = _block(_block(s[key], cls), _INIT)
        hits = [t for t in _FILE_WORK if t in init]
        assert not hits, f"{key}: the store's `{_INIT}` does file work on the main actor: {hits}"
        calls = _call_sites(init, "decodeBundledFile")
        assert calls and all(_inside(c, _detached_spans(init)) for c in calls), (
            f"{key}: the store's `{_INIT}` no longer hands the bundle to a Task.detached — reading "
            "and decoding it there runs on the main thread while the tabs mount"
        )


def _check_decode_detached_only(s):
    for key, _ in _STORES:
        spans = _detached_spans(s[key])
        stray = [c for c in _call_sites(s[key], "decodeBundledFile") if not _inside(c, spans)]
        assert not stray, (
            f"{key}: decodeBundledFile() is called outside a Task.detached closure "
            f"({len(stray)} site(s)) — from the main actor it reads the bundle on main"
        )


def _check_file_work_confined(s):
    for key, _ in _STORES:
        start, end = _span(s[key], _DECODE)
        stray = [
            t for t in _FILE_WORK
            for m in re.finditer(re.escape(t), s[key]) if not start <= m.start() < end
        ]
        assert not stray, f"{key}: bundle file work outside `decodeBundledFile`: {stray}"


def _check_decode_nonisolated(s):
    for key, _ in _STORES:
        decls = re.findall(r"^.*\bfunc decodeBundledFile\(.*$", s[key], flags=re.M)
        assert len(decls) == 1 and _DECODE in decls[0], (
            f"{key}: decodeBundledFile is not a `nonisolated static func` — a main-actor one runs "
            f"on main even when a Task.detached awaits it: {decls}"
        )


def _check_debug_trap(s):
    for key, _ in _STORES:
        assert _DEBUG_TRAP in _block(s[key], _DECODE), (
            f"{key}: decodeBundledFile lost its DEBUG main-queue trap (`{_DEBUG_TRAP}`)"
        )


def _check_dtos_nonisolated(s):
    for key, decls in _NONISOLATED.items():
        for decl in decls:
            kind, name = decl.split(" ")
            pattern = rf"^nonisolated (?:private )?{kind} {re.escape(name)}\b"
            assert re.search(pattern, s[key], flags=re.M), (
                f"{key}: `{name}` is not `nonisolated` — its Decodable conformance would be "
                "main-actor isolated and unusable on the detached decode"
            )


def _check_prefetch_waits(s):
    for key, cls in _STORES:
        body = _block(_block(s[key], cls), _PREFETCH)
        wait = body.find("await bundledContentLoaded()")
        latch = body.find("guard !didPrefetch")
        assert wait != -1 and latch != -1 and wait < latch, (
            f"{key}: prefetch() no longer waits for the bundled fallback before anything else — "
            "an offline caller that awaits it would read no content"
        )


def _check_learn_first_paint(s):
    body = _block(s["learn_vm"], _LOAD_MOCK)
    wait = body.find("await MoneyMovesContentStore.shared.bundledContentLoaded()")
    load = body.find("loadMoneyMoves()")
    assert wait != -1 and load != -1 and wait < load, (
        "LearnViewModel.loadMockData() builds the Money Moves row without waiting for the bundled "
        "catalog — the first paint would show only the sample placeholders"
    )


def _check_audio_init(s):
    init = _block(_block(s["audio"], _AUDIO_CLASS), _INIT)
    hits = [t for t in ("setCategory(", "setActive(") if t in init]
    assert not hits, f"AudioManager's init talks to the audio server on the main actor: {hits}"
    assert _call_sites(init, "configureAudioSession"), (
        "AudioManager's init no longer calls configureAudioSession()"
    )


def _check_audio_configure_detached(s):
    body = _block(_block(s["audio"], _AUDIO_CLASS), _CONFIGURE)
    spans = _detached_spans(body)
    calls = _call_sites(body, "setPlaybackCategory")
    direct = [
        t for t in ("setCategory(", "setActive(", "audioSession.", "AVAudioSession")
        for m in re.finditer(re.escape(t), body) if not _inside(m.start(), spans)
    ]
    assert calls and all(_inside(c, spans) for c in calls) and not direct, (
        "configureAudioSession() no longer hands setCategory to a Task.detached — from init it is "
        f"a synchronous audio-server XPC on the main thread (direct: {direct})"
    )


def _check_audio_setter_nonisolated(s):
    cls = _block(s["audio"], _AUDIO_CLASS)
    decls = re.findall(r"^.*\bfunc setPlaybackCategory\(.*$", cls, flags=re.M)
    assert len(decls) == 1 and _SETTER in decls[0], (
        f"setPlaybackCategory is not a `nonisolated static func` — a main-actor one runs on main: {decls}"
    )
    assert "setCategory(.playback" in _block(cls, _SETTER), "setPlaybackCategory no longer sets `.playback`"


def _check_audio_category_sites(s):
    cls = _block(s["audio"], _AUDIO_CLASS)
    allowed = [_span(cls, _SETTER), _span(cls, _ACTIVATE)]
    stray = [m.start() for m in re.finditer(r"\bsetCategory\(", cls) if not _inside(m.start(), allowed)]
    assert not stray, (
        f"AudioManager: setCategory( outside setPlaybackCategory / activateAudioSession "
        f"({len(stray)} site(s)) — a new main-thread audio-server XPC"
    )


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "store_init": _check_store_init,
    "decode_detached_only": _check_decode_detached_only,
    "file_work_confined": _check_file_work_confined,
    "decode_nonisolated": _check_decode_nonisolated,
    "debug_trap": _check_debug_trap,
    "dtos_nonisolated": _check_dtos_nonisolated,
    "prefetch_waits": _check_prefetch_waits,
    "learn_first_paint": _check_learn_first_paint,
    "audio_init": _check_audio_init,
    "audio_configure_detached": _check_audio_configure_detached,
    "audio_setter_nonisolated": _check_audio_setter_nonisolated,
    "audio_category_sites": _check_audio_category_sites,
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


_DETACHED_DECODE = r"await Task\.detached\(priority: \.utility\) \{ Self\.decodeBundledFile\(\) \}\.value"

MUTATIONS: list[tuple] = [
    # (label, guard, file key, mutate, message the failing assertion must carry)
    ("journey-init-inline", "store_init", "journey",
     _sub(_DETACHED_DECODE, "Self.decodeBundledFile()"),
     "journey: the store's `private init()` no longer hands the bundle to a Task.detached"),
    ("journey-init-detached-only-in-a-comment", "store_init", "journey",
     # Applied to the RAW text: proves the scan reads code, not the comment left behind.
     None,
     "journey: the store's `private init()` no longer hands the bundle to a Task.detached"),
    ("money-init-reads", "store_init", "money",
     _sub(r"(private init\(\) \{\n)", r'\1        _ = try? Data(contentsOf: URL(fileURLWithPath: "/"))\n'),
     "money: the store's `private init()` does file work on the main actor"),
    ("journey-decode-on-prefetch", "decode_detached_only", "journey",
     _sub(r"(func prefetch\(\) async \{\n)", r"\1        _ = Self.decodeBundledFile()\n"),
     "journey: decodeBundledFile() is called outside a Task.detached closure"),
    ("money-bundle-lookup-in-apply", "file_work_confined", "money",
     _sub(r"(private func applyBundled\(_ file: MoneyMovesContentFile\?\) \{\n)",
          r'\1        _ = Bundle.main.url(forResource: "money_moves", withExtension: "json")\n'),
     "money: bundle file work outside `decodeBundledFile`"),
    ("journey-decode-main-actor", "decode_nonisolated", "journey",
     _sub(r"nonisolated private static func decodeBundledFile", "private static func decodeBundledFile"),
     "journey: decodeBundledFile is not a `nonisolated static func`"),
    ("money-no-debug-trap", "debug_trap", "money",
     _rm(_DEBUG_TRAP),
     "money: decodeBundledFile lost its DEBUG main-queue trap"),
    ("money-file-dto-isolated", "dtos_nonisolated", "money_models",
     _sub(r"nonisolated struct MoneyMovesContentFile", "struct MoneyMovesContentFile"),
     "money_models: `MoneyMovesContentFile` is not `nonisolated`"),
    ("journey-card-dto-isolated", "dtos_nonisolated", "journey",
     _sub(r"nonisolated private struct JourneyBundleCard", "private struct JourneyBundleCard"),
     "journey: `JourneyBundleCard` is not `nonisolated`"),
    ("read-along-decoder-isolated", "dtos_nonisolated", "read_along",
     _sub(r"nonisolated extension ReadAlongSentence", "extension ReadAlongSentence"),
     "read_along: `ReadAlongSentence` is not `nonisolated`"),
    ("journey-prefetch-no-wait", "prefetch_waits", "journey",
     _rm("await bundledContentLoaded()"),
     "journey: prefetch() no longer waits for the bundled fallback"),
    ("money-prefetch-waits-after-latch", "prefetch_waits", "money",
     _sub(r"(func prefetch\(\) async \{\n)(\s*await bundledContentLoaded\(\)\n)(\s*guard !didPrefetch else \{ return \}\n)",
          r"\1\3\2"),
     "money: prefetch() no longer waits for the bundled fallback"),
    ("learn-row-sync-paint", "learn_first_paint", "learn_vm",
     _rm("await MoneyMovesContentStore.shared.bundledContentLoaded()"),
     "LearnViewModel.loadMockData() builds the Money Moves row without waiting"),
    ("audio-init-xpc", "audio_init", "audio",
     _sub(r"(private init\(\) \{\n)", r"\1        try? audioSession.setCategory(.playback)\n"),
     "AudioManager's init talks to the audio server on the main actor"),
    ("audio-configure-inline", "audio_configure_detached", "audio",
     _sub(r"Task\.detached\(priority: \.utility\) \{\s*Self\.setPlaybackCategory\(\)\s*\}", "Self.setPlaybackCategory()"),
     "configureAudioSession() no longer hands setCategory to a Task.detached"),
    ("audio-configure-direct-xpc", "audio_configure_detached", "audio",
     _sub(r"(private func configureAudioSession\(\) \{\n)",
          r"\1        try? audioSession.setCategory(.playback, mode: .spokenAudio)\n"),
     "configureAudioSession() no longer hands setCategory to a Task.detached"),
    ("audio-setter-main-actor", "audio_setter_nonisolated", "audio",
     _sub(r"nonisolated private static func setPlaybackCategory", "private static func setPlaybackCategory"),
     "setPlaybackCategory is not a `nonisolated static func`"),
    ("audio-category-in-observers", "audio_category_sites", "audio",
     _sub(r"(private func setupObservers\(\) \{\n)",
          r"\1        try? audioSession.setCategory(.playback, mode: .spokenAudio)\n"),
     "AudioManager: setCategory( outside setPlaybackCategory / activateAudioSession"),
]


def _comment_only_detach(raw: str) -> str:
    """Leave `Task.detached` only inside a `//` comment next to an inline main-actor decode."""
    out, n = re.subn(
        rf"let file = ({_DETACHED_DECODE})",
        r"let file = Self.decodeBundledFile() // \1",
        raw,
        count=1,
    )
    assert n == 1, "comment mutation anchor not found"
    return out


@pytest.mark.parametrize("label,guard,key,mutate,message", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, key, mutate, message):
    sources = _sources()
    mutated = dict(sources)
    if mutate is None:
        mutated[key] = _strip(_comment_only_detach(_FILES[key].read_text(encoding="utf-8")))
    else:
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
