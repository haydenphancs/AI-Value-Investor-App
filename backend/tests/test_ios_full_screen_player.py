"""Books full-screen player (`FullScreenAudioPlayer`) — TestFlight 1.0(8), wiser_learn E2–E4.

E2  Share was an empty action closure (a visible, dead button), and so was the ••• button.
    Developer decisions (2026-09-22): Share sends the book + current chapter + the app link through
    `ShareContent`; ••• opens a menu — Go to Text · Share · Stop Playback.
E3  The "Read" (back to the text) control only rendered when a host passed `onNavigateToCore`, and
    only the two Book screens did — so it vanished when the player was expanded from a tab root,
    which is exactly the tester's screenshot. Those two hosts also applied the core number to THEIR
    book, so with book A playing and book B open, Read opened B. Read now sits between Speed and
    Sleep (tester directive) and works from every host.
E4  `.ignoresSafeArea()` on the GeometryReader zeroes its `safeAreaInsets`, so the header's
    `.padding(.top, geometry.safeAreaInsets.top)` was 0 and the header sat inside the Dynamic
    Island band (and the bottom row over the home indicator). Insets now come from the key window.
    Related: `AudioArtworkLarge`'s glow sized the layout (392pt on a 402pt screen, masked by
    lopsided padding and an `offset(x: -7)`).

Source scans with comments stripped and declarations brace-bounded. The layout, Read routing (root
host, another book's detail screen, a Money Moves article cover), Share payload and menu were
verified on the iPhone 17 Pro simulator.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend/ios/ios"
PLAYER = IOS / "Views/Screens/FullScreenAudioPlayer.swift"
ROOT = IOS / "Views/Screens/RootContainerView.swift"
OVERLAY = IOS / "Views/Modifiers/GlobalAudioOverlay.swift"
BOOK_DETAIL = IOS / "Views/Screens/BookDetailView.swift"
BOOK_CORE = IOS / "Views/Screens/BookCoreDetailView.swift"
ARTWORK = IOS / "Views/Atoms/AudioArtworkThumbnail.swift"
AUDIO_MANAGER = IOS / "Services/AudioManager.swift"
WINDOW_METRICS = IOS / "Core/Utilities/WindowMetrics.swift"
LEARN_MODELS = IOS / "Models/LearnModels.swift"


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
    return _strip_comments(path.read_text())


def _block_after(src: str, anchor: str) -> str:
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
    pytest.fail(f"unbalanced braces after `{anchor}`")


def _player_struct() -> str:
    return _block_after(_code(PLAYER), "struct FullScreenAudioPlayer: View")


# ---------------------------------------------------------------------------------------------
# E4 — header below the Dynamic Island, content centred
# ---------------------------------------------------------------------------------------------

def test_the_player_reads_window_insets_not_the_zeroed_proxy_insets():
    player = _player_struct()
    assert "safeAreaInsets" not in player.replace("WindowMetrics.safeAreaInsets", ""), (
        "a GeometryReader that ignores the safe area reports ZERO insets — reading them put the "
        "header inside the Dynamic Island band")
    body = _block_after(player, "var body: some View")
    assert "let insets = WindowMetrics.safeAreaInsets" in body
    assert ".padding(.top, insets.top)" in body
    assert ".frame(height: insets.bottom + AppSpacing.xl)" in body, (
        "the bottom row must clear the home indicator")


def test_the_player_still_spans_the_whole_window():
    """Removing the outer ignore would make `.move(edge: .bottom)` stop short of the screen edge
    (the background's safe-area extension would stay visible) and let the keyboard squeeze it."""
    body = _block_after(_player_struct(), "var body: some View")
    reader_close = body.rfind("}", 0, body.find(".sheet(isPresented: $showSpeedPicker)"))
    tail = body[reader_close:]
    assert tail.lstrip("}\n ").startswith(".ignoresSafeArea()"), (
        "the GeometryReader itself must keep `.ignoresSafeArea()`")
    assert "backgroundGradient\n                    .ignoresSafeArea()" in body


def test_one_window_lookup_shared_with_has_dynamic_island():
    metrics = _code(WINDOW_METRICS)
    assert "connectedScenes" in metrics and "isKeyWindow" in metrics
    manager = _code(AUDIO_MANAGER)
    island = _block_after(manager, "static var hasDynamicIsland: Bool")
    assert "WindowMetrics.safeAreaInsets.top" in island
    assert "connectedScenes" not in manager, "AudioManager grew its own window lookup again"


def test_content_is_symmetric_again():
    player = _player_struct()
    assert ".padding(.trailing, AppSpacing.xxxl)" not in player
    assert ".padding(.leading, AppSpacing.sm)" not in player
    assert ".offset(x: -7)" not in player
    body = _block_after(player, "var body: some View")
    assert ".padding(.horizontal, AppSpacing.sm)" in body


def test_the_artwork_glow_is_decoration_not_layout():
    art = _block_after(_code(ARTWORK), "struct AudioArtworkLarge: View")
    body = _block_after(art, "var body: some View")
    glow = body.find(".frame(width: size * 1.4, height: size * 1.4)")
    outer = body.rfind(".frame(width: size, height: size)")
    assert 0 <= glow < outer, body
    # The outer frame belongs to the OUTERMOST ZStack: nothing but `.onAppear` follows it.
    assert body[outer:].count("}") == 2 and ".onAppear" in body[outer:], (
        "the artwork's layout frame must be applied to the outer ZStack, not a nested view")


def test_the_artwork_shrinks_before_anything_else():
    player = _player_struct()
    art = _block_after(player, "private var artworkSection: some View")
    assert ".frame(maxWidth: maxArtworkSize, maxHeight: maxArtworkSize)" in art
    assert ".aspectRatio(1, contentMode: .fit)" in art and ".zIndex(-1)" in art
    reader = _block_after(art, "GeometryReader { box in")
    assert re.search(
        r"AudioArtworkLarge\(episode:\s*episode,\s*size:\s*max\(1,\s*min\(box\.size\.width,\s*box\.size\.height\)\)\)",
        reader), "the artwork must be sized from the shrinking box; a fixed size overflows it"
    body = _block_after(player, "var body: some View")
    assert body.count("Spacer(minLength: AppSpacing.sm)\n                        .layoutPriority(-1)") == 2


def test_the_header_block_was_trimmed():
    header = _block_after(_player_struct(), "private var headerSection: some View")
    assert "VStack(spacing: AppSpacing.sm)" in header
    assert ".padding(.top, AppSpacing.xs)" in header


# ---------------------------------------------------------------------------------------------
# E3 — Read from every host, between Speed and Sleep
# ---------------------------------------------------------------------------------------------

def test_read_sits_between_speed_and_sleep():
    row = _block_after(_player_struct(), "private var secondaryControlsSection: some View")
    order = [row.find(f'Text("{label}")') for label in ("Speed", "Read", "Sleep", "Share")]
    assert all(i >= 0 for i in order), order
    assert order == sorted(order), f"expected Speed · Read · Sleep · Share, got offsets {order}"
    read = _block_after(row, "if let route = readerRoute")
    assert "openReader(route)" in read


def test_read_is_offered_only_for_a_route_the_catalog_resolves():
    route = _block_after(_player_struct(), "private var readerRoute: NarratedCoreRoute?")
    assert "onNavigateToCore != nil" in route
    assert "LibraryBook.narratedCore(for: route) == nil ? nil : route" in route
    opener = _block_after(_player_struct(), "private func openReader(")
    call = opener.find("onNavigateToCore?(route)")
    collapse = opener.find("audioManager.collapsePlayer()")
    assert 0 <= call < collapse, opener


def test_every_host_hands_the_player_a_handler():
    root = _code(ROOT)
    assert "FullScreenAudioPlayer(onNavigateToCore: { readerRoute = $0 })" in root
    assert "FullScreenAudioPlayer()" not in root, "the root host is the one the tester was on"
    assert ".narratedCoreReader(item: $readerRoute)" in root
    reset = _block_after(root, ".onPresentationReset")
    assert "readerRoute = nil" in reset
    overlay = _code(OVERLAY)
    assert "FullScreenAudioPlayer(onNavigateToCore: openNarratedCore)" in overlay
    assert ".narratedCoreReader(item: $readerRoute)" in overlay
    # Only previews may build a handler-less player.
    for path in IOS.rglob("*.swift"):
        code = _strip_comments(path.read_text())
        code = re.sub(r"#Preview\s*\{.*", "", code, flags=re.S)
        assert "FullScreenAudioPlayer()" not in code, f"{path} builds a player with no Read handler"


def test_the_overlay_jumps_in_place_only_for_the_same_book():
    handler = _block_after(_code(OVERLAY), "private func openNarratedCore(")
    same = handler.find("host.curriculumOrder == route.curriculumOrder")
    jump = handler.find("host.openCore(route.coreNumber)")
    other = handler.find("readerRoute = route")
    assert 0 <= same < jump < other, handler


@pytest.mark.parametrize("path", [BOOK_DETAIL, BOOK_CORE], ids=lambda p: p.name)
def test_book_hosts_declare_which_book_they_show(path):
    code = _code(path)
    call = code.find(".globalAudioOverlay(token: compactToken, readerHost: BookReaderHost(")
    assert call >= 0, "a Book screen must pass its book, or Read jumps to the wrong book"
    host = _block_after(code[call:], "openCore:")
    assert "curriculumOrder: book.curriculumOrder" in code[call : call + 200]
    assert "coreNumber" in host
    assert "onNavigateToCore" not in code


def test_the_reader_cover_carries_its_environment():
    cover = _block_after(_code(OVERLAY), "private struct NarratedCoreReaderCover: ViewModifier")
    assert ".fullScreenCover(item: $route)" in cover, (
        "item-based: `isPresented:` silently fails to present inside another cover")
    for needed in (".environmentObject(AudioManager.shared)", ".environment(appState)",
                   ".environment(\\.appState, appState)", "LibraryBook.narratedCore(for: route)",
                   "self.route = nil"):
        assert needed in cover, needed


def test_the_catalog_lookup_uses_the_static_instances():
    lookup = _block_after(_code(LEARN_MODELS), "static func narratedCore(for route: NarratedCoreRoute)")
    assert "sampleData.first(where: { $0.curriculumOrder == route.curriculumOrder })" in lookup, (
        "a freshly built LibraryBook gets a new UUID and the reader loses its read-along")
    assert "coreChapters.first(where: { $0.number == route.coreNumber })" in lookup


# ---------------------------------------------------------------------------------------------
# E2 — Share and the ••• menu are live
# ---------------------------------------------------------------------------------------------

def test_share_presents_the_book_and_chapter_through_share_content():
    player = _player_struct()
    row = _block_after(player, "private var secondaryControlsSection: some View")
    share_at = row.find('Text("Share")')
    share_btn = row[row.rfind("Button(action:", 0, share_at):share_at]
    assert "showShareSheet = true" in share_btn, "the Share button's action is dead again"
    assert ".sheet(isPresented: $showShareSheet) {\n            ShareSheet(items: shareItems)" in player
    assert "ShareContent.items(shareBody)" in _block_after(player, "private var shareItems: [Any]")
    body = _block_after(player, "private var shareBody: String")
    assert "episode.title, episode.subtitle" in body
    assert '"Core \\(core.number): \\(coreTitle)"' in body and '"Core \\(core.number)"' in body
    assert ".filter { !$0.isEmpty }" in body, "empty parts must not leave blank lines"


def test_the_more_menu_offers_go_to_text_share_and_stop():
    menu = _block_after(_player_struct(), "private var moreOptionsMenu: some View")
    assert menu.lstrip("{ \n").startswith("Menu {"), "••• must be a Menu"
    text = menu.find('Label("Go to Text"')
    share = menu.find('Label("Share"')
    stop = menu.find('Label("Stop Playback"')
    assert 0 <= text < share < stop, menu
    assert "openReader(route)" in menu[:text]
    assert "showShareSheet = true" in menu[text:share]
    assert "Button(role: .destructive)" in menu[share:stop]
    assert "audioManager.stop()" in menu[share:stop]
    assert '.accessibilityLabel("More options")' in menu
    assert ".contentShape(Rectangle())" in menu
