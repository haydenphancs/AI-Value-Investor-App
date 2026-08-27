"""SF Symbol availability guard — the compiler cannot do this one for us.

`Image(systemName:)` takes a **String**. Alone among Apple's versioned APIs it therefore gets
NO availability checking: a symbol introduced after `IPHONEOS_DEPLOYMENT_TARGET` compiles clean,
renders on the developer's simulator, and draws **nothing** on a user's device — no warning, no
crash, no log.

That shipped in build 3. `sparkles.2` is iOS 26.0 against a deployment target of 18.0, written
out 39 times across 38 files, so every TestFlight tester below iOS 26 saw the app's entire AI
iconography as blank space. It was reported as "Should it be an AI icon?" — the tester could only
see an empty tile. All local simulators were iOS 26.x, so it never reproduced here.

This module reads Apple's own availability database and fails the build if any symbol NAME in the
Swift tree or the bundled content JSON needs a newer iOS than the app supports.

A too-new symbol is legal in exactly one place: inside an `if #available(iOS N, *)` whose N covers
it (that is how `AppSymbols.ai` keeps `sparkles.2` on iOS 26 while falling back below). Any gating
form this module does not recognise FAILS — conservative on purpose, since a false alarm costs a
human one look and a false pass costs a release.
"""

from __future__ import annotations

import json
import plistlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS_ROOT = REPO / "frontend" / "ios" / "ios"
PBXPROJ = REPO / "frontend" / "ios" / "ios.xcodeproj" / "project.pbxproj"

# Literals that collide with a symbol name but are not symbols. Scoped to (file suffix, literal)
# so an entry can never suppress the same word elsewhere. Keep this list tiny and justified.
_NOT_SYMBOLS = {
    # A word in the depositary-receipt suffix table, not an icon. (`receipt` is an iOS 18.2 symbol.)
    ("Core/Utilities/CompanyNameFormatter.swift", "receipt"),
}

# Content files whose icon values reach `Image(systemName:)`. These can never be `#available`-gated,
# so they must satisfy the deployment target outright.
_CONTENT_JSON = [
    IOS_ROOT / "Resources" / "MoneyMoves" / "money_moves.json",
    IOS_ROOT / "Resources" / "Journey" / "journey_lessons.json",
]
_ICON_KEYS = {"icon", "iconname", "systemicon", "systemiconname", "sfsymbol", "symbol"}


# --------------------------------------------------------------------------- deployment target


def _deployment_target() -> tuple[int, ...]:
    text = PBXPROJ.read_text(encoding="utf-8", errors="replace")
    found = set(re.findall(r"IPHONEOS_DEPLOYMENT_TARGET = ([0-9.]+);", text))
    assert found, "IPHONEOS_DEPLOYMENT_TARGET not found in project.pbxproj — scan would be vacuous"
    # If build configs ever disagree, the guard must hold the OLDEST to the bar.
    return min(_ver(v) for v in found)


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.strip().rstrip(".").split("."))


# --------------------------------------------------------------------------- availability db


def _availability_plists() -> list[Path]:
    """Newest simulator runtime first, then the host. Runtime bundles are flat; the host one
    is a macOS-style bundle with Contents/Resources."""
    out: list[tuple[tuple[int, ...], Path]] = []
    runtimes = Path("/Library/Developer/CoreSimulator/Volumes")
    if runtimes.is_dir():
        for p in runtimes.glob(
            "*/Library/Developer/CoreSimulator/Profiles/Runtimes/iOS *.simruntime"
            "/Contents/Resources/RuntimeRoot/System/Library/CoreServices"
            "/CoreGlyphs.bundle/name_availability.plist"
        ):
            m = re.search(r"iOS ([0-9.]+)\.simruntime", str(p))
            out.append((_ver(m.group(1)) if m else (0,), p))
    out.sort(key=lambda t: t[0], reverse=True)
    paths = [p for _, p in out]
    host = Path(
        "/System/Library/CoreServices/CoreGlyphs.bundle/Contents/Resources/name_availability.plist"
    )
    if host.is_file():
        paths.append(host)
    return paths


def _load_db() -> dict[str, tuple[int, ...]]:
    """symbol name -> minimum iOS version tuple."""
    for path in _availability_plists():
        try:
            raw = plistlib.load(path.open("rb"))
            symbols, years = raw["symbols"], raw["year_to_release"]
        except Exception:
            continue
        db: dict[str, tuple[int, ...]] = {}
        for name, year in symbols.items():
            ios = years.get(year, {}).get("iOS")
            if ios:
                db[name] = _ver(ios)
        if db:
            return db
    if sys.platform != "darwin":
        pytest.skip("SF Symbols availability database is only present on macOS")
    # On macOS a missing database is a BROKEN GUARD, never a quiet pass.
    pytest.fail(
        "no readable name_availability.plist found. This guard cannot run, which means a "
        "too-new SF Symbol could ship undetected. Checked: "
        + ", ".join(str(p) for p in _availability_plists())
        or "(no candidate paths)"
    )


