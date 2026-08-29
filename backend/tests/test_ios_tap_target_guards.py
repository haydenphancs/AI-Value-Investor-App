"""A card that READS as one control must BE one control, edge to edge.

TestFlight, 2026-08-24, on the Signal Ticker Detail screen (Home → Whale
Accumulation / Congressional Buys → a ticker):

    "The first tap looks like it's clickable on everywhere within the tap, but
     it isn't. The tabs under it are perfect."

The header card's `Button` wrapped only the `AMZN ›` line, so the company name,
the "Funds accumulating" line, the price and the market cap were dead pixels
inside a card that is visually a single tappable surface — while every
`SignalHolderRow` beneath it is tappable across its whole width. Nothing catches
this: it compiles, it renders identically, and no runtime assertion or schema
test can see a hit region.

There is no XCTest target, so the invariant is pinned from Python by reading the
Swift source (see .claude/rules/testing.md). Both scans are comment-stripped and
brace-bounded to the declaration they mean, and each carries an anti-vacuity
control — a scan that silently stops matching turns every other assertion green.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SIGNAL_DETAIL = _REPO / "frontend/ios/ios/Views/Screens/SignalTickerDetailView.swift"
_HOLDER_ROW = _REPO / "frontend/ios/ios/Views/Molecules/SignalHolderRow.swift"


def _strip_comments(src: str) -> str:
    """Remove // and /* */ comments so a guard cannot be satisfied by prose.

    The doc comment above `header(_:)` names every token these tests grep for, so
    an un-stripped scan would pass on the EXPLANATION after the code was reverted.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    # `[ \t]*`, NOT `\s*`: `\s` matches newlines, so `^\s*//` consumed the blank
    # line before a comment and collapsed two lines into one. Every line number
    # this file reports is derived from the stripped text, so that silently
    # shifted them (SnapshotCard 91->85, BookDetailView 1078->1029).
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _decl_body(src: str, prefix: str) -> str:
    """The brace-matched body of the declaration starting at ``prefix``.

    Asserting against a whole FILE passes when the token lives in a different
    declaration — `SignalTickerDetailView` has three other `Button`s (the toolbar
    back button, Retry, and the holder rows' callback), any one of which would
    satisfy a file-wide scan for `.buttonStyle(.plain)`.
    """
    at = src.index(prefix)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text())


# ── the reported defect ──────────────────────────────────────────────────────

def test_the_signal_header_card_is_tappable_as_a_whole():
    """`header(_:)` must OPEN with the Button, so the button's label is the card.

    The regression shape is precise and easy to reintroduce: move the `Button`
    inward so it wraps only the symbol line again. Then the card still renders
    identically and still navigates — from one small run of glyphs.
    """
    body = _decl_body(_code(_SIGNAL_DETAIL), "private func header(")

    # The first statement in the body is the Button — nothing wraps it, so its
    # label IS the card rather than a fragment inside one.
    first = next(ln.strip() for ln in body.splitlines()[1:] if ln.strip())
    assert first.startswith("Button"), (
        f"header(_:) no longer opens with the Button (found {first!r}) — the tap "
        "target has shrunk back to a fragment of the card"
    )

    assert "headerContent(detail)" in body, (
        "the Button's label must be the whole card content"
    )
    assert ".buttonStyle(.plain)" in body, (
        "without .plain the label is tinted as a system button and the card's own "
        "colours are lost"
    )


def test_the_signal_header_content_is_hit_testable_across_its_padding():
    """`.contentShape` AFTER the padding, or the gutter stays dead.

    A `Button` label made of `Text`s and a `Spacer` is hit-tested on the GLYPHS.
    Wrapping the card in a Button is only half the fix: without a content shape
    the padded margins and the `Spacer` gutter between the symbol block and the
    price block still swallow taps — which is most of the card's area, and
    exactly the region the reporter was pressing.
    """
    body = _decl_body(_code(_SIGNAL_DETAIL), "private func headerContent(")

    assert ".contentShape(Rectangle())" in body, (
        "headerContent lost its content shape — the padding and the Spacer gutter "
        "are dead pixels again"
    )
    # Order matters: a content shape applied BEFORE the padding describes the
    # unpadded frame and leaves the margins dead.
    assert body.index(".padding(AppSpacing.lg)") < body.index(".contentShape(Rectangle())"), (
        ".contentShape must come after .padding, or it measures the unpadded frame"
    )


