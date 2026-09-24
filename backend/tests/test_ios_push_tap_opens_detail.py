"""A notification TAPPED outside the app opens its DETAIL screen — never the ticker directly.

Developer, 2026-09-23, with a screenshot of the in-app detail ("TER -10.4%", the full body,
"Received …", then "Open TER ›" / "Read the news ›"):

    "For notification showing on Locked screen iphone or any notification shows outside of the
     app, I want it to open the detail screen first, so they can read the content before they
     decide to go any further. Not to open the ticker right away. You need to check Alerts on
     this too."

Before: `AppDelegate.didReceive` resolved the payload to a bare `NotificationRoute`, and
`HomeDashboardView` presented `NotificationRouteDestination` — the ticker (or report) screen —
in a cover. The alert's own words were gone the moment it was tapped. Tracking → Alerts had
opened a detail first since Aug 2026; the push door was the only one that skipped it.

After, and what this file pins:
  1. `didReceive` hands over the notification ITSELF (`PushedNotification`: title, body,
     delivery time, payload keys), not a destination.
  2. ONE owner — the shell, `ContentView` — observes it (`initial: true`, for the cold launch)
     and presents `PushNotificationDetailScreen`, which renders the SAME `NotificationDetailView`
     the Alerts rows open. Destinations go through the same sheet → cover hand-off.
  3. The screen shows the pushed copy at once and swaps in the full row (the payload body is
     the 180-char banner cut), healing on sign-in when a cold launch beat the session.
  4. No push path presents a ticker, report or whale screen except through the cover a detail
     screen reports into.

Source scans (no XCTest target): comment-stripped and brace-bounded per
`.claude/rules/testing.md`; every assertion was mutation-tested by hand — see MUTATION_LOG.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_DELEGATE = _IOS / "Core" / "AppDelegate.swift"
_PUSH_MGR = _IOS / "Core" / "Services" / "PushNotificationManager.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_CONTENT = _IOS / "ContentView.swift"
_HOME = _IOS / "Views" / "Screens" / "HomeDashboardView.swift"
_SCREEN = _IOS / "Views" / "Screens" / "PushNotificationDetailScreen.swift"
_VM = _IOS / "ViewModels" / "PushNotificationDetailViewModel.swift"
_DETAIL = _IOS / "Views" / "Screens" / "NotificationDetailView.swift"
_MODELS = _IOS / "Models" / "NotificationModels.swift"
_ROUTE_CONTENT = _IOS / "Views" / "Molecules" / "NotificationRouteDestination.swift"
_REPO = _IOS / "Core" / "Repositories" / "NotificationRepository.swift"
_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"

_DID_RECEIVE = "didReceive response: UNNotificationResponse\n    ) async {"


def _strip_comments(src: str) -> str:
    """Drop `//` comments (whole-line and trailing). Every fix here is explained in a comment
    that names the very tokens the assertions look for; an un-stripped scan passes on prose."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            out.append("")
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _read(path: Path) -> str:
    # Deliberately NOT pytest.skip: a guard whose subject vanished must fail, not go quiet.
    assert path.exists(), f"{path} is missing — this guard would otherwise pass vacuously"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _block(src: str, opener: str) -> str:
    """The brace-matched block starting at the first `{` at or after `opener`."""
    at = src.index(opener)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


def _swift_sources():
    return [p for p in _IOS.rglob("*.swift") if "Preview Content" not in p.parts]


# ── 1. The tap hands over the notification, not a destination ────────────────


def test_did_receive_hands_over_the_notification_itself():
    body = _block(_read(_DELEGATE), _DID_RECEIVE)
    assert "PushedNotification(" in body, (
        "didReceive no longer builds a PushedNotification — the detail screen has nothing to show"
    )
    for field in ("content.title", "content.body", "notification.date", "request.identifier"):
        assert field in body, f"didReceive no longer passes {field} to the detail screen"
    assert "handleTap(pushed)" in body, "didReceive no longer hands the tapped push over"
    assert "handleTap(route:" not in body, (
        "didReceive routes straight to a destination again — the tap would skip the detail"
    )


def test_the_manager_parks_and_delivers_the_notification():
    src = _read(_PUSH_MGR)
    assert "private var pendingRoute: PushedNotification?" in src, (
        "the cold-launch parking spot no longer holds the whole notification"
    )
    deliver = _block(src, "private func deliver(_ pushed: PushedNotification)")
    assert "appState?.pendingPushNotification = pushed" in deliver
    assert "handleTap(route:" not in src and "handleTap(ticker:" not in src, (
        "a destination-only tap entry point is back; anything calling it skips the detail"
    )


