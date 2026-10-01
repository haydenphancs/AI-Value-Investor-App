"""Earnings card geometry, ported from Swift and exercised on OUTLIER inputs (2026-09-30).

Python ports of the pure functions the Earnings card draws with:

* `EarningsSurpriseBarChart.surpriseDomain / normalizedY / barGeometry / signedPercent`
  (+ `ChartDomain.make / robust` and `CompactNumberFormat.percentString` they call);
* `EarningsChartView.minValue / maxValue / formatYValue / visiblePriceWindow`;
* `EarningsQuarterData.result / formattedSurprise / surpriseText / surpriseColor`.

Every port mirrors the Swift expression-for-expression; the source-scan guards in
`test_ios_earnings_deepcheck_guards.py` pin those same expressions in the Swift, so a port
cannot quietly drift from the code it stands for. The assertions are the CORRECT DEGRADED
behaviour: an outlier bar is pinned inside its 100pt plot and flagged off-scale (never
drawn at its true length), a non-finite value is skipped (never placed), revenue never
gets a negative axis, and a quarter with no consensus is never "matched".
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

PLOT_HEIGHT = 100.0  # EarningsSurpriseBarChart.chartHeight
MINIMUM_SPAN = 1.0   # ChartDomain.minimumSpan


def _swift_round(x: float) -> float:
    """Swift `.rounded()` = to nearest, ties AWAY from zero (Python's round() is banker's)."""
    return math.copysign(math.floor(abs(x) + 0.5), x)


# ── ChartDomain (Core/Utilities/ChartDomain.swift) ───────────────────────────


def chart_domain_make(values, include_zero=True, headroom=0.15, fallback=(0.0, 1.0)):
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return fallback
    lower, upper = min(finite), max(finite)
    if include_zero:
        lower, upper = min(lower, 0.0), max(upper, 0.0)
    span = upper - lower
    pad = max(span * headroom, MINIMUM_SPAN / 2 if span == 0 else 0.0)
    if upper > 0 or span == 0:
        upper += pad
    if lower < 0:
        lower -= pad
    if upper - lower < MINIMUM_SPAN:
        mid = (upper + lower) / 2
        lower, upper = mid - MINIMUM_SPAN / 2, mid + MINIMUM_SPAN / 2
        if include_zero:
            lower, upper = min(lower, 0.0), max(upper, MINIMUM_SPAN)
    return (lower, upper)


def chart_domain_robust(values, include_zero=True, headroom=0.15, fallback=(0.0, 1.0)):
    finite = sorted(v for v in values if math.isfinite(v))
    n = len(finite)
    if n < 4:
        return chart_domain_make(finite, include_zero, headroom, fallback)
    lo, hi = finite[0], finite[-1]
    q1 = finite[n // 4]
    q3 = finite[min((n * 3) // 4, n - 2)]
    iqr = max(q3 - q1, abs(q3) * 0.1, MINIMUM_SPAN)
    fenced_lo = max(lo, q1 - 3 * iqr)
    fenced_hi = min(hi, q3 + 3 * iqr)
    return chart_domain_make([fenced_lo, fenced_hi], include_zero, headroom, fallback)


# ── CompactNumberFormat (Core/Utilities/CompactNumberFormat.swift) ───────────


def _scaled(v: float) -> str:
    if v >= 10:
        return "%.0f" % v
    one_decimal = _swift_round(v * 10) / 10
    if one_decimal == _swift_round(one_decimal):
        return "%.0f" % one_decimal
    return "%.1f" % one_decimal


def percent_string(value: float) -> str:
    if not math.isfinite(value):
        return "—"
    magnitude = abs(value)
    sign = "-" if value < 0 else ""
    if magnitude >= 1_000_000:
        return sign + _scaled(magnitude / 1_000_000) + "M%"
    if magnitude >= 1_000:
        return sign + _scaled(magnitude / 1_000) + "k%"
    return "%d%%" % int(_swift_round(value))


# ── EarningsSurpriseBarChart ─────────────────────────────────────────────────


def plottable(quarters):
    """`plottableSurprises`: no bar for a non-finite surprise or a no-consensus quarter."""
    out = []
    for q in quarters:
        s = q.get("surprise")
        if result(q) == "noEstimate" or s is None or not math.isfinite(s):
            continue
        out.append(s)
    return out


def surprise_domain(surprises):
    magnitudes = sorted(abs(s) for s in surprises if math.isfinite(s))
    if not magnitudes:
        return (-10.0, 10.0)
    abs_max = magnitudes[-1]
    cap = abs_max
    if len(magnitudes) >= 4:
        fence = chart_domain_robust(magnitudes, include_zero=True, headroom=0)[1]
        if abs_max > fence * 2:
            cap = fence
    elif len(magnitudes) >= 2:
        second = magnitudes[-2]
        if second > 0 and abs_max > second * 10:
            cap = second * 1.5
    rounded = max(math.ceil(min(cap, abs_max)), 1)
    return (-float(rounded), float(rounded))


def normalized_y(value, height, domain):
    span = max(domain[1] - domain[0], 0.01)
    return (value - domain[0]) / span * height * 0.85 + height * 0.075


def bar_geometry(surprise, height, domain):
    plotted = min(max(surprise, domain[0]), domain[1])
    zero_y = normalized_y(0, height, domain)
    value_y = normalized_y(plotted, height, domain)
    bar_height = abs(value_y - zero_y)
    center_y = height - (zero_y + value_y) / 2
    return center_y, bar_height, plotted != surprise


def bar_extent(surprise, domain, height=PLOT_HEIGHT):
    """[top, bottom] of the drawn bar in view coordinates (y grows down)."""
    center, h, off = bar_geometry(surprise, height, domain)
    return center - h / 2, center + h / 2, off


def surprise_text(surprise, is_match):
    """`EarningsQuarterData.surpriseText` — the ONE formatter the 1Y caption, the 3Y
    off-scale label and every bar's VoiceOver label share (round 2, R10)."""
    if not math.isfinite(surprise):
        return "—"
    magnitude = abs(surprise)
    if magnitude < 0.05:
        return "0%" if is_match else "<0.1%"
    sign = "+" if surprise > 0 else "-"
    if magnitude >= 1000:
        return sign + percent_string(magnitude)
    if magnitude >= 99.95:
        return sign + ("%.0f" % magnitude) + "%"
    return sign + ("%.1f" % magnitude) + "%"


