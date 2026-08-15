"""Money Moves article-detail chrome — four defects that shipped, pinned so they stay fixed.

None of these were caught by a build or by the theme audit, because none of them are type
errors and none involve a token. They were found by reading the screen against the layout it
actually has now, after the hero headline moved off the artwork.

1. THE READING-PROGRESS TRACK was `Color.white.opacity(0.12)` — a frozen colour, correct only
   in dark. Its own comment claimed it "reads well over the orange hero", and that hero had
   already been deleted. In light mode it was white on #F4F5F8: an invisible rail.

2. THE STICKY-HEADER FADE was a hardcoded 200→280 scroll window inherited from the old 380pt
   full-bleed hero. Under the current layout the title sits around 309pt on a 402pt device, so
   the mini header reached FULL opacity with the hero title still under it — two titles, one
   showing through translucent material. A constant cannot fix that: artwork height is
   (width − 32) × 9/16 and AppTypography scales to 1.4×, so the title's top swings ~130pt
   across devices and text sizes, which is wider than the whole fade window.

3. THE MINI-HEADER CHIPS were correct and the COMMENT was wrong — it claimed they mirror the
   hero's capsules, which would have been a bug in both appearances.

4. THE GRAIN OVERLAY re-rolled `CGFloat.random` inside its Canvas closure, so the speck field
   changed on every evaluation (shimmer under scroll) at ~1,540 fills per draw.

Source-scan style, per the house pattern (there is no XCTest target). Every window is
brace/anchor-bounded and comment-stripped, and the last test proves none of them are vacuous.
Category 1 (pure) — no network, no Supabase.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
IOS = REPO / "frontend/ios/ios"

SCREEN = IOS / "Views/Screens/MoneyMoveArticleDetailView.swift"
HERO = IOS / "Views/Organisms/MoneyMoveArticleHeroHeader.swift"
GRAIN = IOS / "Views/Atoms/GrainyTextureOverlay.swift"
PROGRESS_ATOM = IOS / "Views/Atoms/ProgressBar.swift"


def _strip(src: str) -> str:
    """A rule quoted in a `//` comment must never satisfy an assertion about the code.

    Load-bearing here: the corrected A3 comment explains at length why `cardBackground` is
    WRONG for the mini header, so a naive search would find the token and pass.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def _fade_window(src: str) -> str:
    """The opacity derivation ONLY: `headerFadeDistance` up to `readingProgress`.

    ⚠️ Both bounds are load-bearing. Stopping at `var body` instead swallows
    `readingProgress`, which legitimately reads `scrollOffset` — so the "no scroll threshold"
    assertion would fail on correct code. And not stopping at all picks up the 120pt bottom
    spacer and the 36pt share button, defeating the large-literal check.
    """
    after = src.split("headerFadeDistance", 1)[1]
    return after.split("private var readingProgress", 1)[0]


# --------------------------------------------------------------------------
# 1. The reading-progress rail
# --------------------------------------------------------------------------
def test_reading_progress_rail_reuses_the_progress_atom():
    """Four atoms are already `ZStack{track;fill}` and all four fill the track with
    `cardBackgroundLight`. A fifth hand-rolled copy is how one of them ends up with a frozen
    colour again — which is precisely what happened."""
    src = _strip(SCREEN.read_text())
    rail = src.split("private var readingProgressBar", 1)
    assert len(rail) == 2, "readingProgressBar is gone — did the rail move?"
    body = rail[1].split("\n    }", 1)[0]

    assert "ProgressBar(" in body, "the rail no longer reuses the ProgressBar atom"
    assert "showPercentage: false" in body, "the rail must not render a percentage label"
    assert "Color.white" not in body, "a frozen white track is back on the rail"

    atom = _strip(PROGRESS_ATOM.read_text())
    assert "AppColors.cardBackgroundLight" in atom, (
        "ProgressBar's track is no longer the adaptive surface token — the rail inherits "
        "whatever this is, so it must stay adaptive")


def test_the_screen_holds_no_frozen_colour_literals():
    """Wider than the rail: any `Color.white`/`Color.black` on this screen is a colour that
    cannot adapt, and this screen exists in both appearances."""
    src = _strip(SCREEN.read_text())
    offenders = [l.strip() for l in src.splitlines()
                 if "Color.white" in l or "Color.black" in l]
    assert not offenders, "non-adaptive colour literals on the article screen:\n  " + \
                          "\n  ".join(offenders)


