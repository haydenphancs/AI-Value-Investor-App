"""Guards against the Home-feed layout hang.

WHY THIS FILE EXISTS. Expanding a Daily Scanners card and then scrolling Home pinned the main
thread at 100% inside ONE non-terminating `GraphHost.runTransaction`:

    _UIHostingView.beginTransaction
      → GraphHost.flushTransactions → GraphHost.runTransaction
        → LazySubviewPlacements.placeSubviews
          → LazyHVStack<>.lengthAndSpacing(subviews:predecessors:minorGeometry:)
            → _ViewList_Node.applyNodes ... (recursion depth grew 196 → 590 between samples)

It never recovered — the app had to be force-quit. Measured with `sample <pid>`: 100.0% of
main-thread samples busy before the fix, 2.9% after, with `LazySubviewPlacements.placeSubviews`
going from 770/578 samples to zero.

A lazy stack caches each subview's measured size and derives every offset by walking its
predecessors, so a child that RESIZES IN PLACE invalidates that cache mid-placement and
restarts the walk. Home has exactly such a child: a `ScannerCard` expands inline, animating the
horizontal carousel's height. Users reached the hang through the App-Exclusive Signals lock,
because `onLockedTap` collapses the expanded scanner card with `withAnimation` — the same
in-place resize — so it read as "tapping the lock freezes the app".

Nothing about this is visible in a build or a unit test: both layouts compile, and both render
identically until the resize happens. Per `.claude/rules/testing.md` §3 and the
`project_source_scan_guard_vacuity` memory, every scan below is brace-bounded, comment-stripped,
and was mutation-tested by hand.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_HOME = _IOS / "Views/Screens/HomeDashboardView.swift"
_SCANNERS = _IOS / "Views/Organisms/DailyScannersSection.swift"
_SCANNER_CARD = _IOS / "Views/Molecules/ScannerCard.swift"
_THEME_TILE = _IOS / "Views/Molecules/TrendingThemeTile.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails.

    Load-bearing here: the fix's own comment names `LazyVStack`, `LazySubviewPlacements` and
    `LazyHVStack` while explaining why they are gone. An un-stripped scan for "LazyVStack"
    would fail on the explanation, and an un-stripped scan for its ABSENCE would pass on a
    revert whose comment survived.
    """
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _modifier_chain(src: str, anchor: str) -> str:
    """The `anchor` line plus the `.modifier(...)` lines chained onto it, comments stripped.

    Needed because a whole declaration block is too coarse to assert a modifier on ONE view:
    `ScannerCard.header` contains a second `.fixedSize(horizontal: false, vertical: true)` on
    the info popover's Text, so `token in block` passed even with the title's removed. A
    mutation test caught it.
    """
    code = _strip_comments(src)
    start = code.find(anchor)
    assert start != -1, f"{anchor!r} not found — this scan has drifted"
    lines = code[start:].splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        if line.strip().startswith("."):
            out.append(line)
        elif not line.strip():
            continue
        else:
            break
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


# ── 1. The fix: Home's feed must not be laid out lazily ────────────────────────


def test_home_scroll_content_is_not_a_lazy_stack():
    block = _decl_block(_read(_HOME), "private var content: some View")

    # Anti-vacuity: prove this really is the Home feed and not some other block. Without
    # these, a renamed `content` would make the assertions below pass on an empty string.
    assert "DailyScannersSection(" in block, "scan drifted — Home feed no longer builds Daily Scanners"
    assert "ExclusiveSignalsSection(" in block, "scan drifted — Home feed no longer builds the signals card"

    for lazy in ("LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid"):
        assert lazy not in block, (
            f"Home's scroll content is using {lazy} again. It contains a section that resizes "
            "IN PLACE (an expanded ScannerCard animates the carousel's height), and a lazy "
            "stack re-walks its predecessor chain when a cached subview size is invalidated "
            "mid-placement — measured as a permanent 100%-CPU main-thread hang that required "
            "a force-quit. Home has at most eight fixed sections built from in-memory data, "
            "so laziness buys nothing here. Use a plain VStack."
        )