def test_the_header_matches_the_rows_the_reporter_called_perfect():
    """Parity with `SignalHolderRow`, which the report singles out as correct.

    The two sit on the same screen, one above the other. Diverging is what made
    the inconsistency legible to a user in the first place.
    """
    row = _decl_body(_code(_HOLDER_ROW), "struct SignalHolderRow")
    header = _decl_body(_code(_SIGNAL_DETAIL), "private func header(")
    content = _decl_body(_code(_SIGNAL_DETAIL), "private func headerContent(")

    for token in (".buttonStyle(.plain)",):
        assert token in row and token in header, f"{token} must appear in both"
    assert ".contentShape(Rectangle())" in row and ".contentShape(Rectangle())" in content


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_comment_stripper_actually_strips():
    """If this ever stopped stripping, every assertion above would pass on the doc
    comment that explains the rule — the canonical way a source scan goes vacuous."""
    raw = _SIGNAL_DETAIL.read_text()
    assert ".contentShape" in raw
    # The doc comment on header(_:) names `.contentShape` in prose.
    assert "`.contentShape` AFTER the padding" in raw, (
        "the doc comment changed; this control needs a phrase that exists ONLY in "
        "a comment, or it stops proving the stripper works"
    )
    assert "`.contentShape` AFTER the padding" not in _code(_SIGNAL_DETAIL)


def test_the_decl_bounding_actually_bounds():
    """A whole-file scan would be satisfied by the wrong declaration. Prove the
    bound is real: `headerContent` draws the card, `header` does not."""
    src = _code(_SIGNAL_DETAIL)
    assert ".cardSurface(" in _decl_body(src, "private func headerContent(")
    assert ".cardSurface(" not in _decl_body(src, "private func header(")
    # …and the file as a whole contains both, so the distinction is only visible
    # BECAUSE the scan is bounded.
    assert ".cardSurface(" in src


@pytest.mark.parametrize("prefix", [
    "private func header(",
    "private func headerContent(",
])
def test_both_declarations_still_exist(prefix):
    """A rename must fail loudly here rather than silently skipping the guard."""
    assert prefix in _code(_SIGNAL_DETAIL)


# ── the back button: a 44pt target, app-wide ─────────────────────────────────
#
# TestFlight, same reporter, 2026-08-24:
#
#     "Sometimes when I hit the back (<) button, it doesn't work."
#
# Not intermittent — a dead zone. Ten of the app's seventeen back buttons were a
# bare `Image(systemName: "chevron.left")` inside a `Button` with no frame, no
# padding and no content shape, so the touch target was the GLYPH: ~10x18pt at
# `iconMedium`, against Apple's 44x44pt minimum. On iOS 26 a toolbar draws a 44pt
# glass circle behind the item while the target still follows the label, so the
# affordance and the target disagree by design.
#
# MEASURED on the Signal Ticker Detail screen (iPhone 17 Pro, iOS 26) before the
# fix: the circle spans y 62–105pt; a tap at (38, 65), plainly inside it, left the
# screen byte-identical, while (38, 96) dismissed. After adopting NavBackButton the
# same (38, 65) dismisses.

_ATOMS = _REPO / "frontend/ios/ios/Views/Atoms/NavBackButton.swift"
_VIEWS = _REPO / "frontend/ios/ios/Views"


_TARGET_MODIFIERS = (".frame(", ".padding(", ".contentShape(", ".background(")