DB = _load_db()
TARGET = _deployment_target()


# --------------------------------------------------------------------------- swift scanning


def strip_comments(src: str) -> str:
    """Remove `//` and (nesting) `/* */` comments while preserving string literals and line count.

    A character state machine, not a regex: the rationale comments in this codebase quote the very
    tokens being asserted, and `"https://…"` must not lose its tail to a naive `//` strip.
    """
    out: list[str] = []
    i, n = 0, len(src)
    in_str = in_multiline = in_line_comment = False
    block = 0
    while i < n:
        c, two, three = src[i], src[i : i + 2], src[i : i + 3]
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
                out.append(c)
            i += 1
        elif block:
            if two == "/*":
                block += 1
                i += 2
            elif two == "*/":
                block -= 1
                i += 2
            else:
                if c == "\n":
                    out.append(c)
                i += 1
        elif in_multiline:
            if three == '"""':
                in_multiline = False
                out.append(three)
                i += 3
            else:
                out.append(c)
                i += 1
        elif in_str:
            if c == "\\":
                out.append(src[i : i + 2])
                i += 2
            else:
                if c == '"':
                    in_str = False
                out.append(c)
                i += 1
        elif three == '"""':
            in_multiline = True
            out.append(three)
            i += 3
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif two == "//":
            in_line_comment = True
            i += 2
        elif two == "/*":
            block = 1
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def available_regions(src: str) -> list[tuple[int, int, tuple[int, ...]]]:
    """(start, end, ios_version) spans guarded by `if #available(iOS N, *) { … }`.

    Also recognises an `@available(iOS N, *)` attribute on the declaration that follows it, whose
    span is that declaration's brace block.
    """
    regions: list[tuple[int, int, tuple[int, ...]]] = []
    for m in re.finditer(r"[@#]available\s*\(\s*iOS\s+([0-9.]+)", src):
        version = _ver(m.group(1))
        brace = src.find("{", m.end())
        if brace == -1:
            continue
        depth, j = 0, brace
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    regions.append((brace, j, version))
                    break
            j += 1
    return regions


