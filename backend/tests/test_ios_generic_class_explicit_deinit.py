"""Every generic class in the iOS app declares an explicit `nonisolated deinit {}`.

2026-10-11: Xcode Archive failed with "Command SwiftCompile failed with a nonzero exit code".
The log showed swift-frontend (Swift 6.3.3) SEGFAULTING in the -O "EarlyPerfInliner" pass on
`AccountSnapshotStore`'s deallocating deinit — a compiler bug, swiftlang/swift#87462 / #88173,
fixed only in Swift 6.4 (PR #87504). All four ingredients are this project's defaults:

  * `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor` makes a class's IMPLICIT deinit MainActor-isolated;
  * the deployment target (iOS 18.0) is below 18.4, so that isolated deinit calls the stdlib
    back-deploy shim `_deinitOnExecutorMainActorBackDeploy` (non-generic, inlinable);
  * on a GENERIC class, SILGen hands that non-generic shim the class's generic substitutions;
  * at -O the inliner reads the shim's (null) generic signature and crashes.

Debug builds never run that pass, so ⌘B and the canonical build stay green and only the Release
Archive fails. A written-out deinit is nonisolated (Sema forces it), so the bad call is never
emitted; `isolated deinit` IS the bug's reproducer.

Scope: every `class Name<…>` in ios/, Shared/ and CaydexWidgets/, plus every class declared
inside the body of a generic type or of an extension of one (it inherits the generic signature).
Local classes inside generic functions are not scanned — none exist; add them here if one does.
Comments are stripped before scanning (.claude/rules/testing.md §3). Delete this file once the
toolchain is Swift 6.4+ AND the deinit lines are removed together.
"""

from __future__ import annotations

import re
from pathlib import Path

_IOS_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_SCAN_DIRS = ("ios", "Shared", "CaydexWidgets")

_GENERIC_TYPE = re.compile(r"\b(?:class|struct|enum|actor)\s+([A-Za-z_]\w*)\s*<")
_GENERIC_CLASS = re.compile(r"\bclass\s+([A-Za-z_]\w*)\s*<")
# `class func` / `class var` / `class let` / `class subscript` are members, not class declarations.
_ANY_CLASS = re.compile(r"\bclass\s+(?!(?:func|var|let|subscript|override|final|private|fileprivate|public)\b)([A-Za-z_]\w*)")
_NONISOLATED_DEINIT = re.compile(r"\bnonisolated\s+deinit\s*\{")
_ISOLATED_DEINIT = re.compile(r"(?<!non)isolated\s+deinit")


def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure."""
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _block_at(src: str, idx: int) -> tuple[int, str]:
    """(start, text) of the brace-balanced block opened by the first `{` at or after `idx`."""
    open_brace = src.find("{", idx)
    assert open_brace != -1, f"no block opens after offset {idx}"
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return open_brace, src[open_brace : i + 1]
    raise AssertionError(f"unbalanced braces after offset {idx}")


def _direct_body(block: str) -> str:
    """The block with the TEXT inside every nested `{…}` blanked (braces kept), so a deinit
    counts only for ITS class: an inner type's deinit sits at depth ≥ 2 and vanishes."""
    out, depth = [], 0
    for ch in block:
        if ch == "{":
            depth += 1
            out.append(ch)
        elif ch == "}":
            out.append(ch)
            depth -= 1
        else:
            out.append(ch if depth <= 1 else " ")
    return "".join(out)


def _class_problem(name: str, block: str) -> str | None:
    body = _direct_body(block)
    if _ISOLATED_DEINIT.search(body):
        return f"{name}: `isolated deinit` is the swiftlang/swift#87462 reproducer"
    if len(_NONISOLATED_DEINIT.findall(body)) != 1:
        return f"{name}: no explicit `nonisolated deinit {{}}`"
    return None


