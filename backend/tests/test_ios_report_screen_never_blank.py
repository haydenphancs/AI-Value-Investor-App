"""A report-ready push must land on the report, or on something the user can leave. Never nothing.

The owner, 2026-11-05: tapped "BRK-B analysis is ready" on the locked iPhone and got a dark screen
with only the status bar. Reproduced first try on the Simulator with the real payload and the app
killed, and the same payload on the same build opened the report with a live session:

  1. A tap that launches the app reaches `ContentView` during `.restoring`, the ~0.5 s before
     `/users/me` answers, and a report push skips the detail screen, so the cover opens at once.
  2. `TickerReportViewModel.init` calls `loadReport()`, whose `isSignedIn` guard is false during
     `.restoring`. It returned with `isLoading`, `reportData`, `error` and
     `needsPaidRegeneration` all empty.
  3. `body` was an `if / else if` chain with no final `else`, so it drew the bare background. The
     screen hides the nav bar, and only `reportContent` draws a header, so there was no way out.
  4. Nothing retried. The session arrived half a second later and the screen stayed blank for
     good: the byte-identical frame at +25 s.

Sentry saw nothing, because nothing failed. The same chain had already shipped this once
(`needsPaidRegeneration`, whose branch still says "the screen was blank").

Source scans (no XCTest target): comment-stripped and brace-bounded per `.claude/rules/testing.md`;
every assertion was mutation-tested by hand — see MUTATION_LOG at the bottom.
"""

from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_VIEW = _IOS / "Views" / "Screens" / "TickerReportView.swift"
_VM = _IOS / "ViewModels" / "TickerReportViewModel.swift"
_CONTENT = _IOS / "ContentView.swift"
_COVER = _IOS / "Views" / "Modifiers" / "AlertDestinationCover.swift"


def _strip_comments(src: str) -> str:
    """Drop `//` comments. The comment beside each fix names the tokens these scans look for, so
    an un-stripped scan would pass on prose after the code was reverted."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            out.append("")
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _read(path: Path) -> str:
    # Never pytest.skip: a guard whose subject vanished must fail, not go quiet.
    assert path.exists(), f"{path} is missing — this guard would otherwise pass vacuously"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _brace_end(src: str, start: int) -> int:
    """The index just past the `}` closing the `{` at `start`."""
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError("unbalanced braces")


def _block(src: str, opener: str) -> str:
    """The brace-matched block at the first `{` at or after `opener`."""
    at = src.index(opener)
    start = src.index("{", at)
    return src[start:_brace_end(src, start)]


def _state_chain(body: str) -> list[tuple[str, str]]:
    """The arms of the `if viewModel.isLoading` chain, as `(condition, arm)`, in order.

    A bare `else` is the condition `""`. Reading the chain arm by arm, rather than searching for
    `else {`, is the point: the file holds other `else`s, and a text search passes on any of them.
    """
    at = body.index("if viewModel.isLoading")
    arms: list[tuple[str, str]] = []
    condition = "viewModel.isLoading"
    while True:
        start = body.index("{", at)
        end = _brace_end(body, start)
        arms.append((condition, body[start + 1:end - 1].strip()))
        follow = re.match(r"\s*else\s+if\s+([^{]+?)\s*\{", body[end:])
        if follow:
            condition = follow.group(1)
            at = end + follow.end() - 1
            continue
        if re.match(r"\s*else\s*\{", body[end:]):
            start = body.index("{", end)
            arms.append(("", body[start + 1:_brace_end(body, start) - 1].strip()))
        return arms


def test_the_state_chain_can_never_render_nothing():
    """THE guard. The chain must end in an unconditional `else`, so a state nobody has thought of
    yet gets a screen."""
    body = _block(_read(_VIEW), "var body: some View")
    conditions = [condition for condition, _ in _state_chain(body)]
    assert conditions[-1] == "", (
        f"TickerReportView's state chain ends on `{conditions[-1]}` with no final `else`: when "
        "that is false too, the screen is the bare background with no header and no way out"
    )


def test_the_chain_covers_every_state_the_view_model_publishes():
    body = _block(_read(_VIEW), "var body: some View")
    conditions = [condition for condition, _ in _state_chain(body)]
    for state in (
        "viewModel.isLoading",
        "let report = viewModel.reportData",
        "let error = viewModel.error",
        "viewModel.needsPaidRegeneration",
        "viewModel.awaitingSession",
    ):
        assert state in conditions, f"no arm for `{state}`: the catch-all would be drawing it"


def test_the_final_arm_is_a_visible_error():
    """The catch-all must offer the error screen's Retry and Go Back, not an `EmptyView`."""
    body = _block(_read(_VIEW), "var body: some View")
    _, arm = _state_chain(body)[-1]
    assert re.fullmatch(r'errorView\("[^"]+"\)', arm), f"the final arm is not an errorView: {arm!r}"