def test_appstate_parks_a_notification_and_clears_it_at_session_end():
    src = _read(_APP_STATE)
    assert "var pendingPushNotification: PushedNotification?" in src
    for stale in ("pendingPushRoute", "pendingPushTicker", "pendingTrackingTab"):
        assert stale not in src, f"{stale} is back — a second, destination-only push channel"
    ended = _block(src, "private func discardDataForEndedSession()")
    assert "pendingPushNotification = nil" in ended, (
        "a tapped push parked before sign-out would open for the NEXT account (auth.md §7)"
    )


# ── 2. One owner, presenting the detail ──────────────────────────────────────


def test_exactly_one_view_observes_the_tapped_push():
    observers = [
        p.name for p in _swift_sources()
        if ".onChange(of: appState.pendingPushNotification" in _read(p)
    ]
    assert observers == ["ContentView.swift"], (
        f"the tapped push is observed by {observers}; two owners of one parked value race "
        "each other's clear, which is how a tap went nowhere"
    )


def test_the_shell_presents_the_detail_then_the_destination():
    src = _read(_CONTENT)
    handler = _block(src, ".onChange(of: appState.pendingPushNotification, initial: true)")
    assert "appState.pendingPushNotification = nil" in handler
    assert "presentTappedPush(pushed)" in handler

    sheet = _block(src, ".sheet(item: $pushDetail, onDismiss:")
    assert "openedPushDestination = pendingPushDestination" in sheet, (
        "the sheet's onDismiss no longer promotes the parked choice — a cover cannot present "
        "while its sheet is up, so choosing 'Open TER' would do nothing"
    )
    after = src[src.index(".sheet(item: $pushDetail, onDismiss:"):]
    content = _block(after, "{ pushed in")
    assert "NavigationStack" in content and "PushNotificationDetailScreen(pushed: pushed)" in content
    assert "pendingPushDestination = destination" in content and "pushDetail = nil" in content, (
        "the detail's choice is no longer parked and the sheet closed"
    )
    assert ".alertDestinationCover($openedPushDestination)" in src, (
        "the chosen destination no longer opens in the cover that gives it a NavigationStack"
    )


def test_a_tap_takes_down_whatever_is_open_before_presenting():
    """Measured on the simulator: a push tapped while a ticker was open from a Home tile did
    NOTHING — SwiftUI logged "only presenting a single sheet is supported" and queued the detail
    until the user closed the ticker by hand. The tap must unwind first, then present once UIKit
    reports the stack clear (a binding goes nil before the dismissal animation completes)."""
    body = _block(_read(_CONTENT), "private func presentTappedPush(_ pushed: PushedNotification)")
    assert "ModalPresentationProbe.isAnythingPresented" in body, (
        "the tap no longer checks for an open presentation, so it queues behind one"
    )
    assert "appState.dismissAllPresentations()" in body, (
        "the tap no longer takes down what is on screen — the detail waits for a manual close"
    )
    teardown = body.index("appState.dismissAllPresentations()")
    wait = body.index("waitUntilNothingPresented()")
    last_present = body.rindex("present()")
    assert teardown < wait < last_present, (
        "the push is presented before the teardown finishes — ContentView's own reset nils it "
        "on the same bump, or UIKit refuses it mid-dismissal"
    )
    present = _block(body, "let present = {")
    assert "pushDetail = pushed" in present and "openedPushDestination = direct" in present, (
        "the deferred presenter no longer covers both the detail and the direct report"
    )
    probe = _read(_IOS / "Core" / "Utilities" / "ModalPresentationProbe.swift")
    assert "presentedViewController != nil" in probe
    wait_fn = _block(probe, "static func waitUntilNothingPresented(")
    loop = _block(wait_fn, "while isAnythingPresented")
    exit_ = _block(loop, "if clock.now >= deadline")
    assert "return false" in exit_ and "log.warning(" in exit_, (
        "the wait is unbounded or silent — a presentation no tab root owns would hang the tap"
    )


# ── Reports open the report itself (developer, 2026-09-23) ───────────────────
#
# "for 'reports' only, it will open the report right away, no need to open a screen."


_ALERT_DEST = _IOS / "Models" / "AlertDestination.swift"
_INBOX_SECTION = _IOS / "Views" / "Organisms" / "NotificationInboxSection.swift"
_ALERTS_TAB = _IOS / "Views" / "Organisms" / "AlertsTabContent.swift"


