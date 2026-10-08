"""Signal of Confidence deep check (2026-09-30) — the iOS half of findings #16 #55
(newest share count), #56 (dividend card labels), #85 (plot clip), #89 (one money
format) and #92 (an unmeasured share-count change).

There is no XCTest target, so these are SOURCE-SCAN guards over the Swift tree plus
Python ports of the right-axis geometry and the formatter. Per testing.md §3 every scan
strips comments first and is brace-bound to the declaration it means, and each was
mutation-tested once by hand (break the Swift → the test fails → restore).
"""

from __future__ import annotations

import math
import pathlib
import re
from typing import List, Optional

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "SignalOfConfidenceModels.swift"
_CHART = _IOS / "Views" / "Molecules" / "SignalOfConfidenceChartView.swift"
_MINI = _IOS / "Views" / "Molecules" / "CapitalAllocationMiniChart.swift"
_CARD = _IOS / "Views" / "Molecules" / "DividendInfoCard.swift"
_SECTION = _IOS / "Views" / "Organisms" / "SignalOfConfidenceSectionCard.swift"


# ── source helpers ───────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    """Drop // line comments and /* */ blocks, leaving string literals intact."""
    out: List[str] = []
    i, n = 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _code(path: pathlib.Path) -> str:
    return _strip_comments(path.read_text(encoding="utf-8"))


def _body(code: str, signature: str) -> str:
    """The brace-balanced body that follows the FIRST occurrence of ``signature``."""
    i = code.find(signature)
    assert i != -1, f"declaration not found: {signature!r}"
    j = code.index("{", i + len(signature) - (1 if signature.endswith("{") else 0))
    depth = 0
    for k in range(j, len(code)):
        if code[k] == "{":
            depth += 1
        elif code[k] == "}":
            depth -= 1
            if depth == 0:
                return code[j + 1:k]
    raise AssertionError(f"unbalanced braces after {signature!r}")


# ── #16 / #55: the highlighted newest share count ────────────────────────────


def test_newest_shares_is_the_newest_reported_count_never_a_zero():
    code = _code(_CHART)
    newest = _body(code, "private var newestShares: Double?")
    assert "?? 0" not in newest and "last?.sharesOutstanding" not in newest, \
        "an unreported newest quarter becomes a bold '0.00M' over the axis minimum"
    assert "reportedShares.last" in newest
    reported = _body(code, "private var reportedShares: [Double]")
    assert "compactMap" in reported and "isFinite" in reported
    # the connector and the label must anchor on the SAME value
    chart = _body(code, "private var chartContent: some View")
    assert "if let lastShares = newestShares" in chart


def test_right_axis_has_no_fake_ticks_and_no_overprint():
    code = _code(_CHART)
    axis = _body(code, "private var rightYAxisLabels: some View")
    assert "if hasReportedShares" in axis, \
        "with no reported count the synthetic 0…1 range printed 1.00M / 0.50M / 0.00M"
    assert "if let newest = newestShares" in axis
    for edge in ("sharesRange.max", "midValue", "sharesRange.min"):
        assert f"if !collidesWithNewest({edge}, in: geometry)" in axis, \
            f"the static {edge} label is drawn even under the bold newest label"
    assert "!reportedShares.isEmpty" in _body(code, "private var hasReportedShares: Bool")
    collide = _body(code, "private func collidesWithNewest(")
    assert "axisLabelMinSeparation" in collide and "sharesYFractionFromTop" in collide


def test_the_plot_is_clipped():
    """#85 defense in depth: a mark can never paint below the plot (the TestFlight class)."""
    chart = _body(_code(_CHART), "private var chartContent: some View")
    style = _body(chart, ".chartPlotStyle")
    assert ".clipped()" in style


# Python port of SignalOfConfidenceChartView's right-axis geometry. `_MIN_SEP` is the
# default-size value of `axisLabelMinSeparation` (20pt since TestFlight 1.0 (11): the old 14pt
# let CRWV's "566M" axis top sit 16pt above the bold "551M", touching).
_BAND_LOW, _BAND_HIGH, _MIN_SEP, _HEIGHT = 0.15, 0.85, 20.0, 280.0


def _shares_range(shares: List[Optional[float]]):
    vals = [s for s in shares if s is not None and math.isfinite(s)]
    if not vals or max(vals) <= 0:
        return 0.0, 1.0
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.1, hi * 0.01)
    return max(lo - pad, 0.0), hi + pad


def _frac_from_top(value, rng):
    lo, hi = rng
    span = hi - lo
    frac = 0.5 if (not math.isfinite(value) or span <= 0) else min(max((value - lo) / span, 0), 1)
    return 1.0 - (_BAND_LOW + frac * (_BAND_HIGH - _BAND_LOW))


