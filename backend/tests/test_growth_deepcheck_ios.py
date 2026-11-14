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


# ── 2026-11-13: "Name the periods" (owner) — the Growth drill-down's header and legend ──
#
# Every figure on the Growth sheet is a CLOSED year or quarter, so nothing there says
# "Current": the header is the newest bar named by its period ("FY2026: 34.33%"), line 1 is
# the newest period with BOTH a company and a same-period peer YoY ("Q4 '26 21.85% · Industry
# 27.50% · 0.79× vs industry"), and line 2 is the card row's own figure named by its fiscal
# year ("FY2026 34.33% · Industry not out yet"), coloured like the card. Each guard below is
# a function run on the real source AND on an in-memory mutation of it (testing.md §3.3:
# the mutation must make it fail), so none can pass vacuously.


def _sheet() -> str:
    return _code(_SHEET)


def _must_fail(check, code: str, old: str, new: str) -> None:
    """``check`` passes on ``code`` but fails once ``old`` is replaced by ``new``."""
    assert old in code, f"mutation is stale — {old!r} not in the source"
    check(code)
    with pytest.raises(AssertionError):
        check(code.replace(old, new, 1))


def _check_no_current(code: str) -> None:
    # Word-bounded: a literal "Current…" (or a `Current` type) trips it; the `currentValue`
    # / `currentPeriod` fields (lower-case) do not.
    hit = re.search(r"\bCurrent", code)
    assert hit is None, f"the Growth sheet still says Current: …{code[hit.start():hit.start() + 40]!r}"


def test_the_growth_sheet_never_says_current():
    code = _sheet()
    _must_fail(_check_no_current, code, '"Last meaningful growth: ', '"Current growth: ')
    _must_fail(_check_no_current, code, '"\\(name): \\(pct(y))"', '"Current: \\(pct(y))"')


def _check_header(code: str) -> None:
    header = _body(code, "private var header: some View")
    assert "Text(headerText)" in header
    assert "periodLabel(lm.period)" in header, "the last-meaningful line must name its period"
    text = _body(code, "private var headerText: String")
    assert "guard let p = latestPoint" in text, "the header is the NEWEST bar"
    assert "periodLabel(p.period)" in text
    assert '"\\(name): \\(pct(y))"' in text and '"\\(name): \\(p.formattedValue) · YoY n/m"' in text
    label = _body(code, "private func periodLabel(_ label: String) -> String")
    assert "selectedPeriod == .annual" in label and '"FY\\(label)"' in label
    assert '!label.hasPrefix("FY")' in label, "never 'FYFY2026'"


def test_the_header_names_the_newest_bar_by_its_period():
    code = _sheet()
    _must_fail(_check_header, code, "let name: String = periodLabel(p.period)",
               "let name: String = p.period")
    _must_fail(_check_header, code, "guard let p = latestPoint else", "guard let p = lastMeaningful else")
    _must_fail(_check_header, code, '"FY\\(label)"', '"\\(label)"')


