"""The App Store screenshot mode must never reach an App Store build.

`StoreScreenshotMode` (frontend/ios/ios/Core/Utilities/StoreScreenshotMode.swift) swaps live
market data for SAMPLE values on fictional companies and answers some API calls with fixtures, so
the listing's screenshots never show a real price (the market-data licence forbids public price
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


def _block_after(src: str, anchor: str) -> str:
    """The brace-balanced block that opens at the first `{` after `anchor` (pass source with
    comments already stripped)."""
    start = src.find(anchor)
    assert start != -1, f"{anchor!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace:i + 1]
    raise AssertionError(f"unbalanced braces after {anchor!r}")


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


def test_the_capture_script_refuses_a_build_older_than_the_screenshot_sources():
    """Review 2026-10-08: the mode check above passes for ANY Debug build with the mode, so a
    build from before a fixture change captured the old fixtures — before 2026-10-08, a LIVE
    Updates feed. The code binary (the executable or its .debug.dylib, whichever is newer) must
    be newer than every StoreScreenshot*.swift, or the run stops before the first shot."""
    code = _script_code()
    assert re.search(r'for bin in "\$APP/\$EXE" "\$APP/\$EXE\.debug\.dylib"; do', code)
    loop = code[code.index('for src in "$REPO"/frontend/ios/ios/Core/Utilities/StoreScreenshot*.swift; do'):]
    loop = loop[: loop.index("\ndone") + 5]
    assert '[ "$(stat -f %m "$src")" -gt "$BUILT" ]' in loop
    assert "exit 1" in loop
    # It runs before anything touches the simulator.
    assert code.index("StoreScreenshot*.swift") < code.index("xcrun simctl boot")


def test_the_capture_script_never_pipes_into_head():
    """`set -o pipefail` + `| head` = a SIGPIPE exit status for the producer once head stops
    reading, and `set -e` then ends the whole run — on a long list, never on a short one."""
    code = _script_code()
    assert re.search(r"^set -euo pipefail$", code, re.M)
    assert not re.search(r"\|\s*head\b", code), "use sed -n '1,Np' on a captured variable instead"


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


# ── The Updates shot (2026-10-08) ──────────────────────────────────────────────────────────
# Dropped on 2026-10-06 (adversarial review, HIGH): only its chips were sample — the Insights
# card is a LIVE AI brief seeded with real index % moves, and live headlines can name real
# people. It is back ONLY because every Updates read is now answered by a fixture.

_SCRIPT = _IOS / "scripts" / "capture-store-screenshots.sh"


def _shots() -> List[Tuple[str, str, str]]:
    """(name, tab, sample) for every `shot` line of the capture script, comments stripped."""
    out: List[Tuple[str, str, str]] = []
    for line in _SCRIPT.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].strip()
        match = re.fullmatch(r"shot\s+(\S+)\s+(\S+)\s+([01])(?:\s+\S.*)?", code)
        if match:
            out.append((match.group(1), match.group(2), match.group(3)))
    return out


def _home_tabs() -> List[Tuple[str, str]]:
    """(case name, raw value) of every `HomeTab` case, in declaration order: the tab bar's order."""
    models = _strip_comments((_IOS / "ios" / "Models" / "HomeModels.swift").read_text(encoding="utf-8"))
    return re.findall(r'^\s*case (\w+) = "([^"]+)"', _block_after(models, "enum HomeTab: String, CaseIterable"), re.M)


def test_the_shots_follow_the_apps_tab_bar_order():
    """Owner, 2026-10-08: the listing shows the screenshots in the order users see the app —
    Home, Updates, Research, Tracking, Wiser. App Store Connect keeps the upload order and the
    files sort by their number, so each shot is numbered by its tab's place in `HomeTab` (the tab
    bar's own order, read here rather than typed out). Its tab argument must also be what
    `StoreScreenshotMode.startTab` matches — the raw value, lowercased — or the shot silently
    captures the default tab under another tab's file name."""
    tabs = _home_tabs()
    assert len(tabs) == 5, f"parsed {tabs} — this scan has drifted"
    shots = _shots()
    assert [tab for _, tab, _ in shots] == [raw.lower() for _, raw in tabs], (
        f"shots {[(name, tab) for name, tab, _ in shots]} do not follow the tab bar {tabs}"
    )
    expected = [f"{index:02d}-{raw.lower()}" for index, (_, raw) in enumerate(tabs, start=1)]
    assert [name for name, _, _ in shots] == expected
    mode = _strip_comments(_MODE_FILE.read_text(encoding="utf-8"))
    assert "HomeTab.allCases.first { $0.rawValue.lowercased() == name }" in mode


