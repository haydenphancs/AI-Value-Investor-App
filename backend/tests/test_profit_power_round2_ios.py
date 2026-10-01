"""Profit Power round 2 (2026-09-30) — iOS half, pinned from Python.

  R1 / R14  The first fix pass made both margin charts' y-domain `ChartDomain.robust`
            over ONE pooled list of every series. Pooling made the fence itself create
            "outliers": a gross line sitting above four low lines (WMT 24% vs 1-5%, COST,
            AMZN, any software company) fell beyond q3 + 3·IQR of the pool and was drawn
            flat on the plot edge with an arrow on every column — on the default Annual tab
            of blue-chip tickers. Its tests only checked single-cluster data and never
            asserted that ORDINARY values stay on scale. Now the axis is the OLD rounded
            min/max of every value except one that is BOTH extreme in size (|v| > 100%)
            AND outside the pooled fence.
  R39       A pinned-above arrow was centred on the plot's top edge, which is flush with
            the horizontal ScrollView that clips; its tip was cut off.
  R35       The report's Profitability drill-down anchored "Current" (and its
            verdict-coloured vs-industry line) on the last period WITH a value, so a
            trailing revenue gap made an older year read as current.

Two kinds of test, as in `test_profit_power_deepcheck_ios.py`: a Python PORT of the
domain math whose threshold is READ FROM THE SWIFT, and source-scan guards that bind the
port's structure to the Swift. Scans strip comments and are brace-bound to the declaration
they mean (`.claude/rules/testing.md` §3); each was mutation-tested once by hand against
the first-pass Swift (pooled-fence domain, edge-centred arrows, backward-scanning sheet)
and fails there.
"""
from __future__ import annotations

import math
import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MOLECULES = _IOS / "Views" / "Molecules"
_PP_CHART = _MOLECULES / "ProfitPowerChartView.swift"
_PROF_CHART = _MOLECULES / "ProfitabilityChartView.swift"
_PROF_SHEET = _MOLECULES / "ProfitabilityChartSheet.swift"


# ── source helpers ────────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _decl_body(src: str, prefix: str) -> str:
    at = src.index(prefix)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _pp_view() -> str:
    return _decl_body(_code(_PP_CHART), "struct ProfitPowerChartView")


def _prof_view() -> str:
    return _decl_body(_code(_PROF_CHART), "struct ProfitabilityChartView")


def _sheet() -> str:
    return _decl_body(_code(_PROF_SHEET), "struct ProfitabilityChartSheet")


def _swift_constant(view: str, name: str) -> float:
    m = re.search(rf"private static let {name}: Double = ([0-9.]+)", view)
    assert m, f"{name} not found — the port cannot read the Swift threshold"
    return float(m.group(1))


# ── Python port of ChartDomain.make / .robust (Core/Utilities/ChartDomain.swift) ──

_MIN_SPAN = 1.0


def _make(values, include_zero=True, headroom=0.15, fallback=(0.0, 1.0)):
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return fallback
    lo, hi = min(finite), max(finite)
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = hi - lo
    pad = max(span * headroom, _MIN_SPAN / 2 if span == 0 else 0.0)
    if hi > 0 or span == 0:
        hi += pad
    if lo < 0:
        lo -= pad
    if hi - lo < _MIN_SPAN:
        mid = (hi + lo) / 2
        lo, hi = mid - _MIN_SPAN / 2, mid + _MIN_SPAN / 2
        if include_zero:
            lo, hi = min(lo, 0.0), max(hi, _MIN_SPAN)
    return (lo, hi)


