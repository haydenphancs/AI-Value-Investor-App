"""Home Market Pulse / Holdings tiles are all the same width (TestFlight 1.0(8), home E3).

Tester: *"I need all these card should have a same width."* Each tile sized itself to its
own label inside an `HStack` (`.frame(minWidth: 88)`), so "Nasdaq Composite ETF" stood out,
and at larger text sizes "Russell 2000 ETF" or a six-figure Bitcoin price would too. A hard
width was already tried and rejected: it truncated 9-character prices at larger Dynamic Type
(`test_ios_a11y_parity.py::test_market_pulse_tile_can_grow` still pins that).

The fix has two halves that are useless apart:
  1. `EqualWidthHStack` — a custom `Layout` that gives every tile the widest tile's width
     and the tallest tile's height (per row);
  2. the card's frame gains `maxWidth`/`maxHeight: .infinity` so a tile with NARROWER
     content actually fills that cell — with `minWidth` alone a flexible frame reports
     clamp(proposal, min, child) and the Holdings tiles (no sparkline) stayed narrow.

`EqualWidthGeometry` holds the arithmetic and is EXECUTED here via `xcrun swift -` (no
XCTest target exists); the Layout and the call sites are pinned by comment-stripped,
brace-bounded scans. Category 1 (pure).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
IOS = BACKEND.parent / "frontend/ios/ios"
GEOMETRY = IOS / "Core/Utilities/EqualWidthGeometry.swift"
LAYOUT = IOS / "Views/Atoms/EqualWidthHStack.swift"
CARD = IOS / "Views/Molecules/MarketPulseCard.swift"
SECTIONS = [IOS / "Views/Organisms/MarketPulseSection.swift",
            IOS / "Views/Organisms/YourWatchlistSection.swift"]
FRAME = ".frame(minWidth: 88, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)"
# A bare `HStack(` — NOT the tail of `EqualWidthHStack(` (a plain substring check matched it).
_PLAIN_HSTACK = re.compile(r"(?<![A-Za-z])HStack\(spacing: 10\)")


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


# ── A. The geometry, executed ─────────────────────────────────────────────────────────

HARNESS = r"""
var failures = 0
func check<T: Equatable>(_ name: String, _ got: T, _ expect: T) {
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(got)|expect=\(expect)") }
}
typealias G = EqualWidthGeometry
func S(_ w: CGFloat, _ h: CGFloat) -> CGSize { CGSize(width: w, height: h) }

// cellSize: widest width and tallest height, independently, rounded UP; junk ignored.
check("cell_empty", G.cellSize(fitting: []), .zero)
check("cell_one", G.cellSize(fitting: [S(108, 60)]), S(108, 60))
check("cell_independent_axes", G.cellSize(fitting: [S(108, 60), S(119, 50), S(90, 85)]), S(119, 85))
check("cell_tester_row", G.cellSize(fitting: [S(108, 70), S(119, 70), S(108, 70), S(108, 70), S(108, 70), S(108, 70)]).width, 119)
check("cell_round_up", G.cellSize(fitting: [S(99.2, 60.01)]), S(100, 61))
check("cell_whole_stays", G.cellSize(fitting: [S(108, 70)]), S(108, 70))
check("cell_nan_per_axis", G.cellSize(fitting: [S(.nan, 50), S(100, .nan)]), S(100, 50))
check("cell_inf_per_axis", G.cellSize(fitting: [S(.infinity, 40), S(90, .infinity)]), S(90, 40))
check("cell_neg_inf", G.cellSize(fitting: [S(-.infinity, -.infinity)]), .zero)
check("cell_negative", G.cellSize(fitting: [S(-10, -5)]), .zero)
check("cell_all_non_finite", G.cellSize(fitting: [S(.nan, .infinity), S(.infinity, .nan)]), .zero)
check("cell_zeros", G.cellSize(fitting: [.zero, .zero]), .zero)
check("cell_order_independent", G.cellSize(fitting: [S(90, 85), S(119, 50), S(108, 60)]), S(119, 85))

// rowSize: n cells + (n-1) gaps; empty is ZERO (not -spacing); junk sanitised.
check("row_empty", G.rowSize(cell: S(108, 70), count: 0, spacing: 10), .zero)
check("row_negative_count", G.rowSize(cell: S(108, 70), count: -3, spacing: 10), .zero)
check("row_one_has_no_gap", G.rowSize(cell: S(108, 70), count: 1, spacing: 10), S(108, 70))
check("row_six_pulse", G.rowSize(cell: S(108, 70), count: 6, spacing: 10), S(698, 70))
check("row_spacing_zero", G.rowSize(cell: S(108, 70), count: 3, spacing: 0), S(324, 70))
check("row_spacing_negative", G.rowSize(cell: S(108, 70), count: 3, spacing: -10), S(324, 70))
check("row_spacing_nan", G.rowSize(cell: S(108, 70), count: 3, spacing: .nan), S(324, 70))
check("row_spacing_inf", G.rowSize(cell: S(108, 70), count: 3, spacing: .infinity), S(324, 70))
check("row_cell_non_finite", G.rowSize(cell: S(.infinity, .nan), count: 2, spacing: 10), S(10, 0))