# ── 2. The precondition that makes rule 1 necessary ────────────────────────────


def test_scanner_card_still_expands_in_place():
    """If this ever stops being true, revisit rule 1 rather than assuming it still applies.

    The hang needs a subview whose height changes while its parent is placing it. That is the
    inline leaderboard: `ScannerCard` reveals it inside its own body, animated, instead of
    pushing a sheet or a detail screen.
    """
    block = _decl_block(_read(_SCANNER_CARD), "var body: some View")
    assert "if isExpanded {" in block, (
        "ScannerCard no longer expands in place. That was the resize driving the Home hang — "
        "re-check whether the VStack rule in this file is still the right fix."
    )
    assert "value: isExpanded" in block, "ScannerCard's expansion is no longer animated"


# ── 3. The sibling deadlock in the same family, already fixed once ─────────────


def test_daily_scanners_does_not_write_layout_state_during_layout():
    """`.scrollPosition(id:)` writes its binding back DURING layout to keep the focused item in
    place when content size changes — which is precisely what an expanding card does every
    frame. That rewrite re-invalidates layout and froze the main thread; the carousel derives
    its active page read-only from a PreferenceKey instead. Same failure family as rule 1.
    """
    src = _strip_comments(_read(_SCANNERS))
    assert ".scrollPosition(" not in src, (
        "DailyScannersSection is using .scrollPosition again. Its layout-time state write "
        "deadlocks SwiftUI when a card resizes inside the carousel — see the header comment "
        "in DailyScannersSection.swift. Derive the active page read-only instead."
    )
    # Anti-vacuity: the read-only derivation it was replaced with must still be there.
    assert "onPreferenceChange" in src, "the read-only active-page derivation is gone"


# ── 4. A fill-mode image must not size the container its overlay anchors to ────


def test_theme_tile_change_chip_anchors_to_the_band_not_the_photo():
    """The Emerging Frontiers percentage capsule was rendering with its top sheared off.

    `themeImage` is `contentMode: .fill`, so it reports an OVERSIZED intrinsic size — a 5:4
    hero in a ~192pt-wide tile wants ~152pt for a 116pt band. A ZStack sizes to its largest
    child, so the stack took the PHOTO's height, `.topTrailing` anchored the chip to the
    photo's top rather than the band's, and `.frame(height:)` then centre-cropped the stack.
    The chip lost half the overflow off the top.

    It varied per tile with each photo's aspect ratio, which is what made it read as a text
    clipping problem rather than a layout one. A background and an overlay are sized by their
    host and never drive layout, so both anchor to the band.
    """
    band = _decl_block(_read(_THEME_TILE), "private var imageBand: some View")

    # Anti-vacuity: prove this is the band that sizes the hero.
    assert "imageHeight" in band, "scan drifted — imageBand no longer sizes itself"

    assert "ZStack" not in band, (
        "TrendingThemeTile's image band is a ZStack again. A `contentMode: .fill` image "
        "reports an oversized intrinsic height and a ZStack sizes to its largest child, so "
        "the `.topTrailing` chip anchors to the photo instead of the band and gets "
        "centre-cropped. Keep the hero as `.background` and the chip as `.overlay`."
    )
    assert ".background(themeImage)" in band, "the hero must be a non-sizing background"
    assert ".overlay(alignment: .topTrailing)" in band, (
        "the change chip must be a non-sizing overlay on the sized band"
    )


# ── 5. Expandable content must not slide across what sits above it ─────────────

