"""Source-scan guards: the holdings row's name column must be able to grow.

TestFlight, build 1.0 (6), Tracking -> Assets -> Holdings — *"Reduce the price chart shorter?
So the ticker and its name have a longer to show more."* Names rendered as "Oracle Corpo…"
and "Plug Power …".

The cause was not the chart's size. `AssetRow` pinned the ticker/name column at
`.frame(width: 80, alignment: .leading)` — a HARD width, min == max — while `SparklineView`
is one `GeometryReader` with no intrinsic width and a minimum of ZERO. The chart therefore
absorbed every point of slack in the row (~118-138pt on a 393pt device) and the layout engine
could never hand any of it back to a column pinned at exactly 80. At 11pt caption, 80pt fits
~14 characters; "Oracle Corporation" is 18.

This repo has already made this exact fix twice, each with a comment explaining the trap —
`MarketPulseCard.swift` and `ScannerLeaderboardRow.swift` — and the former is pinned by
`test_ios_a11y_parity.py::test_market_pulse_tile_can_grow`. `AssetRow` missed that sweep.
These tests are the equivalent pin for this row.

Two of the assertions below are about DRIFT rather than the bug itself:
`TrackedAssetsSkeleton` exists solely to mirror this row's geometry so the list does not jump
on load, and it previously carried a LITERAL DUPLICATE of the hardcoded 80. It now reads
`AssetRow`'s constants instead, and that is asserted — a copied number diverges silently, and
this one already had (its `Spacer(minLength:)` was `md` where the row's was `sm`, and its call
site was missing 32pt of horizontal padding, so the placeholder cards really were wider than
the rows replacing them).

Comments are stripped before every assertion — the comments beside these fixes quote
`width: 80`, `minWidth`, `sparklineWidth` and `CompanyNameFormatter` verbatim
(`.claude/rules/testing.md` §3). `test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_ROW = _IOS / "Views/Molecules/AssetRow.swift"
_SKELETON = _IOS / "Views/Molecules/TrackedAssetsSkeleton.swift"
_SCREEN = _IOS / "Views/Screens/TrackingView.swift"
_MODELS = _IOS / "Models/TrackingModels.swift"
_SECTION = _IOS / "Views/Organisms/AssetsListSection.swift"

# Anchored on the ticker column's OWN frame. The price column's fill frames also say
# `maxWidth: .infinity` (2026-11-11), so a file-wide substring check stayed green with the
# ticker column's maxWidth deleted.
_TICKER_COLUMN_RE = re.compile(r"\.frame\(minWidth: Self\.tickerColumnMinWidth,\s*maxWidth: \.infinity")


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


# ── 1. The column can grow ────────────────────────────────────────────


def test_the_ticker_column_is_not_hard_pinned():
    """The assertion that would have prevented the bug. Mirrors
    `test_ios_a11y_parity.py::test_market_pulse_tile_can_grow`."""
    src = _strip_comments(_ROW.read_text())
    assert ".frame(width: 80" not in src, (
        "the ticker/name column is hard-pinned again. A hard width is min AND max, so the "
        "slack in the sparkline can never reach the text — which is how 'Oracle Corporation' "
        "became 'Oracle Corpo…'."
    )
    assert ".frame(minWidth: Self.tickerColumnMinWidth" in src, (
        "the column lost its minWidth floor"
    )
    assert _TICKER_COLUMN_RE.search(src), (
        "the ticker column lost its `maxWidth: .infinity` — without it the column takes only "
        "its ideal width and the freed space goes back to the chart"
    )


def test_the_sparkline_is_pinned_so_a_long_name_cannot_starve_it():
    """The other half: with a flexible column, an unpinned GeometryReader (minimum width 0)
    would let a long name collapse the chart to nothing."""
    src = _strip_comments(_ROW.read_text())
    assert ".frame(width: Self.sparklineWidth, height: 32)" in src, (
        "the sparkline is no longer pinned — its GeometryReader has a minimum width of ZERO, "
        "so a long company name would now collapse it entirely"
    )
    assert "static let sparklineWidth: CGFloat = 80" in src, (
        "the sparkline width constant changed or moved. Below ~80pt an early-session row "
        "draws only a small fraction of the box (spanFrom/spanTo) and reads as a dot."
    )


# ── 2. Text that cannot fit degrades instead of clipping ──────────────


def test_every_text_in_the_row_can_shrink_before_it_truncates():
    """All THREE: ticker, company name, price.

    The ticker was the last unguarded one, and it is the worst place to leave a gap.
    `headingSmall` is reading tier (1.4x -> 22.4pt) and this column carries FMP pair
    forms (BTCUSD, DOGEUSD) and index keys (^GSPC), not just 3-4 letter equities. An
    unbreakable 6-character word at 22.4pt is wider than the ~80pt the column resolves
    to, so it either overflows the card or wraps — and a wrap changes the row HEIGHT
    that `AssetsListSection` hard-sizes the entire List from, with scrolling disabled.
    """
    body = _decl_block(_ROW.read_text(), "var body: some View")
    assert body.count(".minimumScaleFactor(0.85)") == 3, (
        "ticker, company name and price each need a scale factor. All three fonts are "
        "READING tier (1.4x) and are intrinsically sized inside a column whose floor is "
        "80pt, so at a large content size they grow straight into the chart — or out of "
        "the card."
    )
    ticker_block = body[body.index("Text(asset.ticker)"):body.index("Text(asset.companyName)")]
    assert ".lineLimit(1)" in ticker_block and ".minimumScaleFactor" in ticker_block, (
        "the ticker Text is unguarded again; a wrapped symbol silently changes row height"
    )
    assert ".truncationMode(.tail)" in body, (
        "the name lost tail truncation — a mid-string ellipsis on a company name is worse "
        "than a trailing one"
    )


# ── 3. The skeleton cannot drift from the row ─────────────────────────


def test_the_skeleton_reads_the_rows_constants_rather_than_copying_them():
    src = _strip_comments(_SKELETON.read_text())
    assert "AssetRow.sparklineWidth" in src and "AssetRow.tickerColumnMinWidth" in src, (
        "TrackedAssetsSkeleton hardcodes its geometry again. It exists to mirror AssetRow so "
        "the list does not jump on load, and a copied number diverges silently — this file "
        "already carried a literal duplicate of the old hardcoded 80."
    )
    assert ".frame(width: 80" not in src, "a hardcoded 80 is back in the skeleton"


def test_no_spacer_after_the_chart_in_the_row_or_the_skeleton():
    """Owner, 2026-11-11: "i want the ticker and its name longer, so some companies has
    longer name can be seen." The navigation side used to end in `Spacer(minLength: sm)`,
    8pt+ of empty space after the chart that the names could never use. With it gone the
    ticker column's `maxWidth: .infinity` takes all the slack and the chart sits one gap
    from the price column. The skeleton mirrors it (it once carried 8pt vs 12pt and the
    list jumped on load)."""
    row_body = _decl_block(_ROW.read_text(), "var body: some View")
    skel_row = _decl_block(_SKELETON.read_text(), "private var row")
    assert "Spacer(" not in row_body, (
        "a Spacer is back in the row: it takes slack the company names need"
    )
    assert "Spacer(" not in skel_row, "the skeleton must match the real row (no Spacer)"


def test_the_skeleton_is_inset_like_every_other_section():
    """It was rendered bare in a LazyVStack that pads nothing, while every sibling self-pads
    AppSpacing.lg — so the placeholder cards were 32pt WIDER than the rows replacing them and
    the list snapped inward on load."""
    src = _strip_comments(_SCREEN.read_text())
    match = re.search(r"TrackedAssetsSkeleton\(\)\s*\n\s*\.padding\(\.horizontal, AppSpacing\.lg\)", src)
    assert match is not None, (
        "TrackedAssetsSkeleton() is no longer horizontally inset at its call site, so the "
        "loading cards are wider than the real ones and the list jumps when data lands"
    )


# ── 4. The name itself is shortened for display ───────────────────────


def test_the_displayed_name_drops_legal_entity_suffixes():
    block = _decl_block(_MODELS.read_text(), "func toTrackedAsset()")
    assert "CompanyNameFormatter.clean(companyName)" in block, (
        "the holdings row shows the raw legal name again. 'Oracle Corporation' does not fit "
        "the column at any reasonable width; 'Oracle' does."
    )


def test_the_raw_name_is_kept_in_the_data_layer():
    """Display-only, per CompanyNameFormatter's own header — search and matching must still
    see the original string."""
    src = _strip_comments(_MODELS.read_text())
    assert src.count("CompanyNameFormatter.clean") == 1, (
        "CompanyNameFormatter must be applied at exactly ONE place, the DTO->display-model "
        "boundary. Applying it in the DTO decode as well would destroy the raw name."
    )


# ── 5. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    fake = (
        "struct Decoy: View {\n"
        "    var body: some View { Text(x).minimumScaleFactor(0.85) }\n"
        "}\n"
        "\n"
        "func toTrackedAsset() {\n"
        "    // CompanyNameFormatter.clean(companyName) .frame(width: 80\n"
        "    return nil\n"
        "}\n"
    )
    block = _decl_block(fake, "func toTrackedAsset()")
    assert "CompanyNameFormatter" not in block, "comments are not being stripped"
    assert ".frame(width: 80" not in block, "comments are not being stripped"
    assert "minimumScaleFactor" not in block, (
        "the scan leaked into the neighbouring `Decoy` declaration"
    )

    # The call-site regex must actually require the modifier, not just the call.
    assert re.search(
        r"TrackedAssetsSkeleton\(\)\s*\n\s*\.padding\(\.horizontal, AppSpacing\.lg\)",
        "TrackedAssetsSkeleton()\n} else if",
    ) is None, "the skeleton-padding regex matches a bare call, so that test is vacuous"

    # The ticker check must not be satisfied by the price column's fill frame.
    decoy = (
        ".frame(minWidth: Self.tickerColumnMinWidth,\n"
        "       alignment: .leading)\n"
        ".frame(maxWidth: .infinity, alignment: .trailing)\n"
    )
    assert _TICKER_COLUMN_RE.search(decoy) is None, (
        "the ticker-column regex matches a maxWidth on ANOTHER frame, so it is vacuous"
    )


def test_the_ticker_column_check_bites_on_the_real_file():
    """Delete the ticker column's `maxWidth: .infinity,` from the real source: the price
    column's fill frames still say `maxWidth: .infinity`, and the check must still fail."""
    src = _strip_comments(_ROW.read_text())
    anchor = "                           maxWidth: .infinity,\n"
    assert src.count(anchor) == 1, "mutation anchor drifted: the ticker column's maxWidth line"
    mutated = src.replace(anchor, "")
    assert "maxWidth: .infinity" in mutated, (
        "the decoy premise is gone (no other maxWidth: .infinity in the row) — fine, but then "
        "this test no longer proves anything; re-check it"
    )
    assert _TICKER_COLUMN_RE.search(mutated) is None