# --------------------------------------------------------------------------
# 2. The measured fade
# --------------------------------------------------------------------------
def test_mini_header_fade_is_measured_not_a_scroll_threshold():
    """The opacities must derive from MEASURED hero edges, never from `scrollOffset`.

    `scrollOffset` still exists on this screen — `readingProgress` needs it — so the check is
    specifically that it does not leak back into the fade.
    """
    src = _strip(SCREEN.read_text())
    window = _fade_window(src)

    assert "heroNavBottom" in window and "heroTitleBottom" in window
    assert "chromeOpacity" in window and "titleOpacity" in window
    assert "scrollOffset" not in window, (
        "the fade is keyed off scrollOffset again — that is the hardcoded-threshold bug")

    numbers = [int(n) for n in re.findall(r"\b(\d+)\b", window)]
    assert not [n for n in numbers if n >= 100], (
        f"a large literal is back in the fade derivation ({sorted(n for n in numbers if n >= 100)}) "
        "— 200/280 were the stale thresholds from the old 380pt full-bleed hero")


def test_hero_reports_both_edges_and_the_screen_consumes_them():
    """Two independent ramps, so the bar's chrome can arrive with the hero's own back button
    (~250pt earlier) while its TITLE waits for the hero title to clear."""
    hero = _strip(HERO.read_text())
    screen = _strip(SCREEN.read_text())

    assert hero.count(".onGeometryChange") >= 2, (
        "the hero must report BOTH its nav row and its title edge")
    for cb in ("onNavBottomChange", "onTitleBottomChange"):
        assert f"var {cb}:" in hero, f"{cb} is not declared on the hero"
        assert f"{cb}?(" in hero, f"{cb} is declared but never called"
        assert f"{cb}:" in screen, f"the screen does not pass {cb}"

    assert "MoneyMoveArticleSpace" in hero and "MoneyMoveArticleSpace" in screen, (
        "both halves must agree on the named coordinate space")
    # The title ramp must gate the mini title only, not the whole bar.
    bar = screen.split("private var miniHeader", 1)[1].split("\n    }", 1)[0]
    assert "titleOpacity" in bar, "the mini header's title is not on its own ramp"


def test_hero_metrics_default_to_offscreen():
    """`fade(0) == 1`, so a default-zero @State would paint the mini header fully opaque over
    an unscrolled hero for the frame before the first measurement lands."""
    src = _strip(SCREEN.read_text())
    for name in ("heroNavBottom", "heroTitleBottom"):
        decl = [l for l in src.splitlines() if f"var {name}" in l]
        assert decl, f"{name} not declared"
        assert ".greatestFiniteMagnitude" in decl[0], (
            f"{name} must start offscreen, not at 0 — 0 reads as 'already scrolled past'")


def test_article_swap_resets_the_hero_metrics():
    """`.id(shown.id)` rebuilds the scroll view, but the SCREEN's @State survives a related-
    article swap, so the outgoing article's edges would flash the bar over the new hero."""
    src = _strip(SCREEN.read_text())
    body = src.split("private func openRelated", 1)[1].split("\n    }", 1)[0]
    for name in ("heroNavBottom", "heroTitleBottom"):
        assert f"{name} = .greatestFiniteMagnitude" in body, (
            f"openRelated does not reset {name}")


def test_named_space_is_not_anchored_to_an_ignores_safe_area_view():
    """⚠️ The space must sit on the ZStack that hosts the mini header. Put it on a view that
    extends under the status bar and every reported edge grows by the ~59pt inset, so both
    ramps fire a full bar-height early — and it looks *almost* right, which is worse."""
    src = _strip(SCREEN.read_text())
    lines = [l for l in src.splitlines() if l.strip()]
    idx = [i for i, l in enumerate(lines) if ".coordinateSpace(.named(" in l]
    assert idx, "the named coordinate space is gone — the hero cannot report into it"
    for i in idx:
        assert ".ignoresSafeArea" not in lines[i], "coordinateSpace chained onto ignoresSafeArea"
        assert ".ignoresSafeArea" not in lines[i - 1], (
            "coordinateSpace is applied directly to a view that ignores the safe area — "
            "every measurement would be off by the status-bar inset")