# Components that reveal expandable content as the LAST child of a container that also
# holds header/hero/CTA content above it. Deliberately a fixed list: the rule is about
# WHERE the content is revealed, not about `.move` being bad.
_BOTTOM_REVEAL_EXPANDABLES = [
    "Views/Molecules/ScannerCard.swift",          # the Daily Scanners leaderboard (reported)
    "Views/Molecules/NewsCardView.swift",         # inline AI summary
    "Views/Molecules/SignalDisclosureRow.swift",  # leader rows
    "Views/Molecules/ThinkingProcessCard.swift",  # expanded body
]


@pytest.mark.parametrize("rel", _BOTTOM_REVEAL_EXPANDABLES,
                         ids=[Path(f).stem for f in _BOTTOM_REVEAL_EXPANDABLES])
def test_expandable_content_fades_rather_than_sliding_over_its_siblings(rel):
    """⚠️ `.move(edge: .top)` on content revealed at the BOTTOM of a clipped container.

    A top-edge move starts an inserted view offset UPWARD by its own height and slides it
    down into place. When the content's final position is the bottom of a card, it therefore
    begins life on top of that card's own header, hero and CTA — and `.combined(with:
    .opacity)` keeps it translucent for the whole trip, so both layers of text are legible at
    once. The card's `clipShape` bounds the overlap INSIDE the card, which is what makes it
    read as text swimming up from behind rather than as a view flying in.

    It shipped on the Daily Scanners card, whose 10-row leaderboard travels furthest, and was
    reported from TestFlight as "words coming from the background ... looks like a bug".
    The other three are the same mechanism with shorter content.

    A plain `.opacity` moves nothing and cannot overlap anything. This is NOT a blanket ban on
    `.move(edge: .top)`: the audio status island and the top-pinned banners are genuinely
    edge-anchored, have nothing above them, and keep it — see `test_..._is_not_vacuous`.
    """
    code = _strip_comments(_read(_IOS / rel))
    assert ".move(edge:" not in code, (
        f"{rel}: expandable content is sliding again. Its final position is the BOTTOM of a "
        "clipped container, so a top-edge move drags it across the header/hero/CTA at partial "
        "opacity. Use `.transition(.opacity)`.")
    assert ".transition(.opacity)" in code, (
        f"{rel}: lost its fade transition — if the transition was removed outright rather than "
        "changed, the absence check above proves nothing")
    assert "isExpanded" in code, f"{rel}: no longer an expandable — this scan has drifted"


def test_the_bottom_reveal_scan_is_not_vacuous():
    """Three ways this scan could quietly stop testing anything.

    The third is the important one: the fix's own comment names `.move(edge: .top)` in every
    one of these files while explaining why it is gone, so an un-stripped scan for its ABSENCE
    fails forever and would push the next reader to delete the explanation.
    """
    # 1. The files exist and are real views.
    for rel in _BOTTOM_REVEAL_EXPANDABLES:
        src = _read(_IOS / rel)
        assert "var body: some View" in src, f"{rel}: not a view any more"
        assert len(_strip_comments(src)) > 400, f"{rel}: suspiciously small"

    # 2. Comment stripping actually strips — and these files really do carry the token in prose.
    scanner = _read(_SCANNER_CARD)
    assert "move(edge: .top)" in scanner, (
        "ScannerCard no longer explains why the slide was removed; the guard still passes but "
        "the reasoning is gone")
    assert "move(edge:" not in _strip_comments(scanner), "comment stripping regressed"

    # 3. The rule is scoped, not global: genuinely top-anchored transitions stay legal.
    island = _strip_comments(_read(_IOS / "Views/Screens/RootContainerView.swift"))
    assert ".move(edge: .top)" in island, (
        "RootContainerView's AudioStatusIsland lost its top-edge move. That one is CORRECT — "
        "it drops from the top of the screen with nothing above it. If this was swept up by "
        "the rule above, the rule has been over-applied.")


# ── 6. Expanding must not move or blink the card it lives in ──────────────────

_SIGNALS_SECTION = _IOS / "Views/Organisms/ExclusiveSignalsSection.swift"
_SIGNAL_ROW = _IOS / "Views/Molecules/SignalDisclosureRow.swift"