def test_an_updates_shot_runs_only_with_every_updates_read_canned():
    shots = _shots()
    assert len(shots) >= 4, f"parsed only {shots} — the scan would be vacuous"
    updates = [shot for shot in shots if shot[1] == "updates"]
    assert updates, "the 1.01 set has an Updates shot (02-updates)"
    assert all(sample == "1" for _, _, sample in updates), "an Updates shot must run in sample mode"
    src = _strip_comments(_FIXTURE_FILE.read_text(encoding="utf-8"))
    mode = _strip_comments(_MODE_FILE.read_text(encoding="utf-8"))
    assert "URLProtocol.registerClass(StoreShotURLProtocol.self)" in mode
    assert re.search(r"canInit\(with request: URLRequest\) -> Bool \{\s*route\(for: request\) != nil\s*\}", src)
    # The matcher must RETURN its route (review 2026-10-08: a matcher whose branch returns nil
    # still passed a text-only check, and that read would have gone to the live backend)…
    router = _block_after(src, "private static func route(for request: URLRequest) -> Route?")
    loader = _block_after(src, "override func startLoading()")
    for path, case, builder in (
        ("/api/v1/updates/tabs", "updatesTabs", "updatesTabsJSON"),
        ("/api/v1/updates/feed", "updatesFeed", "updatesFeedJSON"),
        ("/api/v1/updates/sentiment-trend", "sentimentTrend", "sentimentTrendJSON"),
    ):
        branch = _block_after(router, f'if path.hasSuffix("{path}")')
        assert re.search(rf"\breturn \.{case}\b", branch), f"{path} is not canned: the Updates shot would show live data"
        assert "return nil" not in branch, f"{path} can fall through to the live backend"
        # …and the loader must answer that route with its fixture.
        assert re.search(rf"case \.{case}\b[^:]*:\s*body = StoreShotFixtures\.{builder}\(", loader), (
            f".{case} is not answered with {builder}"
        )


def test_the_sample_updates_text_carries_no_figure():
    """No digit, % or $ in the sample outlets, headlines, summaries, Insights bullets or Insights
    headline: the FMP rule forbids public price display, and a sample % reads as a real move.
    Every string literal counts, however short ("Oil fell 3%." is 12 characters), and the floor
    comes from what was parsed, so trimming the sample feed never trips it."""
    src = _strip_comments(_FIXTURE_FILE.read_text(encoding="utf-8"))
    block = src[src.index("static let sampleSources"):src.index("static func updatesFeedJSON(")]
    stories = block.count("StoreShotStory(headline:")
    array = block[block.index("static let sampleInsightBullets"):]
    bullets = re.findall(r'"([^"]*)"', array[array.index("= ["):array.index("\n    ]")])
    texts = re.findall(r'"([^"]*)"', block)
    texts += re.findall(r'insight\["headline"\] = "([^"]*)"', src)
    assert stories >= 1 and bullets, f"parsed {stories} stories and {len(bullets)} bullets — the scan would be vacuous"
    assert len(texts) >= 2 * stories + len(bullets) + 1, f"parsed only {len(texts)} sample strings"
    for text in texts:
        assert not re.search(r"[0-9%$]", text), text


