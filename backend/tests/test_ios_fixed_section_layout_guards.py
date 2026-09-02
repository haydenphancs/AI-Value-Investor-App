"""A lazy stack whose children are a FIXED list must stay eager — app-wide.

`test_ios_home_layout_guards.py` pins this rule for `HomeDashboardView` and nothing else, which
is how `AppSettingsView` and `ProfileView` were free to carry the same shape for months. On
2026-09-01 a wedge was captured on App Settings — 2206/2206 main-thread samples inside
`CA::Transaction::commit -> _UIHostingView.layoutSubviews -> LazySubviewPlacements.updateValue
-> LazyStack.place -> _ViewList_Node.applyNodes`, taps and scrolls both dead — the same
recursion the Home incident measured growing 196 -> 590 frames between samples.

THE RULE, and both halves are load-bearing:

  * A lazy container whose DIRECT children are a fixed, hand-written list must be a plain
    `VStack`. Laziness virtualizes nothing there, and a child that RESIZES IN PLACE invalidates
    the cached subview sizes mid-placement, restarting the predecessor walk.
  * A lazy container over UNBOUNDED, data-driven children must STAY lazy. The inverse mistake —
    an over-eager sweep — would materialize ~50 news articles with their AsyncImages, or the
    whole chat backlog, on the main thread at once. That failure has also already shipped here
    (see `UpdatesView`'s de-nesting comment), so it gets a guard too.

Per `.claude/rules/testing.md` §3 and `project_source_scan_guard_vacuity`, every scan below is
comment-stripped (the fix comments name `LazyVStack` while explaining why it is gone, so an
un-stripped scan passes on the prose after a revert), brace-bounded to the declaration meant,
and carries an anti-vacuity token. Mutation-tested by hand on 2026-09-01.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"

_LAZY = ("LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid")


def _read(rel: str) -> str:
    p = _IOS / rel
    if not p.exists():
        pytest.fail(f"{rel} moved — re-point this guard, do not delete it")
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
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


# ── Must be EAGER: fixed, hand-written children ───────────────────────────────
#
# (file, declaration header, anti-vacuity token, why it resizes in place)
_EAGER = [
    ("Views/Screens/ProfileView.swift", "var body: some View", "creditManagementSection",
     "userIdentitySection is a 3-way async branch in SLOT 0 and the credits section is inserted "
     "on the same flag"),
    ("Views/Screens/AppSettingsView.swift", "var body: some View", "generalSection",
     "the password row switches between Change Password and Set a Password, two different "
     "heights, on `hasPassword` arriving from GET /users/me"),
    ("ContentView.swift", "private var researchTabContent: some View", "PersonaSelectionSection",
     "the credits card is inserted once effectiveCreditBalance resolves"),
    ("ContentView.swift", "private var reportsTabContent: some View", "ReportsListSection",
     "a 72pt spacer animates in and out on isSelectingReports"),
    ("Views/Organisms/TickerHoldersContent.swift", "var body: some View", "RecentActivitiesSection",
     "RecentActivitiesSection swaps three tab bodies and three Show-All toggles"),
    ("Views/Screens/WhaleProfileView.swift", "var body: some View", "profile",
     "profile.isLocked swaps the locked stub for two full sections when the tier changes"),
    ("Views/Screens/SearchView.swift", "var body: some View", "RecentSearchesSection",
     "Recent vs Results swap on every keystroke"),
    ("Views/Screens/AllWhalesView.swift", "var body: some View", "AllWhalesFlatList",
     "a 3-way swap between a full list and a short empty state as the user types"),
    ("Views/Screens/NotificationsSettingsView.swift", "var body: some View", "NotificationPermissionBanner",
     "InlineRetryNotice is inserted after registerIfAuthorized() resolves"),
    ("Views/Screens/LearnView.swift", "private var learnTabContent: some View", "fullLearnDashboard",
     "the books and money-moves sections land async behind `if !isEmpty`"),
    ("Views/Screens/InvestorJourneyView.swift", "var body: some View", "ChatWithBookPromptCard",
     "four `if let getLevelProgress(...)` sections populate from JourneyProgressStore.hydrate()"),
    ("Views/Screens/MoneyMovesDetailView.swift", "var body: some View", "MoneyMovesCategorySection",
     "an optional hero and bookmark row appear and disappear as bookmarks change"),
    ("Views/Screens/TrendingAnalysisDetailView.swift", "var body: some View", "heroSection",
     "nothing resizes today; kept eager so a future conditional cannot silently re-arm it"),
    ("Views/Screens/DisclaimersView.swift", "var body: some View", "DisclaimerCard",
     "static cards; laziness was pointless"),
]


@pytest.mark.parametrize(
    "rel,header,token,why", _EAGER, ids=[f"{r.split('/')[-1]}:{h.split()[-3]}" for r, h, _, _ in _EAGER]
)
def test_fixed_section_container_is_not_lazy(rel, header, token, why):
    block = _decl_block(_read(rel), header)

    # Anti-vacuity: prove the brace-bounded block really is the one meant. Without this a
    # renamed declaration would make the assertion below pass on unrelated code.
    assert token in block, (
        f"{rel} :: {header} no longer contains {token!r} — this scan has drifted onto the wrong "
        f"block and is proving nothing. Re-point it."
    )

    for lazy in _LAZY:
        assert lazy not in block, (
            f"{rel} :: {header} is using {lazy} again. Its direct children are a fixed, "
            f"hand-written list, so laziness virtualizes nothing — and a child here resizes in "
            f"place ({why}), which is what wedges the main thread inside "
            f"LazySubviewPlacements -> _ViewList_Node.applyNodes. Use a plain VStack."
        )


# ── Must STAY LAZY: unbounded, data-driven children ───────────────────────────
#
# The inverse guard. An over-eager sweep is its own shipped failure mode.
_LAZY_REQUIRED = [
    ("Views/Screens/UpdatesView.swift", "var body: some View",
     "~50 news articles with AsyncImage thumbnails, and the only pinnedViews stack left"),
    ("Views/Organisms/ChatMessagesList.swift", "var body: some View",
     "the unbounded chat backlog"),
    ("Views/Organisms/AlertsTabContent.swift", "var body: some View",
     "the notification inbox pages 30 rows at a time"),
    ("Views/Screens/CreditHistoryView.swift", "var body: some View",
     "an unbounded credit ledger"),
    ("Views/Organisms/TickerNewsContent.swift", "var body: some View",
     "unbounded news rows with AsyncImage thumbnails — DetailScrollContainer explicitly blesses this one"),
]


@pytest.mark.parametrize(
    "rel,header,why", _LAZY_REQUIRED, ids=[r.split("/")[-1] for r, _, _ in _LAZY_REQUIRED]
)
def test_unbounded_container_stays_lazy(rel, header, why):
    block = _decl_block(_read(rel), header)
    assert any(lazy in block for lazy in _LAZY), (
        f"{rel} :: {header} lost its lazy container. It renders {why}, so going eager "
        f"materializes all of it on the main thread at once — the opposite failure, and one "
        f"this codebase has also already shipped. Keep it lazy."
    )