# ── 5. Regressions found by the adversarial review of this change ─────
#
# Making the ticker column FLEXIBLE made the row's height depend on its width for
# the first time. These two both follow from that and neither is visible at the
# default text size.


def test_the_height_probe_is_measured_at_a_real_rows_width():
    """`AssetsListSection` hard-sizes the whole List from ONE hidden AssetRow.

    The probe sits in the List's `.background`, which spans the full width, while
    the real rows are inset by `listRowInsets` on both sides — so the probe is 32pt
    wider. That was harmless while the ticker column was a hard 80pt box, because a
    row's height could not then depend on its width. Now a long symbol can fit on
    one line in the probe's wider column and wrap in the real row's, and the List is
    sized from a height no row on screen actually has — with `scrollDisabled(true)`,
    the bottom holdings become unreachable.
    """
    section = _IOS / "Views/Organisms/AssetsListSection.swift"
    probe = _decl_block(section.read_text(), "private var rowHeightProbe")
    assert ".padding(.horizontal, AppSpacing.lg)" in probe, (
        "the height probe is no longer inset to match listRowInsets, so it measures a "
        "32pt-wider row than the ones it stands in for"
    )
    insets = _strip_comments(section.read_text())
    assert "leading: AppSpacing.lg" in insets and "trailing: AppSpacing.lg" in insets, (
        "the row insets changed — the probe's padding must be changed to match, or the "
        "guard above is pinning the wrong number"
    )