def signed_percent(surprise):
    """`EarningsSurpriseBarChart.signedPercent`. It was `"+"` + whole-percent
    `percent_string`, which printed 0.3 as "+0%", -0.4 as "0%" and 10.3 as "+10%"."""
    return surprise_text(surprise, surprise == 0)


# The AVGO 3Y revenue vector from the TestFlight report (11 ordinary quarters, one -88%).
_AVGO = [1.2, 0.8, 2.1, 4.0, 0.5, 1.9, 0.3, 1.1, 2.6, 0.9, 1.4, -88.07]


@pytest.mark.parametrize("vector", [
    _AVGO,
    [0.5, 1.2, 2.1, 3.9, 0.8, 1.1, 2.4, 0.6, 1.7, 3.1, 0.9, -99.9],    # dropped-digit actual
    [0.4, 1.0, 2.2, -3.0, 1.1, 0.7, 45_000.0],                          # near-zero consensus
    [2.0, -1.5, 3.0, 1.0, 1e6],
    [2.0, -1.5, 3.0, 1.0, -1e6],
    [1.0, 2.0, -1e6],                                                   # small-n path
    [-1e6, 1e6],
    [1e6],
    [-30.0, 9.0, 3.0, 2.0, 1.5, 2.5, 3.0, 1.0, 3.5, 4.0, 2.2, 1.8],
])
def test_every_bar_stays_inside_the_plot(vector):
    """The defect: a bar 624pt tall in a 100pt frame. Every bar's [top, bottom] must lie
    inside [0, 100], whatever the surprise."""
    domain = surprise_domain(vector)
    assert domain[0] == -domain[1], "the domain must stay symmetric (centred 0% line)"
    for s in vector:
        top, bottom, _ = bar_extent(s, domain)
        assert 0.0 <= top <= bottom <= PLOT_HEIGHT, (s, domain, top, bottom)
        _, h, _ = bar_geometry(s, PLOT_HEIGHT, domain)
        assert h <= PLOT_HEIGHT * 0.425 + 1e-9, "a bar can only span half the drawable band"