def test_scanner_card_title_cannot_be_squeezed_by_the_expand_animation():
    """⚠️ A MOVEMENT guard, and the numbers are why it exists.

    `.animation(_:value: isExpanded)` on the card body animates the whole subtree, so the
    VStack's height is interpolated. Mid-flight the card is shorter than its own content and
    SwiftUI compresses whichever child is compressible — which is the title, because
    `minimumScaleFactor` lets it scale and `lineLimit(2)` lets it drop to one line.

    Measured at 60fps on Today's Top Movers: the title went 79px -> 32px (one squeezed line)
    -> 81px inside 250ms, and the ~26pt the header lost dragged the hero, CTA and leaderboard
    with it. Reported as "the title shakes / the whole card is shaking". After
    `fixedSize(vertical:)` the same measurement is 81px in EVERY frame — swing 0.

    All three modifiers are asserted together on purpose: `fixedSize` stops the vertical
    squeeze, while `lineLimit`/`minimumScaleFactor` handle Dynamic Type (width-driven, and
    the file records that the title reflowed to four lines without them). Removing either
    half to "fix" the other reintroduces a shipped bug.
    """
    # Scoped to the TITLE's own modifier chain. The whole header block is too coarse: it also
    # contains the info popover's Text, which legitimately carries the same `fixedSize` — so a
    # block-level check passed with the title's removed. A mutation test caught exactly that.
    chain = _modifier_chain(_read(_SCANNER_CARD), "Text(scanner.title)")
    for token in ("lineLimit(2)", "minimumScaleFactor(0.85)",
                  "fixedSize(horizontal: false, vertical: true)"):
        assert token in chain, (
            f"ScannerCard's title lost `{token}`. All three are load-bearing: fixedSize stops "
            "the expand animation squeezing the title (79->32->81px, measured), the other two "
            "keep Dynamic Type from reflowing it to four lines.\nchain was:\n" + chain)


def test_the_signals_card_does_not_shadow_its_own_content():
    """`.shadow()` derives from the alpha of everything beneath it, so a shadow applied to the
    CARD forces the entire section — title, badge, subtitle, every row — into an offscreen
    layer. A row expanding resizes the section, so that layer is re-rasterized per frame for
    an 18pt blur across a full-width card.

    Shadowing the background SHAPE instead rasterizes one rounded rectangle and leaves the
    content out of the offscreen pass. Identical glow, nothing to re-raster.

    (Honest note: an A/B on the simulator could not measure a difference — 1 direction-reversal
    vs 0 in the stationary band. Offscreen raster cost is not comparable between a Mac GPU and
    a phone, so this is kept as the strictly-cheaper form rather than a proven fix.)
    """
    src = _strip_comments(_read(_SIGNALS_SECTION))
    clip = src.find(".clipShape(")
    assert clip != -1, "the signals card lost its clipShape — this scan has drifted"
    assert ".shadow(" not in src[clip:], (
        "a `.shadow` is applied AFTER `.clipShape` on the signals card, which shadows the "
        "whole card's CONTENT and forces a full offscreen re-raster on every resize frame. "
        "Put the shadow on the background shape instead.")
    bg = src.find(".background(")
    assert bg != -1 and ".shadow(" in src[bg:clip], (
        "the glow is gone entirely — it should live on the background shape, not be deleted")


def test_the_signals_expand_is_not_animated():
    """The section resizes when a row expands; with no animation there is no per-frame
    re-render, so there is nothing that CAN blink. Reported as "the title and the whole card
    is blinking when I expand", and accepted by the user as worth an unanimated expand.

    This also removes the last `withAnimation` transaction on this section — the one a
    `.repeatForever` glow entangled with and hard-froze the main thread on (see the header
    comment in ExclusiveSignalsSection.swift).
    """
    row = _strip_comments(_read(_SIGNAL_ROW))
    assert "isExpanded.toggle()" in row, "the row no longer toggles — scan drifted"
    assert "withAnimation" not in row, (
        "`withAnimation` is back on the signals row expand. That resizes the whole "
        "App-Exclusive Signals card every frame, which is what was reported as blinking.")
    section = _strip_comments(_read(_SIGNALS_SECTION))
    assert "withAnimation" not in section, (
        "`withAnimation` is back on the signals section's collapse — an animated collapse "
        "resizes the card per frame exactly as an animated expand does.")