def _check_line_one(code: str) -> None:
    pair = _body(code, "private var sectorPair: (company: Double, sector: Double, period: String)?")
    assert "for p in series.reversed()" in pair, "line 1 must WALK BACK over the selected series"
    assert "let c = p.yoyChangePercent, let s = p.sectorAverageYoY" in pair, \
        "both figures from ONE point = one join period"
    assert "c.isFinite, s.isFinite" in pair and "continue" in pair
    assert "return (c, s, p.period)" in pair, "the pair must carry its own period"
    assert "latestPoint" not in pair and "series.last" not in pair, \
        "the newest bar alone: a not-out-yet peer figure hides every earlier pair"
    legend = _body(code, "private var legendAndDelta: some View")
    # Line 1 draws `periodPair`: the same walk-back, dropped only when it would repeat line 2's
    # year (review round 2026-11-13: both lines named "FY2025" on every December filer).
    assert "if let pair = periodPair" in legend
    assert "} else if sectorPair == nil, let text = companyOnlyText {" in legend, \
        "'Company only' only when NO pair exists, never in place of a deduplicated line 1"
    dedupe = _body(code, "private var periodPair: (company: Double, sector: Double, period: String)?")
    assert "guard let pair = sectorPair else { return nil }" in dedupe
    assert re.search(r"if cardLineText != nil, let cardPeriod = cardRow\?\.currentPeriod,\s*"
                     r"periodLabel\(pair\.period\) == cardPeriod \{\s*return nil\s*\}", dedupe), dedupe
    assert re.search(r"deltaText\(name:\s*periodLabel\(pair\.period\),\s*company:\s*pair\.company,"
                     r"\s*sector:\s*pair\.sector,\s*peer:\s*peerWord\)", legend)
    assert "color: pairColor(company: pair.company, sector: pair.sector)" in legend
    assert "legendLine(text, color: AppColors.textMuted)" in legend, "no pair → muted line"
    color = _body(code, "private func pairColor(company: Double, sector: Double) -> Color")
    gate = color.find("guard card.starRating > 0 else { return AppColors.textPrimary }")
    assert gate != -1, "an unrated card paints no verdict"
    assert gate < color.find("AppColors.gain") and gate < color.find("AppColors.loss")
    assert re.search(r"if d > 0 \{ return AppColors\.gain \}\s*if d < 0 \{ return AppColors\.loss \}", color), \
        "higher growth than the peer is the good side"
    only = _body(code, "private var companyOnlyText: String?")
    for phrase in ('" · Company only"', '" n/m · last meaningful ', '" · growth % not meaningful"'):
        assert phrase.strip('"') in only, phrase
    assert only.count("periodLabel(") >= 2, "every fallback is named by its period"


def test_line_one_walks_back_to_the_newest_same_period_pair():
    code = _sheet()
    _must_fail(_check_line_one, code, "for p in series.reversed()", "for p in [series.last].compactMap { $0 }")
    _must_fail(_check_line_one, code, "guard card.starRating > 0 else { return AppColors.textPrimary }\n        let d",
               "let d")
    _must_fail(_check_line_one, code, "if d > 0 { return AppColors.gain }", "if d > 0 { return AppColors.loss }")
    _must_fail(_check_line_one, code, "if let pair = periodPair", "if let pair = sectorPair")
    _must_fail(_check_line_one, code, "periodLabel(pair.period) == cardPeriod", "false")


def _check_line_two(code: str) -> None:
    text = _body(code, "private var cardLineText: String?")
    assert re.search(r"guard let row = cardRow, let value = row\.currentValue, value\.isFinite,\s*"
                     r"let period = row\.currentPeriod, !period\.isEmpty else \{ return nil \}", text), \
        "line 2 needs the row's value AND the period it covers"
    assert "deltaText(name: period, company: value, sector: median, peer: peer)" in text
    assert '"\\(period) \\(pct(value)) · \\(peer) not out yet"' in text
    assert "row.currentPeerMedian" in text
    # "Not out yet" only for a company with a peer line; a peer-free company (a listed
    # non-lender member, a small Financial Services industry) has no figure coming.
    assert 'guard annualSeriesHasPeer else { return "\\(period) \\(pct(value))" }' in text
    has_peer = _body(code, "private var annualSeriesHasPeer: Bool")
    assert "growthData.dataPoints(for: selectedMetric, period: .annual)" in has_peer
    assert ".contains { $0.sectorAverageYoY != nil }" in has_peer
    row = _body(code, "private var cardRow: DeepDiveMetric?")
    assert "Self.cardHistoryKey(for: selectedMetric)" in row and "$0.historyKey == key" in row
    word = _body(code, "private func cardPeerWord(_ row: DeepDiveMetric) -> String")
    assert "row.peerLevel" in word and "period: .annual" in word and "card.peerGroupLevel" in word
    color = _body(code, "private func cardLineColor(_ row: DeepDiveMetric) -> Color")
    assert re.search(r"guard card\.starRating > 0, let score = row\.currentScore,\s*"
                     r"\(1\.\.\.5\)\.contains\(score\) else \{ return AppColors\.textPrimary \}", color)
    assert "if score >= 4 { return AppColors.gain }" in color
    assert "if score <= 2 { return AppColors.loss }" in color
    # Identical on Annual and Quarterly: nothing line 2 reads looks at the selected tab.
    for body in (text, row, word, color):
        assert "selectedPeriod" not in body and "series" not in body
    legend = _body(code, "private var legendAndDelta: some View")
    assert "if let row = cardRow, let text = cardLineText" in legend
    assert "legendLine(text, color: cardLineColor(row))" in legend