def scan_swift(path: Path) -> list[tuple[str, int, tuple[int, ...], bool]]:
    """-> (symbol, line, required_version, is_gated_adequately) for too-new symbols."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    code = strip_comments(raw)
    regions = available_regions(code)
    rel = str(path.relative_to(IOS_ROOT))
    hits = []
    for m in re.finditer(r'"([A-Za-z0-9._]+)"', code):
        name = m.group(1)
        required = DB.get(name)
        if required is None or required <= TARGET:
            continue
        if (rel, name) in _NOT_SYMBOLS:
            continue
        gated = any(s <= m.start() <= e and v >= required for s, e, v in regions)
        hits.append((name, code[: m.start()].count("\n") + 1, required, gated))
    return hits


def _swift_files() -> list[Path]:
    return sorted(IOS_ROOT.rglob("*.swift"))


# --------------------------------------------------------------------------- the guards


def test_no_swift_symbol_outranks_the_deployment_target():
    violations = []
    for path in _swift_files():
        for name, line, required, gated in scan_swift(path):
            if not gated:
                v = ".".join(map(str, required))
                violations.append(f"{path.relative_to(REPO)}:{line}  \"{name}\" needs iOS {v}")
    target = ".".join(map(str, TARGET))
    assert not violations, (
        f"SF Symbol(s) newer than IPHONEOS_DEPLOYMENT_TARGET (iOS {target}). "
        "`Image(systemName:)` is string-typed, so these compile and then render BLANK on every "
        "device below the version shown — silently. Use a name available at the deployment "
        "target, or gate it: `if #available(iOS N, *) { … }` (see Theme/AppSymbols.swift).\n  "
        + "\n  ".join(violations)
    )


def content_icons() -> list[tuple[str, str]]:
    """(where, symbol_name) for every icon-shaped value in the bundled content files."""
    found: list[tuple[str, str]] = []

    def walk(node, path_name: str, trail: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k.lower() in _ICON_KEYS:
                    found.append((f"{path_name}{trail}.{k}", v))
                walk(v, path_name, f"{trail}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path_name, f"{trail}[{i}]")

    for path in _CONTENT_JSON:
        if path.is_file():
            walk(json.loads(path.read_text(encoding="utf-8")), path.name)
    return found


def test_no_content_json_symbol_outranks_the_deployment_target():
    """Bundled content cannot be `#available`-gated — and its Supabase twin is edited without an
    app release, so this file is the only place the constraint can be stated."""
    violations = []
    for where, name in content_icons():
        required = DB.get(name)
        if required and required > TARGET:
            violations.append(f'{where} = "{name}" needs iOS {".".join(map(str, required))}')
    target = ".".join(map(str, TARGET))
    assert not violations, (
        f"content icon(s) need a newer iOS than the deployment target (iOS {target}). Content "
        "renders blank on older devices and cannot be version-gated — author only symbols "
        "available at the deployment target.\n  " + "\n  ".join(violations)
    )


# --------------------------------------------------------------------------- anti-vacuity
#
# A source scan that stops matching turns every assertion above green. Each control below fails
# for a DIFFERENT way this module could quietly stop testing anything.


def test_the_availability_database_is_real():
    """Control 1: a truncated or reshaped plist must fail, not silently know nothing."""
    assert len(DB) > 5000, f"availability db has only {len(DB)} symbols — it did not load properly"
    assert DB.get("sparkles.2") == (26, 0), (
        "`sparkles.2` no longer reads as iOS 26.0 — the db this guard trusts has changed shape, "
        "and lookups may now silently return None for everything"
    )
    assert DB.get("sparkles") == (13, 0), "`sparkles` should be an ancient (iOS 13) symbol"
    assert DB["sparkles"] <= TARGET < DB["sparkles.2"], (
        "the fixture this guard was built around no longer straddles the deployment target"
    )


def test_the_scan_actually_reaches_the_tree():
    """Control 2: a broken path or glob would scan zero files and pass."""
    files = _swift_files()
    assert len(files) > 400, f"only {len(files)} Swift files found under {IOS_ROOT}"
    matched = set()
    for path in files[:80]:
        code = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        matched |= {m.group(1) for m in re.finditer(r'"([A-Za-z0-9._]+)"', code)} & DB.keys()
    assert len(matched) > 20, (
        f"only {len(matched)} known symbol names matched across 80 files — literal extraction "
        "has regressed and the guard is inspecting nothing"
    )


def test_comment_stripping_works_in_both_directions():
    """Control 3: the reason for rule #1 in .claude/rules/testing.md — the rationale comment beside
    a fix names every token the guard greps for, so an unstripped scan passes on prose."""
    assert '"sparkles.2"' not in strip_comments('// let x = "sparkles.2"\nlet y = 1')
    assert '"sparkles.2"' not in strip_comments('/* let x = "sparkles.2" */\nlet y = 1')
    assert '"sparkles.2"' in strip_comments('let x = "sparkles.2"  // the AI mark')
    # A URL keeps its tail: `//` inside a string is not a comment.
    assert "https://example.com/a" in strip_comments('let u = "https://example.com/a"')
    # Line numbers must survive, or every violation reports the wrong location.
    assert strip_comments("a\n// c\nb").count("\n") == 2


def test_the_available_gate_is_recognised_and_is_not_a_blanket_pass():
    """Control 4: the `#available` arm must exempt only what it actually covers."""
    gated = 'struct S {\n  static let a: String = {\n    if #available(iOS 26.0, *) { return "sparkles.2" }\n    return "sparkles"\n  }()\n}'
    regions = available_regions(gated)
    idx = gated.index('"sparkles.2"')
    assert any(s <= idx <= e and v >= (26, 0) for s, e, v in regions), "gate not recognised"

    ungated = 'let a = "sparkles.2"'
    assert not available_regions(ungated), "a gate was invented where none exists"

    # A gate that does not reach far enough must NOT exempt.
    weak = 'if #available(iOS 19.0, *) { let a = "sparkles.2" }'
    idx = weak.index('"sparkles.2"')
    assert not any(s <= idx <= e and v >= (26, 0) for s, e, v in available_regions(weak)), (
        "an iOS 19 gate wrongly exempted an iOS 26 symbol"
    )


def test_the_known_good_token_is_what_keeps_the_tree_clean():
    """Control 5: pins the fix itself. If AppSymbols stops gating, the guard above must be the
    thing that notices — this asserts the scanner sees that file as gated rather than as absent."""
    token = IOS_ROOT / "Theme" / "AppSymbols.swift"
    assert token.is_file(), "Theme/AppSymbols.swift is gone — the 39 call sites have no token"
    hits = scan_swift(token)
    assert hits, "AppSymbols no longer names a too-new symbol; this control has gone vacuous"
    assert all(gated for *_, gated in hits), "AppSymbols names a too-new symbol OUTSIDE #available"


def test_the_content_scan_actually_reaches_the_content():
    """Control 6: added because a mutation test caught this test passing on ZERO input.

    The Swift control above says nothing about the JSON walk — if a content file is renamed or
    moved, `is_file()` skips it and the guard reports a clean bill of health over nothing at all.
    """
    for path in _CONTENT_JSON:
        assert path.is_file(), (
            f"{path} is missing — the content icon guard silently inspects nothing without it. "
            "If the file legitimately moved, update _CONTENT_JSON."
        )
    icons = content_icons()
    assert len(icons) > 50, f"only {len(icons)} icon values found across the content files"
    known = [n for _, n in icons if n in DB]
    assert len(known) > 40, (
        f"only {len(known)} of {len(icons)} content icons resolve to real SF Symbols — the walk "
        "is picking up the wrong keys, so availability is being checked on nothing"
    )