def test_avgo_outlier_is_pinned_flagged_and_labelled_with_its_true_value():
    domain = surprise_domain(_AVGO)
    assert domain == (-8.0, 8.0), domain  # the fence on |s| (7.7) rounded up
    top, bottom, off = bar_extent(-88.07, domain)
    assert off, "a clamped bar must be flagged so the chevron + label are drawn"
    assert bottom == pytest.approx(PLOT_HEIGHT - PLOT_HEIGHT * 0.075), "pinned at the inset edge"
    assert signed_percent(-88.07) == "-88.1%"
    # The ordinary quarters are drawn in full and stay legible (4% → ≥ 20pt).
    for s in _AVGO[:-1]:
        assert not bar_extent(s, domain)[2], s
    assert bar_geometry(4.0, PLOT_HEIGHT, domain)[1] >= 20


@pytest.mark.parametrize("surprise,label", [
    (1e6, "+1M%"), (-1e6, "-1M%"), (45_000.0, "+45k%"), (-99.9, "-99.9%"), (-1300.0, "-1.3k%"),
    (-99.97, "-100%"), (10.3, "+10.3%"), (-250.0, "-250%"),
])
def test_off_scale_label_is_the_true_compact_value(surprise, label):
    assert signed_percent(surprise) == label


def test_a_three_to_ten_x_outlier_no_longer_flattens_the_rest():
    """[-30, 9, 3, 2, …] was not capped by the 10x rule, so the domain was ±30 and a 2%
    bar was 2.8pt tall."""
    vector = [-30.0, 9.0, 3.0, 2.0, 1.5, 2.5, 3.0, 1.0, 3.5, 4.0, 2.2, 1.8]
    domain = surprise_domain(vector)
    assert domain[1] < 30
    assert bar_geometry(2.0, PLOT_HEIGHT, domain)[1] >= 8
    assert bar_extent(-30.0, domain)[2] and not bar_extent(9.0, domain)[2]


def test_ordinary_data_keeps_its_old_axis():
    """No outlier → exactly the pre-fix domain ±ceil(max|s|), nothing clamped."""
    vector = [7.1, 15.5, -7.7, 0, 5.5, -12.3, 8.2, 18.6]
    assert surprise_domain(vector) == (-19.0, 19.0)
    volatile = [-50.0, 40.0, -30.0, 60.0, 20.0, -45.0, 35.0, 25.0]
    assert surprise_domain(volatile) == (-60.0, 60.0)
    assert not any(bar_extent(s, surprise_domain(volatile))[2] for s in volatile)
    # A value only a little past the fence is drawn in full rather than pinned.
    near = [0.1] * 10 + [5.0, 6.0]
    assert surprise_domain(near) == (-6.0, 6.0)


@pytest.mark.parametrize("vector,expected", [
    ([], (-10.0, 10.0)),
    ([0.0, 0.0, 0.0, 0.0], (-1.0, 1.0)),          # all matched: never a (-0, 0) domain
    ([0.4], (-1.0, 1.0)),
    ([3.2], (-4.0, 4.0)),                          # single element: no second-largest
    ([0.5, 1.0, -88.0], (-2.0, 2.0)),              # small-n 10x rule
    ([0.0, 0.0, 5.0], (-5.0, 5.0)),                # second largest 0: no cap
])
def test_degenerate_and_small_inputs(vector, expected):
    assert surprise_domain(vector) == expected


def test_non_finite_surprises_are_skipped_not_placed():
    quarters = [{"actual": 1.0, "estimate": 0.9, "surprise": s}
                for s in (1.0, 2.0, float("nan"), float("inf"), -3.0, float("-inf"))]
    values = plottable(quarters)
    assert values == [1.0, 2.0, -3.0]
    assert surprise_domain(values) == surprise_domain([1.0, 2.0, -3.0])
    # The domain itself ignores a stray non-finite (defence in depth).
    assert surprise_domain([1.0, float("nan"), 2.0]) == surprise_domain([1.0, 2.0])


def test_a_quarter_without_consensus_gets_no_bar():
    q = {"actual": 0.42, "estimate": 0.42, "surprise": 0.0, "has_estimate": False}
    assert plottable([q]) == []


# ── EarningsChartView value domain ───────────────────────────────────────────