def _brace_match(src: str, open_at: int):
    """Index of the `}` closing the `{` at ``open_at``, or None."""
    depth = 0
    for j in range(open_at, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return None


def _enclosing_button(src: str, idx: int) -> "str | None":
    """The full brace-matched `Button` expression containing ``idx``.

    THREE spellings live in this codebase, and skipping any one makes the sweep
    below vacuous:

      Button(action: …) { LABEL }            — the action is in PARENS
      Button(action: { … }) { LABEL }        — action closure, THEN the label
      Button { ACTION } label: { LABEL }     — two trailing closures

    In the last two the glyph is in the SECOND `{…}` group, so anything that
    inspects only the first closure misses it. An earlier version did exactly
    that and left this file green while the reported button was still a bare
    glyph; mutation-testing is what surfaced it. Rather than special-casing each
    spelling, walk the `{…}` groups after `Button` and take the one that actually
    contains the glyph.

    Scoping to the BUTTON, not to the first `}` after the glyph, matters too:
    three legitimate back buttons render
    `HStack { chevron; Text("Back") }.padding(…).background(Capsule())`, where
    the padding that makes them a real target sits after the inner HStack closes.
    """
    at = src.rfind("Button", max(0, idx - 500), idx)
    if at == -1:
        return None

    pos = at
    while True:
        br = src.find("{", pos)
        if br == -1 or br > idx:
            return None
        close = _brace_match(src, br)
        if close is None:
            return None
        if br < idx < close:
            # +260 sweeps up the modifiers chained onto the closing brace.
            return src[at:close + 260]
        pos = close + 1


def _back_button_sites():
    """Every raw chevron-left inside a Button, with its enclosing Button.

    Returns (path, line, has_target). `has_target` is true when the button
    carries a frame, padding, background or content shape — anything that makes
    the touch area bigger than the glyph itself.
    """
    out = []
    for path in sorted(_VIEWS.rglob("*.swift")):
        src = _strip_comments(path.read_text())
        for m in re.finditer(r'Image\(systemName:\s*"chevron\.left"\)', src):
            # A decorative or balancing chevron is not a tap target.
            btn = _enclosing_button(src, m.start())
            if btn is None:
                continue
            has_target = any(t in btn for t in _TARGET_MODIFIERS)
            out.append((path.relative_to(_REPO), src[:m.start()].count("\n") + 1, has_target))
    return out


def test_the_shared_back_button_atom_carries_the_hig_minimum():
    code = _code(_ATOMS)
    assert "static let hitTarget: CGFloat = 44" in code, (
        "NavBackButton.hitTarget is the whole point of the atom — 44pt is Apple's "
        "minimum, not a design knob"
    )
    body = _decl_body(code, "struct NavBackButton")
    assert ".contentShape(Rectangle())" in body, (
        "a 44pt frame without a content shape is still hit-tested on the glyph"
    )
    assert body.index(".frame(") < body.index(".contentShape(Rectangle())"), (
        "the frame must come first, or the shape describes the un-framed glyph"
    )


def test_no_back_button_is_a_bare_glyph():
    """The app-wide sweep. Ten sites failed this before the fix."""
    bare = [(str(p), l) for p, l, ok in _back_button_sites() if not ok]
    assert bare == [], (
        "back buttons whose tap target is only the glyph — adopt NavBackButton:\n  "
        + "\n  ".join(f"{p}:{l}" for p, l in bare)
    )


def test_the_back_button_scanner_is_not_vacuous():
    """A scan that stops matching turns the sweep above green for free.

    Two controls: the scanner must still FIND back buttons at all, and a bare
    glyph inside a Button must still be classified as untargeted.
    """
    sites = _back_button_sites()
    assert len(sites) >= 5, (
        f"the chevron scanner found only {len(sites)} back buttons — it has stopped "
        "matching, so test_no_back_button_is_a_bare_glyph proves nothing"
    )
    # Synthetic controls, run through the REAL scanner rather than a hand-cut
    # string — both Button spellings, because the trailing-closure form was the
    # one an earlier version of `_enclosing_button` skipped entirely, leaving the
    # sweep green while the reported button was still a bare glyph.
    trailing = '''
    Button { dismiss() } label: {
        Image(systemName: "chevron.left")
            .font(AppTypography.iconSmall)
            .foregroundColor(AppColors.textPrimary)
    }
    '''
    inline = '''
    Button(action: { dismiss() }) {
        Image(systemName: "chevron.left")
            .font(AppTypography.iconMedium)
            .foregroundColor(AppColors.textPrimary)
    }
    '''
    targeted = '''
    Button { dismiss() } label: {
        Image(systemName: "chevron.left")
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }
    '''
    for label, src, expect_target in (
        ("trailing-closure bare", trailing, False),
        ("inline-action bare", inline, False),
        ("targeted", targeted, True),
    ):
        m = re.search(r'Image\(systemName:\s*"chevron\.left"\)', src)
        btn = _enclosing_button(src, m.start())
        assert btn is not None, f"{label}: the scanner did not find the Button at all"
        got = any(t in btn for t in _TARGET_MODIFIERS)
        assert got is expect_target, (
            f"{label}: classifier said has_target={got}, expected {expect_target}"
        )


# ── hit slop: a 44pt target on the shared detail header ──────────────────────
#
# TestFlight, 2026-08-24: "The Back icon is hard to click on it sometimes which
# could be a problem for many users / elderly people. Is this called hitslop in
# software?"
#
# `TickerDetailHeader` backs ALL FIVE detail screens (stock, index, ETF, crypto,
# commodity). Its five icons drew in a 40x40pt box — 4pt under Apple's minimum —
# with no declared content shape, so the target also depended on SwiftUI's default
# Image hit-testing, which is an implementation detail. The report came from iOS
# 18.7.8; the simulator that reproduces it runs iOS 26.
#
# MEASURED on the index detail header (iPhone 17 Pro):
#   40pt build — a tap at (26, 105) MISSED; (26, 100) hit. Bottom edge ~101pt.
#   44pt+slop  — (28, 104) HITS, and (2, 83) — six points OUTSIDE the box, in
#                pure slop — hits too.
#
# Slop alone was NOT enough and that is the trap this guards: the header row is an
# HStack with no vertical padding, so its height equals its tallest child. At 40pt
# icons the row was 40pt tall and vertical slop bought nothing — the parent
# rejected the point before the icon was asked. Size to 44 FIRST; slop is margin
# on top, in whatever direction the parent has room.

_HIT_SLOP = _REPO / "frontend/ios/ios/Views/Modifiers/HitSlop.swift"
_DETAIL_HEADER = _REPO / "frontend/ios/ios/Views/Molecules/TickerDetailHeader.swift"


def test_hit_slop_expands_touch_without_moving_layout():
    """The three-modifier idiom, in order. Any two of them is a different thing."""
    code = _code(_HIT_SLOP)
    assert "static let minimumTarget: CGFloat = 44" in code, (
        "44 is Apple's HIG minimum comfortable target — not a tunable"
    )
    body = _decl_body(code, "func hitSlop(")
    grow = body.index(".padding(inset)")
    shape = body.index(".contentShape(Rectangle())")
    shrink = body.index(".padding(-inset)")
    assert grow < shape < shrink, (
        "hitSlop must grow, THEN declare the shape, THEN give the layout back: "
        f"got padding@{grow} shape@{shape} negative@{shrink}"
    )


def test_the_shared_detail_header_meets_the_minimum_target():
    """All five icons on the header that backs all five detail screens."""
    code = _code(_DETAIL_HEADER)

    assert "40, height: 40" not in code, (
        "the header icons are back to a 40pt box — under the HIG minimum, and the "
        "size the reporter could not reliably hit"
    )
    n_frames = code.count(".frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget)")
    n_slop = code.count(".hitSlop()")
    n_icons = len(re.findall(r"Image\(systemName:", code))
    assert n_frames == n_icons, (
        f"{n_frames} of {n_icons} header icons are sized to the minimum target"
    )
    assert n_slop == n_icons, (
        f"{n_slop} of {n_icons} header icons carry hit slop"
    )


def test_the_header_slop_cannot_overlap_a_neighbouring_icon():
    """Overlapping targets do not error — SwiftUI hands the overlap to whichever
    sibling is on top — so a too-generous slop turns "hard to hit" into "hit the
    wrong one". The right icons sit `AppSpacing.md` apart, so the slop must be at
    most half of that."""
    theme = _code(_REPO / "frontend/ios/ios/Theme/AppTheme.swift")
    md = int(re.search(r"static let md:\s*CGFloat\s*=\s*(\d+)", theme).group(1))
    slop = int(re.search(r"static let standard:\s*CGFloat\s*=\s*(\d+)", _code(_HIT_SLOP)).group(1))
    assert "HStack(spacing: AppSpacing.md)" in _code(_DETAIL_HEADER), (
        "the right-hand icon row no longer uses AppSpacing.md — re-derive the slop "
        "against whatever spacing replaced it"
    )
    assert slop * 2 <= md, (
        f"slop {slop}pt on each side exceeds the {md}pt gap between icons: their "
        "touch areas would overlap and taps near the boundary would fire the wrong one"
    )


# ── app-wide: every icon-only button reaches the minimum target ──────────────
#
# The 24-site sweep that followed the header fix. An "icon-only button" is one
# whose label is a glyph and nothing else — for those, the icon's box IS the tap
# target. A glyph sitting inside a bigger button (a row with a title, an avatar
# beside text) is NOT one: the row is the target and the glyph's frame is
# irrelevant, so flagging it would be noise that trains people to ignore this.

_BUTTON_TOKEN = re.compile(r"\bButton\s*[({]")
_FRAME_WH = re.compile(r"\.frame\(width:\s*(\d+),\s*height:\s*(\d+)\)")


def _brace_close(src: str, open_at: int):
    depth = 0
    for j in range(open_at, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return None


def _button_around(src: str, idx: int):
    """(label, label+trailing modifiers) of the Button containing ``idx``.

    `\\bButton\\s*[({]` rather than a plain search: `PlainButtonStyle` contains
    the substring "Button", and matching it made the scan attribute the NEXT
    declaration's contents to a button that had already closed — which is how a
    plain `Image` that is not a button at all got reported as an offender.
    """
    starts = [m.start() for m in _BUTTON_TOKEN.finditer(src, max(0, idx - 500), idx)]
    if not starts:
        return None
    at = starts[-1]
    pos = at
    while True:
        br = src.find("{", pos)
        if br == -1 or br > idx:
            return None
        # Between one closure and the next of the SAME Button there can only be
        # `)`, `label:` or whitespace. Anything else means the Button ended and
        # we are wandering into whatever follows it.
        if pos != at and not re.fullmatch(r"[\s)]*(label:)?[\s]*", src[pos:br]):
            return None
        close = _brace_close(src, br)
        if close is None:
            return None
        if br < idx < close:
            return src[at:close + 1], src[at:close + 300]
        pos = close + 1


def _icon_only_buttons():
    """(path, line, w, h, compliant) for every icon-only button with a fixed box."""
    out = []
    for path in sorted(_VIEWS.rglob("*.swift")):
        src = _strip_comments(path.read_text(errors="ignore"))
        for m in _FRAME_WH.finditer(src):
            w, h = int(m.group(1)), int(m.group(2))
            found = _button_around(src, m.start())
            if found is None:
                continue
            label, with_mods = found
            if "Text(" in label:
                continue                       # the row is the target, not this glyph
            if "Image(systemName:" not in label:
                continue
            compliant = min(w, h) >= 44 or ".hitSlop(" in with_mods
            out.append((str(path.relative_to(_REPO)), src[:m.start()].count("\n") + 1,
                        w, h, compliant))
    return out


def test_every_icon_only_button_reaches_the_minimum_target():
    """A 44pt box, or slop that gets it there. 24 sites failed this before the sweep."""
    bad = [(f, l, w, h) for f, l, w, h, ok in _icon_only_buttons() if not ok]
    assert bad == [], (
        "icon-only buttons under 44pt with no hit slop — give them "
        ".hitSlop(reaching:) or a 44pt box:\n  "
        + "\n  ".join(f"{f}:{l}  {w}x{h}" for f, l, w, h in bad)
    )


def test_the_icon_only_scanner_is_not_vacuous():
    """Three controls, because each failure mode has bitten this scan already."""
    sites = _icon_only_buttons()
    assert len(sites) >= 25, (
        f"the scanner found only {len(sites)} icon-only buttons — it has stopped "
        "matching, so the sweep above proves nothing"
    )

    # 1. A bare small box in an icon-only button IS flagged.
    sample = '''
    Button(action: { tap() }) {
        Image(systemName: "star")
            .frame(width: 32, height: 32)
    }
    '''
    src = _strip_comments(sample)
    m = _FRAME_WH.search(src)
    label, with_mods = _button_around(src, m.start())
    assert "Text(" not in label and ".hitSlop(" not in with_mods, (
        "the classifier no longer recognises an unslopped icon-only button"
    )

    # 2. A glyph inside a button that also has a TITLE is NOT flagged — the row
    #    is the target. (AppSettingsView's Delete Account row, CommunityDiscussionRow.)
    row = '''
    Button(action: { tap() }) {
        HStack {
            Image(systemName: "trash").frame(width: 28, height: 28)
            Text("Delete Account")
        }
    }
    '''
    src = _strip_comments(row)
    m = _FRAME_WH.search(src)
    label, _ = _button_around(src, m.start())
    assert "Text(" in label, "a titled row must be excluded, or the sweep cries wolf"

    # 3. A plain Image following a Button is NOT attributed to it.
    after = '''
    Button(action: { tap() }) {
        Image(systemName: "xmark").frame(width: 28, height: 28)
    }
    .buttonStyle(PlainButtonStyle())

    private var waveformIcon: some View {
        Image(systemName: "chart.bar.fill")
            .frame(width: 24, height: 24)
    }
    '''
    src = _strip_comments(after)
    second = list(_FRAME_WH.finditer(src))[1]
    assert _button_around(src, second.start()) is None, (
        "the scan walked past the Button into the next declaration — `PlainButtonStyle` "
        "contains the substring 'Button' and this is exactly how that goes wrong"
    )


# ── the New Portfolio sheet must be able to close ────────────────────────────
#
# TestFlight, 2026-08-24: "Cencel doesn't work, check and fix for me".
#
# REPRODUCED, and the first theory was wrong. Cancel's tap target is fine: on the
# simulator at the .medium detent, taps at the capsule CENTRE and in its top
# margin both dismissed. What the reporter's screenshot actually shows is the
# sheet at FULL height with the toolbar at the top of the screen — not at the
# .medium position the code asks for. `.onAppear` focuses the field, so the
# keyboard rises during the presentation animation and forces the sheet past the
# only detent it declared. Reproduced by dragging the sheet to .large: that is
# the reporter's exact geometry.
#
# Two independent things are pinned here because either alone would let it back:
#   * the sheet must declare a detent it can legally grow into, and
#   * closing must not depend solely on `@Environment(\.dismiss)` resolving —
#     `showNewPortfolioSheet` is otherwise only ever set to TRUE.

_NEW_PORTFOLIO = _REPO / "frontend/ios/ios/Views/Organisms/NewPortfolioSheet.swift"
_TRACKING_VM = _REPO / "backend/../frontend/ios/ios/ViewModels/TrackingViewModel.swift"


def test_the_sheet_has_a_detent_the_keyboard_can_grow_into():
    code = _code(_NEW_PORTFOLIO)
    assert ".presentationDetents([.medium])" not in code, (
        "a single .medium detent plus focus-on-appear is the reported bug: the "
        "keyboard forces the sheet past the only height it is allowed to be"
    )
    assert ".presentationDetents([.medium, .large])" in code, (
        "the sheet needs a legal expanded detent; .medium stays first so it still "
        "opens compact"
    )
    assert ".onAppear { nameFocused = true }" in code, (
        "anti-vacuity: the focus-on-appear that raises the keyboard is still here, "
        "so the detent above is still load-bearing rather than decorative"
    )


def test_closing_does_not_depend_on_the_environment_alone():
    """`close()` clears the view-model flag, which is what `.sheet(isPresented:)`
    actually reads. `dismiss()` stays as the second half, not the only half."""
    code = _code(_NEW_PORTFOLIO)
    body = _decl_body(code, "private func close()")
    assert "viewModel.showNewPortfolioSheet = false" in body, (
        "close() must clear the flag — nothing else in the app ever sets it false"
    )
    assert "nameFocused = false" in body, (
        "resign focus first, so the keyboard collapses and the sheet is back at a "
        "legal detent before it animates away"
    )
    # Every exit routes through close(), including the success path in submit().
    assert 'Button("Cancel") { close() }' in code
    assert "isSubmitting = false\n                close()" in code, (
        "the create-success path must close the same way Cancel does"
    )


def test_the_sheet_uses_navigationstack_like_its_working_siblings():
    """`EditPortfolioSheet` and `ManageTickersSheet` both dismiss via the
    environment inside a NavigationStack and both work. This sheet was the only
    one combining environment dismissal with a deprecated NavigationView."""
    code = _code(_NEW_PORTFOLIO)
    assert "NavigationStack {" in code
    assert "NavigationView {" not in code, (
        "NavigationView is deprecated and was the odd one out among these sheets"
    )


# ── app-wide: a row-shaped Button is tappable across the whole row ────────────
#
# TestFlight, 2026-08-24, ticker report → Deep Dive Modules:
#
#     "To expand a card/module, they can hit anywhere on the top of the card,
#      not just on the title or the down icon."
#
# The SECOND report of the defect this file was created for. The first fix was
# scoped to one screen, so the class survived and resurfaced elsewhere. This
# scanner is the sweep, so there cannot be a third.
#
# The mechanism, restated because it is the part that keeps being forgotten: a
# Button is hit-tested on the shape its label DRAWS, not on the label's frame.
# `Spacer()` draws nothing and `.padding` is empty space, so a header row of
# icon + title + Spacer + chevron responds ONLY on those three glyphs — and the
# Spacer is the widest part of the row. `.contentShape(Rectangle())`, applied
# AFTER the padding, is what makes the row one target.
#
# ⚠️ The exemption rule is where this scanner goes wrong, and it already did
# twice. A fill DOES donate a hit region, so a filled label needs no shape — but
# the first version searched the whole label closure for `.background`/`.fill`
# and thereby MISSED six real offenders, because those tokens matched a 36x36
# icon chip and a badge pill nested INSIDE the row. `_fill_on_root` therefore
# depth-tracks: only a modifier on the label's OUTERMOST view counts. A curated
# allowlist was tried alongside it and deleted — measured, it exempted nothing
# the depth check did not already handle, so it was pure misdirection.

_ROW_WIDE = ("Spacer()", "Spacer(minLength", ".frame(maxWidth: .infinity")
_FILL_ON_LABEL = re.compile(r"\.(background|cardSurface|cardFill)\(")


def _row_shaped_buttons():
    """(rel_path, line, has_shape) for every Button whose label spans a row.

    "Spans a row" = the label contains a `Spacer()` or `.frame(maxWidth:)` AND
    some padding. That is exactly the shape whose empty regions swallow taps.
    """
    out = []
    for path in sorted(_VIEWS.rglob("*.swift")):
        rel = str(path.relative_to(_REPO / "frontend/ios/ios"))
        src = _code(path)
        for m in _BUTTON_TOKEN.finditer(src):
            # The label is the LAST closure belonging to this Button, so both
            # `Button(action:) { … }` and `Button { … } label: { … }` resolve.
            i = m.start()
            pos, label = i, None
            while True:
                br = src.find("{", pos)
                if br == -1:
                    break
                if pos != i and not re.fullmatch(r"[\s)]*(label:)?\s*", src[pos:br]):
                    break
                cl = _brace_close(src, br)
                if cl is None:
                    break
                label = (br, cl)
                pos = cl + 1
            if label is None:
                continue
            # INTERIOR of the label closure. Including the opening `{` line would
            # make `_root_indent` read the Button's own indentation instead of
            # its content's, and every root-placement check would then miss.
            body = src[label[0] + 1:label[1]]
            if not any(t in body for t in _ROW_WIDE):
                continue
            if ".padding(" not in body:
                continue
            if _fill_on_root(body):
                continue          # a drawn surface donates the shape
            ok = _shape_is_effective(body)
            out.append((rel, src[:i].count("\n") + 1, ok))
    return out


def _root_modifiers(body: str) -> list:
    """Lines of ``body`` that are modifiers on the label's OUTERMOST view.

    Depth-tracked rather than indentation-matched. Indentation cannot express
    this: when the root is a container the modifiers sit at the container's
    indent (`HStack { … }` then `.padding`), but when the root is a single view
    they sit one level deeper (`Text("more")` then an indented `.font`). A
    modifier belongs to the root exactly when its leading `.` is at nesting
    depth 0 of the label interior.

    This is the check that makes the guard mean what its message says. Both
    failure modes it catches ship silently: a `.contentShape` applied to a
    nested child (BookDetailView's content column, leaving a 44pt timeline
    column dead) and one applied before the padding (measuring the unpadded
    frame) compile and render identically to the correct code.
    """
    out, depth = [], 0
    for idx, line in enumerate(body.splitlines()):
        stripped = line.strip()
        leading_depth = depth
        for ch in line:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        if leading_depth == 0 and stripped.startswith("."):
            out.append((idx, stripped))
    return out


def _fill_on_root(body: str) -> bool:
    """A drawn surface donates a hit region ONLY when applied to the label
    itself. Searching the whole closure is what made the first version of this
    scanner clear six real offenders: it matched a 36x36 icon chip's `.fill(`
    and a badge pill's `.background(` nested inside the row."""
    return any(_FILL_ON_LABEL.match(t) for _, t in _root_modifiers(body))


def _shape_is_effective(body: str) -> bool:
    """True when a content shape is on the label root AND after the padding."""
    mods = _root_modifiers(body)
    shape = [i for i, t in mods if t.startswith(".contentShape(")]
    if not shape:
        return False
    pad = [i for i, t in mods if t.startswith(".padding(")]
    return not pad or max(shape) > max(pad)


def test_every_row_shaped_button_declares_its_hit_area():
    """20 sites failed this before the sweep, across 15 files.

    The sweep also fixed 6 inline text links and added 4 missing plain button
    styles, but those are NOT in this scanner's population (a bare `Text` label
    has no Spacer and no padding) — they have their own tests below. Saying "26"
    here would credit this assertion with coverage it does not have.
    """
    bad = [(f, l) for f, l, ok in _row_shaped_buttons() if not ok]
    assert bad == [], (
        "row-shaped Buttons whose padding and Spacer are dead pixels — add "
        ".contentShape(Rectangle()) as the LAST modifier inside the label, after "
        "the padding:\n  " + "\n  ".join(f"{f}:{l}" for f, l in bad)
    )


def test_the_reported_deep_dive_header_is_one_target():
    """The exact card from the report, asserted by name rather than only by the
    sweep — a scanner that stops matching would take the sweep green with it."""
    body = _decl_body(
        _code(_REPO / "frontend/ios/ios/Views/Organisms/ReportDeepDiveSection.swift"),
        "struct ReportDeepDiveSection",
    )
    head = body[:body.index("if isExpanded")]
    assert ".contentShape(Rectangle())" in head, (
        "the Deep Dive module header lost its content shape — only the title and "
        "the chevron respond again"
    )
    # Order matters: before the padding it measures the unpadded frame.
    assert head.rindex(".padding(") < head.index(".contentShape(Rectangle())"), (
        ".contentShape must come after .padding, or the margins stay dead"
    )


def test_the_show_more_footers_that_gained_a_hit_area_also_gained_a_button_style():
    """Paired on purpose. These four had NO button style and looked right only
    because the label hardcodes `AppColors.primaryBlue`. The moment the full row
    became hit-testable, the default style would paint a press highlight across
    it — so shipping the shape without the style is a visible regression."""
    for rel in (
        "Views/Molecules/ReportInsiderActivityTable.swift",
        "Views/Molecules/ReportKeyManagementTable.swift",
        "Views/Organisms/ReportHiddenMarketSignalsSection.swift",
        "Views/Organisms/ReportMacroGeopoliticalSection.swift",
    ):
        src = _code(_REPO / "frontend/ios/ios" / rel)
        assert ".contentShape(Rectangle())" in src, f"{rel} lost its content shape"
        assert ".buttonStyle(.plain)" in src, (
            f"{rel} has a full-width hit area but no plain button style — the "
            "default style will tint and highlight the whole row"
        )


_MORE_LINKS = [
    "Views/Organisms/TickerDetailCompanyProfileSection.swift",
    "Views/Organisms/CryptoProfileSection.swift",
    "Views/Organisms/ETFProfileSection.swift",
    "Views/Organisms/CommodityDetailProfileSection.swift",
    "Views/Organisms/IndexDetailProfileSection.swift",
    "Views/Screens/WhaleProfileView.swift",
]


def _more_link_label(rel: str) -> str:
    """The label closure of the `more` / `Show More` disclosure Button."""
    src = _code(_REPO / "frontend/ios/ios" / rel)
    at = re.search(r'Text\(isExpanded \? "Show ?[Ll]ess" : "(more|Show More)"\)', src)
    assert at, f"{rel}: the more/less link moved — this guard has drifted"
    starts = [m.start() for m in _BUTTON_TOKEN.finditer(src, 0, at.start())]
    assert starts, f"{rel}: no Button wraps the more/less link"
    i = starts[-1]
    pos, label = i, None
    while True:
        br = src.find("{", pos)
        if br == -1:
            break
        if pos != i and not re.fullmatch(r"[\s)]*(label:)?\s*", src[pos:br]):
            break
        cl = _brace_close(src, br)
        if cl is None:
            break
        label = (br, cl)
        pos = cl + 1
    assert label, f"{rel}: could not bound the Button label"
    return src[label[0] + 1:label[1]]


@pytest.mark.parametrize("rel", _MORE_LINKS)
def test_the_inline_more_links_reach_the_minimum_target(rel):
    """`more` / `Show less` under a paragraph is a ~17pt text run.

    It gets PADDING plus a content shape, not `.hitSlop()`. Measured with
    dispatched mouse events on the same view structure: slop leaves a Button's
    tap area unchanged (21 live probe points, identical to no slop at all)
    because `hitSlop` ends in `.padding(-inset)` and a Button clips its
    interaction region back to that frame. Real padding plus a shape gives 117.
    `NavBackButton` and `MoreOptionsButton` only look like counter-examples
    because they set an explicit 44pt `.frame` before their slop.

    Brace-bounded to the Button and asserted on the LABEL's own modifiers: a
    file-wide substring check passed while all six were attached OUTSIDE the
    Button, where they did nothing at all.
    """
    body = _more_link_label(rel)
    mods = _root_modifiers(body)
    names = [t.split("(")[0] for _, t in mods]
    assert ".contentShape" in names, (
        f"{rel}: the more/less label has no content shape on its root — the "
        "target is back to the width of the word"
    )
    assert ".padding" in names, (
        f"{rel}: the shape without padding is just the text frame again"
    )
    assert _shape_is_effective(body), (
        f"{rel}: the content shape does not come after the padding, so it "
        "measures the unpadded text frame"
    )


def test_the_more_links_do_not_rely_on_hit_slop_alone(rel=None):
    """The regression that shipped: `.hitSlop(...)` placed after
    `.buttonStyle(...)`, i.e. on the Button rather than inside its label, where
    a content shape cannot reach the Button's own gesture. Every one of the 31
    other hitSlop call sites in this app is inside a label."""
    views = _REPO / "frontend/ios/ios/Views"
    stray = []
    for path in sorted(views.rglob("*.swift")):
        lines = _code(path).splitlines()
        for k, line in enumerate(lines):
            if ".hitSlop(" in line and k > 0 and ".buttonStyle(" in lines[k - 1]:
                stray.append(f"{path.relative_to(views)}:{k + 1}")
    assert stray == [], (
        "hitSlop applied to the Button instead of its label — it expands a "
        "region no gesture is attached to:\n  " + "\n  ".join(stray)
    )


def test_the_row_shaped_scanner_is_not_vacuous():
    """Four controls — each failure mode has already bitten a scan in this file."""
    sites = _row_shaped_buttons()

    # 1. It still finds the population. If the Button walker breaks, every
    #    assertion above passes on an empty list.
    assert len(sites) >= 20, (
        f"only {len(sites)} row-shaped buttons found — the walker has drifted"
    )

    # 2. It finds the specific site the report was about.
    assert any(f.endswith("ReportDeepDiveSection.swift") for f, _, _ in sites), (
        "the reported card is no longer classified as row-shaped"
    )

    # 3. Comment stripping bites — the comment added beside the fix quotes
    #    `.contentShape(Rectangle())`, `Spacer()` and `.padding` verbatim.
    assert ".contentShape" not in _strip_comments("// .contentShape(Rectangle())\nlet x = 1\n")

    # 4. Comment stripping preserves LINE COUNT, so the line numbers this file
    #    reports point at the real source. `^\s*//` used to eat the newline of a
    #    preceding blank line and collapse two lines into one.
    src = "a\n\n    // note\nb\n"
    assert _strip_comments(src).count("\n") == src.count("\n")

    # 5. The root-placement check discriminates. A shape on a NESTED child and a
    #    shape BEFORE the padding both compile and render identically to the
    #    correct code, so a membership test would bless either.
    good = '\n    HStack {\n        Text("x")\n    }\n    .padding(8)\n    .contentShape(Rectangle())\n'
    nested = '\n    HStack {\n        VStack {\n            Text("x")\n        }\n        .contentShape(Rectangle())\n    }\n    .padding(8)\n'
    before = '\n    HStack {\n        Text("x")\n    }\n    .contentShape(Rectangle())\n    .padding(8)\n'
    assert _shape_is_effective(good)
    assert not _shape_is_effective(nested), "a shape on a nested child must not count"
    assert not _shape_is_effective(before), "a shape before the padding must not count"

    # 6. The fill exemption is root-scoped. A nested chip's fill is exactly what
    #    made an earlier version of this scanner clear six real offenders.
    chip = '\n    HStack {\n        RoundedRectangle(cornerRadius: 8)\n            .fill(Color.red)\n        Spacer()\n    }\n    .padding(8)\n'
    assert not _fill_on_root(chip), "a nested chip's fill must not exempt the row"
    assert _fill_on_root('\n    HStack {\n        Text("x")\n    }\n    .background(Color.red)\n')