// originX: consecutive cells are exactly (width + spacing) apart; clamps a negative index.
check("origin_0", G.originX(ofCell: 0, cellWidth: 108, spacing: 10), 0)
check("origin_3", G.originX(ofCell: 3, cellWidth: 108, spacing: 10), 354)
check("origin_negative_index", G.originX(ofCell: -1, cellWidth: 108, spacing: 10), 0)
check("origin_nan_width", G.originX(ofCell: 2, cellWidth: .nan, spacing: 10), 20)
var identities = 0
for n in 1...50 {
    let w: CGFloat = 108, s: CGFloat = 10
    let row = G.rowSize(cell: S(w, 70), count: n, spacing: s)
    // The last cell's trailing edge IS the row's width — nothing clipped, nothing spare.
    check("trailing_edge_n\(n)", G.originX(ofCell: n - 1, cellWidth: w, spacing: s) + w, row.width)
    if n > 1 {
        check("step_n\(n)", G.originX(ofCell: n - 1, cellWidth: w, spacing: s) - G.originX(ofCell: n - 2, cellWidth: w, spacing: s), w + s)
    }
    identities += 1
}
check("identities_ran", identities, 50)
print("DONE|\(failures)")
"""


@pytest.fixture(scope="module")
def swift_output() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=GEOMETRY.read_text() + "\n" + HARNESS,
                              text=True, capture_output=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail("the Swift harness did not complete — the geometry probably stopped compiling "
                    f"standalone (a SwiftUI import?)\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}")
    return proc.stdout


def test_every_geometry_case(swift_output: str):
    failures = [l for l in swift_output.splitlines() if l.startswith("FAIL|")]
    assert not failures, "\n  ".join(failures)


def test_the_harness_actually_asserted_something(swift_output: str):
    oks = {l.split("|", 1)[1] for l in swift_output.splitlines() if l.startswith("ok|")}
    assert len(oks) >= 120, f"only {len(oks)} checks ran"
    for required in ("row_empty", "cell_nan_per_axis", "cell_inf_per_axis", "row_six_pulse",
                     "trailing_edge_n50", "identities_ran"):
        assert required in oks, required


def test_geometry_is_standalone():
    raw = GEOMETRY.read_text()
    code = _strip_comments(raw)
    assert "import CoreGraphics" in code
    assert "import SwiftUI" not in code and "import UIKit" not in code
    assert "import SwiftUI" in raw, "the header comment explaining the rule is gone (anti-vacuity)"
    assert "nonisolated enum EqualWidthGeometry" in code


# ── B. The Layout is a thin wrapper that measures IDEAL sizes ──────────────────────────

def test_layout_measures_ideal_sizes_and_proposes_one_cell():
    layout = _block_after(_code(LAYOUT), "struct EqualWidthHStack: Layout")
    fits = _block_after(layout, "func sizeThatFits(")
    assert "EqualWidthGeometry.rowSize(" in fits and "cell(for: subviews)" in fits
    cell = _block_after(layout, "private func cell(for subviews: Subviews)")
    assert "EqualWidthGeometry.cellSize(" in cell and "sizeThatFits(.unspecified)" in cell
    place = _block_after(layout, "func placeSubviews(")
    for needed in ("proposal: ProposedViewSize(cell)", "anchor: .topLeading",
                   "EqualWidthGeometry.originX(", "bounds.minX", "bounds.minY"):
        assert needed in place, needed
    for banned in ("proposal.width", "proposal.height", "layoutDirection", "Lazy"):
        assert banned not in layout, f"{banned} in EqualWidthHStack"


# ── C. Both strips use it; the card fills its cell ────────────────────────────────────

@pytest.mark.parametrize("path", SECTIONS, ids=[p.stem for p in SECTIONS])
def test_both_strips_lay_tiles_out_with_equal_width(path):
    body = _block_after(_code(path), "var body: some View")
    scroll = _block_after(body, "ScrollView(.horizontal")
    row = _block_after(scroll, "EqualWidthHStack(spacing: 10)")
    assert "MarketPulseCard(item: item)" in row
    assert not _PLAIN_HSTACK.search(scroll) and "LazyHStack" not in scroll
    assert ".padding(.horizontal, AppSpacing.lg)" in scroll[scroll.find(row) + len(row):]
    assert _code(path).count("MarketPulseCard(") == 1


def test_the_card_fills_the_cell_it_is_given():
    body = _block_after(_code(CARD), "var body: some View")
    assert FRAME in body
    assert ".frame(width:" not in body and not re.search(r"maxWidth:\s*\d", body), (
        "a numeric width is the hard width that truncated prices at larger Dynamic Type")
    order = [body.find(FRAME), body.find(".padding(.horizontal, 10)"),
             body.find(".background(AppColors.cardBackground)"), body.find(".cardBorder("),
             body.find(".contentShape(RoundedRectangle(cornerRadius: 12")]
    assert all(i >= 0 for i in order) and order == sorted(order), order
    assert not _PLAIN_HSTACK.search(_code(CARD)), "the preview must use the shipping layout"


def test_comment_stripping_is_not_vacuous():
    sample = "// HStack(spacing: 10)\nlet x = 1 // EqualWidthHStack(spacing: 10)\n"
    stripped = _strip_comments(sample)
    assert "HStack" not in stripped and "let x = 1" in stripped
    assert _PLAIN_HSTACK.search("HStack(spacing: 10)") and not _PLAIN_HSTACK.search("EqualWidthHStack(spacing: 10)")
    for path in SECTIONS:
        assert "ScrollView(.horizontal" in path.read_text()
    assert len(_code(CARD)) > 1500 and len(_code(LAYOUT)) > 600