def value_domain(values):
    """`minValue` / `maxValue` over the finite earnings values (empty → 0 / 1 inputs)."""
    finite = [v for v in values if math.isfinite(v)]
    lo = min(finite) if finite else 0.0
    hi = max(finite) if finite else 1.0
    pad = max((hi - lo) * 0.1, 0.01)
    min_v = max(lo - pad, 0.0) if lo >= 0 else lo - pad
    max_v = min(hi + pad, 0.0) if (hi <= 0 and lo < 0) else hi + pad
    return min_v, max_v


@pytest.mark.parametrize("values", [
    [22.2e6, 25e9],                                   # AVGO dropped-digit vs estimates
    [0.0222e9, 25.0e9, 22.13e9, 15.0e9, 14.0e9],
    [0.05e9, 2.0e9],                                  # biotech, no glitch
    [0.5e6, 100e6],
    [0.05, 1.5],                                      # all-positive EPS grower
    [0.0, 3.0],
])
def test_all_positive_series_never_gets_a_negative_floor(values):
    lo, hi = value_domain(values)
    assert lo >= 0, f"negative revenue axis {lo} for {values}"
    assert lo <= min(values) and hi >= max(values)


def test_mixed_and_negative_series_still_contain_every_value():
    lo, hi = value_domain([-2.0, -0.5, 0.3, 1.2])
    assert lo < -2.0 and hi > 1.2
    lo, hi = value_domain([-0.5, -0.2, -0.01])
    assert hi <= 0 and lo <= -0.5
    lo, hi = value_domain([-0.5, -0.2, 0.0])
    assert hi == 0.0 and lo < -0.5


def test_all_zero_series_does_not_collapse():
    lo, hi = value_domain([0.0, 0.0])
    assert hi > lo


# ── EarningsChartView.formatYValue (EPS) ─────────────────────────────────────


def _drop_negative_zero(text):
    if text.startswith("-") and float(text[1:]) == 0:
        return text[1:]
    return text


def format_eps(value):
    magnitude = abs(value)
    if magnitude >= 100:
        text = "%.0f" % value
    elif magnitude >= 10:
        text = "%.1f" % value
    else:
        text = "%.2f" % value
    return _drop_negative_zero(text)


@pytest.mark.parametrize("value,expected", [
    (-11.68, "-11.7"), (11.68, "11.7"), (-150.0, "-150"), (150.0, "150"),
    (-0.5, "-0.50"), (-0.001, "0.00"), (2.49, "2.49"),
])
def test_eps_axis_label_precision_is_symmetric(value, expected):
    assert format_eps(value) == expected
    assert len(format_eps(-abs(value))) <= len(format_eps(abs(value))) + 1


# ── EarningsQuarterData.result / formattedSurprise / surpriseColor ───────────


def result(q):
    actual = q.get("actual")
    if actual is None:
        return "pending"
    if not q.get("has_estimate", True):
        return "noEstimate"
    if actual > q["estimate"]:
        return "beat"
    if actual < q["estimate"]:
        return "missed"
    return "matched"


def formatted_surprise(q):
    s = q.get("surprise")
    if s is None or not math.isfinite(s):
        return None
    outcome = result(q)
    if outcome in ("noEstimate", "pending"):
        return None
    return surprise_text(s, outcome == "matched")


def surprise_colour(q):
    if q.get("surprise") is None:
        return "textSecondary"
    return {"beat": "bullish", "missed": "bearish", "matched": "accentCyan"}.get(
        result(q), "textSecondary")


@pytest.mark.parametrize("q,text,colour", [
    ({"actual": 0.14, "estimate": 0.01, "surprise": 1300.0}, "+1.3k%", "bullish"),
    ({"actual": 1.0026, "estimate": 1.0, "surprise": 0.26}, "+0.3%", "bullish"),
    ({"actual": 9.996, "estimate": 10.0, "surprise": -0.04}, "<0.1%", "bearish"),
    ({"actual": 10.0004, "estimate": 10.0, "surprise": 0.0}, "<0.1%", "bullish"),
    ({"actual": 0.25, "estimate": 0.25, "surprise": 0.0}, "0%", "accentCyan"),
    ({"actual": 3.5, "estimate": 1.0, "surprise": 250.0}, "+250%", "bullish"),
    ({"actual": 0.0003, "estimate": 0.3, "surprise": -99.97}, "-100%", "bearish"),
    ({"actual": 1.0, "estimate": 2.0, "surprise": -50.0}, "-50.0%", "bearish"),
])
def test_one_y_caption_fits_and_agrees_with_the_dot(q, text, colour):
    assert formatted_surprise(q) == text
    assert surprise_colour(q) == colour
    assert len(text) <= 6, "must fit a ~48pt column at 12pt"