def test_one_rule_decides_what_is_a_report():
    src = _read(_ALERT_DEST)
    rule = _block(src, "static func isReport(_ item: NotificationEventDTO) -> Bool")
    assert 'item.route["route"] == "report"' in rule and 'item.kind == "research_complete"' in rule, (
        "isReport no longer honours BOTH the declared route and the kind fallback"
    )
    assert "research_failed" not in rule, "a failed run has no report to open"
    direct = _block(src, "static func directDestination(for item: NotificationEventDTO) -> AlertDestination?")
    assert "guard isReport(item) else { return nil }" in direct, (
        "directDestination no longer gates on isReport — every kind would skip its detail"
    )
    assert "case .report = destination.target" in direct, (
        "directDestination can return something other than the report"
    )
    listing = _block(src, "static func destinations(for item: NotificationEventDTO) -> [AlertDestination]")
    assert "isReport(item)" in listing, (
        "the destination list decides 'report' by its own rule again — the two can drift"
    )


def test_an_alerts_report_row_opens_the_report_directly():
    src = _read(_INBOX_SECTION)
    rows = _block(src, "static func rows(")
    assert "openDirectly: Binding<AlertDestination?>" in src
    decide = rows[rows.index("AlertDestination.directDestination(for: item)"):]
    assert "openDirectly.wrappedValue = direct" in decide, (
        "a report row no longer opens its report — it goes through the detail again"
    )
    assert rows.index("AlertDestination.directDestination(for: item)") < rows.index(
        "selection.wrappedValue = group"
    ), "the detail is chosen before the report check, so reports never open directly"
    assert rows.index("viewModel.markRead(member)") < rows.index(
        "AlertDestination.directDestination(for: item)"
    ), "a report opened directly is no longer marked read"
    assert "openDirectly: $openedDestination" in _read(_ALERTS_TAB), (
        "the Alerts tab does not route a direct open into its destination cover"
    )


def test_report_rows_are_never_collapsed():
    """Each report row opens ITS report, so a "×2" could only ever open one of the two."""
    key = _block(_read(_INBOX_SECTION), "private static func groupKey(_ item: NotificationEventDTO) -> String?")
    assert "guard !AlertDestination.isReport(item) else { return nil }" in key, (
        "report rows collapse into ×N again, hiding every report but the newest"
    )


def test_a_report_push_opens_the_report_and_marks_it_read():
    body = _block(_read(_CONTENT), "private func presentTappedPush(_ pushed: PushedNotification)")
    assert "AlertDestination.directDestination(for: pushed.event)" in body, (
        "the push door decides 'report' differently from the Alerts door"
    )
    assert "openedPushDestination = direct" in body
    assert "markReadFromNotificationAction(" in body and "dedupKey: pushed.dedupKey" in body, (
        "a report opened straight from a push is never marked read — there is no detail "
        "screen to do it any more"
    )


def test_only_quiet_hours_is_still_explained_on_a_row_or_the_detail():
    """ "not sent to this device" / "couldn't be delivered" were removed on request: the row IS
    the delivery, and the recent `failed` rows were dev noise (a laptop backend with no APNs
    keys). Quiet hours stays — it is the user's own setting."""
    foot = _block(_read(_INBOX_SECTION), "private static func footnote(for item: NotificationEventDTO) -> String")
    note = _block(_read(_DETAIL), "private func deliveryNote(_ event: NotificationEventDTO) -> String?")
    for body, name in ((foot, "footnote"), (note, "deliveryNote")):
        assert '"deferred"' in body, f"{name} lost the quiet-hours note"
        for gone in ('"no_device"', '"failed"', "not sent to this device", "couldn't be delivered",
                     "Not sent to this device", "Couldn't be delivered"):
            assert gone not in body, f"{name} shows {gone} again"


def test_the_push_screen_is_the_alerts_detail_screen():
    """One screen for both doors — they cannot drift apart."""
    src = _read(_SCREEN)
    assert "NotificationDetailView(group: viewModel.group, onOpen: onOpen)" in src
    for forbidden in ("NotificationRouteContent", "TickerDetailView", "WhaleProfileView",
                      "navigationDestination", ".fullScreenCover", ".sheet("):
        assert forbidden not in src, (
            f"PushNotificationDetailScreen references {forbidden} — it must report the choice "
            "up, never navigate (a destination inside a sheet kills TickerDetailView's sheets)"
        )


def test_home_no_longer_opens_a_pushed_destination():
    home = _read(_HOME)
    for stale in ("pendingPushNotification", "pendingPushRoute", "pushRoute", "NotificationRouteBox"):
        assert stale not in home, f"HomeDashboardView handles the push tap again ({stale})"


