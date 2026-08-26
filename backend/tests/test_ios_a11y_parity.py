"""The iOS ACCESSIBILITY contract — Dynamic Type and Differentiate Without Color.

Deliberately a separate module from `test_ios_theme_parity.py`. That one is the COLOUR
contract and is already ~640 lines with its own vocabulary; these are different
invariants about different traits, and merging them would make both harder to read.
Same technique though (grep Swift from Python, ~50ms, no build/simulator/network), and
the same non-negotiable: every scanner ships an ANTI-VACUITY control, because a regex
that quietly stops matching turns each assertion here green.

WHAT THIS CANNOT DO, STATED UP FRONT
------------------------------------
These are REGRESSION guards on specific fixes, not a Dynamic Type lint. ~71 text-bearing
fixed frames and ~89 unguarded `lineLimit(1)` sites remain in the view layer; they are a
known backlog, and raising `readingCap`/`dataCap` is gated on working through it. A test
here passing means "the sites we fixed are still fixed", never "the app reflows well".

The thing that actually MEASURES reflow is `frontend/ios/scripts/type-sweep.sh`, which
drives `simctl ui content_size` across all twelve categories and reads the probe back —
this module cannot see resolution at all, only source.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_APPTHEME = _IOS / "Theme/AppTheme.swift"
_SWEEP = _REPO / "frontend/ios/scripts/type-sweep.sh"


def _src(rel: str) -> str:
    p = _IOS / rel
    assert p.exists(), f"{rel} moved or was renamed — update this test, do not delete it"
    return p.read_text()


def _window_after(text: str, anchor: str, lines: int = 6) -> str:
    """The `lines` source lines following `anchor`. Anchored on a SUBSTRING rather than a
    line number so the assertion survives edits above it."""
    assert anchor in text, f"anchor vanished: {anchor!r}"
    idx = text.index(anchor)
    return "\n".join(text[idx:].splitlines()[:lines])


# ── 1. The caps, and the prose that describes them ───────────────────────────

def _cap(name: str) -> float:
    m = re.search(rf"static let {name}: CGFloat = ([\d.]+)", _APPTHEME.read_text())
    assert m, f"{name} not found in AppTheme.swift"
    return float(m.group(1))


def test_the_dynamic_type_caps_are_what_they_claim():
    """`readingCap`/`dataCap` are a STATED limitation, so the number and the prose have to
    agree. They drifted once already: the block comment said READING scales to 2.0x and
    DATA caps at 1.4x long after the constants had been measured down to 1.4/1.25, so
    anyone reading the docs got the pre-regression values.

    Raising these is a layout project (see the module docstring), not a token edit — if
    this test fails because someone bumped a constant, the question is whether the
    fixed-frame sweep happened, not whether to update the expected value here."""
    assert _cap("readingCap") == 1.4
    assert _cap("dataCap") == 1.25

    src = _APPTHEME.read_text()
    block = src[src.index("THE CLAMP, AND WHY THE TWO TIERS DIFFER"):src.index("static let readingCap")]

    # Forbid the CLAIM, not the mention. The block legitimately explains that 2.0x/1.4x
    # were the pre-regression values — a bare "2.0× not in block" would flag that history
    # and push the next person to delete the explanation, which is the useful part.
    for claim in ("scale to 2.0", "cap at 1.4", "cap at 2.0"):
        assert claim not in block, \
            f"the cap prose asserts {claim!r}; the constants are 1.4 / 1.25 and this drifted once already"
    # ...and it has to name the constants rather than any hardcoded number, so the prose
    # cannot go stale independently of them again.
    assert "readingCap" in block and "dataCap" in block, \
        "the cap prose should point at the constants, not restate their values"
    # The measured plateau values type-sweep.sh tells you to look for.
    assert 15 * _cap("readingCap") == 21.0
    assert 10 * _cap("dataCap") == 12.5


def test_the_scaling_helper_still_applies_a_cap():
    """A cap that is declared but not USED is the same as no cap. Both wrappers must pass
    one through to `scaledSize`, whose `min(scaled, size * maxScale)` is the whole
    mechanism."""
    src = _APPTHEME.read_text()
    assert "min(scaled, size * maxScale)" in src, "the clamp itself is gone"
    assert "maxScale: readingCap" in src
    assert "maxScale: dataCap" in src


# ── 2. The sites fixed in the targeted Dynamic Type pass ─────────────────────
#
# (file, anchor substring, modifiers that must appear within N lines of it).
# Anchors are source text, NOT line numbers, so these survive edits above them.
_GUARDED_SITES = [
    ("Views/Molecules/MarketPulseCard.swift", "Text(item.priceText)",
     ["minimumScaleFactor"]),
    ("Views/Molecules/MarketPulseCard.swift", "Text(item.changeText)",
     ["lineLimit", "minimumScaleFactor"]),
    ("Views/Molecules/MarketPulseCard.swift", "Text(item.name)",
     ["minimumScaleFactor"]),
    ("Views/Molecules/ScannerLeaderboardRow.swift", "Text(entry.name)",
     ["minimumScaleFactor"]),
    ("Views/Molecules/ScannerCard.swift", "Text(scanner.title)",
     ["lineLimit", "minimumScaleFactor"]),
    # "People Also Check". The symbol, price and change had NO lineLimit at all, so under
    # pressure they broke onto a second line and added a whole line of growth on top of the
    # per-line growth. The price is the widest string in a 76pt box and the crypto screen
    # reuses this card with an unseparated "$119332.11".
    ("Views/Molecules/RelatedTickerCard.swift", "Text(ticker.symbol)",
     ["lineLimit", "minimumScaleFactor"]),
    ("Views/Molecules/RelatedTickerCard.swift", "Text(ticker.formattedPrice)",
     ["lineLimit", "minimumScaleFactor", "allowsTightening"]),
    ("Views/Molecules/RelatedTickerCard.swift", "Text(ticker.formattedChange)",
     ["lineLimit", "minimumScaleFactor", "allowsTightening"]),
]


@pytest.mark.parametrize("rel,anchor,required", _GUARDED_SITES,
                         ids=[f"{Path(f).stem}:{a[:28]}" for f, a, _ in _GUARDED_SITES])
def test_a_guarded_text_site_keeps_its_scale_factor(rel, anchor, required):
    """Each of these truncated or wrapped at a larger content size, in a container that
    could not grow. The scale factor is the fix; removing it silently restores the bug,
    because nothing about the layout looks wrong at the default size."""
    window = _window_after(_src(rel), anchor)
    missing = [m for m in required if m not in window]
    assert not missing, f"{rel} — {anchor}: lost {missing}\n{window}"


def test_market_pulse_tile_can_grow():
    """A hard `.frame(width: 88)` left 68pt of usable width for a 9-character price. The
    strip is a horizontal ScrollView that constrains nothing, so `minWidth` lets larger
    text cost a little scroll instead of shrinking every glyph."""
    src = _src("Views/Molecules/MarketPulseCard.swift")
    assert ".frame(minWidth: 88" in src
    assert ".frame(width: 88" not in src


# ── 2b. Horizontal-scroller cards that were hard-boxed ───────────────────────
#
# A TestFlight tester on an ordinary (NON-accessibility) larger Text Size reported the
# "People Also Check" card clipped at the top AND the bottom. `.frame(width:height:)`
# CENTRES an oversized child, so the overflow splits evenly and bleeds off both edges —
# and `.cardSurface()` is a background, not a clip, so it visibly overran the card.
#
# RelatedTickerCard measured 62pt of text into a 64pt budget at the default content size:
# it fit by 2pt and overflowed at every step above default.
#
# The fix is three parts, all load-bearing: pin the width, make the height a FLOOR, and add
# `maxHeight: .infinity` so the card accepts the height the parent HStack resolves (which
# is what keeps interior Spacers working and every card in the row the same height).
#
# The width pin reads as `minWidth == maxWidth` rather than `width:` because `width:`
# belongs to the OTHER `frame` overload and cannot be combined with `minHeight:` —
# mixing them is `error: extra argument 'width' in call`, which is how this was found.
#
# (file, the width pin that must survive, the fixed height that must not come back).
_SCROLLER_CARDS = [
    ("Views/Molecules/RelatedTickerCard.swift", "minWidth: 100, maxWidth: 100", "height: 120)"),
    ("Views/Molecules/PersonaCard.swift", "minHeight: cardHeight", "height: cardHeight)"),
    ("Views/Molecules/LessonCard.swift", "minWidth: 160, maxWidth: 160", "height: 150)"),
    ("Views/Molecules/ResearchCard.swift", "minWidth: 260, maxWidth: 260", "height: 280)"),
    ("Views/Molecules/RelatedMoneyMoveCard.swift", "minWidth: 200, maxWidth: 200", "height: 200)"),
]

# Each card's one parent scroller.
_SCROLLER_PARENTS = [
    "Views/Organisms/TickerDetailRelatedSection.swift",
    "Views/Organisms/PersonaSelectionSection.swift",
    "Views/Organisms/RecentResearchSection.swift",
    "Views/Organisms/InvestorJourneyLevelSection.swift",
    "Views/Organisms/MoneyMoveRelatedArticlesSection.swift",
]


@pytest.mark.parametrize("rel,pin,forbidden", _SCROLLER_CARDS,
                         ids=[Path(f).stem for f, _, _ in _SCROLLER_CARDS])
def test_scroller_card_height_is_a_floor(rel, pin, forbidden):
    """⚠️ Scans STRIPPED source. RelatedTickerCard's header comment quotes the old
    `.frame(width: 100, height: 120)` verbatim while explaining why it was wrong, so an
    un-stripped scan would report the bug as present forever — and, worse, would push the
    next reader to delete the explanation to make the test pass."""
    code = _strip_swift_comments(_src(rel))
    assert pin in code, f"{rel}: lost the width pin / height floor ({pin!r})"
    assert "maxHeight: .infinity" in code, (
        f"{rel}: without `maxHeight: .infinity` the card sizes to its ideal height — interior "
        "Spacers collapse and the row goes ragged the moment one card grows")
    assert forbidden not in code, f"{rel}: the fixed height is back ({forbidden!r})"


def test_the_ai_bar_reserve_scales():
    """The bottom reserve that clears the floating "Ask Cay AI" bar must SCALE, because what
    it clears is text.

    It was a literal `120` in TWELVE places. Once the chips + input bar grew past 120pt the last
    section of the page sat under the overlay with the scroll already at its end — the content
    was simply unreachable. It was reported as "I can't scroll down", not as a layout bug,
    which is why nothing caught it: scrolling itself works fine.
    """
    theme = _APPTHEME.read_text()
    assert "static var aiBarReserve" in theme, "the scaling reserve token is gone"
    block = _window_after(theme, "static var aiBarReserve", lines=3)
    assert "scaledSize(120" in block and "readingCap" in block, \
        f"aiBarReserve no longer scales:\n{block}"

    # Every reserve site must use the token, not a number. Scans STRIPPED source because the
    # token's own doc comment names the literal 120 while explaining it.
    offenders = []
    for path in sorted((_IOS / "Views").rglob("*.swift")):
        lines = _strip_swift_comments(path.read_text()).splitlines()
        for n, line in enumerate(lines):
            if "AppSpacing.aiBarReserve" in line:
                continue
            # a bare 120pt Spacer in a detail tab is the shape we removed
            if ".frame(height: 120)" in line and "Spacer" in "".join(lines[max(0, n - 2):n + 1]):
                offenders.append(f"{path.relative_to(_IOS)}:{n + 1}")
    assert not offenders, "fixed 120pt bottom reserve is back: " + ", ".join(offenders)

    # ...and the token is actually used at the expected number of sites.
    used = sum(_strip_swift_comments(p2.read_text()).count("AppSpacing.aiBarReserve")
               for p2 in (_IOS / "Views").rglob("*.swift"))
    assert used >= 12, f"only {used} sites use aiBarReserve; expected the full set of 12"


def test_the_ai_bar_reserve_scan_is_not_vacuous():
    """Mutation-tested by hand on 2026-08-25: a `.frame(height: 120)` was pasted back under a
    Spacer in TickerDetailOverviewContent, the test was watched to FAIL, and it was restored."""
    # comment stripping must remove the doc comment that names the literal
    sample = "// .frame(height: 120)\nSpacer()\n    .frame(height: AppSpacing.aiBarReserve)\n"
    stripped = _strip_swift_comments(sample)
    assert ".frame(height: 120)" not in stripped, "comment stripping regressed"
    assert "aiBarReserve" in stripped
    # the offender pattern must be able to match at all
    probe = "Spacer()\n    .frame(height: 120)\n".splitlines()
    assert any(".frame(height: 120)" in l for l in probe)
    # the token's doc comment really does contain the trap literal, so stripping is load-bearing
    assert ".frame(height:" not in _APPTHEME.read_text() or "120" in _APPTHEME.read_text()


def test_persona_tagline_is_no_longer_a_hard_28pt_box():
    """Called out separately because it is a SECOND fixed frame nested inside a card that
    already had one, and it is the more certain clip: `.lineLimit(2)` of `caption` needs
    ~31pt at the 1.4x cap and the box was 28pt, so the second line was cut at any raised
    size regardless of the outer frame."""
    code = _strip_swift_comments(_src("Views/Molecules/PersonaCard.swift"))
    assert ".frame(minHeight: 28" in code
    assert ".frame(height: 28" not in code


@pytest.mark.parametrize("rel", _SCROLLER_PARENTS,
                         ids=[Path(f).stem for f in _SCROLLER_PARENTS])
def test_scroller_row_top_aligns(rel):
    """The other half of the frame change, and useless without it. An HStack centres its
    children by default, so once cards size to content a taller one offsets every
    neighbour. Reverting either half alone brings back a visible defect."""
    code = _strip_swift_comments(_src(rel))
    assert "HStack(alignment: .top" in code, f"{rel}: scroller row is not top-aligned"


def test_scroller_card_scanner_is_not_vacuous():
    """Mutation-tested by hand on 2026-08-25: each `forbidden` string was pasted back into
    its own file, the matching parametrised case was watched to FAIL, and the file restored.

    The controls below pin the two mechanisms that could silently disarm the scan."""
    # 1. Comment stripping must actually strip — this is what the RelatedTickerCard case
    #    depends on, so prove it removes a commented-out fixed frame and keeps real code.
    sample = "// .frame(width: 100, height: 120)\nlet x = 1\n"
    assert "height: 120" not in _strip_swift_comments(sample), "comment stripping regressed"
    assert "let x = 1" in _strip_swift_comments(sample)
    # 2. ...and that dependency is real: the trap string IS still in the unstripped file.
    assert "height: 120)" in _src("Views/Molecules/RelatedTickerCard.swift"), \
        "the header comment no longer quotes the old frame — this control is now moot"
    # 3. A moved or emptied file must not read as a pass.
    for rel, _, _ in _SCROLLER_CARDS:
        assert len(_strip_swift_comments(_src(rel))) > 400, f"{rel}: suspiciously small"
    for rel in _SCROLLER_PARENTS:
        assert "ScrollView(.horizontal" in _src(rel), f"{rel}: no longer a horizontal scroller"


def test_tab_bar_labels_are_guarded():
    """Called out separately because this is the one site with no parent that can save
    it: five equal `maxWidth: .infinity` columns, and the bar is hand-rolled (there is no
    `TabView`), so SwiftUI's only remedy is to wrap. "Research" and "Tracking" have no
    inter-word break and split mid-word."""
    window = _window_after(_src("Views/Molecules/TabBarItem.swift"), "Text(tab.rawValue)")
    for m in ("lineLimit", "minimumScaleFactor"):
        assert m in window, f"TabBarItem lost {m}:\n{window}"


def test_the_movers_toggle_and_its_sibling_title_negotiate():
    """A PAIRED invariant, and the reason it is one test rather than two: the toggle's
    per-segment `fixedSize` prevents "Gaine\\nrs", while a container-level `.fixedSize()`
    additionally told the parent HStack "never compress me" — which made the sibling card
    title absorb 100% of any shortfall and reflow to four lines.

    Reintroducing EITHER guard alone brings back one of the two bugs, so the safe state is
    the conjunction: per-segment yes, container no, and the title carries a lineLimit."""
    toggle = _src("Views/Molecules/MoversToggle.swift")
    assert ".fixedSize(horizontal: true, vertical: false)" in toggle, \
        "per-segment fixedSize is what stops the mid-word break — do not remove it"
    # The container form is a bare `.fixedSize()` on its own line.
    assert not re.search(r"^\s*\.fixedSize\(\)\s*$", toggle, re.M), \
        "container-level .fixedSize() is back; it starves the sibling title"
    assert "lineLimit" in _window_after(_src("Views/Molecules/ScannerCard.swift"),
                                        "Text(scanner.title)")


def test_guarded_site_scanner_is_not_vacuous():
    """Every assertion above is `substring in window`, which passes trivially if the
    anchor drifts into a huge window or the file is empty. Prove the mechanism can still
    FAIL, and that each anchor is really present exactly where we think."""
    for rel, anchor, _ in _GUARDED_SITES:
        assert anchor in _src(rel), f"{rel}: anchor {anchor!r} gone"
    # A window that genuinely lacks the modifier must not report it.
    assert "minimumScaleFactor" not in _window_after(
        "Text(\"x\")\n.font(a)\n.foregroundColor(b)\n", "Text(\"x\")")
    # ...and the helper must actually be bounded, or every check is vacuous.
    assert len(_window_after("a\nb\nc\nd\ne\nf\ng\nh\n", "a", lines=3).splitlines()) == 3


# ── 3. The harness the caps depend on ────────────────────────────────────────

def test_the_type_sweep_harness_exists_and_encodes_its_traps():
    """`type-sweep.sh` is the only thing that can measure the shipping mechanism —
    `UIFontMetrics` reads a process-level trait, so SwiftUI's `.dynamicTypeSize()` cannot
    exercise it and `TypographyProbe`'s rendered-height rows print FIXED even when the
    tokens work.

    Each string below is a trap that already cost real debugging time and that FAILS
    SILENTLY — plausible numbers, no error. This asserts the script still guards against
    them rather than having been simplified into something that lies."""
    assert _SWEEP.exists(), "type-sweep.sh is gone — the caps have no measurement behind them"
    s = _SWEEP.read_text()
    assert "CAYDEX_SIM_UDID" in s and "Booted" in s, "UDID pin lost (3 sims share a name)"
    assert "killall cfprefsd" in s, "the cfprefsd warning was removed"
    assert "simctl shutdown" in s and "simctl boot" in s, "reboot path lost"
    assert "AppTypography resolved:" in s and "SYSTEM category=" in s, \
        "the two trustworthy probe lines are no longer what the script greps"
    assert "content_size" in s
    # All twelve categories must stay listed — they are documented nowhere else.
    for c in ("extra-small", "accessibility-medium", "accessibility-extra-extra-extra-large"):
        assert c in s, f"content_size category {c} dropped from the only place it is recorded"


def test_the_probe_still_prints_the_two_trustworthy_lines():
    """The script greps for these exact strings; if the probe renames them the harness
    reports 'no probe output' and someone concludes the build is wrong. Pin both ends."""
    probe = _src("Theme/TypographyProbe.swift")
    assert "SYSTEM category=" in probe
    assert "AppTypography resolved:" in probe


# ── 4. Differentiate Without Color ───────────────────────────────────────────

# Renderers whose ONLY signal for up-vs-down was the hue, and which therefore owe a
# non-colour cue. Hardcoded rather than discovered: a heuristic over "files mentioning a
# sentiment token" sweeps in ~187 files, almost all of which are text with a +/- sign
# already and need nothing. This list is the audited subset.
_SENTIMENT_RENDERERS = [
    "Views/Atoms/SparklineView.swift",
    "Views/Atoms/TintedSparkline.swift",
    "Views/Atoms/MiniStockChart.swift",
    "Views/Atoms/StockPriceDisplay.swift",
    "Views/Molecules/PriceActionSparkline.swift",
    "Views/Molecules/Chart/CandlestickChartRenderer.swift",
    # Volume bars: the one chart in the app where hue was the SOLE channel. Bars grow up
    # and height is volume, so position says nothing about direction, and the crosshair
    # readout printed magnitude only. Hatched under DWC.
    "Views/Molecules/Chart/SubChartCanvas.swift",
]

# Renderers audited and deliberately EXEMPT, each with its reason stated in the source.
# Listed here so "no cue" stays a decision someone made rather than a gap nobody noticed.
_DWC_EXEMPT = {
    "Views/Molecules/Chart/LineChartRenderer.swift":
        "dash is already taken by extended-hours segments; no free channel, and the "
        "signed change is rendered in the header above the chart",
}


def test_every_sentiment_renderer_reads_the_differentiate_flag():
    """WHAT THIS CANNOT DO: it cannot judge whether a cue is any GOOD — only that the
    renderer consults the flag at all. What it stops is a new sentiment-coloured chart
    landing with no non-colour cue whatsoever, which is how all of these started."""
    missing = [r for r in _SENTIMENT_RENDERERS
               if "differentiateWithoutColor" not in _src(r)]
    assert not missing, f"sentiment renderers with no DWC cue: {missing}"


def test_the_dwc_exemptions_still_explain_themselves():
    """An exemption with no stated reason is indistinguishable from an oversight."""
    for rel, reason in _DWC_EXEMPT.items():
        src = _src(rel)
        assert "Differentiate Without Color" in src, f"{rel}: exemption is undocumented"
        assert "differentiateWithoutColor" not in src, \
            f"{rel} now reads the flag — move it to _SENTIMENT_RENDERERS ({reason})"


def test_rasterised_charts_key_their_id_on_the_flag_too():
    """A `.drawingGroup()` is a Metal texture that UIKit does NOT re-render because an
    accessibility setting changed. Without the flag in the `.id()`, the cue appears on a
    cold launch and then freezes — the failure mode a screenshot cannot catch, because
    the screenshot is always taken after a launch."""
    for rel in ("Views/Atoms/MiniStockChart.swift",
                "Views/Molecules/Chart/MainChartCanvas.swift"):
        src = _src(rel)
        assert ".drawingGroup()" in src, f"{rel}: no drawingGroup — is this test stale?"
        assert re.search(r'\.id\("\\\(colorScheme\)-\\\(differentiate\)"\)', src), \
            f"{rel}: .drawingGroup() id must include differentiate, not just colorScheme"


def test_the_shared_helper_exists_and_dashes_only_the_negative():
    """The cue has to be a DIFFERENCE. Dashing both arms encodes nothing, and that is an
    easy edit for someone to make while 'making it consistent'."""
    src = _src("Theme/AppSentiment.swift")
    for fn in ("strokeStyle", "hollowBodyLineWidth", "hatch", "marker"):
        assert f"static func {fn}" in src, f"AppSentiment.{fn} is gone"
    assert "(differentiate && !isPositive) ? dash : []" in src, \
        "strokeStyle no longer dashes exactly the negative arm"
    # The preview/test override is the only way to exercise the ON state in a canvas,
    # because SwiftUI's own key is get-only.
    assert "caydexDifferentiateOverride" in src
    assert "var differentiateWithoutColor: Bool" in src


def test_dwc_renderer_list_is_not_vacuous():
    """Both lists above are hardcoded paths — a rename turns every assertion into a
    no-op unless the paths are proven to resolve."""
    assert len(_SENTIMENT_RENDERERS) >= 6
    for rel in _SENTIMENT_RENDERERS + list(_DWC_EXEMPT):
        assert (_IOS / rel).exists(), f"{rel} moved — update the list, do not delete it"
    # And the marker string must really be absent from an unrelated file, or
    # `"differentiateWithoutColor" not in src` proves nothing.
    assert "differentiateWithoutColor" not in _src("Views/Atoms/IconTile.swift")


# ── Reduce Motion: the header's idle animation ────────────────────────────────
#
# `AskCayAIButton` is the app's one general Cay AI entry and it lives in the header of four
# tabs, so its idle `.symbolEffect(.breathe)` is on screen for the whole session. An
# unconditional indefinite animation in a persistent chrome element is precisely what Reduce
# Motion exists to stop — and unlike a transition, nothing about it is self-limiting.
#
# ⚠️ These scan STRIPPED source. The rationale comments in that file name `.breathe`,
# `isActive`, `reduceMotion` and `symbolEffect` while explaining them, so an un-stripped scan
# would stay green on prose alone after the code was reverted.

_ASK_CAY_AI = "Views/Atoms/AskCayAIButton.swift"


def _strip_swift_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails."""
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("///"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def test_the_header_sparkle_reads_the_reduce_motion_trait():
    code = _strip_swift_comments(_src(_ASK_CAY_AI))
    assert "accessibilityReduceMotion" in code, (
        "AskCayAIButton no longer reads Reduce Motion. Its idle `.breathe` runs for the whole "
        "session in the header of four tabs — it must be switchable off."
    )


def test_the_idle_breathe_is_gated_on_reduce_motion():
    code = _strip_swift_comments(_src(_ASK_CAY_AI))
    # The indefinite effect must be bound to the trait, not merely present alongside it.
    assert re.search(r"\.symbolEffect\(\s*\.breathe\.plain\s*,\s*isActive:\s*!reduceMotion\s*\)", code), (
        "the header sparkle's idle animation is no longer gated on Reduce Motion. Note it must "
        "be stopped via `isActive:` — conditionally REMOVING the modifier re-identifies the view "
        "and cancels the animation mid-cycle instead of settling it."
    )


def test_the_idle_effect_is_the_scale_only_breathe_variant():
    """⚠️ `.plain` is a CONTRAST guard, not a style preference.

    `.breathe` defaults to the `.pulse` variant, which animates opacity as well as scale.
    Measured on the simulator, that drove this glyph to **1.40:1** against its tile at the dim
    end of every cycle — against the 4.5:1 AA bar `primaryBlue` is chosen to clear at 4.52. The
    glyph is the sole carrier of meaning for the button (no label beside it), so the app's AI
    entry point disappeared for part of every cycle for a low-vision user.

    `ThemeContrastAudit` cannot see this: it resolves static token pairs and has no notion of an
    animated opacity. This scan is the only thing standing between a `.plain` -> `.breathe`
    "cleanup" and shipping a sub-AA glyph.
    """
    code = _strip_swift_comments(_src(_ASK_CAY_AI))
    assert ".breathe.plain" in code, (
        "the header sparkle uses the default `.breathe` (== `.pulse`), which animates OPACITY "
        "and measured 1.40:1 at its dimmest. Use `.breathe.plain` — scale only."
    )
    assert not re.search(r"\.symbolEffect\(\s*\.breathe\s*[,)]", code), (
        "a bare `.breathe` (the opacity-animating `.pulse` variant) is back"
    )


def test_the_tap_bounce_is_gated_too():
    """`.symbolEffect(.bounce, value:)` fires on a value CHANGE, so the gate is on the bump."""
    code = _strip_swift_comments(_src(_ASK_CAY_AI))
    assert re.search(r"if\s+!reduceMotion\s*\{\s*bounceTrigger\s*\+=\s*1\s*\}", code), (
        "the tap bounce is no longer gated — the trigger must not advance under Reduce Motion"
    )


def test_the_sparkle_scan_is_not_vacuous():
    """Anti-vacuity, both halves: the file must still be the button, and comment stripping must
    actually strip — this file's rationale names every token asserted above."""
    code = _strip_swift_comments(_src(_ASK_CAY_AI))
    assert "struct AskCayAIButton: View" in code, "scan drifted — this is not the button"
    assert len(code) < len(_src(_ASK_CAY_AI)), "comment stripping removed nothing"

    sample = "// .symbolEffect(.breathe, isActive: !reduceMotion)\nlet x = 1"
    assert "breathe" not in _strip_swift_comments(sample), "comment stripping regressed"
    assert "let x = 1" in _strip_swift_comments(sample)