def _right_axis(shares: List[Optional[float]]):
    """[(label_value, y_points, bold)] the view draws."""
    reported = [s for s in shares if s is not None and math.isfinite(s)]
    if not reported:
        return []
    rng = _shares_range(shares)
    newest = reported[-1]
    ny = _frac_from_top(newest, rng) * _HEIGHT
    out = []
    for v in (rng[1], (rng[0] + rng[1]) / 2, rng[0]):
        y = _frac_from_top(v, rng) * _HEIGHT
        if abs(y - ny) >= _MIN_SEP:
            out.append((v, y, False))
    out.append((newest, ny, True))
    return out


@pytest.mark.parametrize("shares", [
    [1230, 1228, 1225, None],               # CD-shaped: newest unreported
    [1230, 1228, 1225, 1200],               # newest IS the minimum
    [1000, 1000, 1000, 1000],               # flat
    [500, 900, None, 700],
])
def test_port_highlighted_label_never_overprints_a_static_one(shares):
    labels = _right_axis(shares)
    bold = [l for l in labels if l[2]]
    assert len(bold) == 1
    assert bold[0][0] > 0, "the highlighted count is a reported one, never 0"
    for v, y, is_bold in labels:
        if not is_bold:
            assert abs(y - bold[0][1]) >= _MIN_SEP


def test_port_no_reported_counts_draws_no_labels():
    assert _right_axis([None, None, None]) == []
    assert _right_axis([float("nan"), None]) == []


# CRWV as served after the 2026-10-05 fix (Q3 '24 and Q4 '25 counts refused as vendor
# artifacts): the screenshot's crowded pair — the "566M" axis top and the bold "551M".
_CRWV_SHARES = [None, 404.41, 404.41, 486.59, 497.89, None, 527.0, 551.0]


def test_port_crwv_axis_top_is_hidden_beside_the_newest_count():
    labels = _right_axis(_CRWV_SHARES)
    bold = [l for l in labels if l[2]]
    assert [round(l[0]) for l in bold] == [551]
    statics = sorted(round(l[0]) for l in labels if not l[2])
    assert 566 not in statics, "the '566M' top tick still prints 16pt from the bold '551M'"
    assert statics == [390, 478], statics          # mid and min stay
    # The regression this pins: at the old 14pt the top tick was drawn, 16pt away.
    lo, hi = _shares_range(_CRWV_SHARES)
    gap = abs(_frac_from_top(hi, (lo, hi)) - _frac_from_top(551.0, (lo, hi))) * _HEIGHT
    assert 14.0 <= gap < _MIN_SEP, gap


def test_axis_label_separation_scales_from_twenty_points():
    code = _code(_CHART)
    sep = _body(code, "private var axisLabelMinSeparation: CGFloat")
    m = re.search(r"AppTypography\.scaledSize\(\s*(\d+(?:\.\d+)?)\s*,\s*\.caption2\s*,"
                  r"\s*maxScale:\s*AppTypography\.readingCap\s*\)", sep)
    assert m, f"the separation no longer scales with the caption it separates: {sep!r}"
    assert float(m.group(1)) == _MIN_SEP, "the port's _MIN_SEP drifted from the Swift value"
    assert "private let axisLabelMinSeparation" not in code, "a fixed separation is back"


# ── #89: one money format for both charts ────────────────────────────────────


def _money(millions: float) -> str:
    """Port of `SignalOfConfidenceFormat.money(millions:)`."""
    if not math.isfinite(millions):
        return "—"
    sign = "-" if millions < 0 else ""
    m = abs(millions)
    if m == 0:
        return "$0"
    if m < 0.05:
        return f"{sign}<$0.1M"
    if m < 9.95:
        return f"{sign}${m:.1f}M"
    if m >= 1_000_000 or round(m / 1_000) >= 1_000:
        return f"{sign}${m / 1_000_000:.1f}T"
    if m >= 10_000:
        return f"{sign}${m / 1_000:.0f}B"
    if m >= 1_000 or round(m) >= 1_000:
        return f"{sign}${m / 1_000:.1f}B"
    return f"{sign}${m:.0f}M"