def test_no_consensus_is_never_matched_and_has_no_caption():
    """The backend copies the actual into estimate_value when no consensus existed; a plain
    comparison called every such quarter an exact match."""
    q = {"actual": 0.42, "estimate": 0.42, "surprise": None, "has_estimate": False}
    assert result(q) == "noEstimate"
    assert formatted_surprise(q) is None
    assert surprise_colour(q) == "textSecondary"
    # A REAL estimate of 0 keeps its beat/miss (its surprise is nil too).
    assert result({"actual": 0.05, "estimate": 0.0, "surprise": None}) == "beat"
    # An old payload without the key keeps today's behaviour.
    assert result({"actual": 0.42, "estimate": 0.42, "surprise": 0.0}) == "matched"


# ── EarningsChartView.visiblePriceWindow (#67) ───────────────────────────────


def visible_price_window(placed, width):
    inside = [i for i, (x, _) in enumerate(placed) if 0 <= x <= width]
    if not inside:
        return [], 0.0, 1.0
    lower, upper = max(inside[0] - 1, 0), min(inside[-1] + 1, len(placed) - 1)
    drawn = placed[lower:upper + 1]
    band = [placed[i][1] for i in inside] if len(inside) >= 2 else [p for _, p in placed]
    return drawn, min(band), max(band)


def _five_year_decline():
    """Daily closes over 5 years: $60 → $22 over the first 3.75 years, then $20-25."""
    start = date(2021, 9, 30)
    closes = []
    for day in range(5 * 365):
        d = start + timedelta(days=day)
        if day < 1370:
            price = 60 - 38 * day / 1370
        else:
            price = 22.5 + 2.5 * math.sin(day / 20.0)
        closes.append((d, price))
    return closes


def test_price_overlay_fills_the_plot_in_a_one_year_window():
    """1Y = 4 reported quarters + 2 estimates; the anchors are the first and the last
    reported quarter's fiscal end. The old scale (the whole series) put the visible
    $20-25 band in the bottom ~12% of the plot."""
    closes = _five_year_decline()
    width, height = 270.0, 200.0
    step = width / 6
    today = closes[-1][0]
    fiscal = [today - timedelta(days=90 * k + 30) for k in (3, 2, 1, 0)]
    x_first, x_last = 0 * step + step / 2, 3 * step + step / 2
    rate = (x_last - x_first) / (fiscal[-1] - fiscal[0]).days
    placed = [(x_first + rate * (d - fiscal[0]).days, p) for d, p in closes]

    drawn, low, high = visible_price_window(placed, width)
    inside = [(x, p) for x, p in placed if 0 <= x <= width]
    assert len(drawn) == len(inside) + 1, "the nearest close left of the plot is kept"

    def y(price, lo, hi):
        return height - ((price - lo) / max(hi - lo, 0.01) * height * 0.85 + height * 0.075)

    new_span = max(y(p, low, high) for _, p in inside) - min(y(p, low, high) for _, p in inside)
    all_lo, all_hi = min(p for _, p in closes), max(p for _, p in closes)
    old_span = max(y(p, all_lo, all_hi) for _, p in inside) - min(y(p, all_lo, all_hi) for _, p in inside)
    assert new_span >= 0.8 * height * 0.85
    assert old_span < 0.2 * height * 0.85, "fixture no longer reproduces the flattened line"


def test_price_window_degrades_without_two_visible_closes():
    assert visible_price_window([(-50.0, 10.0), (-10.0, 12.0)], 100.0) == ([], 0.0, 1.0)
    drawn, low, high = visible_price_window([(-10.0, 10.0), (50.0, 30.0), (150.0, 20.0)], 100.0)
    assert len(drawn) == 3 and (low, high) == (10.0, 30.0), "one close inside → full-series band"
