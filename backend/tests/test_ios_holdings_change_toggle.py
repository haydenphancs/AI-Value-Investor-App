"""Source-scan guards: tapping a holdings row's price block toggles % <-> $.

TestFlight, build 1.0 (6), Tracking -> Assets -> Holdings — *"If users touch on price or %
or this area, it can toggle between 2 options: % change or $ change."* The column showed the
percent only, with no way to see the dollar move.

Why these are guarded rather than left to review:

1. **The split tap target is the whole feature and it is invisible in a diff review.**
   `AssetRow` used to be ONE `Button` wrapping the entire row. It is now two SIBLING buttons —
   left opens the detail, right toggles — because a `Button` nested inside another `Button`'s
   label is version-fragile in SwiftUI, and this row sits under `.swipeActions` +
   `.contextMenu` on a `List` with a documented gesture-conflict history. A well-meaning
   "tidy-up" that re-wraps the row in one button silently deletes the feature AND makes the
   price block navigate instead.
2. **`PriceChangeLabel`'s direction must keep coming from the PERCENT.** That path carries two
   guards the file documents as shipped bugs — signed zero (`-0.0` rendered `"+-0.00%"`) and
   NaN (drew a red DOWN arrow beside the text `"nan%"`). Deriving the arrow or colour from the
   new `changeAmount` would reintroduce both on a second, unguarded input.
3. **Dollar mode must degrade to an em dash, never to the percent.** An unlabelled percent
   sitting in a column of dollars is a wrong number, not a degraded one.

There is no XCTest target, so these are read from the Swift source. Comments are stripped
before every assertion — the comments beside each of these fixes name `Button`,
`changeAmount`, `PriceChangeLabel` and the em dash verbatim, so an un-stripped scan would pass
on prose after the code was reverted (`.claude/rules/testing.md` §3).
`test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_ROW = _IOS / "Views/Molecules/AssetRow.swift"
_LABEL = _IOS / "Views/Atoms/PriceChangeLabel.swift"
_SECTION = _IOS / "Views/Organisms/AssetsListSection.swift"
_VM = _IOS / "ViewModels/TrackingViewModel.swift"
_MODELS = _IOS / "Models/TrackingModels.swift"


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. See the module docstring."""
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


# ── 1. Two tap targets, and the right one toggles ─────────────────────


def test_the_row_has_two_sibling_tap_targets():
    body = _decl_block(_ROW.read_text(), "var body: some View")
    assert body.count("Button {") == 2, (
        "AssetRow must have exactly two sibling Buttons — one that opens the detail and one "
        "on the price block that toggles. One Button around the whole row is the state this "
        "feature replaced; three suggests a nested Button, which is what the split avoids."
    )
    assert "onTap?()" in body, "the navigation action is gone"
    assert "onToggleChangeDisplay?()" in body, "the toggle action is gone"


def test_the_price_block_is_in_the_toggle_button_not_the_navigation_button():
    """The structural claim, not just the presence of two buttons: the price and change must
    live under the TOGGLE button. If they drift back under the navigation button, tapping the
    price opens the detail again and the feature is silently gone."""
    body = _decl_block(_ROW.read_text(), "var body: some View")
    second = body.index("Button {", body.index("Button {") + 1)
    navigation_button, toggle_button = body[:second], body[second:]

    assert "PriceChangeLabel(" in toggle_button, (
        "the change label is not inside the toggle Button"
    )
    assert "PriceChangeLabel(" not in navigation_button, (
        "the change label is inside the NAVIGATION Button — tapping the price would open the "
        "detail screen instead of toggling"
    )
    assert "SparklineView(" in navigation_button, (
        "the sparkline left the navigation Button — the row's open-detail area shrank"
    )
    assert ".contentShape(Rectangle())" in toggle_button, (
        "without an explicit contentShape the toggle only responds on the glyphs themselves, "
        "not the empty space around them"
    )
    assert ".frame(maxHeight: .infinity)" in toggle_button, (
        "the toggle's hit area no longer spans the row height"
    )


def test_the_row_still_passes_the_mode_down():
    body = _decl_block(_ROW.read_text(), "var body: some View")
    assert "mode: changeDisplayMode" in body, (
        "AssetRow renders PriceChangeLabel without the mode, so it is permanently percent"
    )
    assert "changeAmount: asset.changeAmount" in body, "the dollar value is not passed"
    # Caught by mutation testing: without this, swapping the two inputs
    # (`changePercent: asset.changeAmount ?? 0`) passed every other assertion here, while
    # making the arrow and colour derive from the dollar value AND printing a dollar figure
    # with a % sign after it in percent mode.
    assert "changePercent: asset.changePercent" in body, (
        "PriceChangeLabel is no longer fed the PERCENT as its percent. Its sign, colour and "
        "arrow all derive from that argument, and percent mode formats it with a '%' suffix."
    )


# ── 2. Direction still comes from the percent ─────────────────────────


def test_direction_and_colour_still_derive_from_the_percent():
    src = _LABEL.read_text()
    is_positive = _decl_block(src, "private var isPositive: Bool")
    color = _decl_block(src, "private var color: Color")

    assert "normalizedChange" in is_positive, (
        "isPositive stopped reading the normalised PERCENT — the signed-zero guard "
        '(-0.0 rendered "+-0.00%") only exists on that path'
    )
    for name, block in (("isPositive", is_positive), ("color", color)):
        assert "changeAmount" not in block, (
            f"{name} now derives direction from changeAmount, an input with neither the "
            "signed-zero normalisation nor the NaN guard. Both were shipped bugs."
        )