def scan(sources: dict[str, str]) -> tuple[list[str], list[str]]:
    """(classes checked, problems) over {display path: comment-stripped source}."""
    generic_types = {m.group(1) for src in sources.values() for m in _GENERIC_TYPE.finditer(src)}
    checked: list[str] = []
    problems: list[str] = []
    seen: set[tuple[str, int]] = set()

    def check(path: str, src: str, at: int, name: str) -> None:
        start, block = _block_at(src, at)
        if (path, start) in seen:
            return
        seen.add((path, start))
        label = f"{path}: {name}"
        checked.append(label)
        problem = _class_problem(label, block)
        if problem:
            problems.append(problem)

    for path, src in sources.items():
        # 1. Every generic class.
        for m in _GENERIC_CLASS.finditer(src):
            check(path, src, m.end(), m.group(1))
        # 2. Classes nested in a generic type's body, or in an extension of a generic type.
        containers = [m for m in _GENERIC_TYPE.finditer(src)]
        containers += [m for m in re.finditer(r"\bextension\s+([A-Za-z_]\w*)\b", src) if m.group(1) in generic_types]
        for container in containers:
            start, block = _block_at(src, container.end())
            for inner in _ANY_CLASS.finditer(block, 1):
                check(path, src, start + inner.end(), f"{container.group(1)}.{inner.group(1)}")
    return checked, problems


def _sources() -> dict[str, str]:
    out = {}
    for d in _SCAN_DIRS:
        for p in sorted((_IOS_ROOT / d).rglob("*.swift")):
            out[str(p.relative_to(_IOS_ROOT))] = _strip(p.read_text(encoding="utf-8"))
    return out


def test_every_generic_class_has_an_explicit_nonisolated_deinit():
    sources = _sources()
    assert len(sources) > 500, f"only {len(sources)} Swift files found under {_IOS_ROOT} — the scan has drifted"
    checked, problems = scan(sources)
    # Not vacuous: the class that crashed the 2026-10-11 Archive must be among those checked.
    assert any(c.endswith(": AccountSnapshotStore") for c in checked), (
        f"the scan no longer finds AccountSnapshotStore — it has drifted (checked: {checked})"
    )
    assert not problems, (
        "a generic class without an explicit `nonisolated deinit {}` segfaults the Swift 6.3 -O "
        "optimizer (swiftlang/swift#87462): every Release Archive fails with 'Command SwiftCompile "
        "failed with a nonzero exit code' while Debug builds stay green:\n  " + "\n  ".join(problems)
    )


def test_the_scanner_catches_each_failure_shape():
    """The CONTROL: each shape that would crash the Archive must be reported."""
    cases = {
        "implicit": "final class Box<T> {\n    var items: [T] = []\n}\n",
        "isolated": "final class Box<T> {\n    var items: [T] = []\n    isolated deinit {}\n}\n",
        "only-in-comment": _strip("final class Box<T> {\n    // nonisolated deinit {}\n    var items: [T] = []\n}\n"),
        "deinit-in-nested-type-only": (
            "final class Box<T> {\n    final class Inner {\n        nonisolated deinit {}\n    }\n}\n"
        ),
        "nested-in-generic-struct": "struct Wrap<T> {\n    final class Node {\n        var v: T? = nil\n    }\n}\n",
        "nested-in-generic-extension": (
            "struct Wrap<T> {}\nextension Wrap {\n    final class Node {\n        var v: T? = nil\n    }\n}\n"
        ),
    }
    for label, src in cases.items():
        checked, problems = scan({"Fixture.swift": src})
        assert checked, f"{label}: the scanner found no class at all"
        assert problems, f"{label}: the scanner missed a class that would crash the Release Archive"


def test_the_scanner_passes_the_fixed_shapes():
    ok = {
        "generic": "final class Box<T> {\n    var items: [T] = []\n    nonisolated deinit {}\n}\n",
        "optimize-fallback": "final class Box<T> {\n    @_optimize(none) nonisolated deinit {}\n}\n",
        "class-members-are-not-classes": (
            "struct Wrap<T> {\n    static func make() {}\n}\nfinal class Plain {\n"
            "    class func build() {}\n    class var shared: Int { 1 }\n}\n"
        ),
    }
    for label, src in ok.items():
        _, problems = scan({"Fixture.swift": src})
        assert not problems, f"{label}: false positive {problems}"
