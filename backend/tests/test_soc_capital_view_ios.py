"""Signal of Confidence, Capital ($) view — the iOS half of TestFlight 1.0 (11) (CRWV).

The screenshot: a lone full-height "$3M" bar over a "$0–$3M" axis on a ~$50B company, "$0M"
in every other cell, and a share count that rose 36% with no word on it. The backend half
(`test_soc_preferred_dividends_and_share_glitches.py`) removes the mis-tagged preferred
payment; this pins what the chart does with what remains:

* the bar axis never tops out below a MATERIALITY floor — 1% for the trailing-twelve-month
  Yield view; a quarter's worth of that on the median `marketCap` for the Capital view — so an
  immaterial amount draws as the sliver it is;
* when every quarter is reported and nothing was returned, the left axis is blank (baseline
  included) and the empty band carries a note — but never over an UNREPORTED quarter;
* the buyback card's share-count change names its window ("+36.3% since Q4 '24").

No XCTest target: SOURCE-SCAN guards over the Swift (comments stripped, brace-bound,
mutation-tested by hand — testing.md §3) plus a Python port of the floor arithmetic.
"""

from __future__ import annotations

import math
import pathlib
import re
import statistics
from typing import List, Optional

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "SignalOfConfidenceModels.swift"
_CHART = _IOS / "Views" / "Molecules" / "SignalOfConfidenceChartView.swift"
_CARD = _IOS / "Views" / "Molecules" / "DividendInfoCard.swift"
_SECTION = _IOS / "Views" / "Organisms" / "SignalOfConfidenceSectionCard.swift"
_MINI = _IOS / "Views" / "Molecules" / "CapitalAllocationMiniChart.swift"


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
    assert path.exists(), f"guard is stale — {path.name} moved"
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


def _chart_struct() -> str:
    return _body(_code(_CHART), "struct SignalOfConfidenceChartView: View {")


# ── the materiality floor ────────────────────────────────────────────────────


_FLOOR_CALL = re.compile(
    r"SignalOfConfidenceScale\.materialityFloor\(\s*for:\s*dataPoints\s*,\s*viewType:\s*viewType\s*\)"
)


def _scale_enum() -> str:
    return _body(_code(_MODELS), "enum SignalOfConfidenceScale {")


def test_the_shared_floor_rule():
    scale = _scale_enum()
    assert re.search(r"static let materialYieldFloor: Double = 1\.0\b", scale)
    floor = _body(scale, "static func materialityFloor(")
    yield_arm = floor[floor.index("case .yield:"):floor.index("case .capital:")]
    assert "return materialYieldFloor" in yield_arm
    capital_arm = floor[floor.index("case .capital:"):]
    assert "$0.marketCap" in capital_arm and "isFinite" in capital_arm and "$0 > 0" in capital_arm, \
        "a non-finite or zero cap must never set the floor"
    assert "guard !caps.isEmpty else { return 0 }" in capital_arm, \
        "no cap (an older backend) must mean NO floor, not a crash or a made-up one"
    assert re.search(r"materialYieldFloor\s*/\s*100\s*\)\s*/\s*4", capital_arm), \
        "the Capital floor is a QUARTER's worth of the yearly yield floor"


def test_the_financials_chart_applies_the_shared_floor():
    chart = _chart_struct()
    domain = _body(chart, "private var barDomain: ClosedRange<Double>")
    assert re.search(r"return\s+0\.\.\.Swift\.max\(\s*natural\s*,\s*materialityFloor\s*\)", domain), \
        f"the floor is not applied to the axis: {domain!r}"
    floor = _body(chart, "private var materialityFloor: Double")
    assert _FLOOR_CALL.fullmatch(floor.strip()), f"the chart rolls its own floor: {floor!r}"
    assert "materialYieldFloor: Double" not in chart, "a second copy of the floor constant"


def test_the_report_mini_chart_applies_the_same_floor():
    """Review 2026-10-07: the floor went into one chart only, so CRWV's lone $1.47M buyback
    filled 87% of the report's Capital view."""
    mini = _body(_code(_MINI), "struct CapitalAllocationMiniChart: View {")
    top = _body(mini, "private var maxBarValue: Double")
    call = _FLOOR_CALL.search(top)
    assert call, f"the mini chart's axis ignores the materiality floor: {top!r}"
    floor_var = re.search(r"let (\w+) = " + re.escape(call.group(0)), top)
    assert floor_var, top
    ret = re.search(r"return max\(([^\n]+)\)\s*$", top.strip())
    assert ret and floor_var.group(1) in ret.group(1), \
        f"the floor is computed but not part of the axis top: {top!r}"


def _port_mini_top(totals: List[float], caps: List[Optional[float]], view: str) -> float:
    """Port of `CapitalAllocationMiniChart.maxBarValue`."""
    stacked = max(totals) if totals else 0.0
    good = sorted(c for c in caps if c is not None and math.isfinite(c) and c > 0)
    floor = 1.0 if view == "yield" else (statistics.median(good) / 400 if good else 0.0)
    return max(stacked * 1.15, 0.5 if view == "yield" else 1.0, floor)