def test_the_skeletons_name_bar_fills_the_column_instead_of_overflowing_it():
    """A fixed 96pt bar is wider than the ~80pt the column resolves to.

    Both the name placeholder and the sparkline placeholder are filled with the same
    colour, so an overflowing name bar closed the 16pt gap between them and the two
    rendered as ONE continuous bar — which then visibly split apart when data landed.
    That is precisely the jump this skeleton exists to prevent.
    """
    row = _decl_block(_SKELETON.read_text(), "private var row")
    assert "bar(width: 96" not in row, (
        "the skeleton's name bar is hard-coded wider than its column again"
    )
    assert "bar(width: nil, height: 10)" in row, (
        "the name bar should fill the column (`width: nil` takes bar()'s maxWidth "
        "branch), so it tracks the column at every device width"
    )


# ── 6. One price column per list, whatever each card holds ────────────
#
# Tracking -> Assets -> Holdings, after hours (2026-11-11, iPhone 17 Pro): the cards did not
# line up. Each card sized its price column from its OWN numbers (the price Button had no
# width), and the navigation side — ticker column, chart, Spacer — got the rest, so the chart
# started at x = inner - 120 - column on every card: ~108-118pt columns on cards with an
# after-hours chip, ~72pt on ETHUSD, whose chart sat ~40pt further right. Six cards also lost
# their chip and drew price and change ~25pt short of the edge (see the `stable_fit` guard in
# test_ios_holdings_extended_chip.py).
#
# The fix gives every card the same column: `.frame(minWidth: 0, maxWidth: cap, alignment:
# .trailing)`. With BOTH bounds a frame takes the width it is offered clamped to them,
# whatever its content, and `.layoutPriority(1)` offers every card the same (row - 176pt
# navigation floor). `minWidth: 0` is load-bearing: with `maxWidth` alone the frame hugs a
# narrower child again. The cap is 88pt (scaled) in the regular session and 128pt when the
# list can show a chip — one answer per LIST, never per card.