def test_every_state_has_a_way_back():
    """This screen hides the nav bar and sits inside a full-screen cover, so an arm with no
    `dismiss()` is a screen the user cannot leave."""
    src = _read(_VIEW)
    body = _block(src, "var body: some View")
    assert ".navigationBarHidden(true)" in body, "the nav bar is back: re-read this guard's reason"

    reached = {
        "viewModel.isLoading": "private var loadingView: some View",
        "let report = viewModel.reportData": "private func headerSection(",
        "let error = viewModel.error": "private func errorView(",
        "viewModel.needsPaidRegeneration": "private var unavailableView: some View",
        "viewModel.awaitingSession": "private var reconnectingView: some View",
        "": "private func errorView(",
    }
    for condition, _ in _state_chain(body):
        decl = _block(src, reached[condition])
        if "waitingView(" in decl:
            decl = _block(src, "private func waitingView(")
        assert "dismiss()" in decl, f"the `{condition or 'else'}` state has no way back"


def test_waiting_shows_a_spinner_and_says_why():
    src = _read(_VIEW)
    assert "ProgressView()" in _block(src, "private func waitingView(")
    assert "Reconnecting your account" in _block(src, "private var reconnectingView: some View")


def test_load_report_records_what_it_owes_before_leaving():
    """The exit that caused all of this. It must set the flag the view heals on."""
    load = _block(_read(_VM), "func loadReport()")
    guard = _block(load, "guard AppActions.shared.isSignedIn else")
    assert re.search(r"\bawaitingSession\s*=\s*true\b", guard), (
        "loadReport's signed-out exit no longer records the report it owes: the screen has "
        "nothing to draw and nothing will retry"
    )
    assert re.search(r"\berror\s*=\s*nil\b", guard), (
        "a Retry during `.restoring` must not leave the old error covering the reconnecting state"
    )
    assert re.search(r"\bisLoading\s*=\s*false\b", guard)


def test_a_real_load_clears_what_was_owed():
    load = _block(_read(_VM), "func loadReport()")
    after_guard = load[load.index("guard AppActions.shared.isSignedIn else"):]
    after_guard = after_guard[_brace_end(after_guard, after_guard.index("{")):]
    assert re.search(r"\bawaitingSession\s*=\s*false\b", after_guard), (
        "a load that goes out must clear `awaitingSession`, or every later auth change refetches"
    )


def test_the_flag_is_published():
    """A plain `var` would not redraw the screen, and the heal reads it."""
    assert re.search(r"@Published\s+private\(set\)\s+var\s+awaitingSession\b", _read(_VM))


def test_the_screen_loads_when_the_session_arrives():
    src = _read(_VIEW)
    body = _block(src, "var body: some View")
    opener = ".onChange(of: appState.auth.status, initial: true)"
    assert opener in body, (
        "the report screen must watch auth with `initial: true`. Without the watch nothing "
        "retries; without `initial: true` a session back before first render is missed"
    )
    heal = _block(body, opener)
    assert re.search(r"guard\s+status\s*==\s*\.authenticated\s*,\s*viewModel\.awaitingSession\s+else", heal), (
        "the heal must be gated: on `.authenticated` only, and only while a report is owed"
    )
    assert "viewModel.loadReport()" in heal