def test_port_crwv_report_bar_is_a_sliver_too():
    """The live CRWV report window (Q3 '24 – Q2 '26): one $1.47M buyback, caps $19-80B."""
    totals = [1.47, 0, 0, 0, 0, 0, 0, 0]
    caps = [47_677.4, 47_677.4, 19_000, 80_000, 67_000, 35_000, 40_000, 48_000]
    top = _port_mini_top(totals, caps, "capital")
    assert 1.47 / top < 0.02, "the lone bar still fills the report chart"
    assert 1.47 / _port_mini_top(totals, [None] * 8, "capital") > 0.8, \
        "control: a report stored before the cap shipped keeps the old stretch"
    assert _port_mini_top([3.0, 2.0], [None, None], "yield") == pytest.approx(3.45)
    assert _port_mini_top([0.07, 0.0], [None, None], "yield") == pytest.approx(1.0)


def _median(values: List[float]) -> float:
    return statistics.median(values)


def _port_domain_top(totals: List[float], caps: List[Optional[float]], view: str) -> float:
    """Port of `barDomain` (via `ChartDomain.make`, includeZero, 15% headroom) + the floor."""
    finite = [t for t in totals if math.isfinite(t)]
    lo, hi = min(finite + [0.0]), max(finite + [0.0])
    span = hi - lo
    pad = max(span * 0.15, 0.5 if span == 0 else 0)
    if hi > 0 or span == 0:
        hi += pad
    if hi - lo < 1.0:
        mid = (hi + lo) / 2
        hi = max(mid + 0.5, 1.0)
    natural = max(hi, 1.0)
    if view == "yield":
        floor = 1.0
    else:
        good = sorted(c for c in caps if c is not None and math.isfinite(c) and c > 0)
        floor = _median(good) * (1.0 / 100) / 4 if good else 0.0
    return max(natural, floor)


def test_port_crwv_capital_bar_is_a_sliver_not_the_full_chart():
    """The screenshot's numbers: one $2.59M quarter, caps ~$19-80B."""
    totals = [0, 0, 0, 2.59, 0, 0, 0, 0]
    caps = [47_677.4, 47_677.4, 19_000, 80_000, 67_000, 35_000, 40_000, 48_000]
    top = _port_domain_top(totals, caps, "capital")
    assert top == pytest.approx(statistics.median(caps) / 400)       # ≈ $118M, not $3M
    assert 2.59 / top < 0.03, "the $2.6M bar still fills the chart"
    # control: without caps (an older payload) the old "$0–$3M" axis comes back
    assert _port_domain_top(totals, [None] * 8, "capital") == pytest.approx(2.59 * 1.15)


def test_port_a_material_repurchaser_keeps_its_natural_scale():
    """AAPL-shaped: ~$25B a quarter on ~$3.5T — far above a quarter of 1%; nothing moves."""
    totals = [24_000, 26_000, 25_000, 27_000]
    caps = [3_400_000, 3_500_000, 3_600_000, 3_550_000]
    assert _port_domain_top(totals, caps, "capital") == pytest.approx(27_000 * 1.15)


def test_port_yield_floor_is_one_percent():
    assert _port_domain_top([0.0033, 0, 0], [None] * 3, "yield") == pytest.approx(1.0)
    assert _port_domain_top([3.0, 2.5], [None] * 2, "yield") == pytest.approx(3.45)


@pytest.mark.parametrize("caps, expected", [
    ([None, None], 0.0),
    ([float("nan"), 0.0, -5.0], 0.0),
    ([40_000.0], 100.0),
    ([40_000.0, 80_000.0], 150.0),                 # even count → mean of the middle pair
    ([40_000.0, float("inf"), 80_000.0, None], 150.0),
])
def test_port_capital_floor_ignores_unusable_caps(caps, expected):
    good = sorted(c for c in caps if c is not None and math.isfinite(c) and c > 0)
    floor = statistics.median(good) / 400 if good else 0.0
    assert floor == pytest.approx(expected)


# ── nothing returned: a blank axis and a note, never over an unknown quarter ──


def test_the_note_needs_every_quarter_reported_and_no_cash_returned():
    """Decided from the CASH in both views (review 2026-10-07): a Yield-view gate read a
    rounded or unpriced 0.00% yield as "no dividends or buybacks"."""
    chart = _chart_struct()
    gate = _body(chart, "private var returnedNothing: Bool")
    assert re.fullmatch(r"\s*SignalOfConfidenceScale\.returnedNothing\(dataPoints\)\s*", gate), \
        f"the note's gate changed: {gate!r}"
    rule = _body(_scale_enum(), "static func returnedNothing(")
    assert re.fullmatch(
        r"\s*!points\.isEmpty && points\.allSatisfy \{ point in\s*"
        r"point\.cashFlowReported && !\(point\.dividendAmount \+ point\.buybackAmount > 0\)\s*\}\s*",
        rule,
    ), f"the cash rule changed: {rule!r}"
    for token in ("Yield", "barTotals", "hasCapitalReturn"):
        assert token not in rule, f"the note reads the shown basis again ({token})"

    note = _body(chart, "private var noCapitalReturnNote: some View")
    assert note.strip().startswith("if returnedNothing {")
    assert '"No dividends or buybacks in these quarters"' in note
    assert ".allowsHitTesting(false)" in note, "the note must not swallow the chart's scroll"
    assert "alignment: .bottom" in note, "the note belongs in the bar band, below the line"

    body = _body(chart, "private var chartBody: some View")
    assert re.search(r"\.overlay\(alignment: \.top\) \{\s*noCapitalReturnNote\s*\}", body)
    assert chart.count("noCapitalReturnNote") == 2, "declared once, drawn once"


