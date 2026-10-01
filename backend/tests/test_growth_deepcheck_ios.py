"""Growth deep-check (2026-09-30): the iOS half of findings #32, #63, #71, #72, #73.

There is no XCTest target, so these are SOURCE-SCAN guards over the Swift tree plus
Python ports of the chart geometry. Per testing.md §3 every scan strips comments first
and is brace-bound to the declaration it means, and each was mutation-tested once by
hand (break the Swift → the test fails → restore).

* #32 — GrowthChartView prints per-share values (EPS) to the cent: CompactNumberFormat
        rounded 0.04 / -0.03 to "0" / "-0" and 10.60 to "11".
* #63 — the Growth EPS chip/header reads "EPS (GAAP)" (the Earnings card is adjusted).
* #71 — ONE tick list feeds the gridlines and the y-axis labels (a loss-maker's labels
        used to sit between gridlines with the zero baseline unlabelled).
* #72 — empty series → a "No data" state, not a fabricated 0–1.2 axis; the tab card
        opens on data and offers only chips / periods that have data.
* #73 — the legend names the peer group from `peer_group_levels`, per series.
"""

from __future__ import annotations

import math
import pathlib
import re
from typing import Dict, List, Tuple

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_CHART = _IOS / "Views" / "Molecules" / "GrowthChartView.swift"
_CARD = _IOS / "Views" / "Organisms" / "GrowthSectionCard.swift"
_SHEET = _IOS / "Views" / "Molecules" / "GrowthChartSheet.swift"
_LEGEND = _IOS / "Views" / "Molecules" / "GrowthLegendView.swift"
_MODELS = _IOS / "Models" / "GrowthModels.swift"
_CHIP = _IOS / "Views" / "Atoms" / "GrowthMetricChip.swift"


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
    assert i != -1, f"guard is stale — {signature!r} not found"
    start = code.find("{", i + len(signature) - 1 if signature.endswith("{") else i)
    assert start != -1, f"no body for {signature!r}"
    depth = 0
    for j in range(start, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[start + 1:j]
    raise AssertionError(f"unbalanced braces after {signature!r}")


# ── #32: per-share values to the cent ────────────────────────────────────────


def test_chart_formatter_prints_sub_thousand_values_to_the_cent():
    body = _body(_code(_CHART), "private func formatLargeNumber(_ number: Double) -> String")
    assert "isFinite" in body, "NaN must still render '—', not 'nan'"
    cents = body.find('"%.2f"')
    compact = body.find("CompactNumberFormat.string")
    assert cents != -1, "no two-decimal branch: 0.04 EPS prints '0' again"
    assert compact != -1, "large magnitudes must keep the shared compact formatter"
    assert cents < compact, "the cents branch must run BEFORE the compact formatter"
    assert re.search(r"abs\(number\)\s*<\s*1_000", body), "the branch must be magnitude-gated"
    assert ".rounded()" in body, "round to cents first, or -0.004 prints '-0.00'"


def _compact_scaled(v: float) -> str:
    """Port of CompactNumberFormat.scaled — the reason the branch above exists."""
    if v >= 10:
        return f"{v:.0f}"
    one = round(v * 10) / 10
    if one == round(one):
        return f"{one:.0f}"
    return f"{one:.1f}"


def _compact(value: float) -> str:
    if not math.isfinite(value):
        return "—"
    sign = "-" if value < 0 else ""
    m = abs(value)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if m >= limit:
            return sign + _compact_scaled(m / limit) + suffix
    return sign + _compact_scaled(m)


def _swift_round(x: float) -> float:
    """Swift `.rounded()` = schoolbook rounding (half away from zero)."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _growth_format(number: float) -> str:
    """Port of GrowthChartView.formatLargeNumber after the fix."""
    if not math.isfinite(number):
        return "—"
    if number == 0:
        return "0"
    if abs(number) < 1_000:
        cents = _swift_round(number * 100) / 100
        return f"{0.0 if cents == 0 else cents:.2f}"
    return _compact(number)


def test_why_the_shared_compact_formatter_cannot_label_eps():
    # The defect, pinned: near-break-even EPS collapses and the sign survives alone.
    assert _compact(0.04) == "0" and _compact(-0.03) == "-0"
    assert _compact(10.60) == "11"


@pytest.mark.parametrize("value,expected", [
    (0.04, "0.04"), (-0.03, "-0.03"), (0.02, "0.02"), (10.60, "10.60"), (10.40, "10.40"),
    (-0.004, "0.00"), (0.0, "0"), (-0.0, "0"), (999.994, "999.99"),
    (1_500.0, "1.5K"), (474e9, "474B"), (float("nan"), "—"), (float("inf"), "—"),
])
def test_growth_formatter_port(value, expected):
    assert _growth_format(value) == expected


# ── #71: one tick list for gridlines and labels ──────────────────────────────


def _swift_constant(code: str, name: str) -> float:
    m = re.search(rf"private let {name}: CGFloat = ([0-9.]+)", code)
    assert m, f"guard is stale — {name} not found"
    return float(m.group(1))


def test_gridlines_and_axis_labels_read_the_same_tick_list():
    code = _code(_CHART)
    grid = _body(code, "private var gridValues: [Double]")
    assert grid.strip() == "yTicks", f"gridValues must BE yTicks, got {grid.strip()!r}"
    labels = _body(code, "private var barYAxisLabels: some View")
    assert "yTicks" in labels, "the axis labels must iterate the shared tick list"
    assert "Spacer()" not in labels, "labels spread by Spacers cannot sit on gridlines"
    assert "tickLabelCenterY" in labels, "each label must be placed at its tick's y"
    ticks = _body(code, "private var yTicks: [Double]")
    assert "minTickSpacing" in ticks, "asymmetric domains need the spacing filter"
    # The filter itself, not just the constant's name: each candidate must clear EVERY
    # kept tick by the minimum gap before it is appended (what `_ticks` below ports).
    assert re.search(r"allSatisfy\s*\{[^}]*>=\s*minGap\s*\}", ticks), \
        "the spacing comparison is gone — hi=0.5B over lo=-27B stacks labels again"
    assert re.search(r"if\s+clearsAll\s*\{\s*ticks\.append\(candidate\)\s*\}", ticks)


def _domain(values: List[float]) -> Tuple[float, float]:
    """Port of GrowthChartView.yDomain."""
    lo = min(min(values) if values else 0.0, 0.0)
    hi = max(max(values) if values else 1.0, 0.0)
    if hi > 0:
        hi *= 1.15
    if lo < 0:
        lo *= 1.15
    if lo == hi:
        hi = lo + 1
    return lo, hi


def _snap(value: float, toward_zero: bool) -> float:
    """Port of GrowthChartView.snapToLabel (round-2 R34): the precision the label
    prints — cents below 1,000, else CompactNumberFormat's unit (one decimal of K/M/B/T
    below a scaled 10, whole units from 10). `.towardZero` = trunc, the interior rule
    `.toNearestOrAwayFromZero` = `_swift_round`."""
    if not math.isfinite(value):
        return value
    rnd = math.trunc if toward_zero else _swift_round
    m = abs(value)
    if m < 1_000:
        return rnd(value * 100) / 100
    unit = 1e12 if m >= 1e12 else 1e9 if m >= 1e9 else 1e6 if m >= 1e6 else 1e3
    step = unit if m / unit >= 10 else unit / 10
    return rnd(value / step) * step


def _ticks(lo: float, hi: float, plot_h: float, min_gap: float) -> List[float]:
    """Port of GrowthChartView.yTicks (each candidate snapped to its label first)."""
    span = hi - lo
    if not (span > 0 and math.isfinite(span)):
        return [0.0]
    ticks = [0.0]
    cands: List[float] = []
    if hi > 0:
        cands.append(min(_snap(hi, True), hi))
    if lo < 0:
        cands.append(max(_snap(lo, True), lo))
    if hi > 0:
        cands += [_snap(2 * hi / 3, False), _snap(hi / 3, False)]
    if lo < 0:
        cands += [_snap(lo / 3, False), _snap(2 * lo / 3, False)]
    for c in cands:
        if not (math.isfinite(c) and lo <= c <= hi):
            continue
        if all(abs(k - c) / span * plot_h >= min_gap for k in ticks):
            ticks.append(c)
    return sorted(ticks, reverse=True)


def _y(v: float, lo: float, hi: float, plot_h: float) -> float:
    return (hi - v) / (hi - lo) * plot_h


def _label_center(v: float, lo: float, hi: float, plot_h: float, text_h: float) -> float:
    """Port of tickLabelCenterY."""
    raw = _y(v, lo, hi, plot_h)
    return min(max(raw, text_h / 2), plot_h - text_h / 2)


@pytest.fixture(scope="module")
def geometry() -> Dict[str, float]:
    code = _code(_CHART)
    return {"plot_h": _swift_constant(code, "chartHeight"),
            "min_gap": _swift_constant(code, "minTickSpacing"), "text_h": 14.0}


def test_loss_maker_labels_sit_on_gridlines_and_zero_is_labelled(geometry):
    lo, hi = _domain([-23.7e9, 5e9, 10e9])
    ticks = _ticks(lo, hi, geometry["plot_h"], geometry["min_gap"])
    assert 0.0 in ticks, "the zero baseline must be a labelled tick"
    grid_ys = {round(_y(t, lo, hi, geometry["plot_h"]), 6) for t in ticks}
    for t in ticks:
        raw = _y(t, lo, hi, geometry["plot_h"])
        center = _label_center(t, lo, hi, geometry["plot_h"], geometry["text_h"])
        # Interior labels are exactly on their gridline; only the two edge labels are
        # nudged inside the column by at most half a text height.
        assert abs(center - raw) <= geometry["text_h"] / 2 + 1e-9
        if geometry["text_h"] / 2 <= raw <= geometry["plot_h"] - geometry["text_h"] / 2:
            assert round(center, 6) in grid_ys
    # The old scheme's "-1.4B" label (a third of the WHOLE span) is gone.
    assert not any(abs(t - (hi - (hi - lo) / 3)) < 1e6 for t in ticks)


def test_asymmetric_domain_never_stacks_labels(geometry):
    lo, hi = _domain([0.5e9, -27e9])
    ticks = _ticks(lo, hi, geometry["plot_h"], geometry["min_gap"])
    centers = sorted(_label_center(t, lo, hi, geometry["plot_h"], geometry["text_h"]) for t in ticks)
    gaps = [b - a for a, b in zip(centers, centers[1:])]
    assert min(gaps) >= geometry["text_h"], f"labels overprint: {gaps}"
    # The bottom end is kept, snapped toward zero to the value its label prints (R34).
    assert 0.0 in ticks and min(ticks) == max(_snap(lo, True), lo)


def test_all_positive_series_keeps_the_four_familiar_labels(geometry):
    lo, hi = _domain([1.0, 2.0, 3.0])
    ticks = _ticks(lo, hi, geometry["plot_h"], geometry["min_gap"])
    # Top, two thirds, one third, zero — each snapped to the cent its label prints
    # (round 2, R34), so within half a cent of the exact third and never above `hi`.
    assert len(ticks) == 4 and ticks[-1] == 0.0
    for t, exact in zip(ticks, [hi, 2 * hi / 3, hi / 3]):
        assert abs(t - exact) <= 0.01 and t <= hi


@pytest.mark.parametrize("values", [[], [0.0, 0.0], [-5.0], [1e15, -1e15], [1e-9]])
def test_tick_list_is_never_empty_or_non_finite(values, geometry):
    lo, hi = _domain(values)
    ticks = _ticks(lo, hi, geometry["plot_h"], geometry["min_gap"])
    assert ticks and all(math.isfinite(t) for t in ticks) and 0.0 in ticks


# ── #72: empty state, data-derived selection, chip filter ────────────────────


def test_chart_renders_an_empty_state_instead_of_a_fabricated_axis():
    code = _code(_CHART)
    body = _body(code, "var body: some View")
    assert "dataPoints.isEmpty" in body and "emptyState" in body
    empty = _body(code, "private var emptyState: some View")
    assert "No data" in empty
    assert "componentHeight" in empty, "same height as the chart, or the card jumps"


def test_tab_card_opens_on_data_and_offers_only_metrics_with_data():
    code = _code(_CARD)
    init = _body(code, "init(growthData: GrowthSectionData, onDetailTapped: @escaping () -> Void)")
    assert "initialSelection()" in init, "the card must open on a metric that has data"
    assert "initialValue: .eps" not in init and "initialValue: .annual" not in init
    chips = _body(code, "private var metricChips: some View")
    assert "ForEach(availableMetrics)" in chips
    assert "allCases" not in chips, "every chip offered again, empty ones included"
    available = _body(code, "private var availableMetrics: [GrowthMetricType]")
    assert "metricsWithData()" in available
    card = _body(code, "private var card: some View")
    assert re.search(r"if\s+availablePeriods\.count\s*>\s*1", card), \
        "the period toggle must only offer periods the metric has"
    assert "GrowthChartView(dataPoints: currentDataPoints)" in card
    current = _body(code, "private var currentDataPoints: [GrowthDataPoint]")
    assert "displayedMetric" in current and "displayedPeriod" in current
    body = _body(code, "var body: some View")
    assert "availableMetrics.isEmpty" in body, "no data at all must hide the card"


def test_sheet_and_card_share_one_availability_helper():
    sheet = _code(_SHEET)
    assert "initialSelection(minimum:" in _body(sheet, "init(card: DeepDiveMetricCard, growthData: GrowthSectionData)")
    assert "metricsWithData(minimum:" in _body(sheet, "private var availableMetrics: [GrowthMetricType]")
    models = _code(_MODELS)
    helper = _body(models, "func metricsWithData(minimum: Int = 1) -> [GrowthMetricType]")
    assert "hasPoints" in helper and ".annual" in helper and ".quarterly" in helper


# ── #73: per-series peer word ────────────────────────────────────────────────


def test_tab_legend_no_longer_hard_codes_sector():
    code = _code(_LEGEND)
    body = _body(code, "var body: some View")
    assert "Sector Average" not in body, "the legend still hard-codes the sector wording"
    assert "\\(peerWord) Average (YoY)" in body
    assert "if showsPeerLine" in body, "no dashed line → no legend entry for one"
    card = _body(_code(_CARD), "private var card: some View")
    assert re.search(r"GrowthLegendView\(\s*peerWord:\s*growthData\.peerWord\(", card)


def test_sheet_prefers_the_per_series_level_over_the_card_level():
    body = _body(_code(_SHEET), "private var peerWord: String")
    assert "growthData.peerWord(" in body and "legacyLevel: card.peerGroupLevel" in body
    assert "card.peerGroupLevel ==" not in body, "the ticker-wide level still decides"


def test_peer_group_levels_is_a_defaulted_var_the_mapper_can_assign():
    struct = _body(_code(_MODELS), "struct GrowthSectionData {")
    assert re.search(r"var peerGroupLevels: \[String: String\] = \[:\]", struct)


def _swift_series_keys() -> set:
    body = _body(_code(_MODELS), "static func seriesKey(for metric: GrowthMetricType, period: GrowthPeriodType) -> String")
    bases = re.findall(r'case \.\w+: base = "(\w+)"', body)
    assert len(bases) == 5, f"guard is stale — expected 5 metric bases, got {bases}"
    assert '"annual"' in body and '"quarterly"' in body
    return {f"{b}_{s}" for b in bases for s in ("annual", "quarterly")}


@pytest.mark.asyncio
async def test_swift_series_keys_match_the_backend_wire_keys(monkeypatch):
    """A drift here silently mislabels every legend (the dict lookup just misses)."""
    from tests.test_growth_deepcheck_backend import _FakeFMP, _service, _yoy_metrics_lookup

    svc = _service(monkeypatch, _FakeFMP(), _yoy_metrics_lookup())
    response, _ = await svc._build_growth("TEST")
    backend_keys = set(response.peer_group_levels)
    assert len(backend_keys) == 10, backend_keys
    assert backend_keys == _swift_series_keys()


# ── #63: EPS (GAAP) label ────────────────────────────────────────────────────


def test_growth_eps_is_labelled_gaap_everywhere_it_is_named():
    models = _code(_MODELS)
    display = _body(models, "var displayName: String")
    assert 'case .eps: return "EPS (GAAP)"' in display
    assert 'case eps = "EPS"' in models, "rawValue is a key — keep it stable"
    chip = _body(_code(_CHIP), "var body: some View")
    assert "metricType.displayName" in chip and "metricType.rawValue" not in chip
    sheet = _code(_SHEET)
    assert "Text(m.displayName)" in _body(sheet, "private var metricPicker: some View")
    assert "Text(selectedMetric.displayName)" in _body(sheet, "private var header: some View")
