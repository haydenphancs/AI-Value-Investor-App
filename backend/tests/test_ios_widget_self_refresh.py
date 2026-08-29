"""Source-scan guards: the widget refreshes ITSELF, on a market-hours cadence.

TestFlight, build 1.0 (3): *"Check widget, it doesn't automatically update new
information."* Correct, and there were TWO independent causes — fixing either alone
leaves the tile frozen, which is why both are pinned here.

**1. The extension never fetched.** Verified in git history, not just the tree:
`URLSession` had never appeared under `CaydexWidgets/` or `Shared/`, and `BGTaskScheduler`
has never existed anywhere in this repo. The widget was a pure renderer of an App Group
blob that only the app writes, on cold launch / foreground / sign-in. Miss a day of
opening the app and the tile showed yesterday's numbers for a day.

**2. The reload policy said "not until tomorrow".** `Timeline(policy: .after(next))` where
`next` was the next 00:01. WidgetKit was explicitly told not to ask again for the rest of
the day, and the last scheduled entry was only +180m — so past three hours even the
session LABEL stopped ageing. This one hid behind a comment about label-ageing that read
like the whole story.

WHAT IS *NOT* FIXED, DELIBERATELY: holdings mode still renders what the app wrote.
`/widget/market-mover` takes no identity at all, so the extension may call it. Portfolio
resolves the caller's own holdings, and `WidgetSnapshotStore.swift` documents three
reasons the extension must never hold a credential — `auth.md` §8 (the client token and
the Keychain deliberately diverge during `.restoring`), the inability to refresh an
expired token from an extension, and that giving `GuestIdentity` a Keychain access group
would make the existing read miss and silently abandon that install's data. A test that
let portfolio fetch would be a test that broke all three.

The CADENCE arithmetic is proven separately and properly by
`frontend/ios/scripts/widget-refresh-schedule-check.sh`, which compiles the real
`WidgetRefreshSchedule.swift` and asserts every session boundary, the weekend skip, that
no minute of four days yields a past date, and that a trading day stays inside WidgetKit's
refresh allowance. These scans pin only that it is WIRED UP.

Comments are stripped before every assertion — the comments beside this change quote
`URLSession`, `reloadTimelines` and "does not fetch" verbatim (`.claude/rules/testing.md`
§3). `test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IOS = _ROOT / "frontend/ios"
_WIDGET = _IOS / "CaydexWidgets/MoversWidget.swift"
_INTENT = _IOS / "CaydexWidgets/MoversConfigurationIntent.swift"
_FETCHER = _IOS / "Shared/WidgetMarketFetcher.swift"
_SCHEDULE = _IOS / "Shared/WidgetRefreshSchedule.swift"
_STORE = _IOS / "Shared/WidgetSnapshotStore.swift"
_APICONFIG = _IOS / "Shared/WidgetAPIConfig.swift"
_APPSTATE = _IOS / "ios/Core/State/AppState.swift"
_SCHEDULE_HARNESS = _IOS / "scripts/widget-refresh-schedule-check.sh"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace : i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


def _timeline() -> str:
    return _decl_block(_WIDGET.read_text(), "func timeline(for configuration:")


# ── 1. The extension actually fetches ─────────────────────────────────


def test_the_timeline_fetches_instead_of_only_reading_the_stored_blob():
    body = _timeline()
    assert "await WidgetMarketFetcher.fetchMarket()" in body, (
        "the timeline no longer fetches — the tile is back to changing only when the "
        "app is foregrounded, which is the reported bug"
    )


def test_the_fetch_is_gated_on_market_mode():
    """Holdings needs an identity the extension must never hold."""
    body = _timeline()
    call = body[: body.index("WidgetMarketFetcher.fetchMarket()")]
    assert "mode == .market" in call, (
        "the fetch is no longer restricted to market mode. Portfolio would need a "
        "credential in the extension, breaking auth.md §8 and GuestIdentity both."
    )


def test_only_the_public_route_is_ever_called():
    src = _strip_comments(_APICONFIG.read_text()) + _strip_comments(_FETCHER.read_text())
    assert "market-mover" in src
    assert "portfolio-mover" not in src, (
        "the extension now references the portfolio route, which requires an identity "
        "it cannot hold"
    )


def test_a_failed_fetch_falls_back_to_the_stored_snapshot():
    """A Home Screen tile has no error state, no spinner and no retry button."""
    body = _timeline()
    assert "var snap = snapshot(for: mode)" in body, (
        "the stored snapshot is no longer the starting value, so a failed fetch would "
        "render an empty tile instead of an older-but-real one"
    )
    fetcher = _decl_block(_FETCHER.read_text(), "public static func fetchMarket()")
    assert "return nil" in fetcher and "catch" in fetcher, (
        "the fetcher no longer degrades to nil on error"
    )


# ── 2. The cadence is wired, and cannot loop ──────────────────────────


def test_the_reload_policy_is_the_market_hours_schedule():
    body = _timeline()
    assert "WidgetRefreshSchedule.nextRefresh(after: now)" in body
    assert "policy: .after(reload)" in body, (
        "the policy no longer uses the computed reload date. It used to be the last "
        "ENTRY's date, which was the next 00:01 — WidgetKit was told to go away until "
        "tomorrow, and nothing could wake the extension during the day."
    )


def test_the_extension_never_asks_for_a_reload_while_building_a_timeline():
    """`reloadTimelines()` from inside `timeline()` is a loop that eats the allowance."""
    body = _timeline()
    assert "reloadTimelines" not in body
    assert "WidgetSnapshotStore.writeFromExtension(" in body, (
        "the extension no longer stores its fetch, so the next FAILED fetch falls back "
        "to whenever the app was last opened rather than to the last good response"
    )
    write = _decl_block(_STORE.read_text(), "public static func writeFromExtension(")
    assert "reloading: false" in write, (
        "writeFromExtension now reloads, which is exactly the loop it exists to avoid"
    )


def test_the_schedule_harness_exists_and_is_executable():
    """These scans pin the wiring; that harness pins the arithmetic."""
    assert _SCHEDULE_HARNESS.exists(), "the cadence harness is gone"
    assert _SCHEDULE_HARNESS.stat().st_mode & 0o111, "the cadence harness is not executable"
    src = _SCHEDULE_HARNESS.read_text()
    assert "WidgetRefreshSchedule.swift" in src, "the harness no longer compiles the real source"


def test_the_cadence_spends_its_budget_in_market_hours():
    src = _strip_comments(_SCHEDULE.read_text())

    def _interval(name: str) -> float:
        m = re.search(rf"static let {name}: TimeInterval = ([0-9 *]+)", src)
        assert m, f"{name} is gone from WidgetRefreshSchedule"
        return eval(m.group(1))  # a literal arithmetic expression from our own source

    regular, extended = _interval("regularInterval"), _interval("extendedInterval")
    assert regular < extended, (
        f"the cadences collapsed (regular={regular}s, extended={extended}s). A flat "
        "interval around the clock asks for more refreshes than WidgetKit grants, gets "
        "throttled, and can leave the tile STALER than a modest cadence would."
    )
    # Both must actually be USED, or one of them is decoration.
    body = _decl_block(_SCHEDULE.read_text(), "public static func nextRefresh(after now: Date)")
    assert "regularInterval" in body and "extendedInterval" in body
    assert "nextPremarketOpen" in src, "the overnight/weekend quiet period is gone"


# ── 3. Config the extension can actually reach ────────────────────────


def test_the_app_publishes_its_base_url_for_the_extension():
    """APIConfig is app-target only and DEBUG-probes localhost."""
    src = _strip_comments(_APPSTATE.read_text())
    assert "WidgetAPIConfig.publishBaseURL(" in src, (
        "the app no longer publishes its base URL, so a debug build's widget would call "
        "production while the app calls localhost — or keep calling a dead local port"
    )


def test_the_fetcher_has_a_bounded_timeout():
    src = _strip_comments(_APICONFIG.read_text())
    assert "requestTimeout" in src
    fetcher = _strip_comments(_FETCHER.read_text())
    assert "timeoutInterval = WidgetAPIConfig.requestTimeout" in fetcher, (
        "an unbounded request in a timeline callback burns the extension's budget and "
        "still ends with the stored snapshot"
    )


# ── 4. The toggle ─────────────────────────────────────────────────────


def test_the_toggle_writes_an_override_the_provider_prefers():
    intent = _decl_block(_INTENT.read_text(), "func perform() async throws")
    assert "WidgetModeOverride.set(mode)" in intent
    resolve = _decl_block(_WIDGET.read_text(), "private func effectiveMode(for configuration:")
    assert "WidgetModeOverride.current() ?? configuration.mode" in resolve, (
        "the provider ignores the override, or ignores the configuration. An untouched "
        "install must keep behaving exactly as it did before the toggle existed."
    )


def test_the_toggle_does_not_open_the_app():
    intent = _strip_comments(_INTENT.read_text())
    assert "static var openAppWhenRun: Bool { false }" in intent, (
        "the toggle now launches the app, which defeats the point of an in-tile control"
    )


def test_the_toggle_never_covers_the_session_footer():
    """The rendered Small tile read 'As of 2:14 PM E⇆ Holdings' when this was an overlay.

    That footer is the widget's honesty mechanism — the only thing telling the reader
    whether a number is from today — so nothing may be positioned over it.
    """
    block = _decl_block(_WIDGET.read_text(), "private func homeScreen<Content: View>")
    assert ".overlay(" not in block, (
        "the mode toggle is an overlay again; on Small it draws straight through the "
        "session footer"
    )
    assert "ModeToggle(current: entry.configuredMode)" in block


# ── 5. Market mode is a market summary, not a mover list ──────────────


def test_market_mode_renders_the_brief_when_there_is_one():
    src = _strip_comments(_WIDGET.read_text())
    assert "MarketBriefView(" in src, "the Market tile is back to being a mover list"
    brief = _decl_block(_WIDGET.read_text(), "private var marketBrief: WidgetMarketBrief?")
    assert "entry.configuredMode == .market" in brief, (
        "the brief is no longer gated on market mode and would replace the Holdings "
        "mover tile, which is the one place the individual name IS the point"
    )


def test_a_missing_brief_falls_back_to_the_mover_layout():
    """The backend session-gates the roll-up, so absent is ORDINARY."""
    src = _strip_comments(_WIDGET.read_text())
    for family in ("SmallView(entry: entry", "MediumView(entry: entry", "LargeView(entry: entry"):
        assert family in src, (
            f"{family} is gone — a market tile whose headline expired would render "
            "nothing at all, a worse regression than the staleness being fixed"
        )


# ── 6. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    assert _strip_comments("// WidgetMarketFetcher.fetchMarket()\ncode()") == "code()"
    assert _strip_comments("code() // reloadTimelines") == "code()"

    fake = "struct X {\n  func timeline(for configuration: A) {\n    A()\n  }\n}\nfunc o() { B() }"
    block = _decl_block(fake, "func timeline(for configuration:")
    assert "A()" in block and "B()" not in block, "_decl_block leaked past the declaration"

    for path in (_WIDGET, _INTENT, _FETCHER, _SCHEDULE, _STORE, _APICONFIG, _APPSTATE):
        assert path.exists(), f"{path} moved — every scan above would silently pass"