@pytest.mark.parametrize("millions, text", [
    (1_499, "$1.5B"), (1_500, "$1.5B"),            # was "$1B" / "$2B" on the Financials tab
    (2_300 * 0.9, "$2.1B"), (2_300 * 0.6, "$1.4B"), (2_300 * 0.3, "$690M"),  # axis ticks distinct
    (12_300, "$12B"), (999.6, "$1.0B"), (999_600, "$1.0T"), (1_500_000, "$1.5T"),
    (-1_499, "-$1.5B"), (float("nan"), "—"),
    # TestFlight 1.0 (11), CRWV: a measured zero printed "$0M" in every cell. It reads "$0"
    # (the axis baseline's text, and the report header's), and a real amount under $10M
    # keeps a decimal instead of rounding to zero — or to a duplicate axis tick.
    (0, "$0"), (-0.0, "$0"),
    (0.3, "$0.3M"), (0.04, "<$0.1M"), (0.05, "$0.1M"), (0.949, "$0.9M"), (0.95, "$0.9M"),
    (0.96, "$1.0M"), (1.47, "$1.5M"), (2.59, "$2.6M"), (-0.3, "-$0.3M"),
    (9.94, "$9.9M"), (9.95, "$10M"), (10.4, "$10M"), (999.4, "$999M"),
    (1.69 * 0.9, "$1.5M"), (1.69 * 0.6, "$1.0M"), (1.69 * 0.3, "$0.5M"),  # was "$1M" twice
    (1.6 * 0.9, "$1.4M"), (1.6 * 0.6, "$1.0M"),                          # was "$1M" twice
])
def test_port_money_format(millions, text):
    assert _money(millions) == text


def test_money_bottom_of_scale_is_pinned_in_the_swift():
    """The "$0" / sub-$1M branches, in order, in the REAL formatter (comments stripped)."""
    money = _body(_code(_MODELS), "static func money(millions: Double) -> String")
    zero = re.search(r'if m == 0 \{\s*return "\$0"\s*\}', money)
    tiny = re.search(r'if m < 0\.05 \{\s*return sign \+ "<\$0\.1M"\s*\}', money)
    sub = re.search(r'if m < 9\.95 \{\s*return sign \+ String\(format: "\$%\.1fM", m\)\s*\}', money)
    assert zero and tiny and sub, money
    assert zero.start() < tiny.start() < sub.start() < money.index('"$%.1fT"'), \
        "the small-amount branches must run before the tier rules"
    assert '"$0M"' not in money


def test_both_charts_use_the_one_money_format():
    models = _code(_MODELS)
    money = _body(models, "static func money(millions: Double) -> String")
    for token in ('"$%.1fT"', '"$%.0fB"', '"$%.1fB"', '"$%.0fM"', "isFinite"):
        assert token in money, token
    # whole billions only at or above $10B (in millions)
    whole = money.index('"$%.0fB"')
    assert "m >= 10_000" in money[:whole], "whole-billion rounding without the ≥ $10B guard"

    full = _body(_code(_CHART), "private func formatLargeNumber(_ number: Double) -> String")
    mini = _body(_code(_MINI), "private func formatMoney(_ millions: Double) -> String")
    for name, body in (("SignalOfConfidenceChartView", full), ("CapitalAllocationMiniChart", mini)):
        assert "SignalOfConfidenceFormat.money(millions:" in body, f"{name} hand-rolls its format"
        assert "String(format:" not in body, f"{name} hand-rolls its format"


# ── #56: the dividend card compares like with like, over an honest window ────


def test_dividend_card_pairs_dividend_yield_with_the_dividend_average():
    card = _body(_code(_CARD), "struct DividendInfoCard: View {")
    assert "5Y Avg" not in card, "the average covers at most eight quarters"
    assert "dividendInfo.averageYieldLabel" in card
    t12m = card.index('"Dividend Yield (T12M)"')
    avg = card.index("dividendInfo.averageYieldLabel")
    assert t12m < avg
    between = card[t12m:avg]
    assert "formattedDividendYield" in between
    assert "Buyback" not in between and "totalYield" not in between, \
        "a dividend+buyback figure sits next to the dividend-only average again"
    assert "Total Yield (Div + Buyback)" in card

    section = _body(_code(_SECTION), "struct SignalOfConfidenceSectionCard: View {")
    assert "dividendYield: signalData.summary.dividendYield" in section
    assert "currentYield:" not in section


#: The quarter branch, whole: the `n % 4 == 0` test, its order, and BOTH arms. Matching the
#: ternary as one expression means a mutation that keeps every token but swaps the arms
#: still fails (round-2 R49: the old guard asserted only the fallback string, so "(8Y)"
#: over two years passed).
_QUARTER_BRANCH = re.compile(
    r'\s*return\s+n % 4 == 0\s*\?\s*"Avg Dividend Yield \(\\\(n / 4\)Y\)"'
    r'\s*:\s*"Avg Dividend Yield \(\\\(n\)Q\)"\s*'
)
_YEAR_BRANCH = re.compile(r'\s*return\s+"Avg Dividend Yield \(\\\(n\)Y\)"\s*')