def test_the_expand_movement_scans_are_not_vacuous():
    """Each assertion above could pass on a file that no longer says anything."""
    # The header block must really be the header, not the whole file.
    card = _read(_SCANNER_CARD)
    block = _decl_block(card, "private var header: some View")
    assert "scanner.title" in block, "the header block no longer renders the title"
    assert len(block) < len(card), "the 'header' block is the entire file — brace bounding failed"
    assert "var body: some View" not in block, "the header window ran past the declaration"

    # The chain must be the TITLE's alone. This is the hole a mutation test found: the header
    # block holds a second `fixedSize` (the info popover's Text), so a block-scoped assertion
    # stayed green with the title's modifier deleted.
    chain = _modifier_chain(card, "Text(scanner.title)")
    assert chain.count("fixedSize(horizontal: false, vertical: true)") == 1, (
        "the title chain should carry exactly one fixedSize — if it now sees two, the window "
        "has widened past the title and the assertion is no longer about the title")
    assert "popover" not in chain and "Button" not in chain, (
        "the title's modifier chain leaked into the sibling info button")
    assert "scanner.subtitle" not in chain, "the chain ran past the title into the subtitle"

    # The absence-of-withAnimation checks must be running on real, non-trivial sources.
    for path, marker in ((_SIGNAL_ROW, "struct SignalDisclosureRow"),
                         (_SIGNALS_SECTION, "struct ExclusiveSignalsSection")):
        src = _read(path)
        assert marker in src, f"{path.name}: not the expected view"
        assert len(_strip_comments(src)) > 800, f"{path.name}: suspiciously small"
        assert "isExpanded" in src or "expandedSignalID" in src, f"{path.name}: no longer expandable"

    # Comment stripping must strip — these files now name `withAnimation` in prose.
    assert "withAnimation" in _read(_SIGNAL_ROW), (
        "SignalDisclosureRow no longer explains why the expand is unanimated")
    assert "withAnimation" not in _strip_comments(_read(_SIGNAL_ROW)), "comment stripping regressed"


# ── 7. Several cards may be expanded at once ──────────────────────────────────

_HOME_VIEW = _IOS / "Views/Screens/HomeDashboardView.swift"


def test_both_home_sections_allow_more_than_one_card_open():
    """Daily Scanners and App-Exclusive Signals were one-open-at-a-time: each section held a
    single optional id, so expanding one card closed the others. Readers asked to be able to
    open all three. The state is a SET on both sides.

    Asserting the Set on the SECTIONS and on the Home screen that owns them, because a single
    `ID?` anywhere in that chain reimposes the limit.
    """
    for rel, prop in (("Views/Organisms/DailyScannersSection.swift", "expandedCardIDs"),
                      ("Views/Organisms/ExclusiveSignalsSection.swift", "expandedSignalIDs")):
        code = _strip_comments(_read(_IOS / rel))
        assert f"@Binding var {prop}: Set<" in code, (
            f"{rel}: `{prop}` is not a Set — a single optional id means one card open at a time")
        assert f"{prop}.contains(" in code and f"{prop}.insert(" in code and f"{prop}.remove(" in code, (
            f"{rel}: the per-card binding no longer toggles set membership")

    home = _strip_comments(_read(_HOME_VIEW))
    for prop in ("expandedScannerIDs", "expandedSignalIDs"):
        assert f"@State private var {prop}: Set<" in home, (
            f"HomeDashboardView.{prop} is not a Set — the sections cannot hold more than one "
            "card open if the state that owns them cannot")