def test_the_heal_can_never_charge():
    """`loadReport()` honours the entry's `allowPaidOnOpen`, so a notification tap stays free.
    `regenerateForCredits()` would bill 20 credits for a tap on an alert."""
    body = _block(_read(_VIEW), "var body: some View")
    heal = _block(body, ".onChange(of: appState.auth.status, initial: true)")
    assert "regenerateForCredits" not in heal
    load = _block(_read(_VM), "func loadReport()")
    assert "_fetchReport(allowPaidGeneration: self.allowPaidOnOpen)" in load


def test_the_screen_reads_the_key_its_presenters_inject():
    """`\\.appState` defaults to a throwaway AppState when a chain breaks. Its status never moves,
    so the screen would sit on "Reconnecting" for good."""
    assert "@Environment(\\.appState) private var appState" in _read(_VIEW)

    reports_cover = _block(_read(_CONTENT), ".fullScreenCover(item: $selectedReport)")
    assert "TickerReportView(report: report)" in reports_cover, "the Reports cover moved"
    assert ".environment(\\.appState, appState)" in reports_cover, (
        "the Reports tab's report cover must inject `\\.appState`"
    )

    notification_cover = _block(_read(_COVER), "func body(content: Content) -> some View")
    assert ".environment(\\.appState, appState)" in notification_cover, (
        "the notification cover must inject `\\.appState`"
    )


def test_the_chain_reader_sees_a_missing_else():
    """The guard depends on this reader, so it gets a check of its own."""
    closed = 'if viewModel.isLoading { a } else if b { c } else { d }'
    assert [condition for condition, _ in _state_chain(closed)] == ["viewModel.isLoading", "b", ""]

    open_ended = 'if viewModel.isLoading { a } else if b { c }\n.onChange(of: x) { _, s in if s { y } else { z } }'
    assert [condition for condition, _ in _state_chain(open_ended)] == ["viewModel.isLoading", "b"]


def test_the_comment_stripper_drops_the_prose_these_scans_would_match():
    stripped = _strip_comments("    // awaitingSession = true\n    let a = 1 // awaitingSession = true\n")
    assert "awaitingSession" not in stripped
    assert "let a = 1" in stripped


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────
# Each mutation applied to a COPY of the Swift sources (other sessions share this checkout and may
# build at any moment), every test run against the copies, the named test watched go RED, the
# real files confirmed unchanged by sha256. 20 mutants, 0 survived.
#
#   M1   the final `else` removed                       → test_the_state_chain_can_never_render_nothing
#   M1b  the same, with the fix left behind as a comment → test_the_state_chain_can_never_render_nothing
#   M2   the final arm becomes `EmptyView()`            → test_the_final_arm_is_a_visible_error
#   M3   the guard stops setting `awaitingSession`      → test_load_report_records_what_it_owes_before_leaving
#   M4   the guard keeps the previous error             → test_load_report_records_what_it_owes_before_leaving
#   M5   the `.onChange` removed                        → test_the_screen_loads_when_the_session_arrives
#   M6   `initial: true` dropped                        → test_the_screen_loads_when_the_session_arrives
#   M7   the heal ungated                               → test_the_screen_loads_when_the_session_arrives
#   M8   the heal calls `regenerateForCredits()`        → test_the_heal_can_never_charge
#   M9   a real load stops clearing the flag            → test_a_real_load_clears_what_was_owed
#   M10  `@Published` dropped from the flag             → test_the_flag_is_published
#   M11  the waiting view's button no longer dismisses  → test_every_state_has_a_way_back
#   M12  the `awaitingSession` arm dropped              → test_the_chain_covers_every_state_the_view_model_publishes
#   M13  the Reports cover stops injecting `\.appState` → test_the_screen_reads_the_key_its_presenters_inject
#   M14  the notification cover stops injecting it      → test_the_screen_reads_the_key_its_presenters_inject
#   M15  `loadReport` always allows paid generation     → test_the_heal_can_never_charge
#   M16  the waiting view loses its spinner             → test_waiting_shows_a_spinner_and_says_why
#   M17  the unavailable view loses Go Back             → test_every_state_has_a_way_back
#   M18  the reconnecting copy replaced                 → test_waiting_shows_a_spinner_and_says_why
#   M19  the guard stops clearing `isLoading`           → test_load_report_records_what_it_owes_before_leaving
