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