def test_expanding_one_home_section_does_not_collapse_the_other():
    """The cross-section collapse was the machinery enforcing one-open-at-a-time: a tap
    swallowed by a scanner card was forwarded as "outside the signals section, so close it".
    With every card expandable that would close a reader's signals rows the instant they
    touched a scanner card.

    The tap is still SWALLOWED — that is `.onTapGesture`'s doing inside the card, not this
    wiring — so a tap on a card still cannot bubble up and collapse the card you just touched.
    """
    home = _strip_comments(_read(_HOME_VIEW))
    for wiring in ("onBodyTap: collapseExpandedSignal", "onBodyTap: collapseExpandedScanner"):
        assert wiring not in home, (
            f"`{wiring}` is back. That is the cross-section auto-collapse, i.e. "
            "one-open-at-a-time by another name.")
    # The swallow must survive, in both molecules.
    for rel in ("Views/Molecules/ScannerCard.swift", "Views/Molecules/SignalDisclosureRow.swift"):
        code = _strip_comments(_read(_IOS / rel))
        assert "onTapGesture { onBodyTap?() }" in code, (
            f"{rel}: lost the body-tap swallow — a tap on the card will now bubble to Home's "
            "collapse gesture and close the very card the reader touched")


def test_home_does_not_re_animate_the_signals_collapse():
    """`collapseExpandedSignal` is reachable from the tap-outside gesture and the pre-paywall
    path. It must stay unanimated: an animated resize of that card is what was reported as the
    whole card blinking, and the two collapses used to share one `withAnimation` here."""
    body = _read(_HOME_VIEW)
    start = body.index("private func collapseExpandedSignal()")
    window = _strip_comments(body[start : body.index("\n    }", start)])
    assert "expandedSignalIDs.removeAll()" in window, "the signals collapse no longer clears"
    assert "withAnimation" not in window, (
        "`withAnimation` is back around the signals collapse in HomeDashboardView. The signals "
        "card is deliberately unanimated (see SignalDisclosureRow); animating it from the "
        "outside reintroduces exactly the resize that was reported as blinking.")


def test_the_multi_expand_scans_are_not_vacuous():
    """Absence assertions above pass on an empty or renamed file; presence ones pass on the
    wrong declaration. Pin both ends."""
    home = _read(_HOME_VIEW)
    assert "struct HomeDashboardView" in home, "Home scan drifted"
    assert "DailyScannersSection(" in home and "ExclusiveSignalsSection(" in home, (
        "Home no longer hosts both sections, so asserting the absence of cross-collapse "
        "wiring between them proves nothing")
    # `onBodyTap` must still EXIST as a parameter — the absence check above is about the
    # cross-section handler, not about deleting the hook.
    for rel in ("Views/Molecules/ScannerCard.swift", "Views/Molecules/SignalDisclosureRow.swift"):
        assert "var onBodyTap:" in _read(_IOS / rel), f"{rel}: the onBodyTap hook is gone"
    # The collapse-window helper must really be bounded to the function.
    start = home.index("private func collapseExpandedSignal()")
    window = home[start : home.index("\n    }", start)]
    assert "collapseExpandedScanner" not in window, "the window ran into the sibling helper"


# ── 8. The expanded leader list's scroll affordance ───────────────────────────


def test_the_leader_list_uses_its_own_always_visible_indicator():
    """⚠️ `.scrollIndicators(.visible)` does NOT mean "always visible" — do not go back to it.

    It means "do not suppress them"; UIKit still fades the indicator once scrolling stops. A
    reader who has not touched the list therefore sees no bar and cannot tell there is more
    below, which is exactly what was reported ("Always show the scroll bar, so users can know
    there are more"). Measured on the simulator: no indicator at rest right after expanding.

    There is no API to pin the system one, so it is hidden and a custom capsule is drawn from
    `onScrollGeometryChange`.
    """
    code = _strip_comments(_read(_SIGNAL_ROW))
    assert ".scrollIndicators(.hidden)" in code, (
        "the system scroll indicator is back. `.visible` fades when idle, so the list stops "
        "advertising that it has more rows — use the custom indicator.")
    assert "onScrollGeometryChange" in code, "the custom indicator lost its geometry source"
    assert "scrollIndicator" in code, "the custom indicator view is gone"