# --------------------------------------------------------------------------
# 3. The chip fill
# --------------------------------------------------------------------------
def test_mini_header_chips_are_adaptive_and_not_cardBackground():
    """The bar sits on `.ultraThinMaterial`, where `cardBackground` disappears in BOTH
    appearances (#FFFFFF ~ the light frost, #1E2330 ~ the dark frost). A scrim derived from an
    adaptive ink is correct on either side by construction and survives Reduce Transparency."""
    src = _strip(SCREEN.read_text())
    bar = src.split("private var miniHeader", 1)[1].split("\n    }\n", 1)[0]
    fills = re.findall(r"\.fill\(([^)]*\)?[^)]*)\)", bar)
    assert fills, "no chip fills found in the mini header"
    for f in fills:
        assert "AppColors." in f, f"non-token chip fill on the mini header: {f!r}"
        assert "AppColors.cardBackground" not in f, (
            "the mini-header chip uses cardBackground — invisible on ultraThinMaterial in "
            "BOTH appearances. The hero's capsules are the ones that want it.")


# --------------------------------------------------------------------------
# 4. The grain field
# --------------------------------------------------------------------------
def test_grain_overlay_is_deterministic():
    """Every `.random` must be seeded, and the field must be computed once as a `static let`.

    Unseeded randomness inside the Canvas closure meant a different picture on every
    evaluation. That is a visible shimmer while scrolling, and it is also what made
    `rendersAsynchronously` unsafe to use.
    """
    src = _strip(GRAIN.read_text())
    randoms = re.findall(r"\.random\(([^)]*)\)", src)
    assert randoms, "no randomness at all — is this still the grain overlay?"
    unseeded = [r for r in randoms if "using:" not in r]
    assert not unseeded, (
        f"unseeded randomness is back in the grain: {unseeded} — the field will re-roll on "
        "every Canvas evaluation")
    assert "RandomNumberGenerator" in src, "no seeded generator in the file"
    assert "static let field" in src, (
        "the speck field must be a static let, computed once — a per-instance field re-rolls "
        "for every cover on screen")


def test_grain_overlay_is_not_rasterised_into_a_drawing_group():
    """`Canvas` already rasterises to one layer, so `.drawingGroup()` adds a redundant Metal
    pass — and per the iOS theme rules anything inside one needs `.id(colorScheme)` or it
    keeps stale colours across an appearance flip."""
    assert ".drawingGroup()" not in _strip(GRAIN.read_text())


def test_grain_overlay_caps_its_speck_count():
    """Count scales with area, so an unbounded frame would turn one draw into a stall."""
    src = _strip(GRAIN.read_text())
    assert "maxSpecks" in src and "min(" in src, "the speck count is unbounded"


# --------------------------------------------------------------------------
# Anti-vacuity — every window above must be real, and stripping must strip
# --------------------------------------------------------------------------
def test_source_scan_helpers_are_not_vacuous():
    for p in (SCREEN, HERO, GRAIN, PROGRESS_ATOM):
        assert p.exists(), f"{p} does not exist — every scan over it would pass vacuously"

    screen = _strip(SCREEN.read_text())
    window = _fade_window(screen)
    assert window.strip(), "the fade window is empty"
    assert "private var miniHeader" in screen and "private func openRelated" in screen, (
        "the miniHeader / openRelated anchors moved; their windows would be empty")
    # The window must END before readingProgress, or the scrollOffset assertion fails on
    # correct code — and someone would then "fix" it by deleting the assertion.
    assert "readingProgress" not in window, "_fade_window over-reaches into readingProgress"
    assert "chromeOpacity" in window and "titleOpacity" in window, "_fade_window under-reaches"

    # Comment-stripping must really strip. The corrected A3 comment argues at length that
    # `cardBackground` is WRONG here, so the raw source contains the exact token
    # test_mini_header_chips_are_adaptive_and_not_cardBackground asserts is absent.
    raw = SCREEN.read_text()
    doc = raw.split("private var miniHeader", 1)[0]
    assert "cardBackground" in doc, (
        "the reasoning comment naming cardBackground is gone — the strip check below would "
        "then prove nothing")
    assert "cardBackground" not in _strip(doc).split("private var readingProgressBar", 1)[0], (
        "_strip() failed to remove the commented-out mention of cardBackground")

    # And the fade-window number scan must be capable of firing on the values that shipped.
    probe = "let fadeStart: CGFloat = 200\nlet fadeEnd: CGFloat = 280\n"
    assert [int(n) for n in re.findall(r"\b(\d+)\b", probe) if int(n) >= 100] == [200, 280]
    # ...and the chip-fill regex must actually match a real fill, not silently find nothing.
    assert re.findall(r"\.fill\(([^)]*\)?[^)]*)\)", "  .fill(AppColors.textPrimary.opacity(0.15))")