def test_the_left_axis_is_blank_when_nothing_was_returned():
    axis = _body(_chart_struct(), "private var leftYAxisLabels: some View")
    texts = re.findall(r"Text\(([^\n]+)\)\n", axis)
    assert len(texts) == 4, texts
    for t in texts[:3]:
        assert t.startswith("hasCapitalReturn ?") and t.endswith(': ""'), \
            f"a tick prints with no bar to measure: {t!r}"
    # the baseline goes only with the note (no cash at all) — a real but sub-0.005% yield
    # keeps its "0%"
    assert texts[-1] == 'returnedNothing ? "" : (viewType == .yield ? "0%" : "$0")', texts[-1]


def _returned_nothing(points: List[dict]) -> bool:
    """Port of `SignalOfConfidenceScale.returnedNothing`."""
    return bool(points) and all(
        p.get("reported", True) and not (p["div"] + p["bb"] > 0) for p in points
    )


@pytest.mark.parametrize("points, expected, why", [
    ([{"div": 0.0, "bb": 0.0}] * 8, True, "a non-returner: nothing paid in any quarter"),
    ([{"div": 0.0, "bb": 0.3}] + [{"div": 0.0, "bb": 0.0}] * 7, False,
     "a $0.3M buyback whose yield rounds to 0.00% — cash, not the yield, decides"),
    ([{"div": 3_500.0, "bb": 20_000.0}] * 8, False,
     "a top repurchaser with every cap source down (all yields 0.00)"),
    ([{"div": 0.0, "bb": 0.0}, {"div": 0.0, "bb": 0.0, "reported": False}], False,
     "an unreported quarter is an unknown, not a zero"),
    ([], False, "no quarters, nothing to claim"),
    ([{"div": float("nan"), "bb": 0.0}], True, "a NaN amount is no payment (never from JSON)"),
])
def test_port_returned_nothing_reads_cash(points, expected, why):
    assert _returned_nothing(points) is expected, why


# ── the share-count change names its window ──────────────────────────────────


def test_the_buyback_card_says_since_when():
    card = _body(_code(_CARD), "struct BuybackOnlyInfoCard: View {")
    assert re.search(r"var shareCountWindowStart: String\? = nil", card)
    fmt = _body(card, "private var formattedShareCountChange: String")
    guard = fmt.index('guard shareCountChangeKnown else { return "—" }')
    since = fmt.index('"\\(change) since \\(start)"')
    assert guard < since, "an unmeasured change must stay '—', never '+0.0% since …'"
    assert re.search(r'guard let start = shareCountWindowStart, !start\.isEmpty else \{ return change \}', fmt)

    section = _body(_code(_SECTION), "struct SignalOfConfidenceSectionCard: View {")
    assert "shareCountWindowStart: signalData.shareCountWindowStart" in section

    data = _body(_code(_MODELS), "struct SignalOfConfidenceSectionData {")
    start = _body(data, "var shareCountWindowStart: String?")
    assert re.fullmatch(
        r"\s*dataPoints\.first \{ \(\$0\.sharesOutstanding \?\? 0\) > 0 \}\?\.period\s*", start
    ), f"the window must start at the oldest REPORTED count (the server's rule): {start!r}"


def _window_start(shares: List[Optional[float]], periods: List[str]) -> Optional[str]:
    """Port of `SignalOfConfidenceSectionData.shareCountWindowStart`."""
    for s, p in zip(shares, periods):
        if (s or 0) > 0:
            return p
    return None


def test_port_window_start_matches_the_servers_oldest_measured_point():
    periods = ["Q3 '24", "Q4 '24", "Q1 '25", "Q2 '26"]
    assert _window_start([None, 404.41, 404.41, 551.0], periods) == "Q4 '24"   # CRWV, fixed
    assert _window_start([0.0, None, 12.0, 13.0], periods) == "Q1 '25"
    assert _window_start([None, None, None, None], periods) is None


# ── the display model carries the cap, defaulted, without disturbing the order ─


def test_the_display_point_carries_market_cap_before_the_flag():
    point = _body(_code(_MODELS), "struct SignalOfConfidenceDataPoint: Identifiable {")
    cap = re.search(r"^\s*var marketCap: Double\? = nil\s*$", point, re.M)
    flag = re.search(r"^\s*var cashFlowReported: Bool = true\s*$", point, re.M)
    assert cap and flag and cap.start() < flag.start(), \
        "marketCap must be defaulted and declared before cashFlowReported (memberwise order)"