def test_line_two_is_the_card_rows_own_figure_named_by_its_year():
    code = _sheet()
    _must_fail(_check_line_two, code, ", !period.isEmpty else { return nil }", " else { return nil }")
    _must_fail(_check_line_two, code, "guard card.starRating > 0, let score", "guard let score")
    _must_fail(_check_line_two, code, "growthData.peerGroupLevel(for: selectedMetric, period: .annual)",
               "growthData.peerGroupLevel(for: selectedMetric, period: selectedPeriod)")
    _must_fail(_check_line_two, code, "guard annualSeriesHasPeer else", "guard true else")
    _must_fail(_check_line_two, code, " not out yet\"", " n/a\"")


def _swift_card_keys(code: str) -> Dict[str, object]:
    body = _body(code, "static func cardHistoryKey(for metric: GrowthMetricType) -> String?")
    out: Dict[str, object] = {}
    for case, value in re.findall(r"case \.(\w+): return (nil|\"\w+\")", body):
        out[case] = None if value == "nil" else value.strip('"')
    return out


def test_line_two_matches_the_card_row_by_the_backends_history_key():
    keys = _swift_card_keys(_sheet())
    assert keys == {"eps": "eps_growth", "revenue": "revenue_growth",
                    "operatingProfit": "operating_income_growth", "freeCashFlow": "fcf_growth",
                    "netIncome": None}, keys
    from app.services.agents.ticker_report_data_collector import (
        _HISTORY_KEY_BY_LABEL_PREFIX, _HISTORY_UNITS,
    )
    backend = {key for _, key in _HISTORY_KEY_BY_LABEL_PREFIX}
    for key in filter(None, keys.values()):
        assert key in backend and _HISTORY_UNITS[key] == "percent", key


def _check_one_row(code: str) -> None:
    line = _body(code, "private func legendLine(_ text: String, color: Color) -> some View")
    assert re.search(r"\.foregroundColor\(color\)\s*\.multilineTextAlignment\(\.center\)\s*"
                     r"\.lineLimit\(1\)\s*\.minimumScaleFactor\(Self\.legendLineMinScale\)\s*"
                     r"\.allowsTightening\(true\)", line), "each comparison stays on ONE row"
    scale = re.search(r"static let legendLineMinScale: CGFloat = ([\d.]+)", code)
    assert scale and float(scale.group(1)) == 0.85
    legend = _body(code, "private var legendAndDelta: some View")
    # Every comparison goes through legendLine; the only bare Texts are the three swatch names.
    assert legend.count("legendLine(") == 3
    assert len(re.findall(r"\bText\(", legend)) == 3
    tail = code[code.index("private var legendAndDelta: some View"):]
    assert ".padding(.horizontal, AppSpacing.sm)" in tail.split("#Preview")[0]
    assert ".padding(.horizontal, AppSpacing.md)\n        .frame(maxWidth" not in tail


def test_both_legend_lines_stay_on_one_row():
    code = _sheet()
    _must_fail(_check_one_row, code, "            .lineLimit(1)\n", "")
    _must_fail(_check_one_row, code, "legendLineMinScale: CGFloat = 0.85", "legendLineMinScale: CGFloat = 0.5")
    _must_fail(_check_one_row, code, "legendLine(text, color: AppColors.textMuted)",
               "Text(text).foregroundColor(AppColors.textMuted)")