def test_dollar_mode_degrades_to_a_dash_not_to_the_percent():
    block = _decl_block(_LABEL.read_text(), "private var formattedChange: String")
    assert "mode == .amount" in block, "the label no longer has a dollar mode"
    assert block.count('return "—"') == 2, (
        "dollar mode must return the em dash when there is no computable amount — exactly as "
        "the non-finite percent case does. Falling back to the percentage would put an "
        "unlabelled percent in a column of dollars."
    )
    # The percent branch must still be reachable and still print a %.
    assert '%"' in block, "the percent branch stopped rendering a percent sign"


# ── 3. The dollar value itself ────────────────────────────────────────


def test_the_dollar_change_is_derived_and_guarded():
    block = _decl_block(_MODELS.read_text(), "var changeAmount: Double?")
    assert "previousClose" in block, "changeAmount must derive from previousClose"
    assert "isFinite" in block, (
        "changeAmount must reject non-finite inputs — a NaN price would render as '+$nan'"
    )


def test_the_dollar_string_puts_the_sign_before_the_dollar():
    """One formatter, shared by the model (VoiceOver) and PriceChangeLabel (the visible
    text), so the two can never disagree about a sign."""
    block = _decl_block(_MODELS.read_text(), "static func formatSignedCurrency")
    assert 'sign = normalized >= 0 ? "+" : "-"' in block, (
        'a loss must read "-$0.05", not "$-0.05" — the sign leads, the currency symbol follows'
    )
    assert "abs(normalized)" in block, (
        "the magnitude must be formatted unsigned, or a negative renders two signs"
    )
    assert "== 0 ? 0 :" in block, (
        "signed zero is not collapsed — the formatter preserves the sign bit, so -0.0 would "
        'print "-$0.00" beside an UP arrow'
    )
    assert "numberStyle = .currency" in block, (
        "grouping is gone — a four-figure move would print +$1234.56 next to a grouped price"
    )

    # The label must USE it rather than re-implementing the formatting a second time.
    label = _decl_block(_LABEL.read_text(), "private var formattedChange: String")
    assert "formatSignedCurrency" in label, (
        "PriceChangeLabel formats dollars itself again — that is how the visible text and the "
        "VoiceOver string drift apart"
    )


# ── 4. The list passes the mode everywhere, including the probe ───────


def test_the_section_passes_the_mode_to_the_rows_and_to_the_height_probe():
    src = _strip_comments(_SECTION.read_text())
    assert src.count("changeDisplayMode: changeDisplayMode") == 2, (
        "AssetsListSection renders TWO AssetRows — the visible one and a hidden height probe "
        "in .background that sizes the whole List. Both must get the mode, or the probe "
        "measures a different row than the one on screen."
    )
    assert "onToggleChangeDisplay: onToggleChangeDisplay" in src, (
        "the toggle callback is not forwarded to the rows"
    )


# ── 5. The choice is remembered ───────────────────────────────────────


def test_the_mode_is_persisted_and_restored():
    src = _strip_comments(_VM.read_text())
    assert 'changeDisplayModeKey = "TrackingView.changeDisplayMode"' in src, (
        "the UserDefaults key is gone — matching sortOption / isInsightsEnabled beside it"
    )
    assert "UserDefaults.standard.set(changeDisplayMode.rawValue" in src, (
        "the mode is no longer written on change"
    )
    assert "ChangeDisplayMode(rawValue: raw)" in src, (
        "the mode is no longer restored in init() — it would reset to percent every launch"
    )


def test_toggling_switches_the_whole_column_and_gives_feedback():
    block = _decl_block(_VM.read_text(), "func toggleChangeDisplayMode()")
    assert "changeDisplayMode.toggled" in block, "the toggle no longer flips the mode"
    assert "Haptics.selection()" in block, (
        "no haptic on a tap whose only visible result is a text swap. Use Haptics, not a raw "
        "feedback generator — that wrapper is what honours the haptic_feedback setting."
    )


# ── 6. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    fake = (
        "struct Decoy: View {\n"
        "    var body: some View { Button { } label: { PriceChangeLabel(x) } }\n"
        "}\n"
        "\n"
        "private var isPositive: Bool {\n"
        "    // changeAmount normalizedChange\n"
        "    return true\n"
        "}\n"
    )
    block = _decl_block(fake, "private var isPositive: Bool")
    assert "changeAmount" not in block, "comments are not being stripped"
    assert "normalizedChange" not in block, "comments are not being stripped"
    assert "PriceChangeLabel" not in block, (
        "the scan leaked into the neighbouring `Decoy` declaration"
    )

    # And the two-button count must actually be able to fail.
    one_button = _decl_block(
        "var body: some View { Button { a() } label: { b() } }\n", "var body: some View"
    )
    assert one_button.count("Button {") == 1, (
        "the Button counter no longer distinguishes one tap target from two, so "
        "test_the_row_has_two_sibling_tap_targets is vacuous"
    )
