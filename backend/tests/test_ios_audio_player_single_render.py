"""The global audio player draws ONE copy of itself, parks on the real tab bar, and keeps the
screens it floats over usable — the Money Moves "double edge" and what the audit around it found.

D1  Wiser → Money Moves (See All) showed a doubled top edge on the mini player. That screen is a
    PUSH inside the Wiser tab's NavigationStack, yet it carried `.globalAudioOverlay(...,
    showBottomMiniPlayer: true)`, the shape meant for `.fullScreenCover`s (a cover hides the root's
    player, so it has to draw its own). `RootContainerView` already draws the player over anything
    in the tab tree, so the screen got two capsules: the root's, parked on the 73pt
    `CustomTabBar.approximateHeight`, and the overlay's, parked on the real (~75pt) bar — a couple of
    points higher, its edge showing above the root's. The same overlay drew a second status island
    and a second full-screen player on the asset screens the Tracking tab pushes. The overlay now
    asks UIKit whether its host is presented and draws only then (or while that is unknown), and
    Money Moves takes no overlay at all.
D2  The root parked the capsule on that 73pt estimate. The bar is text-sized, so the intended 12pt
    gap was ~10pt at the default size and ~1pt at the largest. The bar's height is measured now.
D3  The capsule's hit shape covered the transparent gap below it, so a tap aimed at the top of the
    tab bar or of the Ask Cay AI field opened the full-screen player.
D4  Nothing in the tab tree reserved room for the floating capsule: the last ~40pt of Wiser,
    Journey, Library, Research, Whale Profile and Trade Group sat under it for good, and on
    Reports → Select it hid the Delete button entirely.
D5  `progress` was unclamped, so a playhead past the estimated duration drew past the track.
D6  Owner ruling (2026-10-31): where the player lives next to Ask Cay AI. It never floats ABOVE an
    Ask Cay AI bar (the asset screens' suggested-question chips sit there) and never as a pill at
    the top of the screen (the old status island, on iPhones without a Dynamic Island, covered the
    stock header's Search and bell — and a Dynamic Island shows Now Playing only once the user
    LEAVES the app, so hiding the player there left nothing at all). Every screen with the bar
    shows a square to its left holding the mini player's play/pause disc; the AI chat screen shows
    a pill in its top bar between history and close. Tap = play/pause, press and hold = the
    full-screen player, in both. Same on every iPhone.

Source scans with comments stripped and declarations brace-bounded; every guard is shown to fail on
the mutation it exists for. Not run on a device or simulator when written (no Swift toolchain where
this was authored) — the on-device checks are listed in the commit that added this file.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend/ios/ios"
OVERLAY = IOS / "Views/Modifiers/GlobalAudioOverlay.swift"
ROOT = IOS / "Views/Screens/RootContainerView.swift"
MINI_PLAYER = IOS / "Views/Molecules/GlobalMiniPlayer.swift"
TAB_BAR = IOS / "Views/Organisms/CustomTabBar.swift"
AUDIO_MANAGER = IOS / "Services/AudioManager.swift"
MONEY_MOVES = IOS / "Views/Screens/MoneyMovesDetailView.swift"
LEARN = IOS / "Views/Screens/LearnView.swift"
CHAT_BAR = IOS / "Views/Molecules/CaydexAIChatBar.swift"
SQUARE = IOS / "Views/Molecules/ChatBarAudioControl.swift"
CIRCLE = IOS / "Views/Atoms/PlayPauseCircle.swift"
PILL = IOS / "Views/Molecules/AudioStatusIsland.swift"
CHAT = IOS / "Views/Screens/AIChatScreen.swift"
CONTENT = IOS / "ContentView.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} is missing — every assertion below would be vacuous"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _block_after(src: str, anchor: str) -> str:
    """The brace-bounded block that follows `anchor`. Raises AssertionError (never `pytest.fail`)
    so the mutation test below can tell a guard that fired from one that crashed."""
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    raise AssertionError(f"unbalanced braces after `{anchor}`")


def _condition(block: str) -> str:
    """The condition of the first `if` in `block`."""
    at = block.index("if ")
    return block[at : block.index("{", at)]


# ── The guards, as functions of the sources so each can be shown to fire ──────────────────────


def _assert_overlay_draws_only_where_the_root_cannot(overlay: str) -> None:
    modifier = _block_after(overlay, "struct GlobalAudioOverlay: ViewModifier")
    body = _block_after(modifier, "func body(content: Content) -> some View")
    mini = _block_after(body, ".overlay(alignment: .bottom)")
    full = _block_after(body, ".overlay {")
    inset = _block_after(body, ".safeAreaInset(edge: .bottom)")
    assert "GlobalMiniPlayer()" in mini, "scanned the wrong block for the mini player"
    assert "FullScreenAudioPlayer(" in full, "scanned the wrong block for the full-screen player"
    for what, block in (("mini player", mini), ("full-screen player", full)):
        assert "drawsPlayerChrome" in _condition(block), (
            f"GlobalAudioOverlay draws its {what} without asking whether the root's own already "
            "shows: on a push inside a tab that is a second copy over the root's — the Money "
            "Moves double edge")
    assert "drawsPlayerChrome" not in inset, (
        "the bottom inset must not depend on who draws the capsule: on a pushed host it is the "
        "root's, and it floats over the screen all the same")
    assert "GlobalMiniPlayer.contentClearance" in inset, (
        "the overlay's inset no longer uses the player's own clearance")


def _assert_an_unknown_context_still_draws(overlay: str) -> None:
    assert re.search(r"@State\s+private\s+var\s+isInsideModalPresentation:\s*Bool\?", overlay), (
        "the presentation context must start UNKNOWN (nil), not as an answer")
    assert re.search(
        r"var\s+drawsPlayerChrome:\s*Bool\s*\{\s*isInsideModalPresentation\s*!=\s*false\s*\}", overlay
    ), (
        "unknown must DRAW: a failed read may only ever leave the old double render, never a "
        "cover with no player at all")
    probe = _block_after(overlay, "struct ModalPresentationReader: UIViewRepresentable")
    assert "presentingViewController != nil" in probe, (
        "the probe must ask UIKit whether the host is presented — the one fact that separates a "
        "cover (draw here) from a push inside a tab (the root draws)")
    assert "override func didMoveToWindow()" in probe and "DispatchQueue.main.async" in probe, (
        "the probe must resolve once the host is in a window, and must not write SwiftUI state "
        "synchronously from inside UIKit's hierarchy update")
    assert "isUserInteractionEnabled = false" in probe, "the probe must never take a touch"
    assert re.search(
        r"ModalPresentationReader\s*\{\s*isInsideModalPresentation\s*=\s*\$0\s*\}", overlay
    ), "the overlay no longer feeds the probe's answer into `isInsideModalPresentation`"


def _assert_money_moves_leaves_the_player_to_the_root(learn: str, money_moves: str) -> None:
    push = _block_after(learn, ".navigationDestination(isPresented: $showingMoneyMovesDetail)")
    assert "MoneyMovesDetailView()" in push, (
        "Money Moves is no longer pushed from Learn — if it became a cover it needs "
        "`.globalAudioOverlay(showBottomMiniPlayer: true)` back, and this guard needs rewriting")
    for drawn in (".globalAudioOverlay(", "GlobalMiniPlayer(", "AudioStatusIsland(",
                  "FullScreenAudioPlayer("):
        assert drawn not in money_moves, (
            f"MoneyMovesDetailView uses `{drawn}` again. It is a PUSH, so RootContainerView "
            "already draws the player over it; a second copy is the double edge")
    assert ".miniPlayerSafeArea()" in money_moves, (
        "without the overlay's inset, the Battles row needs `.miniPlayerSafeArea()` to scroll "
        "clear of the capsule")


def _assert_the_root_parks_on_the_measured_bar(tab_bar: str, root: str) -> None:
    key = _block_after(tab_bar, "struct TabBarHeightKey: PreferenceKey")
    assert re.search(r"defaultValue:\s*CGFloat\s*=\s*0\b", key), (
        "0 must mean 'not measured': a plausible height as the default would survive `max` over a "
        "real, shorter bar")
    assert "max(value, nextValue())" in key, "every tab draws a bar; the key must keep the largest"
    body = _block_after(_block_after(tab_bar, "struct CustomTabBar: View"), "var body: some View")
    emit = body.find(".preference(key: TabBarHeightKey.self, value: proxy.size.height)")
    assert emit >= 0, "CustomTabBar no longer reports its height"
    assert body.index(".padding(.bottom, AppSpacing.xl)") < emit, (
        "the bar must be measured AFTER its paddings, or the capsule parks inside the bar")
    assert ".onPreferenceChange(TabBarHeightKey.self)" in root, "the root no longer reads the bar"
    assert re.search(
        r"\.padding\(\.bottom,\s*tabBarHeight\s*>\s*0\s*\?\s*tabBarHeight\s*:\s*"
        r"CustomTabBar\.approximateHeight\)",
        root,
    ), "the root must park the capsule on the MEASURED bar, the estimate only as its fallback"


def _assert_only_the_capsule_takes_touches(mini: str) -> None:
    capsule = _block_after(mini, "private func floatingCapsulePlayer(")
    gap = capsule.index(".padding(.bottom, bottomSpacing)")
    shape = capsule.find(".contentShape(")
    tap = capsule.find(".onTapGesture")
    assert 0 <= shape < gap and 0 <= tap < gap, (
        "the capsule's hit shape and tap must come BEFORE its bottom gap: after it, the transparent "
        "strip above the tab bar or the Ask Cay AI field opens the full-screen player")
    assert ".contentShape(Rectangle())" not in capsule, (
        "a Rectangle hit shape takes the capsule's transparent corners too; use its own shape")


# ── D6: where the player lives beside Ask Cay AI ─────────────────────────────────────────────


def _assert_the_chat_bar_carries_the_square_on_its_left(chat_bar: str) -> None:
    bar = _block_after(chat_bar, "struct CaydexAIChatBar: View")
    # Declared after `isBusy`: the memberwise init is positional, so it must come last or every
    # existing call site (the report's is pinned) stops compiling.
    busy = bar.find("var isBusy: Bool = false")
    flag = bar.find("var showsAudioControl: Bool = true")
    assert 0 <= busy < flag, "`showsAudioControl` must default to true and be declared after `isBusy`"
    body = _block_after(bar, "var body: some View")
    square = body.find("ChatBarAudioControl(side: inputHeight)")
    card = body.find("HStack(spacing: AppSpacing.md)")
    surface = body.find(".cardSurface(cornerRadius: AppCornerRadius.extraLarge, elevation: .raised)")
    assert square >= 0, "the Ask Cay AI bar no longer carries the audio square"
    assert square < card < surface, (
        "the square must come BEFORE the input card (on its LEFT) and outside it — its own "
        "container, not a button inside the bar")
    assert "if showsAudioControl" in body[:square], "the square ignores `showsAudioControl`"
    assert re.search(r"\.onGeometryChange\(for: CGFloat\.self\)[^}]*size\.height", body[surface:]), (
        "the square's side must follow the input card's measured height")
    assert "inputHeight = height" in body[surface:], "the measured height never reaches the square"


def _assert_the_square_plays_and_holds_opens(square: str) -> None:
    view = _block_after(square, "struct ChatBarAudioControl: View")
    body = _block_after(view, "var body: some View")
    assert "if audioManager.hasActiveEpisode" in body, "the square must show only while an episode is active"
    assert "PlayPauseCircle(isPlaying: audioManager.isPlaying)" in body, (
        "the square must hold the mini player's disc (`PlayPauseCircle`)")
    tap = _block_after(body, ".onTapGesture")
    hold = _block_after(body, ".onLongPressGesture(minimumDuration:")
    assert "togglePlayPause()" in tap and "expandPlayer()" not in tap, "tap = play/pause"
    assert "expandPlayer()" in hold and "togglePlayPause()" not in hold, "press and hold = full player"
    assert "Button(" not in body, (
        "not a Button: a Button fires on the hold's touch-up too, toggling playback AND opening "
        "the player")
    assert "max(side, Self.minimumSide)" in body, "the square must never shrink below the disc"


def _assert_every_player_shares_the_disc(mini: str, circle: str) -> None:
    disc = _block_after(circle, "struct PlayPauseCircle: View")
    assert ".fill(AppColors.primaryFill)" in disc and "pause.fill" in disc and "play.fill" in disc
    button = _block_after(mini, "private var playPauseButton: some View")
    assert "PlayPauseCircle(isPlaying: audioManager.isPlaying)" in button, (
        "the mini player stopped drawing the shared disc — the square and the pill would drift "
        "from it")
    assert "Circle()" not in button, "the mini player draws its own disc again"


def _assert_the_chat_shows_its_pill_in_the_top_bar(chat: str, pill: str) -> None:
    top = _block_after(chat, "private var topBar: some View")
    assert re.search(r"\.overlay\s*\{\s*AudioStatusIsland\(\)\s*\}", top), (
        "the chat's player must be the pill overlaid in its top bar, between history and close")
    bars = [m.start() for m in re.finditer(r"\bCaydexAIChatBar\(", chat)]
    assert len(bars) == 1, bars
    call = chat[bars[0]: chat.index("\n            )", bars[0])]
    assert "showsAudioControl: false" in call, (
        "the chat's own Ask Cay AI bar must opt out of the square — one player per screen")
    view = _block_after(pill, "struct AudioStatusIsland: View")
    assert "if audioManager.hasActiveEpisode" in _block_after(view, "var body: some View")
    shape = _block_after(view, "private var pill: some View")
    assert "PlayPauseCircle(" in shape, "the pill must use the mini player's disc"
    tap = _block_after(shape, ".onTapGesture")
    hold = _block_after(shape, ".onLongPressGesture(minimumDuration:")
    assert "togglePlayPause()" in tap and "expandPlayer()" in hold, "tap = play/pause, hold = player"


def _assert_nothing_floats_at_the_top(root: str, overlay: str, manager: str) -> None:
    assert "AudioStatusIsland(" not in root and "AudioStatusIsland(" not in overlay, (
        "a status island floats at the top again — on iPhones without a Dynamic Island it covered "
        "the stock header's Search and bell")
    assert ".overlay(alignment: .top)" not in _block_after(overlay, "struct GlobalAudioOverlay: ViewModifier")
    assert "hasDynamicIsland" not in root + overlay + manager, (
        "the player must not branch on the Dynamic Island: iOS shows Now Playing there only once "
        "the user leaves the app")
    assert re.search(
        r"if audioManager\.hasActiveEpisode && !audioManager\.showFullScreenPlayer && "
        r"!audioManager\.isCompactMode \{", root
    ), "the root must draw nothing while the screen on top hosts its own player"


def _sources() -> dict[str, str]:
    return {
        "overlay": _code(OVERLAY),
        "root": _code(ROOT),
        "mini": _code(MINI_PLAYER),
        "tab_bar": _code(TAB_BAR),
        "learn": _code(LEARN),
        "money_moves": _code(MONEY_MOVES),
        "manager": _code(AUDIO_MANAGER),
        "chat_bar": _code(CHAT_BAR),
        "square": _code(SQUARE),
        "circle": _code(CIRCLE),
        "pill": _code(PILL),
        "chat": _code(CHAT),
    }


_GUARDS = {
    "overlay-draws-only-where-root-cannot":
        lambda s: _assert_overlay_draws_only_where_the_root_cannot(s["overlay"]),
    "unknown-context-still-draws": lambda s: _assert_an_unknown_context_still_draws(s["overlay"]),
    "money-moves-leaves-player-to-root":
        lambda s: _assert_money_moves_leaves_the_player_to_the_root(s["learn"], s["money_moves"]),
    "root-parks-on-measured-bar":
        lambda s: _assert_the_root_parks_on_the_measured_bar(s["tab_bar"], s["root"]),
    "only-capsule-takes-touches": lambda s: _assert_only_the_capsule_takes_touches(s["mini"]),
    "chat-bar-square-on-the-left":
        lambda s: _assert_the_chat_bar_carries_the_square_on_its_left(s["chat_bar"]),
    "square-tap-plays-hold-opens": lambda s: _assert_the_square_plays_and_holds_opens(s["square"]),
    "players-share-the-disc": lambda s: _assert_every_player_shares_the_disc(s["mini"], s["circle"]),
    "chat-pill-in-top-bar": lambda s: _assert_the_chat_shows_its_pill_in_the_top_bar(s["chat"], s["pill"]),
    "nothing-floats-at-the-top":
        lambda s: _assert_nothing_floats_at_the_top(s["root"], s["overlay"], s["manager"]),
}


@pytest.mark.parametrize("guard", sorted(_GUARDS))
def test_guard_holds(guard):
    _GUARDS[guard](_sources())


# (label, guard, source, old, new) — `old` must still be found, so a mutation that stopped applying
# fails loudly instead of passing as a vacuous "the guard fired".
_MUTATIONS = [
    ("mini-player-ungated", "overlay-draws-only-where-root-cannot", "overlay",
     "if drawsPlayerChrome && showBottomMiniPlayer", "if showBottomMiniPlayer"),
    ("full-player-ungated", "overlay-draws-only-where-root-cannot", "overlay",
     "if drawsPlayerChrome && audioManager.showFullScreenPlayer", "if audioManager.showFullScreenPlayer"),
    ("inset-gated-on-who-draws", "overlay-draws-only-where-root-cannot", "overlay",
     "if showBottomMiniPlayer && audioManager.hasActiveEpisode\n",
     "if drawsPlayerChrome && showBottomMiniPlayer && audioManager.hasActiveEpisode\n"),
    ("unknown-hides", "unknown-context-still-draws", "overlay",
     "isInsideModalPresentation != false", "isInsideModalPresentation == true"),
    ("probe-asks-the-wrong-question", "unknown-context-still-draws", "overlay",
     "controller.presentingViewController != nil", "controller.isBeingPresented"),
    ("money-moves-overlay-back", "money-moves-leaves-player-to-root", "money_moves",
     ".navigationBarHidden(true)",
     ".navigationBarHidden(true)\n        .globalAudioOverlay(token: UUID().uuidString, showBottomMiniPlayer: true)"),
    ("money-moves-no-room", "money-moves-leaves-player-to-root", "money_moves",
     ".miniPlayerSafeArea()", ""),
    ("root-on-the-estimate", "root-parks-on-measured-bar", "root",
     "tabBarHeight > 0 ? tabBarHeight : CustomTabBar.approximateHeight", "CustomTabBar.approximateHeight"),
    ("bar-reports-nothing", "root-parks-on-measured-bar", "tab_bar",
     ".preference(key: TabBarHeightKey.self, value: proxy.size.height)",
     ".preference(key: TabBarHeightKey.self, value: 0)"),
    ("plausible-default", "root-parks-on-measured-bar", "tab_bar",
     "static var defaultValue: CGFloat = 0", "static var defaultValue: CGFloat = 73"),
    ("hit-shape-over-the-gap", "only-capsule-takes-touches", "mini",
     ".padding(.bottom, bottomSpacing)", ".padding(.bottom, bottomSpacing)\n        .contentShape(Rectangle())"),
    ("square-moves-right", "chat-bar-square-on-the-left", "chat_bar",
     "                if showsAudioControl {\n                    ChatBarAudioControl(side: inputHeight)\n                }\n",
     ""),
    ("square-off-by-default", "chat-bar-square-on-the-left", "chat_bar",
     "    var showsAudioControl: Bool = true\n", "    var showsAudioControl: Bool = false\n"),
    ("square-tap-opens-player", "square-tap-plays-hold-opens", "square",
     "                    .onTapGesture {\n                        withAnimation(.spring(response: 0.2)) {\n"
     "                            audioManager.togglePlayPause()",
     "                    .onTapGesture {\n                        withAnimation(.spring(response: 0.2)) {\n"
     "                            audioManager.expandPlayer()"),
    ("square-hold-does-nothing", "square-tap-plays-hold-opens", "square",
     "                        audioManager.expandPlayer()\n                    }\n                    .accessibilityElement",
     "                    }\n                    .accessibilityElement"),
    ("mini-player-own-disc", "players-share-the-disc", "mini",
     "            PlayPauseCircle(isPlaying: audioManager.isPlaying)\n",
     "            Circle().fill(AppColors.primaryFill).frame(width: 44, height: 44)\n"),
    ("chat-bar-keeps-square", "chat-pill-in-top-bar", "chat",
     "                showsAudioControl: false\n", ""),
    ("chat-pill-gone", "chat-pill-in-top-bar", "chat",
     "            AudioStatusIsland()\n", "            EmptyView()\n"),
    ("island-floats-again", "nothing-floats-at-the-top", "overlay",
     "            .overlay(alignment: .bottom) {",
     "            .overlay(alignment: .top) { AudioStatusIsland() }\n            .overlay(alignment: .bottom) {"),
    ("root-draws-while-compact", "nothing-floats-at-the-top", "root",
     "if audioManager.hasActiveEpisode && !audioManager.showFullScreenPlayer && !audioManager.isCompactMode {",
     "if audioManager.hasActiveEpisode && !audioManager.showFullScreenPlayer {"),
]


@pytest.mark.parametrize("label,guard,key,old,new", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, key, old, new):
    sources = _sources()
    assert old in sources[key], f"{label}: the mutation no longer applies — re-point it"
    sources[key] = sources[key].replace(old, new, 1)
    with pytest.raises(AssertionError):
        _GUARDS[guard](sources)


# ── Who may draw the capsule at all ───────────────────────────────────────────────────────────

# Every file that renders `GlobalMiniPlayer(`, and why it may. A screen that draws its own is only
# right if it is a COVER (the root's capsule is hidden there); a push in a tab must leave it to the
# root. A new entry is a decision to review, not a formality.
_MINI_PLAYER_HOSTS = {
    "Views/Screens/RootContainerView.swift": "the tab tree's capsule, parked on the tab bar",
    "Views/Modifiers/GlobalAudioOverlay.swift": "covers' capsule, gated on drawsPlayerChrome",
    "Views/Molecules/GlobalMiniPlayer.swift": "its own #Preview",
}


def test_only_the_known_hosts_draw_the_mini_player():
    drawn = {
        str(path.relative_to(IOS))
        for path in IOS.rglob("*.swift")
        if "GlobalMiniPlayer(" in _strip_comments(path.read_text(encoding="utf-8"))
    }
    assert len(drawn) >= 3, f"the scan found almost nothing ({sorted(drawn)}) — it has drifted"
    unexpected = sorted(drawn - set(_MINI_PLAYER_HOSTS))
    assert not unexpected, (
        f"{unexpected} now draw(s) `GlobalMiniPlayer(`. If the screen is pushed inside a tab, the "
        "root already draws one there — that is the Money Moves double edge. If it has an Ask Cay "
        "AI bar, its player is the square beside the bar (D6), never a capsule above it. Otherwise "
        "add it to _MINI_PLAYER_HOSTS with the reason.")
    gone = sorted(set(_MINI_PLAYER_HOSTS) - drawn)
    assert not gone, f"{gone} no longer draw the player; drop them from _MINI_PLAYER_HOSTS"


# ── Room for the capsule on the screens it floats over ────────────────────────────────────────

_RESERVES_ROOM = [
    ("Views/Screens/LearnView.swift", "private var learnTabContent: some View"),
    ("Views/Screens/InvestorJourneyView.swift", "struct InvestorJourneyView: View"),
    ("Views/Screens/BookLibraryView.swift", "struct BookLibraryView: View"),
    ("Views/Screens/MoneyMovesDetailView.swift", "struct MoneyMovesDetailView: View"),
    ("Views/Screens/WhaleProfileView.swift", "struct WhaleProfileView: View"),
    ("Views/Screens/TradeGroupDetailView.swift", "struct TradeGroupDetailView: View"),
    ("ContentView.swift", "private var researchTabContent: some View"),
    ("ContentView.swift", "private var reportsTabContent: some View"),
]


@pytest.mark.parametrize(
    "rel,decl", _RESERVES_ROOM, ids=[d.split(":")[0].split()[-1] for _, d in _RESERVES_ROOM]
)
def test_in_tab_screens_scroll_clear_of_the_player(rel, decl):
    block = _block_after(_code(IOS / rel), decl)
    assert "ScrollView(" in block, f"{rel} :: {decl} holds no scroll view — the scan has drifted"
    assert ".miniPlayerSafeArea()" in block, (
        f"{rel} :: {decl} lost `.miniPlayerSafeArea()`. The root's capsule floats over this screen "
        "and its list ends in 32–40pt of padding, so the last ~40pt sit under the player for good.")


def test_the_reports_delete_button_clears_the_player():
    research = _block_after(_code(CONTENT), "struct ResearchViewWithBinding: View")
    assert re.search(r"@Environment\(\\\.miniPlayerVisible\)\s+private\s+var\s+miniPlayerVisible", research)
    reports = _block_after(research, "private var reportsTabContent: some View")
    bar = reports.index("ReportsSelectionBar(")
    lift = reports.find(".padding(.bottom, miniPlayerVisible ? GlobalMiniPlayer.footprint : 0)", bar)
    assert lift > bar, (
        "the Reports selection bar no longer lifts above the mini player: the capsule is parked over "
        "exactly that strip, drawn on top of the tab, and hides Delete while audio plays")


# ── One clearance, one clamp ──────────────────────────────────────────────────────────────────


def test_one_clearance_for_the_floating_player():
    mini = _code(MINI_PLAYER)
    assert re.search(r"static let footprint: CGFloat = playerHeight \+ AppSpacing\.md\b", mini)
    assert re.search(r"static let contentClearance: CGFloat = footprint \+ AppSpacing\.lg\b", mini)
    root = _code(ROOT)
    for modifier in ("struct MiniPlayerSafeAreaModifier: ViewModifier",
                     "struct MiniPlayerContentInsetModifier: ViewModifier"):
        assert "GlobalMiniPlayer.contentClearance" in _block_after(root, modifier), modifier
    assert not re.search(r"\b104\b", root), "the stale 104pt clearance is back in RootContainerView"
    inset = _block_after(_code(OVERLAY), ".safeAreaInset(edge: .bottom)")
    assert not re.search(r"\.frame\(height:\s*\d+\)", inset), (
        "the overlay's inset went back to a literal instead of GlobalMiniPlayer.contentClearance")


def test_progress_is_clamped_to_its_track():
    progress = _block_after(_code(AUDIO_MANAGER), "var progress: Double")
    assert "min(max(currentTime / duration, 0), 1)" in progress, (
        "`progress` is unclamped again: a playhead past the estimated duration draws the mini "
        "player's fill and the full-screen slider past the end of their tracks")


def test_the_comment_stripper_strips():
    raw = OVERLAY.read_text(encoding="utf-8")
    assert "double edge" in raw, "the overlay no longer explains the double render it prevents"
    assert "double edge" not in _strip_comments(raw), "comment stripping regressed"