def _check_pct(code: str) -> None:
    body = _body(code, "private func pct(_ v: Double) -> String")
    assert 'guard v.isFinite else { return "—" }' in body
    assert '"%.2f%%"' in body and '"%.0f%%"' not in body, "the exact figure, to 2 decimals"
    assert "(v * 100).rounded() / 100" in body and "cents == 0 ? 0 : cents" in body


def test_the_sheet_prints_growth_to_two_decimals():
    code = _sheet()
    _must_fail(_check_pct, code, '"%.2f%%"', '"%.0f%%"')
    _must_fail(_check_pct, code, "cents == 0 ? 0 : cents", "cents")


def _check_delta_rule(code: str) -> None:
    body = _body(code, "private func deltaText(name: String, company: Double, sector: Double, peer: String) -> String")
    assert "if company > 0 && sector >= 2.0" in body
    assert 'String(format: "%.2f×", company / sector)' in body and 'ratio != "0.00×"' in body
    assert 'String(format: "%+.1f", company - sector)' in body
    assert '"\\(head) · \\(spread) pts vs \\(peerLower)"' in body


def test_the_ratio_rule_the_port_below_mirrors():
    code = _sheet()
    _must_fail(_check_delta_rule, code, "sector >= 2.0", "sector > 0")
    _must_fail(_check_delta_rule, code, 'if ratio != "0.00×"', "if true")


def test_the_preview_draws_both_lines_for_an_orcl_shaped_row():
    preview = _sheet().split("#Preview", 1)[1]
    assert re.search(r'historyKey: "eps_growth".*?currentValue: 34\.33,\s*currentScore: 5,\s*'
                     r'currentPeriod: "FY2026"', preview, re.S)
    assert "currentPeerMedian" not in preview, "the preview shows the 'not out yet' wording"
    assert 'sectorAverageYoY: nil' in preview, "the newest bar's peer figure is not out yet"


# Python ports of the sheet's text and colour rules, so the outliers can be asserted.


def _pct(v: float) -> str:
    if not math.isfinite(v):
        return "—"
    cents = _swift_round(v * 100) / 100
    shown = (0.0 if cents == 0 else cents) if math.isfinite(cents) else v
    return f"{shown:.2f}%"


def _delta(name: str, company: float, sector: float, peer: str) -> str:
    head = f"{name} {_pct(company)} · {peer} {_pct(sector)}"
    if company > 0 and sector >= 2.0:
        ratio = f"{company / sector:.2f}×"
        if ratio != "0.00×":
            return f"{head} · {ratio} vs {peer.lower()}"
    return f"{head} · {company - sector:+.1f} pts vs {peer.lower()}"


def _pair(series):
    for period, c, s in reversed(series):
        if c is None or s is None or not (math.isfinite(c) and math.isfinite(s)):
            continue
        return c, s, period
    return None


def _pair_color(star: int, c: float, s: float) -> str:
    if star <= 0:
        return "textPrimary"
    d = c - s
    return "gain" if d > 0 else "loss" if d < 0 else "textPrimary"


def _card_line(value, period, median, peer="Industry"):
    if value is None or not math.isfinite(value) or not period:
        return None
    if median is not None and math.isfinite(median):
        return _delta(period, value, median, peer)
    return f"{period} {_pct(value)} · {peer} not out yet"


def _card_color(star: int, score) -> str:
    if star <= 0 or score is None or not 1 <= score <= 5:
        return "textPrimary"
    return "gain" if score >= 4 else "loss" if score <= 2 else "textPrimary"


@pytest.mark.parametrize("value,expected", [
    (34.33, "34.33%"), (-5911.7, "-5911.70%"), (100.0, "100.00%"), (0.004, "0.00%"),
    (-0.004, "0.00%"), (-0.0, "0.00%"), (0.005, "0.01%"), (1e9, "1000000000.00%"),
    (float("nan"), "—"), (float("inf"), "—"), (float("-inf"), "—"),
])
def test_pct_port(value, expected):
    assert _pct(value) == expected