def test_nothing_presents_a_notification_destination_without_a_detail_first():
    """Only the cover a DETAIL screen reports into may render a notification's destination."""
    users = sorted(
        p.name for p in _swift_sources()
        if "NotificationRouteContent(route:" in _read(p)
    )
    assert users == ["AlertDestinationCover.swift"], (
        f"NotificationRouteContent is presented from {users}; anything but the detail-fed cover "
        "is a door that skips the detail"
    )
    assert "struct NotificationRouteDestination" not in _read(_ROUTE_CONTENT), (
        "the stack-owning wrapper the push tap used to present directly is back"
    )


# ── 3. Pushed copy first, full row second ────────────────────────────────────


def test_the_screen_seeds_from_the_push_and_heals_on_sign_in():
    src = _read(_SCREEN)
    assert "PushNotificationDetailViewModel(pushed: pushed)" in src
    assert ".task { await viewModel.load() }" in src
    heal = _block(src, ".onChange(of: appState.auth.status)")
    assert "status == .authenticated" in heal and "viewModel.awaitingSession" in heal, (
        "the heal is gone or ungated — a cold launch from the tap races restoreSession, and "
        "nothing else asks for the full row again"
    )
    assert "viewModel.load()" in heal


def test_the_view_model_seeds_fetches_and_marks_read():
    src = _read(_VM)
    init = _block(src, "init(pushed: PushedNotification")
    assert "CollapsedGroup(items: [pushed.event])" in init, (
        "the screen no longer renders the pushed copy immediately"
    )
    load = _block(src, "func load() async")
    assert "repository.fetchNotification(dedupKey: dedupKey)" in load
    assert "group = NotificationInboxSection.CollapsedGroup(items: [row])" in load
    assert "NotificationInboxViewModel.shared.markRead(row)" in load, (
        "opening a pushed notification no longer marks it read — the Alerts row does on tap"
    )
    assert "if case .signInRequired = appError" in load and "awaitingSession = true" in load, (
        "a pre-flight refusal is no longer classified, so the heal never has a reason to fire"
    )
    for forbidden in ("reportMutationFailure", "showToast", "errorMessage"):
        assert forbidden not in load, (
            f"load() surfaces a failure ({forbidden}) — the pushed copy is already on screen, "
            "so a failed enrichment must be logged, not shown"
        )
    assert "log.warning(" in load, "a failed lookup is no longer logged (never swallow silently)"


def test_the_payload_copy_keeps_the_decoder_intact():
    src = _read(_MODELS)
    dto = src[src.index("struct NotificationEventDTO:"):]
    dto = _block(dto, "struct NotificationEventDTO:")
    assert "init(from decoder: Decoder) throws" in dto, "the network decoder is gone"
    ext = _block(src, "extension NotificationEventDTO {")
    assert "route: [String: String], createdAt: String" in ext, (
        "the memberwise init the pushed copy is built through is gone"
    )
    pushed = _block(src, "struct PushedNotification:")
    assert 'key != "aps"' in pushed, "the payload's `aps` dictionary leaks into the route map"
    assert 'flat["dedup_key"]' in pushed, "the pushed copy no longer knows which row it is"


def test_the_lookup_is_wired_end_to_end():
    assert "func fetchNotification(dedupKey: String) async throws -> NotificationEventDTO?" in _read(_REPO)
    endpoint = _read(_ENDPOINT)
    assert '"/api/v1/users/me/notifications/lookup"' in endpoint
    assert 'return ["dedup_key": dedupKey]' in endpoint
    policy = endpoint[endpoint.index("case .listNotifications, .markNotificationsRead"):]
    policy = policy[: policy.index("return")]
    assert ".lookupNotification" in policy, "the lookup is not declared .signInRequired"


# ── Anti-vacuity ─────────────────────────────────────────────────────────────


def test_the_comment_stripper_drops_the_prose_these_scans_would_match():
    stripped = _strip_comments(
        "// .onChange(of: appState.pendingPushNotification, initial: true)\n"
        "let a = 1 // handleTap(pushed)\n"
        "let b = 2"
    )
    assert "pendingPushNotification" not in stripped and "handleTap" not in stripped
    assert "let a = 1" in stripped and "let b = 2" in stripped


