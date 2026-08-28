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
    assert "maxWidth: .infinity" in src, (
        "without maxWidth the column takes only its ideal width and the freed space goes "
        "back to the chart"
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


def test_the_skeleton_spacer_matches_the_row():
    row_gap = _strip_comments(_ROW.read_text()).count("Spacer(minLength: AppSpacing.sm)")
    skel_gap = _strip_comments(_SKELETON.read_text()).count("Spacer(minLength: AppSpacing.sm)")
    assert row_gap == 1 and skel_gap == 1, (
        f"the placeholder's trailing gap must match the real row's (row={row_gap}, "
        f"skeleton={skel_gap}). They were 8pt vs 12pt before this."
    )


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