def _column_sources() -> dict:
    return {"row": _ROW.read_text(), "section": _SECTION.read_text(),
            "skeleton": _SKELETON.read_text()}


def _toggle_and_navigation(row_src: str):
    body = _decl_block(row_src, "var body: some View")
    second = body.index("Button {", body.index("Button {") + 1)
    return body[second:], body[:second]


def content_independent_column(src: dict) -> None:
    modifier = _decl_block(src["row"], "struct HoldingsPriceColumn: ViewModifier")
    assert re.search(r"\.frame\(\s*minWidth: 0,\s*maxWidth: max\(regularWidth,", modifier), (
        "the price column is no longer clamped with BOTH bounds. With maxWidth alone the frame "
        "takes its content's width when that is narrower, and every card's column — and with "
        "it the chart's x — depends on that card's numbers again."
    )
    assert "alignment: .trailing" in modifier, "price and change must sit flush right"
    assert "@ScaledMetric(relativeTo: .headline)" in modifier and "AssetRow.priceColumnWidth" in modifier, (
        "the regular width must scale on headingSmall's curve (.headline) from the row's constant"
    )
    toggle, navigation = _toggle_and_navigation(src["row"])
    assert ".holdingsPriceColumn(reservesExtendedMove: reservesExtendedMove)" in toggle, (
        "the price column is not sized by HoldingsPriceColumn"
    )
    assert "holdingsPriceColumn" not in navigation
    assert ".frame(width:" not in toggle, (
        "a hard width on the price column is also its MINIMUM: above 119pt it pushes a 375pt "
        "phone's row past its card (navigation floor 176 + 16 + column > 311)"
    )


def column_constants(src: dict) -> None:
    row = _strip_comments(src["row"])

    def const(name: str) -> float:
        m = re.search(rf"static let {name}: CGFloat = (\d+(?:\.\d+)?)", row)
        assert m, f"AssetRow.{name} not found — this scan has drifted"
        return float(m.group(1))

    ticker, spark = const("tickerColumnMinWidth"), const("sparklineWidth")
    regular, extended = const("priceColumnWidth"), const("extendedPriceColumnWidth")
    # AppSpacing.lg (16) between the navigation side's two children and before the price
    # column; 64 = list insets + card padding.
    # No Spacer after the chart since 2026-11-11 (see the no-Spacer test above).
    navigation_floor = ticker + 16 + spark
    offer_375 = (375 - 64) - 16 - navigation_floor
    assert regular <= offer_375, (
        f"priceColumnWidth {regular} exceeds what a 375pt phone can offer ({offer_375}) — that "
        "phone's column is then clamped below the regular width"
    )
    assert regular <= extended, "the after-hours column must not be narrower than the regular one"
    assert (regular, extended) == (88.0, 128.0), (
        f"the column widths changed ({regular}, {extended}). 88 fits '$98,765.43' unshrunk and a "
        "BRK-A price inside its 0.85 scale factor; 128 fits a double-digit chip beside a "
        "double-digit day change. Re-derive both (the doc comments on the constants) and check "
        "the 'Column alignment' preview before changing this pin."
    )