def test_the_sample_home_borrows_no_real_theme_or_company():
    """Review 2026-10-08: the sample Home borrowed `MockHomeRepository.themes` (invented % moves
    on Caydex's real theme names) and `.trillionClub` (real companies' filing figures). Below
    the fold today, but with no label either one could pass for real the day a layout change
    brings it into the frame. Both stay EMPTY (the sections hide); the one mock still borrowed
    is the locked signals list, which carries no symbol and no leader."""
    src = _strip_comments(_FIXTURE_FILE.read_text(encoding="utf-8"))
    dashboard = _block_after(src, "static var dashboard: HomeDashboardData")
    assert re.search(r"\bthemes: \[\]", dashboard)
    assert re.search(r"\btrillionClub: \.empty\b", dashboard)
    borrowed = set(re.findall(r"MockHomeRepository\.(\w+)", src))
    assert borrowed == {"lockedSignals"}, f"the sample Home borrows {sorted(borrowed)} from the mocks"
    home = _strip_comments((_IOS / "ios" / "Core" / "Repositories" / "HomeRepository.swift").read_text(encoding="utf-8"))
    locked = home[home.index("static let lockedSignals"):]
    locked = locked[: locked.index("\n    }\n") + 6]
    assert 'topSymbol: String(repeating: "•"' in locked
    assert "leaders: []" in locked and "isLocked: true" in locked


# ── Fictional companies, no label (owner, 2026-10-08) ──────────────────────────────────────
# The rule (marketing.md §1): sample prices "labelled 'Sample data' or on a fictional ticker".
# The owner chose NO label, so every ticker that carries a price, a % move or a chart in the
# fixtures must be FICTIONAL. Each one below was checked against FMP's full stock list
# (/stable/stock-list, 93,922 symbols, 2026-10-08): the symbol does not exist, and the name's
# first word appears in no real company's name. Check a NEW ticker the same way before adding it
# here — or turn the label back on (`StoreScreenshotMode.showsLabel`).
_VERIFIED_FICTIONAL = {
    "NRWD", "QLVN", "TRVK", "BRVX", "HLVA",                    # holdings: Home, Tracking, Updates chips
    "QVX5", "TLVR", "BRN3", "KSV2", "ARLX", "VYNC",            # Home market strip
    "ZEPX", "KLRO", "VNTQ", "PXLR", "DRVA",                    # movers: gainers
    "GLNT", "FNRX", "ALVQ", "BLNX", "SKVR",                    # movers: losers
    "NVLQ", "TMRX", "WVLN", "JXTA", "LMRQ",                    # heavy volume
    "HPRV", "YLDX", "RQVN", "FLXQ", "MRVQ",                    # short interest
}


def _fixture_tickers() -> set:
    src = _strip_comments(_FIXTURE_FILE.read_text(encoding="utf-8"))
    tickers = set(re.findall(r'symbol: "([A-Z0-9.\-]+)"', src))
    tickers |= set(re.findall(r'pulseItem\("[^"]*", "([A-Z0-9.\-]+)"', src))
    tickers |= set(re.findall(r'entry\(\d+, "([A-Z0-9.\-]+)"', src))
    return tickers


def test_every_priced_company_in_the_fixtures_is_fictional():
    import json

    tickers = _fixture_tickers()
    assert len(tickers) >= 25, f"parsed only {sorted(tickers)} — the scan would be vacuous"
    unverified = sorted(tickers - _VERIFIED_FICTIONAL)
    assert not unverified, f"not verified fictional (check FMP's stock list first): {unverified}"
    # Offline cross-check against the repo's own US universe (a subset of the real listings).
    universe = json.loads((_IOS.parent.parent / "backend" / "data" / "benchmark_universe.json").read_text())
    real = {t.split(".")[0] for row in universe["industries"] for t in row.get("tickers", [])}
    assert len(real) > 1000
    assert not (tickers & real), sorted(tickers & real)


def test_the_sample_data_label_is_opt_in():
    """Off by default since the companies are fictional; CAYDEX_STORE_SHOT_LABEL=1 draws it."""
    src = _strip_comments(_MODE_FILE.read_text(encoding="utf-8"))
    assert 'environment["CAYDEX_STORE_SHOT_LABEL"] == "1"' in src
    assert "guard isOn, showsLabel, labelWindow == nil" in src