def test_the_block_reader_stops_at_its_own_brace():
    src = "func a() { if x { y() } }\nfunc b() { z() }"
    assert _block(src, "func a()") == "{ if x { y() } }"


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────
# Each mutation applied to the Swift source by hand, the named test watched go RED, then the
# file restored byte-for-byte (sha256 compared before/after).
#
#   S1  didReceive → `handleTap(route: NotificationRoute(payload:))`   → test_did_receive_hands_over_the_notification_itself
#   S2  drop `initial: true` from ContentView's handler                → test_the_shell_presents_the_detail_then_the_destination
#                                                                        (+ consolidation: cold launch, one owner)
#   S3  onDismiss no longer promotes the parked destination            → test_the_shell_presents_the_detail_then_the_destination
#   S4  a second `.onChange(of: appState.pendingPushNotification)` on Home
#                                                                      → test_exactly_one_view_observes_the_tapped_push,
#                                                                        test_home_no_longer_opens_a_pushed_destination
#   S5  heal ungated (`awaitingSession` dropped)                       → test_the_screen_seeds_from_the_push_and_heals_on_sign_in
#   S6  `markRead(row)` removed                                        → test_the_view_model_seeds_fetches_and_marks_read
#   S7  lookup failure toasted via reportMutationFailure               → test_the_view_model_seeds_fetches_and_marks_read
#   S8  AlertsTabContent reset clears the sheet before the parked choice
#                                                                      → deep_research_route::test_the_alerts_tab_takes_its_own_presentations_down
#   S9  ContentView reset drops `pendingPushDestination`               → deep_research_route::test_the_shell_takes_a_tapped_push_down_too
#   S10 `key != "aps"` removed from the payload flattener              → test_the_payload_copy_keeps_the_decoder_intact
#   S11 `pendingPushNotification = nil` dropped from session end       → test_appstate_parks_a_notification_and_clears_it_at_session_end
#   S12 the push screen renders `NotificationRouteContent` itself      → test_the_push_screen_is_the_alerts_detail_screen,
#                                                                        test_nothing_presents_a_notification_destination_without_a_detail_first
#   S13 a `pushRoute` state reappears on Home                          → test_home_no_longer_opens_a_pushed_destination
#   S14 `.signInRequired` classification replaced                      → test_the_view_model_seeds_fetches_and_marks_read
#   S15 the view model seeds an empty group                            → test_the_view_model_seeds_fetches_and_marks_read
#   S16 `appState.dismissAllPresentations()` removed from presentPushDetail
#                                                                      → test_a_tap_takes_down_whatever_is_open_before_presenting
#   S17 `pushDetail = pushed` moved above the wait                     → test_a_tap_takes_down_whatever_is_open_before_presenting
#   S18 the deadline check replaced by `if false`  (SURVIVED the first cut, which only looked for
#       the word `deadline`; now pins the comparison inside the loop)  → test_a_tap_takes_down_whatever_is_open_before_presenting
#
# Backend half (tests/test_notification_lookup_by_key.py), same method:
#   B1  `.eq("user_id", …)` removed from `get_by_dedup_key`            → test_the_lookup_is_scoped_to_the_caller_as_well_as_the_key
#   B2  a read failure returns None instead of raising                 → test_a_read_failure_raises_instead_of_reading_as_not_found
#   B3  the row mapped without `_to_response`                          → test_the_row_comes_back_flattened_exactly_as_the_list_flattens_it
#
# Reports open directly + delivery labels removed (2026-09-23, second round):
#   R1  `guard isReport(item)` dropped from directDestination           → test_one_rule_decides_what_is_a_report
#   R2  a report row sets `selection` instead of `openDirectly`         → test_an_alerts_report_row_opens_the_report_directly
#   R3  AlertsTabContent passes `openDirectly: .constant(nil)`          → test_an_alerts_report_row_opens_the_report_directly
#   R4  `groupKey` no longer excludes reports                           → test_report_rows_are_never_collapsed
#   R5  the report push is not marked read                              → test_a_report_push_opens_the_report_and_marks_it_read
#   R6  the report push assigns `pushDetail` (detail) instead           → test_a_report_push_opens_the_report_and_marks_it_read
#   R7  "not sent to this device" re-added to the row footnote          → test_only_quiet_hours_is_still_explained_on_a_row_or_the_detail
#   R8  "Couldn't be delivered" re-added to the detail                  → test_only_quiet_hours_is_still_explained_on_a_row_or_the_detail
#   R9  isReport drops the `research_complete` kind fallback            → test_one_rule_decides_what_is_a_report
#                                                                        (+ badge_and_filters::test_a_report_notification_opens_the_report)
#   R10 destinations(for:) inlines its own report rule again            → same two
#   R11 the quiet-hours note dropped                                    → test_only_quiet_hours_is_still_explained_on_a_row_or_the_detail