def _assert_average_label_logic(label: str) -> None:
    """The quarter-to-year conversion of `DividendInfo.averageYieldLabel`, pinned in the
    Swift itself (comments stripped, brace-bound) — not only in the Python port below."""
    assert '"Trailing Avg Dividend Yield"' in label
    assert "5Y Avg" not in label
    assert re.search(r"let n = Int\(raw\.dropLast\(\)\), n > 0", label), \
        "the count guard is gone: 'Q' / '0Q' / 'xQ' would claim a window"
    q_branch = _body(label, 'if raw.hasSuffix("Q")')
    assert _QUARTER_BRANCH.fullmatch(q_branch), \
        f"the quarter branch no longer converts whole years only: {q_branch!r}"
    q = label.index('raw.hasSuffix("Q")')
    y = label.find('raw.hasSuffix("Y")')
    assert y > q, "the 'Y' suffix branch must follow the 'Q' one"
    assert _YEAR_BRANCH.fullmatch(_body(label, 'if raw.hasSuffix("Y")'))


def test_dividend_info_carries_the_window_label():
    model = _body(_code(_MODELS), "struct DividendInfo {")
    assert re.search(r"var avgYieldWindowLabel: String\? = nil", model)
    _assert_average_label_logic(_body(model, "var averageYieldLabel: String"))


@pytest.mark.parametrize("before, after, why", [
    ("\\(n / 4)Y)", "\\(n)Y)", "8Q would read '(8Y)' over two years"),
    ("n % 4 == 0", "true", "6Q would read '(1Y)'"),
    ('? "Avg Dividend Yield (\\(n / 4)Y)"\n                : "Avg Dividend Yield (\\(n)Q)"',
     '? "Avg Dividend Yield (\\(n)Q)"\n                : "Avg Dividend Yield (\\(n / 4)Y)"',
     "arms swapped: 8Q would read '(8Q)', 6Q '(1Y)'"),
    ("n > 0 else", "true else", "'0Q' would claim a zero-length window"),
])
def test_the_label_guard_rejects_each_mutation(before, after, why):
    """The guard above, mutation-tested in place: each edit to the REAL Swift source must
    fail it (testing.md §3 — a guard that stays green on the bug proves nothing)."""
    src = _MODELS.read_text(encoding="utf-8")
    assert src.count(before) == 1, f"mutation anchor drifted: {before!r}"
    mutated = _strip_comments(src.replace(before, after))
    label = _body(_body(mutated, "struct DividendInfo {"), "var averageYieldLabel: String")
    with pytest.raises(AssertionError):
        _assert_average_label_logic(label)


def _average_label(raw: Optional[str]) -> str:
    """Port of `DividendInfo.averageYieldLabel`."""
    if raw is None:
        return "Trailing Avg Dividend Yield"
    raw = raw.strip()
    try:
        n = int(raw[:-1]) if len(raw) >= 2 else 0
    except ValueError:
        n = 0
    if n <= 0:
        return "Trailing Avg Dividend Yield"
    if raw.endswith("Q"):
        return f"Avg Dividend Yield ({n // 4}Y)" if n % 4 == 0 else f"Avg Dividend Yield ({n}Q)"
    if raw.endswith("Y"):
        return f"Avg Dividend Yield ({n}Y)"
    return "Trailing Avg Dividend Yield"


@pytest.mark.parametrize("raw, label", [
    ("8Q", "Avg Dividend Yield (2Y)"), ("4Q", "Avg Dividend Yield (1Y)"),
    ("6Q", "Avg Dividend Yield (6Q)"), ("5Y", "Avg Dividend Yield (5Y)"),
    (None, "Trailing Avg Dividend Yield"), ("Q", "Trailing Avg Dividend Yield"),
    ("xQ", "Trailing Avg Dividend Yield"), ("0Q", "Trailing Avg Dividend Yield"),
])
def test_port_average_label(raw, label):
    assert _average_label(raw) == label


# ── #92: an unmeasured share-count change ────────────────────────────────────


def test_unknown_share_count_change_is_not_rendered_as_unchanged():
    models = _code(_MODELS)
    summary = _body(models, "struct SignalOfConfidenceSummary {")
    assert re.search(r"var shareCountChangeKnown: Bool = true", summary)
    desc = _body(summary, "var shareCountDescription: String")
    guard = desc.index("guard shareCountChangeKnown")
    assert guard < desc.index('"Share count unchanged."'), \
        "the 0.0 placeholder reaches Cay AI as 'Share count unchanged.'"
    assert "not reported" in desc[guard:guard + 120]

    card = _code(_CARD)
    buyback = _body(card, "struct BuybackOnlyInfoCard: View {")
    fmt = _body(buyback, "private var formattedShareCountChange: String")
    assert re.search(r'guard shareCountChangeKnown else \{ return "—" \}', fmt), \
        "an unmeasured change still prints '+0.0%'"
    section = _body(_code(_SECTION), "struct SignalOfConfidenceSectionCard: View {")
    assert "shareCountChangeKnown: signalData.summary.shareCountChangeKnown" in section