def _robust(values, include_zero=True, headroom=0.15, fallback=(0.0, 1.0)):
    finite = sorted(v for v in values if math.isfinite(v))
    n = len(finite)
    if n < 4:
        return _make(finite, include_zero, headroom, fallback)
    q1 = finite[n // 4]
    q3 = finite[min((n * 3) // 4, n - 2)]
    iqr = max(q3 - q1, abs(q3) * 0.1, _MIN_SPAN)
    return _make([max(finite[0], q1 - 3 * iqr), min(finite[-1], q3 + 3 * iqr)],
                 include_zero, headroom, fallback)


def _is_off_scale(v, d):
    return math.isfinite(v) and (v < d[0] or v > d[1])


def _axis_values(values, extreme, fallback):
    """Port of both views' `axisValues`."""
    finite = [v for v in values if math.isfinite(v)]
    lo, hi = _robust(finite, True, 0.0, fallback)
    return [v for v in finite if abs(v) <= extreme or lo <= v <= hi]


def _pp_domain(values):
    """Port of ProfitPowerChartView.marginDomain (threshold read from the Swift)."""
    extreme = _swift_constant(_pp_view(), "extremeMarginPct")
    kept = _axis_values(values, extreme, (0.0, 50.0))
    rounded = [math.ceil(v / 10) * 10 for v in kept] + [math.floor(v / 10) * 10 for v in kept]
    return _make(rounded, True, 0.0, (0.0, 50.0))


def _old_pp_domain(values):
    """The pre-fix marginDomain: every value rounded to 10s, then plain min/max."""
    rounded = [math.ceil(v / 10) * 10 for v in values] + [math.floor(v / 10) * 10 for v in values]
    return _make(rounded, True, 0.0, (0.0, 50.0))


def _first_pass_pp_domain(values):
    """The FIRST-PASS marginDomain (pooled fence) — kept only to prove the fixtures
    below really exercise the defect."""
    lo, hi = _robust(values, True, 0.0, (0.0, 50.0))
    return _make([math.floor(lo / 10) * 10, math.ceil(hi / 10) * 10], True, 0.0, (0.0, 50.0))


def _prof_domain(values):
    """Port of ProfitabilityChartView.yDomain."""
    extreme = _swift_constant(_prof_view(), "extremeValuePct")
    kept = _axis_values(values, extreme, (0.0, 1.0))
    lo = min(min(kept, default=0.0), 0.0)
    hi = max(max(kept, default=1.0), 0.0)
    if hi > 0:
        hi *= 1.12
    if lo < 0:
        lo *= 1.12
    if lo == hi:
        hi = lo + 1
    return (lo, hi)


def _old_prof_domain(values):
    lo = min(min(values, default=0.0), 0.0)
    hi = max(max(values, default=1.0), 0.0)
    if hi > 0:
        hi *= 1.12
    if lo < 0:
        lo *= 1.12
    if lo == hi:
        hi = lo + 1
    return (lo, hi)


def _five_series(gross, op, net, fcf, peer):
    """`allValues` order: per period, gross / net / peer / operating / FCF."""
    out = []
    for i in range(len(gross)):
        for s in (gross, net, peer, op, fcf):
            if s[i] is not None:
                out.append(float(s[i]))
    return out


# FY2016-25-shaped histories (approximate, illustrative). Gross is a SEPARATE top cluster.
_WMT = _five_series(
    [24.1, 24.3, 24.7, 24.8, 24.6, 24.4, 23.5, 23.7, 24.1, 24.9],
    [4.5, 4.1, 3.3, 3.7, 3.4, 4.3, 3.8, 3.3, 4.2, 4.3],
    [2.8, 2.6, 2.0, 1.4, 2.8, 2.4, 2.3, 1.9, 2.4, 2.9],
    [4.6, 4.6, 2.6, 3.1, 4.6, 4.1, 2.0, 3.0, 3.5, 4.0],
    [2.5] * 10,
)
_COST = _five_series(
    [12.6, 12.6, 12.6, 12.6, 12.6, 12.8, 12.5, 12.3, 12.6, 12.6],
    [3.1, 3.0, 3.2, 3.2, 3.4, 3.4, 3.5, 3.4, 3.6, 3.7],
    [2.0, 2.1, 2.2, 2.3, 2.4, 2.6, 2.6, 2.6, 2.9, 2.9],
    [2.4, 2.5, 3.6, 2.7, 4.3, 4.8, 3.6, 2.3, 3.0, 3.7],
    [2.5] * 10,
)
_AMZN = _five_series(
    [33.0, 35.1, 37.1, 40.2, 40.2, 39.6, 42.0, 43.8, 47.0, 49.5],
    [3.1, 2.3, 2.3, 5.3, 5.2, 5.9, 5.3, -0.5, 6.4, 10.7],
    [0.6, 1.7, 1.7, 4.3, 4.1, 6.3, 7.1, -0.5, 5.3, 9.3],
    [7.0, 7.0, 4.6, 8.2, 7.7, 8.4, -2.1, -2.7, 6.9, 5.0],
    [3.0] * 10,
)
_SOFTWARE = _five_series(
    [75, 76, 77, 75, 76, 77, 75, 76, 77, 76],
    [12, 13, 14, 15, 16, 12, 13, 14, 15, 16],
    [8, 9, 10, 11, 12, 13, 14, 15, 16, 10],
    [14, 15, 16, 12, 13, 14, 15, 16, 12, 13],
    [10] * 10,
)


def _biotech_quarters(with_outlier=True):
    """20 ordinary quarters of 5 series, plus ONE −40,000% net margin (a ~$0.2M quarter)."""
    vals = []
    for i in range(20):
        wobble = (i % 5) - 2
        vals += [60.0 + wobble, 20.0 + 2 * wobble, 15.0 - wobble, 12.0 + wobble, 10.0]
    if with_outlier:
        vals[3] = -40000.0
    return vals


_RIVN = [float(v) for row in (
    [-846, -8540, -9000, -8524, 8],
    [-207, -405, -393, -407, 9],
    [-46, -181, -177, -188, 7],
    [-24, -96, -55, -96, 8],
    [2, -60, -40, -60, 8],
) for v in row]


# ── R1 / R14: ordinary multi-cluster data keeps EXACTLY the old axis ──────────


@pytest.mark.parametrize("name,values,old_axis", [
    ("WMT", _WMT, (0, 30)),
    ("COST", _COST, (0, 20)),
    ("AMZN", _AMZN, (-10, 50)),
    ("software", _SOFTWARE, (0, 80)),
    ("biotech without its outlier", _biotech_quarters(with_outlier=False), (0, 70)),
])
def test_multi_cluster_history_keeps_the_old_axis_and_pins_nothing(name, values, old_axis):
    first_pass = _first_pass_pp_domain(values)
    # The fixture really exercises the defect: the pooled fence cut off the gross line.
    assert any(_is_off_scale(v, first_pass) for v in values), (name, first_pass)
    new = _pp_domain(values)
    assert new == _old_pp_domain(values) == old_axis, (name, new)
    pinned = [v for v in values if _is_off_scale(v, new)]
    assert pinned == [], f"{name}: ordinary margins drawn off-scale: {pinned}"


def test_a_tiny_revenue_quarter_is_still_pinned_and_nothing_else_is():
    vals = _biotech_quarters()
    new = _pp_domain(vals)
    assert new == (0, 70), new                  # the old axis of the ordinary quarters
    assert _is_off_scale(-40000.0, new)
    ordinary = [v for v in vals if v != -40000.0]
    assert not any(_is_off_scale(v, new) for v in ordinary), "R14: non-outliers stay on scale"


def test_rivn_shaped_history_pins_only_its_tiny_revenue_year():
    new = _pp_domain(_RIVN)
    pinned = sorted(v for v in _RIVN if _is_off_scale(v, new))
    assert pinned == [-9000.0, -8540.0, -8524.0], (new, pinned)
    # 2021's gross margin (−846%) is extreme in size but INSIDE the fence: it sets the
    # axis rather than being pinned, and 2022-25 still read well below the top sliver.
    assert new[0] == -850 and new[1] == 10, new
    assert (new[1] - (-407)) / (new[1] - new[0]) >= 0.25


@pytest.mark.parametrize("label,op,net", [
    ("a -30% impairment year", [15, 16, 14, 15, -30, 15, 16, 15, 14, 15],
     [10, 11, 9, 10, -35, 10, 11, 10, 9, 10]),
    ("a 60% one-off gain year", [15] * 10, [10, 10, 10, 10, 60, 10, 10, 10, 10, 10]),
    ("a loss-maker below -100% for good", [-150, -140, -160, -155, -145, -150, -158, -149,
                                           -152, -151],
     [-170, -160, -180, -175, -165, -170, -178, -169, -172, -171]),
])
def test_a_real_one_off_year_is_never_pinned(label, op, net):
    values = _five_series([40] * 10, op, net, [12] * 10, [8] * 10)
    new = _pp_domain(values)
    assert new == _old_pp_domain(values), (label, new)
    assert not any(_is_off_scale(v, new) for v in values), label


def test_an_above_scale_outlier_is_pinned_to_the_top_edge():
    """The #Preview "Above-scale pinned" shape: +1,900% / +2,400% on a near-zero-revenue
    year — the case whose up-arrow the ScrollView used to clip (R39)."""
    values = _five_series([62, 64, 66, 65, 67], [18, 20, 1900, 21, 22],
                          [14, 15, 2400, 16, 17], [12, 13, -40, 15, 16], [11.0] * 5)
    new = _pp_domain(values)
    assert sorted(v for v in values if _is_off_scale(v, new)) == [1900.0, 2400.0]
    assert all(v > new[1] for v in (1900.0, 2400.0)), "pinned ABOVE, i.e. at the top edge"


# ── R1 twin: the report's Profitability drill-down ────────────────────────────


@pytest.mark.parametrize("company,sector", [
    ([80.6, 79.1, 72.8, 71.4, 70.5, 65.2], [68.9, 68.1, 69.7, 71.3, 71.4, 74.1]),
    # Company far above its benchmark: two separate clusters.
    ([75.0, 76.0, 77.0, 75.0, 76.0, 77.0], [10.0, 11.0, 12.0, 10.0, 11.0, 12.0]),
    # A loss-maker whose whole net-margin history sits below -100%.
    ([-150.0, -140.0, -160.0, -155.0, -145.0], [5.0, 6.0, 5.5, 6.0, 5.0]),
    # AAPL-shaped ROE (near-zero equity makes it large) vs a ~15% benchmark.
    ([73.0, 82.0, 88.0, 147.0, 175.0, 157.0, 164.0], [14.0, 15.0, 16.0, 15.0, 14.0, 15.0, 16.0]),
])
def test_drill_down_ordinary_data_keeps_the_old_axis(company, sector):
    values = company + sector
    new = _prof_domain(values)
    assert new == pytest.approx(_old_prof_domain(values))
    assert not any(_is_off_scale(v, new) for v in values)


def test_drill_down_still_pins_a_near_zero_equity_roe():
    company = [18.0, 21.0, 5000.0, 24.0, 19.0, 22.0]
    sector = [14.0, 15.0, 15.5, 16.0, 15.0, 14.5]
    d = _prof_domain(company + sector)
    assert d[1] < 200, d
    assert [v for v in company + sector if _is_off_scale(v, d)] == [5000.0]


# ── source scans: the port's structure IS the Swift's ─────────────────────────


def test_profit_power_axis_is_the_old_rounded_min_max_over_axis_values():
    view = _pp_view()
    domain = _decl_body(view, "private var marginDomain")
    assert "ChartDomain.robust(" not in domain, (
        "the pooled fence must not BE the domain — it pins ordinary gross margins"
    )
    assert "axisValues" in domain
    assert "ceil($0 / 10) * 10" in domain and "floor($0 / 10) * 10" in domain
    assert "ChartDomain.make(" in domain
    axis = _decl_body(view, "private var axisValues")
    assert "ChartDomain.robust(" in axis
    assert "abs($0) <= Self.extremeMarginPct || fence.contains($0)" in axis, (
        "a value leaves the axis only when it is extreme in size AND outside the fence"
    )
    assert _swift_constant(view, "extremeMarginPct") == 100


def test_profitability_axis_is_the_old_min_max_over_axis_values():
    view = _prof_view()
    domain = _decl_body(view, "private var yDomain")
    assert "ChartDomain.robust(" not in domain
    assert "axisValues" in domain and "allValues" not in domain
    assert "*= 1.12" in domain, "the old ×1.12 headroom"
    axis = _decl_body(view, "private var axisValues")
    assert "ChartDomain.robust(" in axis
    assert "abs($0) <= Self.extremeValuePct || fence.contains($0)" in axis
    assert _swift_constant(view, "extremeValuePct") == 100


# ── R39: pinned arrows are drawn whole ────────────────────────────────────────


@pytest.mark.parametrize("view_fn", [_pp_view, _prof_view], ids=["profit_power", "drill_down"])
def test_pinned_arrow_is_shifted_inside_the_plot(view_fn):
    view = view_fn()
    marks = _decl_body(view, "private var offScaleMarks")
    symbol = _decl_body(marks, ".symbol")
    assert "arrowtriangle.up.fill" in symbol
    assert ".offset(y: marker.isAbove ? offScaleGlyphInset : -offScaleGlyphInset)" in symbol, (
        "an arrow centred on the top edge is clipped by the horizontal ScrollView"
    )
    m = re.search(r"private let offScaleGlyphInset: CGFloat = ([0-9.]+)", view)
    assert m and 4 <= float(m.group(1)) <= 9, "about half the 9pt glyph"
    assert ".scrollClipDisabled(" not in view, (
        "a scrolled chart would draw over the fixed y-axis column"
    )


def test_profit_power_label_clears_the_shifted_arrow():
    spacing = _decl_body(_pp_view(), "private func offScaleLabelSpacing(")
    assert "offScaleGlyphInset" in spacing


# ── R35: the drill-down's "Current" is the latest period ──────────────────────


def test_drill_down_current_and_verdict_anchor_on_the_latest_period():
    sheet = _sheet()
    latest = _decl_body(sheet, "private var latestPoint")
    assert re.fullmatch(r"\{\s*current\.last\s*\}", latest), (
        "a backward scan presents an older year as 'Current' after a revenue gap"
    )
    pair = _decl_body(sheet, "private var sectorPair")
    assert "last(where:" not in pair and "latestPoint" in pair, (
        "the verdict-coloured vs-industry line must use the latest period only"
    )
    assert "sectorPair" in _decl_body(sheet, "private var isCurrentlyGood")
    current = _decl_body(sheet, "private var currentText")
    assert "latestPoint" in current and "last(where:" not in current
    # The last reported value is shown only under its OWN period.
    header = _decl_body(sheet, "private var header")
    assert re.search(r'Text\("Last reported: \\\(pct\(v\)\) \(\\\(lr\.period\)\)"\)', header)
    assert "lastReported" in _decl_body(sheet, "private var notReportedText")
    legend = _decl_body(sheet, "private var legendAndDelta")
    assert "notReportedText" in legend


def _sheet_port(points):
    """Port of the sheet's header / legend decisions. ``points`` = [(period, company,
    sector)]. Returns (current_text, last_reported, pair)."""
    latest = points[-1] if points else None
    last_reported = next((p for p in reversed(points) if p[1] is not None), None)
    if latest is None:
        current = "—"
    elif latest[1] is None:
        current = f"— ({latest[0]})"
    else:
        current = f"{latest[1]:.2f}%"
    pair = (latest[1], latest[2]) if latest and latest[1] is not None and latest[2] is not None else None
    return current, last_reported, pair


def test_drill_down_port_trailing_revenue_gap():
    biotech = [("2022", 15.0, 11.0), ("2023", 20.0, 12.0), ("2024", None, 12.0),
               ("2025", None, 12.5)]
    current, last_reported, pair = _sheet_port(biotech)
    assert current == "— (2025)"
    assert last_reported == ("2023", 20.0, 12.0)
    assert pair is None, "no 1.67× vs-industry verdict from 2023 under a 2025 chart"
