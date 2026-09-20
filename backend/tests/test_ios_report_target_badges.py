"""Price-target badges sit level with their own pole dots (E4, TestFlight 2026-09-02).

`ReportConsensusBar.targetBadges` used to `.position` the centre of a price-over-percent
badge at the dot's y, so every coloured percent sat half a line BELOW its dot and butted
against the next badge's price — "+61.0%" read as the label of the $461.50 dot beneath it.
Now the PERCENT line is anchored to the dot (`anchor - badgeLineOffset`) and a pure
resolver keeps the three badges `minGap` apart and inside the chart.

The component cannot be rendered on-device today (analyst targets are unlicensed), so the
maths is pinned twice: here from the Swift source, and by an offscreen `ImageRenderer`
sheet that copies the resolver verbatim (session scratchpad `e4/badges.png`). This file
also re-implements the resolver in Python line-for-line and checks the cases the sheet
showed, so a change to the Swift has to be mirrored here on purpose.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_BAR = _REPO / "frontend/ios/ios/Views/Molecules/ReportConsensusBar.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code() -> str:
    assert _BAR.exists(), f"{_BAR} moved — update this guard, do not delete it"
    return _strip_comments(_BAR.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {prefix!r}")


# ── source scan ──────────────────────────────────────────────────────────────

def test_every_badge_is_anchored_by_its_percent_line():
    body = _decl_block(_code(), "private func targetBadges(")
    positions = re.findall(r"\.position\(x:\s*badgeCenterX,\s*y:\s*([^)]+)\)", body)
    assert len(positions) == 3, positions
    for expr in positions:
        assert re.fullmatch(r"anchors\.(high|avg|low) - badgeLineOffset", expr.strip()), (
            f"badge positioned at {expr!r} — the percent is no longer level with its dot"
        )
    assert "Self.resolvedBadgeAnchors(" in body, "the collision resolver is no longer used"
    for name in ("high:", "avg:", "low:", "minGap: badgeMinGap", "height: geometry.size.height"):
        assert name in body


def test_the_offset_is_half_a_line_plus_half_the_spacing():
    src = _code()
    off = _decl_block(src, "private var badgeLineOffset: CGFloat")
    assert "(badgeLineHeight + Self.badgeLineSpacing) / 2" in off
    gap = _decl_block(src, "private var badgeMinGap: CGFloat")
    assert "2 * badgeLineHeight + Self.badgeLineSpacing + 2" in gap
    line = _decl_block(src, "private var badgeLineHeight: CGFloat")
    # Scaled like the caption font, so Dynamic Type does not un-anchor it.
    assert "AppTypography.scaledSize(13, .caption2, maxScale: AppTypography.readingCap)" in line
    assert "VStack(alignment: .center, spacing: 2)" in _decl_block(src, "private func targetBadge(")


def test_the_dots_still_sit_at_their_true_price_y():
    pole = _decl_block(_code(), "private func targetPole(")
    assert "resolvedBadgeAnchors" not in pole, "the dots must never be nudged — only the badges"
    assert pole.count(".position(x: xPos, y: highY)") == 1
    assert pole.count(".position(x: xPos, y: avgY)") == 1
    assert pole.count(".position(x: xPos, y: lowY)") == 1


# ── the resolver, mirrored ───────────────────────────────────────────────────

def _resolve(high, avg, low, *, min_gap, top_inset, bottom_inset, height):
    top = top_inset
    bottom = max(top, height - bottom_inset)
    gap = max(0.0, min_gap)
    h = min(high, avg - gap)
    a = avg
    l = max(low, avg + gap)
    if h < top:
        h = top
        a = max(a, h + gap)
        l = max(l, a + gap)
    if l > bottom:
        l = bottom
        a = min(a, l - gap)
        h = min(h, a - gap)
    clamp = lambda v: min(max(v, top), bottom)
    return clamp(h), clamp(a), clamp(l)


LINE, SPACING = 13.0, 2.0
GAP = 2 * LINE + SPACING + 2          # 30
TOP = 1.5 * LINE + SPACING            # 21.5
BOT = LINE / 2                        # 6.5
H = 200.0


def _r(high, avg, low, height=H):
    return _resolve(high, avg, low, min_gap=GAP, top_inset=TOP, bottom_inset=BOT, height=height)


def test_the_swift_resolver_matches_this_mirror_line_for_line():
    body = _decl_block(_code(), "static func resolvedBadgeAnchors(")
    for line in (
        "let bottom = max(top, height - bottomInset)",
        "var h = min(high, avg - gap)",
        "var l = max(low, avg + gap)",
        "if h < top {", "a = max(a, h + gap)", "l = max(l, a + gap)",
        "if l > bottom {", "a = min(a, l - gap)", "h = min(h, a - gap)",
        "h = min(max(h, top), bottom)",
    ):
        assert line in body, f"resolver drifted from the mirror at: {line}"


def test_well_spaced_dots_are_left_exactly_where_they_are():
    # TER on the screenshot's scale: 30pt / 28pt apart — clear of the 30pt gap? No:
    # 40→70 is exactly 30 (kept), 70→98 is 28 (low nudged to 100).
    assert _r(40, 70, 98) == (40, 70, 100)
    assert _r(40, 80, 120) == (40, 80, 120)


def test_compressed_dots_spread_outward_from_the_average():
    assert _r(60, 80, 100) == (50, 80, 110)
    assert _r(100, 100, 100) == (70, 100, 130)


def test_the_top_edge_cascades_downward_never_stacking():
    h, a, l = _r(0, 30, 60)
    assert h == TOP
    assert a - h >= GAP and l - a >= GAP
    assert l <= H - BOT


def test_the_bottom_edge_cascades_upward():
    h, a, l = _r(140, 170, 200)
    assert l == H - BOT
    assert a - h >= GAP and l - a >= GAP
    assert h >= TOP


def test_a_chart_too_short_for_three_badges_keeps_the_top_and_clamps():
    h, a, l = _r(0, 10, 20, height=50)
    assert h == TOP
    assert TOP <= a <= 50 - BOT and TOP <= l <= 50 - BOT
    assert h <= a <= l


def test_inverted_or_nan_free_inputs_never_reorder():
    # An inverted feed (low above high) still yields high ≤ avg ≤ low on screen.
    h, a, l = _r(120, 80, 40)
    assert h <= a <= l
    assert a == 80


# ── review round (2026-09-19): three more invariants ─────────────────────────

def test_badges_are_sorted_by_dot_y_before_resolving():
    """An inverted feed (consensus above the "high" target) must not put a badge on the
    far side of the pole from its own dot: the triples are sorted by y, and the
    resolver's high/avg/low mean top/middle/bottom on screen."""
    body = _decl_block(_code(), "private func targetBadges(")
    assert ".sorted { $0.y < $1.y }" in body, "the (y, price, percent, colour) triples are no longer sorted"
    assert "high: items[0].y" in body and "avg: items[1].y" in body and "low: items[2].y" in body
    for i in range(3):
        assert f"targetBadge(price: items[{i}].price, percent: items[{i}].percent, color: items[{i}].color)" in body