@pytest.mark.parametrize("company,sector,expected", [
    # The owner's line-1 example.
    (21.85, 27.50, "Q4 '26 21.85% · Industry 27.50% · 0.79× vs industry"),
    # A tiny peer base would explode the multiple: the spread instead.
    (10.0, 0.5, "Q4 '26 10.00% · Industry 0.50% · +9.5 pts vs industry"),
    # Shrinking company: no multiple of a negative.
    (-5.0, 10.0, "Q4 '26 -5.00% · Industry 10.00% · -15.0 pts vs industry"),
    # A multiple that rounds to 0.00× says nothing: the spread.
    (0.004, 5.0, "Q4 '26 0.00% · Industry 5.00% · -5.0 pts vs industry"),
    # The ORCL FCF outlier is printed exactly.
    (-5911.7, 12.0, "Q4 '26 -5911.70% · Industry 12.00% · -5923.7 pts vs industry"),
    (0.0, 0.0, "Q4 '26 0.00% · Industry 0.00% · +0.0 pts vs industry"),
])
def test_delta_text_port(company, sector, expected):
    assert _delta("Q4 '26", company, sector, "Industry") == expected


def test_line_one_walk_back_port():
    nan = float("nan")
    # The newest bar's peer figure is not out yet → the newest COMPLETE pair, by its period.
    assert _pair([("2024", 10.0, 8.0), ("2025", 12.0, None), ("2026", None, 9.0)]) == (10.0, 8.0, "2024")
    assert _pair([("Q3 '26", 30.11, 24.6), ("Q4 '26", 21.85, 27.5), ("Q1 '27", 54.46, None)]) == \
        (21.85, 27.5, "Q4 '26")
    # A non-finite figure is skipped, never compared.
    assert _pair([("2024", 5.0, 4.0), ("2025", 6.0, nan)]) == (5.0, 4.0, "2024")
    assert _pair([]) is None
    assert _pair([("2025", None, 3.0), ("2026", 4.0, None)]) is None


@pytest.mark.parametrize("star,c,s,expected", [
    (4, 21.85, 27.5, "loss"), (4, 34.0, 20.0, "gain"), (4, 5.0, 5.0, "textPrimary"),
    (0, 34.0, 20.0, "textPrimary"), (0, 1.0, 50.0, "textPrimary"), (1, -5911.7, 12.0, "loss"),
])
def test_line_one_colour_port(star, c, s, expected):
    assert _pair_color(star, c, s) == expected


@pytest.mark.parametrize("value,period,median,expected", [
    (34.33, "FY2026", None, "FY2026 34.33% · Industry not out yet"),
    (34.33, "FY2026", 25.0, "FY2026 34.33% · Industry 25.00% · 1.37× vs industry"),
    (-5911.7, "FY2026", None, "FY2026 -5911.70% · Industry not out yet"),
    (34.33, "FY2026", float("nan"), "FY2026 34.33% · Industry not out yet"),
    (34.33, None, 25.0, None),            # no period → no line (never "Current")
    (34.33, "", 25.0, None),
    (None, "FY2026", 25.0, None),         # "N/M" row → no line
    (float("nan"), "FY2026", 25.0, None),
])
def test_line_two_port(value, period, median, expected):
    assert _card_line(value, period, median) == expected


@pytest.mark.parametrize("star,score,expected", [
    (4, 5, "gain"), (4, 4, "gain"), (4, 3, "textPrimary"), (4, 2, "loss"), (4, 1, "loss"),
    (4, None, "textPrimary"), (0, 5, "textPrimary"), (0, 1, "textPrimary"),
    (4, 0, "textPrimary"), (4, 6, "textPrimary"), (4, -1, "textPrimary"),
])
def test_line_two_colour_port(star, score, expected):
    assert _card_color(star, score) == expected
