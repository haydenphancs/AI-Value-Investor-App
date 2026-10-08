"""The App Store screenshot mode must never reach an App Store build.

`StoreScreenshotMode` (frontend/ios/ios/Core/Utilities/StoreScreenshotMode.swift) swaps live
market data for labelled SAMPLE values and answers some API calls with fixtures, so the
listing's screenshots never show a real price (the market-data licence forbids public price
display — .claude/rules/marketing.md §1). In a shipped build the same code would show users
invented prices, so all of it lives behind `#if DEBUG`: both files in full, and every call
site. This scan pins that.

Two rules, per testing.md: comments are stripped before scanning (the explanatory comment next
to a hook names the type), and the `#if`/`#else`/`#endif` nesting is tracked, so a reference in
the `#else` branch of an `#if DEBUG` counts as unguarded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_MODE_FILE = _IOS / "ios" / "Core" / "Utilities" / "StoreScreenshotMode.swift"
_FIXTURE_FILE = _IOS / "ios" / "Core" / "Utilities" / "StoreScreenshotFixtures.swift"

# Identifiers that exist only in the two DEBUG files.
_TOKENS = re.compile(r"\b(StoreScreenshotMode|StoreShot\w*|CAYDEX_STORE_SHOT\w*)\b")


def _strip_comments(src: str) -> str:
    """Drop block comments, `//` lines and trailing `//` tails (string-literal-naive, which
    is fine here: neither file puts `//` inside a string)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            out.append("")
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _unguarded_references(src: str) -> List[Tuple[int, str]]:
    """Lines that name a screenshot-mode token outside an ACTIVE `#if DEBUG` branch."""
    stack: List[bool] = []  # per open #if: True while inside the DEBUG branch of `#if DEBUG`
    found: List[Tuple[int, str]] = []
    for number, line in enumerate(_strip_comments(src).splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#if"):
            stack.append(re.fullmatch(r"#if\s+DEBUG", stripped) is not None)
            continue
        if stripped.startswith("#elseif") or stripped == "#else":
            assert stack, f"line {number}: {stripped} without #if"
            stack[-1] = False
            continue
        if stripped.startswith("#endif"):
            assert stack, f"line {number}: #endif without #if"
            stack.pop()
            continue
        if _TOKENS.search(line) and not any(stack):
            found.append((number, stripped))
    return found


def _swift_files() -> List[Path]:
    return sorted(p for p in _IOS.rglob("*.swift") if "/build/" not in str(p))


def test_both_files_are_debug_only_from_first_line_to_last():
    for path in (_MODE_FILE, _FIXTURE_FILE):
        assert path.exists(), f"{path} is gone — this scan has drifted"
        code = [ln.strip() for ln in _strip_comments(path.read_text()).splitlines() if ln.strip()]
        assert code[0] == "#if DEBUG", f"{path.name}: the first code line must be `#if DEBUG`"
        assert code[-1] == "#endif", f"{path.name}: the last code line must close that `#if DEBUG`"
        # The opening `#if DEBUG` must stay open until the final line: no `#else` of it, and
        # no early `#endif` that would leave code below it in every build.
        depth = 0
        for line in code[:-1]:
            if line.startswith("#if"):
                depth += 1
            elif line.startswith("#endif"):
                depth -= 1
                assert depth >= 1, f"{path.name}: the top-level `#if DEBUG` closes before the end"
            elif depth == 1 and (line == "#else" or line.startswith("#elseif")):
                raise AssertionError(f"{path.name}: the top-level `#if DEBUG` has an `{line}` branch")


def test_every_reference_is_inside_an_active_if_debug_branch():
    offenders = []
    for path in _swift_files():
        src = path.read_text(errors="replace")
        if not _TOKENS.search(src):
            continue
        for number, line in _unguarded_references(src):
            offenders.append(f"{path.relative_to(_IOS)}:{number}: {line}")
    assert not offenders, (
        "StoreScreenshotMode referenced outside `#if DEBUG` — it would ship to the App Store:\n"
        + "\n".join(offenders)
    )


def test_the_hooks_exist_so_this_scan_is_not_vacuous():
    """Each hook the mode needs is present (and therefore scanned above)."""
    content_view = _strip_comments((_IOS / "ios" / "ContentView.swift").read_text())
    app = _strip_comments((_IOS / "ios" / "iosApp.swift").read_text())
    assert "StoreScreenshotMode.installIfEnabled()" in app
    assert "StoreScreenshotMode.showLabelIfEnabled()" in content_view
    assert "StoreShotHomeRepository()" in content_view
    assert "StoreScreenshotMode.startTab" in content_view


_CAPTURE_SCRIPT = _IOS / "scripts" / "capture-store-screenshots.sh"
_PBXPROJ = _IOS / "ios.xcodeproj" / "project.pbxproj"


def _script_code() -> str:
    return "\n".join(
        line for line in _CAPTURE_SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def test_the_capture_script_finds_this_projects_product():
    """The default lookup must find the app the project actually builds — and only this repo's.

    It globbed `ios.app` while the product is `Caydex.app`, so a no-argument run always failed;
    and `DerivedData/ios-*` also holds other checkouts' builds (scratch exports), so without the
    WorkspacePath filter the newest FOREIGN build would have been captured.
    """
    products = set(re.findall(r"PRODUCT_NAME = ([A-Za-z0-9_]+);", _PBXPROJ.read_text()))
    assert "Caydex" in products, products
    code = _script_code()
    assert "Debug-iphonesimulator/Caydex.app" in code
    assert "ios.app" not in code
    assert "WorkspacePath" in code and '"$ws" = "$WORKSPACE"' in code


def test_the_capture_script_refuses_a_build_without_the_screenshot_mode():
    """A Release, stale or foreign build ignores CAYDEX_STORE_SHOT and would capture LIVE
    prices into the 'sample' shots."""
    code = _script_code()
    assert re.search(r'grep -aq "CAYDEX_STORE_SHOT"', code), "no check that the build contains the mode"
    assert "debug.dylib" in _CAPTURE_SCRIPT.read_text() or "*.dylib" in code


def test_the_capture_script_always_clears_the_status_bar():
    code = _script_code()
    assert re.search(r"^trap cleanup EXIT$", code, re.M)
    cleanup = code[code.index("cleanup() {"):]
    cleanup = cleanup[: cleanup.index("\n}") + 2]
    assert "status_bar" in cleanup and "clear" in cleanup


def test_the_scanner_catches_an_unguarded_reference():
    """The scanner itself, on synthetic input: guarded, `#else`, and bare references."""
    guarded = "#if DEBUG\nStoreScreenshotMode.installIfEnabled()\n#endif\n"
    in_else = "#if DEBUG\nlet a = 1\n#else\nStoreScreenshotMode.installIfEnabled()\n#endif\n"
    bare = "let x = StoreShotHomeRepository()\n"
    commented = "// StoreScreenshotMode is DEBUG-only\nlet y = 2\n"
    nested = "#if DEBUG\n#if os(iOS)\nStoreShotURLProtocol.self\n#endif\n#endif\n"
    assert _unguarded_references(guarded) == []
    assert _unguarded_references(in_else) != []
    assert _unguarded_references(bare) != []
    assert _unguarded_references(commented) == []
    assert _unguarded_references(nested) == []