def test_each_badge_line_stays_a_single_line():
    """The anchoring maths assumes price OVER percent on exactly two lines; a four-digit
    target with cents wrapped the 50pt gutter to three lines."""
    badge = _decl_block(_code(), "private func targetBadge(")
    assert badge.count(".lineLimit(1)") == 2
    assert badge.count(".minimumScaleFactor(0.7)") == 2
    fmt = _decl_block(_code(), "private func formatTargetPrice(")
    assert "abs(value) >= 1000" in fmt, "four-digit targets must drop their cents"


def test_the_plot_is_inset_for_the_badges_when_targets_exist():
    y = _decl_block(_code(), "private func yPosition(")
    assert "consensus.hasAnalystTargets ? badgeTopInset : 0" in y
    assert "consensus.hasAnalystTargets ? badgeBottomInset : 0" in y
    assert "return top + plotHeight * (1 - normalizedValue)" in y


def _y(price, lo, hi, *, height=H, inset=True):
    """Mirror of `yPosition` with `minPrice`/`maxPrice` padding (10 % below, 7 % above)."""
    rng = hi - lo
    mn, mx = lo - rng * 0.1, hi + rng * 0.07
    top = TOP if inset else 0.0
    bottom = BOT if inset else 0.0
    plot = max(height - top - bottom, 1)
    return top + plot * (1 - (price - mn) / (mx - mn))


def test_the_ter_screenshot_case_needs_no_clamp_once_the_plot_is_inset():
    """TER on 2026-09-02: targets 390 / 461.5 / 550, current 341.62, two-year low ≈ 65.
    Without the inset the high dot sat at y≈12 (above TOP=21.5) and the resolver clamped
    every badge down; with it the dots are inside the badge band and stay put."""
    lo, hi = 65.0, 550.0
    old = [_y(p, lo, hi, inset=False) for p in (550, 461.5, 390)]
    assert old[0] < TOP, "premise: the un-inset high dot really was above the top inset"
    new = [_y(p, lo, hi) for p in (550, 461.5, 390)]
    assert new[0] >= TOP and new[2] <= H - BOT
    h, a, l = _r(*new)
    # The middle badge sits exactly on its dot; each outer badge moves only by the
    # band deficit (the TER dots are 27pt / 22pt apart under a 30pt badge band) — and,
    # the point of the inset, the top badge is NOT pinned to the top edge any more.
    assert a == new[1]
    assert abs(h - new[0]) <= GAP - (new[1] - new[0]) + 1e-9
    assert abs(l - new[2]) <= GAP - (new[2] - new[1]) + 1e-9
    assert h > TOP + 1
    assert a - h >= GAP - 1e-9 and l - a >= GAP - 1e-9