def test_the_custom_indicator_is_bounded_and_inert():
    """Length is clamped (the system bar ran ~137pt and was reported as long) and the overlay
    must never steal a tap from the leader row underneath it."""
    code = _strip_comments(_read(_SIGNAL_ROW))
    # Both the DECLARATIONS and the clamp EXPRESSION. Grepping the bare names is not enough:
    # deleting a `private static let` leaves the name behind at its use site, so the token check
    # stayed green on a build that no longer compiles. A mutation test caught that.
    for token in ("thumbMinHeight: CGFloat =", "thumbMaxHeight: CGFloat ="):
        assert token in code, f"the indicator lost its `{token.split(':')[0]}` declaration"
    block = _decl_block(_read(_SIGNAL_ROW), "private var scrollIndicator: some View")
    assert re.search(r"min\(\s*max\(.*thumbMinHeight\s*\)\s*,\s*Self\.thumbMaxHeight\s*\)", block), (
        "the thumb length is no longer clamped between the two bounds — it can run the full "
        "track again, which is the ~137pt bar that was reported as long.\nblock was:\n" + block)
    assert "allowsHitTesting(false)" in block, (
        "the scroll indicator can absorb taps meant for the leader row beneath it")
    assert "accessibilityHidden(true)" in block, "decoration must not be announced"
    # The divide-by-zero guard on the fraction maths.
    assert "max(m.content - m.viewport, 1)" in code, (
        "the scrollable-height guard is gone; content == viewport would divide by zero and put "
        "NaN into a frame height")


def test_the_leader_stat_clears_the_scroll_indicator():
    """The stat sat ~4pt clear of the indicator and read as covered. Trailing inset is wider
    than leading on purpose; measured clearance after the fix is ~17pt."""
    block = _decl_block(_read(_SIGNAL_ROW), "private func leaderRow(_ leader: SignalLeader)")
    assert ".padding(.leading, 11)" in block and ".padding(.trailing, 22)" in block, (
        "the leader row went back to an even horizontal inset, which puts the 'N buys' stat "
        "back under the scroll indicator")
    assert ".padding(.horizontal," not in block, (
        "an even horizontal padding is back — that is what put the stat under the indicator")


def test_the_scroll_threshold_tracks_the_list_height():
    """A leader row is ~43pt + 7pt spacing, so ~4 fit in 200pt. If the threshold drifts above
    that, a SHORTER list renders unbounded and taller than a LONGER capped one — at the old
    6/260 pairing a six-item list was ~300pt while a seven-item list was capped at 260."""
    code = _strip_comments(_read(_SIGNAL_ROW))
    assert "scrollThreshold = 4" in code, "the scroll threshold no longer matches the 200pt cap"
    assert "expandedListMaxHeight: CGFloat = 200" in code, "the list height moved without the threshold"


def test_the_scroll_affordance_scans_are_not_vacuous():
    src = _read(_SIGNAL_ROW)
    code = _strip_comments(src)
    assert len(code) < len(src), "comment stripping removed nothing"
    # The fix's own comments name `.scrollIndicators(.visible)` while explaining why it is gone.
    assert "scrollIndicators(.visible)" in src, "the explanation for hiding the system bar is gone"
    assert "scrollIndicators(.visible)" not in code, "comment stripping regressed"
    block = _decl_block(src, "private func leaderRow(_ leader: SignalLeader)")
    assert "leader.stat" in block, "the leaderRow window no longer renders the stat"
    assert "scrollIndicator" not in block, "the leaderRow window leaked into the indicator"
