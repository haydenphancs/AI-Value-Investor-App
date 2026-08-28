"""The Alerts unread badge, and the Activity filter chips.

Two TestFlight asks on Tracking -> Alerts:

  1/ "The Alerts should have a red number of new alerts on top. So users can know to check."
  2/ "Activity is a very long list. Should add tags on the top (same row as Activity), just
      like in the Report tab."

Both are pinned here because both are invisible when they break. The badge's failure mode is
"a control that renders nothing", which looks identical to "no unread notifications". The
filter's is a row silently excluded from every chip, which looks identical to "no such alerts".

Source scans over the Swift tree (there is no XCTest target). Per `.claude/rules/testing.md`
every scan is comment-stripped and brace-bounded, and each assertion below was mutation-tested
by hand: break the source, watch this file go red, restore.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"

_BADGE = _IOS / "Views" / "Atoms" / "UnreadCountBadge.swift"
_SEGMENT = _IOS / "Views" / "Atoms" / "SegmentedTabControl.swift"
_TAB_ITEM = _IOS / "Views" / "Molecules" / "TabBarItem.swift"
_TRACKING_HEADER = _IOS / "Views" / "Organisms" / "TrackingHeader.swift"
_INBOX_VM = _IOS / "ViewModels" / "NotificationInboxViewModel.swift"
_ALERTS_TAB = _IOS / "Views" / "Organisms" / "AlertsTabContent.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_NOTIF_MODELS = _IOS / "Models" / "NotificationModels.swift"
_CHIP = _IOS / "Views" / "Atoms" / "AccentFilterChip.swift"
_FILTER_BAR = _IOS / "Views" / "Molecules" / "ActivityFilterBar.swift"
_REPORTS_LIST = _IOS / "Views" / "Organisms" / "ReportsListSection.swift"
_KINDS = _REPO / "backend" / "app" / "services" / "notification_kinds.py"
_INBOX_SECTION = _IOS / "Views" / "Organisms" / "NotificationInboxSection.swift"
_HOME_MODELS = _IOS / "Models" / "HomeModels.swift"
_RECON = _REPO / "backend" / "app" / "services" / "research_reconciliation_service.py"
_RESEARCH_SVC = _REPO / "backend" / "app" / "services" / "research_service.py"
_SWEEPER = _REPO / "backend" / "app" / "services" / "updates_insight_sweeper.py"
_DESTINATIONS = _IOS / "Models" / "AlertDestination.swift"
_DEST_ROW = _IOS / "Views" / "Molecules" / "AlertDestinationRow.swift"
_NOTIF_DETAIL = _IOS / "Views" / "Screens" / "NotificationDetailView.swift"
_ALERT_DETAIL = _IOS / "Views" / "Screens" / "AlertDetailView.swift"
_ROUTE_DEST = _IOS / "Views" / "Molecules" / "NotificationRouteDestination.swift"
_SMART_MONEY = (
    _REPO / "backend" / "app" / "services" / "notification_senders" / "smart_money_sender.py"
)


def _code_only(src: str) -> str:
    """Strip whole-line comments AND trailing `//` tails.

    Load-bearing. Every fix below is documented in a comment sitting right next to it, and
    those comments name the exact tokens these assertions grep for — an un-stripped scan
    would keep passing on the prose after the code was reverted.
    """
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            out.append("")
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _read(path: Path) -> str:
    # Deliberately NOT skip: a guard whose subject vanished must fail, not go quiet.
    assert path.exists(), f"{path} is missing — this guard would otherwise pass vacuously"
    return _code_only(path.read_text(encoding="utf-8"))


def _balanced(src: str, opener: str, o: str = "{", c: str = "}") -> str:
    """The brace-balanced body of the declaration introduced by `opener`.

    `opener` MUST end at its own `{`, so the slice is the declaration you meant rather than
    whatever block happens to come next.
    """
    start = src.index(opener) + len(opener) - 1
    assert src[start] == o, f"{opener!r} must end at its opening {o!r}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == o:
            depth += 1
        elif src[i] == c:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {o!r} after {opener!r}")


def _py_body(src: str, header: str) -> str:
    """The body of a top-level Python `def`, by indentation.

    `_balanced` is brace-based and therefore Swift-only — pointing it at a Python file
    trips its own "must end at its opening {" assertion, which is the guard behaving
    correctly rather than a Python extractor behaving badly.
    """
    start = src.index(header)
    rest = src[start:]
    # Consume the signature by paren balance first — a multi-line `def` has its closing
    # `) -> None:` at column 0, which an indentation-only scan reads as "the body ended".
    depth, i = 0, 0
    for i, ch in enumerate(rest):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
    body_start = rest.index("\n", i) + 1
    out = [rest[:body_start]]
    for line in rest[body_start:].splitlines(keepends=True):
        if line.strip() and not line[0].isspace():
            break
        out.append(line)
    return "".join(out)


# --------------------------------------------------------------------------------- 1/ the badge


def test_badge_ink_and_fill_are_the_audited_red_pair():
    """`lossFill` + `textOnFill`, in one declaration.

    The trap this exists for is real and already happened once: `lossFill` + `textOnAccent` is
    the obvious "red badge" and its dark arm (#F87171) puts white ink at 2.77:1. That rejection
    is why the badge shipped BLUE. `textOnFill` is the ink that exists for adaptive fills and
    flips to near-black in dark, so the pair measures 5.55 / 6.41.

    Anyone reading the old rationale and "restoring" white ink re-opens the contrast bug, and
    it fails in dark mode only — i.e. not in whatever mode they happened to be testing.
    """
    body = _balanced(_read(_BADGE), "struct UnreadCountBadge: View {")
    assert "AppColors.lossFill" in body, (
        "UnreadCountBadge no longer fills with `lossFill` — the badge is supposed to be red."
    )
    assert "AppColors.textOnFill" in body, (
        "UnreadCountBadge's ink is not `textOnFill`. Ink and fill move TOGETHER: on the "
        "ADAPTIVE `lossFill`, `textOnFill` is the only ink that clears 4.5:1 in both modes."
    )
    assert "AppColors.textOnAccent" not in body, (
        "UnreadCountBadge uses `textOnAccent` on an adaptive fill — that is 2.77:1 on "
        "`lossFill`'s dark arm, the exact pairing the theme guard rejected before."
    )


def test_both_badged_surfaces_go_through_the_one_atom():
    """One count must not render in two colours.

    The bottom tab bar and the Alerts segment show the SAME
    `AppState.unreadNotificationCount` and are on screen together. When the recipe was inline
    they could drift; routing both through the atom makes that structurally impossible, so this
    asserts the routing rather than comparing two colour literals.
    """
    for path, name in ((_TAB_ITEM, "TabBarItem"), (_SEGMENT, "SegmentedTabControl")):
        src = _read(path)
        assert "UnreadCountBadge(" in src, (
            f"{name} no longer renders `UnreadCountBadge`. A second inline badge recipe is how "
            f"one number ends up in two colours on one screen."
        )
        assert "Capsule().fill(AppColors.primaryFill)" not in src, (
            f"{name} has an inline blue badge capsule again."
        )


def test_badges_are_overlays_and_never_take_layout_width():
    """Both hosts are width-starved: five tab columns, three segments on 393pt.

    Every label in both already carries `minimumScaleFactor` for exactly that reason. A badge
    placed as a layout sibling would shrink EVERY label to make room for one of them — and it
    would do it silently, as slightly smaller text.
    """
    for path, name in ((_TAB_ITEM, "TabBarItem"), (_SEGMENT, "SegmentedTabControl")):
        src = _read(path)
        overlay = re.search(
            r"\.overlay\(alignment: \.topTrailing\)\s*\{[^}]*UnreadCountBadge\(", src, re.S
        )
        assert overlay, (
            f"{name}'s badge is not inside `.overlay(alignment: .topTrailing)`. An overlay "
            f"contributes no layout; a sibling would resize every label in the control."
        )


def test_the_alerts_segment_is_badged_from_app_state():
    """And from inside the ORGANISM, not from the screen.

    `TrackingView.swift` holds two copies of this call — `TrackingContentView` is dead,
    `TrackingContentViewWithBinding` is what `ContentView` builds. A parameter threaded from
    the call site can be added to one and missed on the other, and the dead one is the copy
    that still compiles while the live screen shows nothing.
    """
    src = _read(_TRACKING_HEADER)
    body = _balanced(src, "struct TrackingHeader: View {")
    assert "@Environment(\\.appState)" in body, (
        "TrackingHeader no longer reads appState. `\\.appState` and not "
        "`@Environment(AppState.self)` on purpose: the key declares a default, so the "
        "`#Preview` renders instead of trapping on a missing value."
    )
    assert "badges:" in body, "TrackingHeader stopped passing `badges:` to SegmentedTabControl."
    assert "TrackingTab.alerts.rawValue" in body, (
        "The badge is no longer keyed to the Alerts segment."
    )
    assert "appState.unreadNotificationCount" in body, (
        "The Alerts badge is not sourced from `AppState.unreadNotificationCount` — it must be "
        "the same number the bottom tab bar shows, not a second count."
    )


# ------------------------------------------------------------------- 1/ the count behind it


def test_the_inbox_is_shared_so_the_count_can_be_refreshed_off_screen():
    """The whole reason the badge was useless.

    As a `@StateObject` private to `AlertsTabContent`, the view model existed only while the
    Alerts segment was selected — and that screen marks everything read on sight. So the count
    was refreshed exclusively by the surface that immediately zeroes it, and on Assets/Whales
    it was whatever the last push tap left (for a user whose pushes never arrive: `0`, forever).
    """
    vm = _read(_INBOX_VM)
    assert "static let shared = NotificationInboxViewModel()" in vm, (
        "NotificationInboxViewModel.shared is gone. Without it nothing outside the Alerts tab "
        "can refresh the unread count, and the badge is decorative."
    )
    tab = _read(_ALERTS_TAB)
    assert "NotificationInboxViewModel.shared" in tab, (
        "AlertsTabContent is not using the shared inbox."
    )
    assert "@StateObject private var notifications" not in tab, (
        "AlertsTabContent owns a private NotificationInboxViewModel again — that is the "
        "original bug: a second instance whose count nothing else can see."
    )


def test_refresh_is_guarded_on_sign_in_and_discards_a_stale_answer():
    """Two failures, both silent.

    Signed out: publishing a count from a read that never happened would zero the badge for a
    user whose notifications are still unread — the second-writer bug in different clothes.

    Stale: a refresh in flight when the user opens Alerts lands AFTER `markAllReadOnView()` and
    puts the badge back on a list they just read. The epoch is what closes that window.
    """
    body = _balanced(_read(_INBOX_VM), "func refreshUnreadCount() async {")
    assert "AppActions.shared.isSignedIn" in body, (
        "refreshUnreadCount no longer checks sign-in before hitting the network."
    )
    # Both halves AND their order. Asserting the bare token `readEpoch` was VACUOUS: the
    # comparison `epoch == readEpoch` keeps that word alive even when the captured value is
    # replaced by a constant, so mutating `let epoch = readEpoch` to `let epoch = 0` sailed
    # straight through. Caught by hand mutation, which is the whole reason for the exercise.
    assert "let epoch = readEpoch" in body, (
        "refreshUnreadCount is not capturing `readEpoch` — the comparison after the await is "
        "then against a value that never moves, i.e. no guard at all."
    )
    assert "epoch == readEpoch" in body, (
        "refreshUnreadCount does not re-check the epoch after its await — a response that "
        "lands after a mark-all-read will restore a badge the user just cleared."
    )
    fetch = body.index("await repository.fetchNotifications")
    assert body.index("let epoch = readEpoch") < fetch < body.index("epoch == readEpoch"), (
        "The epoch must be captured BEFORE the await and compared AFTER it. Either side on the "
        "wrong side of the network call makes the check tautological."
    )
    assert "limit: 1" in body, (
        "refreshUnreadCount is no longer asking for a single row. It runs on every foreground; "
        "a full 30-row page for one integer is the cost this was written to avoid."
    )


def test_the_session_end_funnel_resets_the_shared_inbox():
    """auth.md §7. The singleton outlives the view, so the view's own reset is not enough."""
    body = _balanced(_read(_APP_STATE), "private func discardDataForEndedSession() {")
    assert "NotificationInboxViewModel.shared.reset()" in body, (
        "The shared inbox is not reset when a session ends — the next account to sign in on "
        "this phone inherits the previous user's notification rows and unread count."
    )


def test_paging_is_driven_by_the_rendered_rows_not_the_models_tail():
    """Filtering broke infinite scroll, and it broke it INVISIBLY.

    The old trigger asked whether the current row was in `items.suffix(5)` — the view model's
    own tail. With a filter on, those five rows can all be hidden, so no visible row ever
    satisfies it and the list simply ends with matching rows unfetched on the next page. On a
    feature whose entire purpose is taming a long list, that is the worst possible failure.
    """
    vm = _read(_INBOX_VM)
    assert "func loadNextPage() async {" in vm, (
        "loadNextPage is gone — paging must be callable without the view model deciding "
        "'am I near the end?' from rows the view may not have drawn."
    )
    assert "items.suffix(5).contains(item)" not in vm, (
        "The model-tail paging guard is back. It cannot see the filter, so a filtered list "
        "stops early."
    )
    section = _read(_IOS / "Views" / "Organisms" / "NotificationInboxSection.swift")
    assert "items.suffix(" in section and "loadNextPage()" in section, (
        "NotificationInboxSection no longer triggers paging from the rows it actually rendered."
    )


# ------------------------------------------------------------------------------- 2/ the chips


def _backend_categories() -> set[str]:
    src = _KINDS.read_text(encoding="utf-8")
    found = set(re.findall(r"^CATEGORY_[A-Z_]+ = \"([a-z_]+)\"", src, re.M))
    assert found, "no CATEGORY_* constants found — this parity check has drifted"
    return found


def test_every_backend_category_maps_to_a_chip():
    """The bidirectional half of the contract, and the one that actually protects users.

    A category with no bucket is a row that no chip can reach. It is not a crash and not a
    blank screen — the row is simply absent whenever any filter is on, which reads as "I have
    no alerts of that kind". Adding a category on the backend is the moment this breaks, and
    that change happens in a different language in a different directory.
    """
    body = _balanced(
        _read(_NOTIF_MODELS),
        "static func bucket(forCategory category: String) -> ActivityFilter? {",
    )
    missing = sorted(c for c in _backend_categories() if f'"{c}"' not in body)
    assert not missing, (
        f"notification categories with no Activity chip: {missing}. Rows in these categories "
        f"vanish from Activity whenever any filter is selected. Map them in "
        f"`ActivityFilter.bucket(forCategory:)` (several categories may share one bucket)."
    )


def test_an_unknown_category_fails_open():
    """Fail-open is the whole reason `nil` is allowed out of `bucket(forCategory:)`.

    The only way to reach it is a backend deployed ahead of the client — precisely the case
    where hiding the row is wrong. Flip these guards to `false` and a newer backend's rows
    disappear from an older build with nothing to indicate why.
    """
    body = _balanced(
        _read(_NOTIF_MODELS),
        "static func admits(_ selection: Set<ActivityFilter>, category: String) -> Bool {",
    )
    assert re.search(r"guard let bucket = bucket\(forCategory: category\) else \{ return true \}", body), (
        "`admits(_:category:)` no longer fails OPEN on an unmapped category — rows from a "
        "newer backend would silently disappear under every filter."
    )
    assert "guard !selection.isEmpty else { return true }" in body, (
        "An empty selection must admit everything, or the list is blank until a chip is tapped."
    )


def test_selected_chip_fill_is_always_a_fill_role_token():
    """Ink and fill move together — the same rule the badge above turns on.

    `AccentFilterChip` puts `textOnAccent` (white) on whatever `accentFill` it is handed. Pass
    a TEXT-role token there and white lands on that token's dark arm at roughly 2.3-2.8:1. It
    fails in dark mode only, so it survives any amount of light-mode review.
    """
    enum_body = _balanced(
        _read(_NOTIF_MODELS),
        "enum ActivityFilter: String, CaseIterable, Identifiable, Hashable, Sendable {",
    )
    body = _balanced(enum_body, "var accentFill: Color {")
    tokens = re.findall(r"AppColors\.(\w+)", body)
    assert tokens, "ActivityFilter.accentFill resolves no AppColors token"
    bad = sorted({t for t in tokens if not t.endswith("Fill")})
    assert not bad, (
        f"ActivityFilter.accentFill returns non-fill tokens {bad}. `AccentFilterChip` puts "
        f"`textOnAccent` on this colour when selected; a text-role token there is ~2.3-2.8:1 "
        f"in dark. Use the matching `*Fill` token."
    )


def test_every_chip_is_visually_distinguishable():
    """Every chip needs its OWN label, accent and fill.

    Asserting merely that each case APPEARS in each switch was vacuous: collapsing two cases
    onto one arm (`case .smartMoney, .reports: return AppColors.alertPurple`) leaves both names
    in the text and passed. Caught by hand mutation. Distinctness is also the property that
    actually matters — two chips in the same colour are two chips the reader cannot tell apart,
    and the chips are the only thing carrying the filter's state.

    ⚠️ Scoped to the ENUM BODY first, and that is not cosmetic: `var label: String {` also
    opens `PriceAlertKind.label` earlier in this same file, so a whole-file scan asserted
    against a switch over `.above` / `.below` / `.percentMove`. It failed loudly here, but with
    overlapping case names it would have PASSED for the wrong reason — which is exactly what
    the brace-bounding rule in `.claude/rules/testing.md` §3 exists to prevent.
    """
    enum_body = _balanced(
        _read(_NOTIF_MODELS),
        "enum ActivityFilter: String, CaseIterable, Identifiable, Hashable, Sendable {",
    )
    cases = re.findall(r"^    case (\w+)$", enum_body, re.M)
    assert len(cases) >= 2, f"expected the ActivityFilter cases, found {cases}"

    labels = re.findall(r'return "([^"]+)"', _balanced(enum_body, "var label: String {"))
    assert len(set(labels)) == len(cases), (
        f"{len(cases)} chips but {len(set(labels))} distinct labels ({labels}). Two chips "
        f"sharing a label are indistinguishable on screen."
    )

    for prop, opener in (("accent", "var accent: Color {"), ("accentFill", "var accentFill: Color {")):
        tokens = re.findall(r"AppColors\.(\w+)", _balanced(enum_body, opener))
        assert len(set(tokens)) == len(cases), (
            f"{len(cases)} chips but {len(set(tokens))} distinct `{prop}` tokens ({tokens}). "
            f"Colour is how a selected chip is read at a glance; duplicates defeat it."
        )


def test_the_chips_are_the_same_component_as_the_reports_tab():
    """The tester asked for them "just like in the Report tab", so they must BE the Reports
    chip, not a lookalike. A second copy of that closure is the drift the request is made of."""
    assert "struct AccentFilterChip: View" in _read(_CHIP)
    reports = _read(_REPORTS_LIST)
    assert "AccentFilterChip(" in reports, (
        "ReportsListSection no longer uses the shared chip — the two rows will drift."
    )
    body = _balanced(reports, "private func personaTagChip(_ persona: AnalysisPersona) -> some View {")
    assert "Capsule().fill" not in body, (
        "ReportsListSection has an inline chip capsule again; it should delegate to the atom."
    )
    bar = _read(_FILTER_BAR)
    assert "AccentFilterChip(" in bar, "ActivityFilterBar is not rendering the shared chip."


def test_activity_filters_both_halves_of_the_section():
    """Activity is roll-up cards AND notification rows. Filtering only one half means a chip
    that visibly does nothing to the cards above the rows it just narrowed."""
    body = _balanced(_read(_ALERTS_TAB), "private func activitySection() -> some View {")
    assert "ActivityFilter.admits(selection, rollup:" in body, (
        "The digest roll-up cards are not filtered — a chip would narrow the notification rows "
        "and leave the cards above them untouched."
    )
    assert "ActivityFilter.admits(selection, category:" in body, (
        "The notification rows are not filtered."
    )
    assert "ActivityFilterBar(" in body, "The Activity header no longer carries the chips."
    assert "activityFilters.wrappedValue.intersection(available)" in body, (
        "The stored selection is applied without intersecting the AVAILABLE buckets. A refresh "
        "that empties a filtered bucket then blanks the list with no chip on screen to undo it."
    )


def test_chips_offered_are_derived_from_rows_actually_present():
    """`allCases` would offer an Earnings chip to an account that has never had an earnings
    notification, and tapping it empties the list. The available set must come from the data."""
    body = _balanced(
        _read(_ALERTS_TAB),
        "private func availableFilters(\n        rollups: [AppAlert],\n        items: [NotificationEventDTO]\n    ) -> [ActivityFilter] {",
    )
    assert "bucket(forRollup:" in body and "bucket(forCategory:" in body, (
        "availableFilters no longer derives the offered chips from both row families."
    )
    bar = _balanced(_read(_FILTER_BAR), "struct ActivityFilterBar: View {")
    assert "available.count >= 2" in bar, (
        "ActivityFilterBar renders chips without checking there are at least two buckets — a "
        "lone chip filters to everything either way, so it is noise on a heading row."
    )


# ------------------------------------------------- round 2: persistence, dedup, new kind


def test_the_chip_selection_survives_a_launch_and_dies_with_the_session():
    """Persisted, but not inherited.

    A tester asked for the selection to stick across launches. That reverses the shipped
    behaviour, so the risk is a later reader "restoring" the old `@State` on the strength
    of the comment that used to sit there. The second half is auth.md §7: the key carries
    no user id, so without an explicit clear the next account to sign in on the phone
    opens Alerts onto someone else's filter.
    """
    tab = _read(_ALERTS_TAB)
    assert "@AppStorage(ActivityFilter.storageKey)" in tab, (
        "The Activity filter selection is no longer persisted. A tester asked for it to "
        "survive a relaunch; `@State` throws it away on every visit to the segment."
    )
    assert "@State private var activityFilters" not in tab, (
        "The filter selection is back on `@State` — it will not survive a relaunch."
    )
    discard = _balanced(_read(_APP_STATE), "private func discardDataForEndedSession() {")
    assert "ActivityFilter.storageKey" in discard, (
        "The persisted filter is not cleared when a session ends. It is a UserDefaults key "
        "with no user id in it — the next account inherits the previous user's filter."
    )


def test_the_stored_format_has_a_stable_order():
    """A `Set`'s iteration order is not stable.

    Joining it directly rewrites the stored string on writes that changed nothing, waking
    every `@AppStorage` reader for no reason. Encoding through `allCases` fixes the order.
    """
    body = _balanced(
        _read(_NOTIF_MODELS), "static func encode(_ selection: Set<ActivityFilter>) -> String {"
    )
    assert "allCases" in body, (
        "`ActivityFilter.encode` no longer iterates `allCases`, so the persisted string's "
        "order follows Set iteration and churns between writes."
    )


def test_the_earnings_duplicate_is_suppressed_only_when_its_twin_is_on_screen():
    """The duplicate is real; hiding it unconditionally would not be.

    `earnings_upcoming` fires only for tomorrow and Upcoming covers today+14d, so nearly
    every such row duplicates a richer card ~40pt above it. But suppression must require
    that card to be genuinely present — if the tracking feed is empty or failed, the
    notification is the only copy left and must survive.
    """
    body = _balanced(
        _read(_ALERTS_TAB),
        "private func duplicatesUpcomingCard(\n        _ item: NotificationEventDTO,\n        upcomingTickers: Set<String>\n    ) -> Bool {",
    )
    assert 'item.kind == "earnings_upcoming"' in body, (
        "The suppression is no longer scoped to `earnings_upcoming` — it would start "
        "hiding unrelated notification kinds."
    )
    assert "upcomingTickers.contains(ticker)" in body, (
        "Suppression no longer checks that the Upcoming card is actually on screen. Hiding "
        "the row when the tracking feed is empty deletes the only copy of the information."
    )
    assert 'guard !ticker.isEmpty else { return false }' in body, (
        "A row with no ticker must NOT be suppressed — it cannot be proven to be a duplicate."
    )


def test_the_chip_set_is_derived_after_suppression():
    """Otherwise Earnings is offered for a row that is hidden, and filters to nothing.

    Both the available chips and the visible list must come from the SAME post-suppression
    array; deriving the chips from the raw items is a chip that empties the list on its
    first tap.
    """
    body = _balanced(_read(_ALERTS_TAB), "private func activitySection() -> some View {")
    assert "let candidates = notifications.items.filter {" in body, (
        "activitySection no longer builds a post-suppression `candidates` array."
    )
    assert "availableFilters(rollups: rollups, items: candidates)" in body, (
        "The chip set is not derived from the suppressed list — an Earnings chip can be "
        "offered whose only row is hidden."
    )
    assert "candidates.filter {" in body, (
        "The visible list is not derived from `candidates`, so suppression is bypassed."
    )


def test_repeats_collapse_only_when_adjacent():
    """Never reorder the feed.

    Grouping globally by ticker would lift a week-old row up next to today's and rewrite
    the chronology — the same "sorting on invented data" the Upcoming/Activity split
    exists to avoid.
    """
    body = _balanced(
        _read(_INBOX_SECTION),
        "static func collapse(_ items: [NotificationEventDTO]) -> [CollapsedGroup] {",
    )
    assert "groups.last" in body, (
        "collapse() no longer compares against the PREVIOUS group only — it is grouping "
        "globally, which reorders the feed."
    )
    assert "Dictionary(grouping:" not in body, (
        "collapse() is bucketing with Dictionary(grouping:), which loses feed order."
    )
    key = _balanced(
        _read(_INBOX_SECTION), "private static func groupKey(_ item: NotificationEventDTO) -> String? {"
    )
    assert "guard !ticker.isEmpty else { return nil }" in key, (
        "Rows with no ticker are being collapsed together. Two unroutable rows are not "
        "evidence of the same event."
    )


def test_a_collapsed_row_marks_every_member_read():
    """The members have no row of their own any more.

    Marking only the newest strands the rest unread forever — and holds the badge up with
    nothing on screen that can clear it, which is the exact bug the Alerts tab exists to
    fix.
    """
    src = _read(_INBOX_SECTION)
    assert "for member in group.items { await viewModel.markRead(member) }" in src, (
        "A collapsed row no longer marks every member read."
    )
    assert "isNew: group.items.contains { viewModel.showsUnreadDot($0) }" in src, (
        "The unread dot is not ORed across the group — a collapsed row can look read while "
        "hiding an unread member."
    )


def test_paging_keys_off_the_flat_list_not_the_collapsed_groups():
    """Collapsing shrinks the rendered count.

    A page that collapses heavily produces fewer rows than the trigger window, so nothing
    satisfies it and the list silently stops — the same failure as the model-tail trigger
    this replaced, one level up.
    """
    src = _read(_INBOX_SECTION)
    assert "let pagingTrigger = Set(items.suffix(3).map(\\.id))" in src, (
        "The paging trigger is no longer computed from the FLAT `items` array."
    )
    assert "guard group.items.contains(where: { pagingTrigger.contains($0.id) })" in src, (
        "Paging is decided from the collapsed groups; a heavily-collapsed page stalls."
    )
    assert "groups.suffix(" not in src, "Paging is measuring the collapsed groups."


def test_the_reports_glyph_is_the_research_tab_glyph_by_construction():
    """"The same icon as the navigator" must be derived, not copied.

    Two literals drift the moment the nav bar changes, and nothing would catch it — the
    row would simply stop matching the tab it points at.
    """
    body = _balanced(_read(_NOTIF_MODELS), "var iconName: String {")
    assert "HomeTab.research.systemIconName" in body, (
        "`research_complete` no longer reads the Research tab's own glyph. Do not hardcode "
        "the symbol name a second time — derive it from HomeTab."
    )
    assert 'case "research_complete": return "sparkles"' not in body, (
        "`research_complete` is back on the literal `sparkles`."
    )
    # And the tab really is what we think it is.
    #
    # ⚠️ Scoped to the HomeTab enum FIRST. `var systemIconName: String {` also opens a
    # different enum earlier in this file (`.whalesAlert`, `.earningsAlert`, …), so a
    # whole-file scan asserted against a switch that has no `.research` arm at all. Third
    # time this exact collision has bitten in this test file — brace-bound, always.
    home = _balanced(
        _balanced(_read(_HOME_MODELS), "enum HomeTab: String, CaseIterable {"),
        "var systemIconName: String {",
    )
    assert 'case .research: return "magnifyingglass"' in home, (
        "The Research tab's glyph changed. That is allowed — but confirm the notification "
        "row followed it (it should, automatically) and update this guard's expectation."
    )


def test_research_failed_notifies_from_exactly_one_place():
    """Both failure paths run; only one wins the claim.

    `claim_and_mark_failed` is the single atomic compare-and-set — PostgREST folds the
    `is_refunded=False` guard into one UPDATE, so exactly one of the worker's `except` and
    the reconciliation sweep gets there. A second send from `research_service` would
    double-notify on the ordinary failure path.
    """
    recon = _RECON.read_text(encoding="utf-8")
    assert "KIND_RESEARCH_FAILED" in recon, (
        "The failed-report notification is gone. A user who paid 20 credits for a report "
        "that failed is told nothing at all — the gap this kind exists to close."
    )
    assert recon.count("_notify_report_failed(") == 2, (
        "Expected exactly one call site plus the definition of `_notify_report_failed`."
    )
    svc = _RESEARCH_SVC.read_text(encoding="utf-8")
    assert "KIND_RESEARCH_FAILED" not in svc, (
        "research_service also sends the failed-report push. Both it and the sweep run on "
        "the ordinary failure path — only one wins the CAS, so this double-notifies."
    )


def test_the_failed_report_body_states_the_refund_only_when_it_happened():
    """A false reassurance is worse than silence.

    The refund is the fact that makes this notification worth sending. But the REFUND LEAK
    branch exists, and claiming credits came back when they did not sends the user to a
    balance that disproves us.
    """
    body = _py_body(_RECON.read_text(encoding="utf-8"), "async def _notify_report_failed(")
    assert "if refunded:" in body, (
        "The body no longer distinguishes a refund that happened from one that did not."
    )
    assert "credits have been" in body and "returned" in body, (
        "The refunded copy no longer states that the credits came back."
    )
    assert "ticker_route(" in body, (
        "The route is hand-written again. Use the one builder — a hand-rolled route dict is "
        "how `ticker_move` shipped without an asset_type and sent crypto to the equity screen."
    )


def test_the_ticker_move_title_carries_the_percentage():
    """Two separate moves must not read as one duplicate.

    The dedup key is per ET day, so consecutive-day alerts are genuinely different events.
    With a bare-ticker title and two LLM paraphrases of the same catalyst, they looked
    identical — which is what a tester reported. The percentage is the field that always
    differs, and it is what the alert is actually about.
    """
    src = _SWEEPER.read_text(encoding="utf-8")
    assert 'title=f"{scope} {cp:+.1f}%"' in src, (
        "The ticker_move title no longer carries the move percentage."
    )
    assert "\n                title=scope," not in src, (
        "The bare-ticker title is back — two different moves will read as one repeat again."
    )


# ------------------------------------------- round 3: detail screen before the destination


def test_a_notification_tap_opens_the_detail_not_the_ticker():
    """The reason has to be readable somewhere.

    A tap used to present `NotificationRouteDestination`, dropping the user onto the ticker
    screen — so the alert's own text existed nowhere but the row, clamped to three lines
    behind an ellipsis. For a `ticker_move` that clipped text IS the grounded catalyst, i.e.
    the answer to the question the alert raises.
    """
    tab = _read(_ALERTS_TAB)
    assert "NotificationDetailView(group: group)" in tab, (
        "The Alerts tab no longer presents the notification detail screen."
    )
    assert "NotificationRouteDestination(route:" not in tab, (
        "The Alerts tab presents the route destination directly again — the tap skips the "
        "detail and lands on the ticker, which is the behaviour a tester reported."
    )
    section = _read(_INBOX_SECTION)
    assert "selection.wrappedValue = group" in section, (
        "The row tap no longer hands the whole collapsed group to the detail screen."
    )


def test_the_detail_screen_lists_every_member_of_a_collapsed_group():
    """Otherwise collapsing HIDES notifications.

    Adjacent repeats merge into one row with a `xN` badge; six PLUG rows render as one. If the
    detail shows only `newest`, the other five are unreachable anywhere in the app.
    """
    body = _read(_NOTIF_DETAIL)
    assert "ForEach(group.items.dropFirst())" in body, (
        "The detail screen no longer lists the rest of the collapsed group — the members "
        "behind the xN badge become unreachable."
    )
    assert "group.count > 1" in body, (
        "The group card is not conditional on there being more than one member."
    )


def test_tab_destinations_are_offered_only_for_stocks():
    """Only `TickerDetailView` accepts a `TickerDestination`.

    The crypto, ETF, index and commodity screens take a bare symbol and silently ignore a tab
    they do not have. A "Financials" row on a BTC alert would open the same screen as the row
    above it — a control that lies, and one that reads as a broken tab bar.
    """
    body = _balanced(
        _read(_DESTINATIONS),
        "static func destinations(for item: NotificationEventDTO) -> [AlertDestination] {",
    )
    assert "assetType == .stock" in body, (
        "Tab destinations are no longer gated on the asset type being a stock."
    )


def _registered_kinds() -> set[str]:
    src = _KINDS.read_text(encoding="utf-8")
    found = set(re.findall(r'^KIND_[A-Z_0-9]+ = "([a-z_0-9]+)"', src, re.M))
    assert found, "no KIND_* constants found — this parity check has drifted"
    return found


def test_every_registered_kind_declares_its_tab_destination():
    """Bidirectional, like the category-to-chip guard.

    A kind with no arm falls through to `default: nil` and its detail screen offers a generic
    "Open TICKER" when it could have landed on the exact tab the alert is about. Nothing on
    screen ever looks wrong, which is why this has to be a build failure.
    """
    body = _balanced(
        _read(_DESTINATIONS),
        "static func tabDestination(\n        forKind kind: String\n    ) -> (label: String, systemImage: String, destination: TickerDestination)? {",
    )
    missing = sorted(k for k in _registered_kinds() if f'"{k}"' not in body)
    assert not missing, (
        f"notification kinds with no arm in `tabDestination(forKind:)`: {missing}. Add an "
        f"explicit case — returning nil is a fine answer, but it has to be written down."
    )


def test_both_detail_screens_use_the_one_destination_row():
    """Two hand-rolled "open the ticker" rows would drift on the first edit.

    The digest cards and the notification rows are one feed to a user; the controls have to be
    the same control.
    """
    assert "struct AlertDestinationRow: View" in _read(_DEST_ROW)
    for path, name in ((_NOTIF_DETAIL, "NotificationDetailView"), (_ALERT_DETAIL, "AlertDetailView")):
        assert "AlertDestinationRow(destination:" in _read(path), (
            f"{name} no longer renders `AlertDestinationRow`."
        )
    detail = _read(_ALERT_DETAIL)
    assert "destinationRows(for: item.ticker)" in detail, (
        "The digest cards' items no longer offer a destination — a roll-up's tickers go back "
        "to being inert text."
    )


def test_the_detail_screens_push_content_not_the_stack_wrapper():
    """`NotificationRouteDestination` wraps itself in a `NavigationStack`.

    Nesting it inside a screen that already owns a stack double-stacks: the pushed screen gets
    its own back button that pops to nothing, and the outer one disappears.
    """
    assert "struct NotificationRouteContent: View" in _read(_ROUTE_DEST), (
        "The stack-free dispatch was removed; the detail screens have nothing safe to push."
    )
    for path, name in ((_NOTIF_DETAIL, "NotificationDetailView"), (_ALERT_DETAIL, "AlertDetailView")):
        src = _read(path)
        assert "NotificationRouteContent(route:" in src, f"{name} is not pushing the content view."
        assert "NotificationRouteDestination(" not in src, (
            f"{name} pushes the NavigationStack wrapper — this double-stacks."
        )


def test_a_report_notification_opens_the_report():
    """"Tap to read the full report" has to read the full report.

    The `report` family resolved to `TickerDetailView`, so the one notification whose body
    makes an explicit promise was the one that broke it. Fixed in the dispatcher so the PUSH
    tap is fixed too, not just the in-app tap.
    """
    body = _balanced(_read(_ROUTE_DEST), "struct NotificationRouteContent: View {")
    assert "TickerReportView(ticker:" in body, (
        "The report route no longer opens the report screen."
    )
    assert "case .report(let reportId, let ticker, let persona):" in body, (
        "The report route is not unpacking the persona — the wrong persona's report opens."
    )
    dest = _balanced(
        _read(_DESTINATIONS),
        "static func destinations(for item: NotificationEventDTO) -> [AlertDestination] {",
    )
    assert 'item.kind == "research_complete"' in dest, (
        "The detail screen no longer offers the report for a research_complete notification."
    )


def test_the_whale_id_rides_along_only_when_there_is_one():
    """An empty key would decode on iOS as present-but-blank.

    Same failure mode the tab/section keys are omitted for. And it has to be built in
    `ticker_route`, the one builder — a hand-written route dict is how `ticker_move` shipped
    with no `asset_type` and sent every crypto alert to the equity screen.
    """
    body = _py_body(_KINDS.read_text(encoding="utf-8"), "def ticker_route(")
    assert "whale_id: Optional[str] = None" in body, (
        "`ticker_route` no longer accepts a whale id."
    )
    assert 'if whale_id:\n        route["whale_id"] = str(whale_id)' in body, (
        "`whale_id` is emitted unconditionally — an empty value decodes as a present-but-blank "
        "key on iOS and defeats the client's own emptiness check."
    )
    sender = _SMART_MONEY.read_text(encoding="utf-8")
    assert "whale_id=whale_id" in sender, (
        "The whale sender no longer passes the whale id it already holds for its dedup key, so "
        "a 13F alert can only ever offer the ticker."
    )


def test_the_report_route_carries_the_persona():
    """A ticker holds one report per persona.

    Six PLUG rows in one tester's feed. Without this the client's (ticker, persona)-keyed
    report screen opens its default persona and five of the six are the wrong report.
    """
    src = _RESEARCH_SVC.read_text(encoding="utf-8")
    assert '"persona": persona_key,' in src, (
        "The report-ready route no longer carries the persona."
    )