def one_answer_per_list(src: dict) -> None:
    section_body = _decl_block(src["section"], "var body: some View")
    assert "let reservesExtendedMove = extendedMoveIsPossible" in section_body, (
        "the list no longer reads the session ONCE per render"
    )
    assert section_body.index("let reservesExtendedMove = extendedMoveIsPossible") < section_body.index("List {")
    section = _strip_comments(src["section"])
    assert section.count("reservesExtendedMove: reservesExtendedMove") == 1, (
        "the visible rows must get the ONE captured answer — a per-cell read of the clock can "
        "give two column widths in one list across the bell"
    )
    probe = _decl_block(src["section"], "private var rowHeightProbe")
    assert "reservesExtendedMove: extendedMoveIsPossible" in probe, (
        "the height probe must lay out the same column as the rows it stands in for"
    )
    gate = _decl_block(src["section"], "private var extendedMoveIsPossible: Bool")
    assert "AssetRow.isOutsideRegularSession" in gate and "extendedSession != nil" in gate
    row_body = _decl_block(src["row"], "var body: some View")
    assert "MarketHoursUtil" not in row_body and "isOutsideRegularSession" not in row_body, (
        "a card decides its own column from the clock again — the list must decide once"
    )


def skeleton_column(src: dict) -> None:
    row = _decl_block(src["skeleton"], "private var row")
    assert ".holdingsPriceColumn(reservesExtendedMove: AssetRow.isOutsideRegularSession)" in row, (
        "the skeleton's price bars no longer take the real column, so the placeholder chart "
        "lands somewhere else and the list jumps when data arrives"
    )
    assert ".layoutPriority(1)" in row, "without the row's priority the skeleton column is sized differently"


COLUMN_GUARDS = {
    "content_independent_column": content_independent_column,
    "column_constants": column_constants,
    "one_answer_per_list": one_answer_per_list,
    "skeleton_column": skeleton_column,
}


@pytest.mark.parametrize("name", sorted(COLUMN_GUARDS))
def test_column_guard_holds(name):
    COLUMN_GUARDS[name](_column_sources())


COLUMN_MUTATIONS = [
    ("content_independent_column", "row", "            minWidth: 0,\n", ""),
    ("content_independent_column", "row",
     "                .holdingsPriceColumn(reservesExtendedMove: reservesExtendedMove)\n", ""),
    ("column_constants", "row", "static let priceColumnWidth: CGFloat = 88",
     "static let priceColumnWidth: CGFloat = 100"),
    ("one_answer_per_list", "section", "reservesExtendedMove: reservesExtendedMove",
     "reservesExtendedMove: AssetRow.isOutsideRegularSession"),
    ("one_answer_per_list", "section", "                 reservesExtendedMove: extendedMoveIsPossible)",
     "                 reservesExtendedMove: false)"),
    ("skeleton_column", "skeleton",
     "            .holdingsPriceColumn(reservesExtendedMove: AssetRow.isOutsideRegularSession)\n", ""),
]


@pytest.mark.parametrize("name,key,old,new", COLUMN_MUTATIONS,
                         ids=[f"{m[0]}:{m[2].strip()[:28]}" for m in COLUMN_MUTATIONS])
def test_every_column_guard_kills_its_mutation(name, key, old, new):
    src = _column_sources()
    assert src[key].count(old) == 1, f"mutation anchor drifted: {old!r}"
    src[key] = src[key].replace(old, new)
    with pytest.raises(AssertionError):
        COLUMN_GUARDS[name](src)


def test_every_column_guard_has_a_mutation():
    assert {m[0] for m in COLUMN_MUTATIONS} == set(COLUMN_GUARDS)
