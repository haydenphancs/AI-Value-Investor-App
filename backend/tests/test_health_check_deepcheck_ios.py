"""iOS half of the Health Check deep-check fixes — source-scan guards + geometry ports.

There is no XCTest target, so the Swift fixes are pinned from Python:

* #37 the value / insight colour comes from the backend STATUS, not the gauge position
  (a pass at gauge 0.25 rendered amber, a neutral at 0.9 rendered red);
* #61 / #74 the Altman zone gauge places the TRUE Z (inverting the backend's clamped,
  rounded `gauge_position` capped every Z at 4.41 → 73.5% of the track) and the zone
  boundaries follow the backend: Distress <= 1.8, Grey (1.8, 3.0], Safe > 3.0;
* #62 the Altman history chart fences its data before widening to the cutoffs, so one
  extreme year cannot shrink the zone bands to slivers;
* #5 a "N/M" row (ROE on negative equity) shows "N/M", no gauge, neutral text colour.

Every scan strips comments first (the comment beside a fix names the very tokens the scan
looks for) and is brace-bound to the declaration it means. Each was mutation-tested once by
hand: break the Swift, watch the test fail, restore.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "HealthCheckModels.swift"
_CARD = _IOS / "Views" / "Molecules" / "HealthCheckMetricCard.swift"
_GAUGE = _IOS / "Views" / "Atoms" / "HealthCheckGaugeBar.swift"
_ZONES = _IOS / "Views" / "Molecules" / "MetricThresholdZones.swift"
_HISTORY = _IOS / "Views" / "Molecules" / "MetricHistoryLineChart.swift"


# ── scanning helpers ─────────────────────────────────────────────────────────


def _code_only(src: str) -> str:
    """Drop `/* */` blocks and `//` comments, keeping string literals intact."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        blanked = re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"',
                         line)
        idx = blanked.find("//")
        out.append(line[:idx] if idx != -1 else line)
    return "\n".join(out)


def _block(src: str, decl: str) -> str:
    """The `{ … }` body of the first declaration matching regex `decl` (brace-balanced)."""
    m = re.search(decl, src)
    assert m, f"declaration {decl!r} not found — the guard would be vacuous"
    start = src.index("{", m.end() - 1 if src[m.end() - 1] == "{" else m.end())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {decl!r}")


def _read(path: Path) -> str:
    return _code_only(path.read_text(encoding="utf-8"))


# ── #37 colour = verdict ─────────────────────────────────────────────────────


def test_value_colour_comes_from_status_not_gauge_position():
    metric = _block(_read(_MODELS), r"struct\s+HealthCheckMetric\s*:\s*Identifiable\s*\{")
    body = _block(metric, r"var\s+valueColor\s*:\s*Color\s*\{")
    assert "status.primaryColor" in body
    assert "gaugePosition" not in body and "colorAtPosition" not in body
    assert "colorAtPosition" not in metric, "the position→colour sampler is back"


# ── #74 one zone convention ──────────────────────────────────────────────────


def test_zone_index_puts_a_boundary_value_in_the_lower_zone():
    zones = _read(_ZONES)
    body = _block(zones, r"func\s+zoneIndex\s*\(")
    assert re.search(r"where\s+v\s*>\s*t\b", body), body
    assert ">=" not in body
    assert '"Distress ≤ 1.8 · Grey 1.8–3.0 · Safe > 3.0"' in zones


def test_zone_index_port_matches_backend_status():
    """Python port of `zoneIndex` against the backend `_zscore_status` at the edges."""
    from app.services.health_check_service import _zscore_status

    def zone_index(v: float, thresholds=(1.8, 3.0)) -> int:
        return min(sum(1 for t in thresholds if v > t), 2)

    by_status = {"negative": 0, "neutral": 1, "positive": 2}
    for z in (0.0, 1.0, 1.79, 1.8, 1.81, 2.5, 2.99, 3.0, 3.01, 12.0, -4.0):
        assert zone_index(z) == by_status[_zscore_status(z)], z


def test_card_zone_caption_follows_status_not_rounded_value():
    metric = _block(_read(_MODELS), r"struct\s+HealthCheckMetric\s*:\s*Identifiable\s*\{")
    body = _block(metric, r"var\s+formattedComparison\s*:\s*String\?\s*\{")
    altman = body[body.index("case .altmanZScore"):body.index("default:")]
    assert "switch status" in altman
    assert not re.search(r"value\s*>\s*[0-9]", altman), "zone re-derived from the rounded value"


def test_card_zone_labels_use_the_inclusive_distress_boundary():
    card = _read(_CARD)
    section = _block(card, r"private\s+var\s+gaugeSection\s*:\s*some\s+View\s*\{")
    assert '"≤ 1.8"' in section and '"1.8 – 3.0"' in section and '"> 3.0"' in section
    assert '"< 1.8"' not in section


# ── #61 the zone gauge places the true Z ─────────────────────────────────────


def test_zone_gauge_reads_the_true_z_value():
    gauge = _read(_GAUGE)
    body = _block(gauge, r"private\s+var\s+zScorePosition\s*:\s*Double\s*\{")
    assert re.search(r"zValue\s*\?\?", body), "the true Z must win over the inverted gauge"
    assert "isFinite" in body
    card = _read(_CARD)
    call = _block(card, r"private\s+var\s+gaugeSection\s*:\s*some\s+View\s*\{")
    call = call[call.index("HealthCheckGaugeBar("):]
    call = call[:call.index(")\n")]
    assert re.search(r"zValue:\s*metric\.type\s*==\s*\.altmanZScore\s*\?\s*metric\.value", call), (
        "the card must pass the metric's real Z into the zone gauge"
    )


def _gauge_round(x: float) -> float:
    return float(f"{x:.2f}")


def _old_zone_x(z: float) -> float:
    """Pre-fix iOS: invert the backend's clamp(z/4.5, .02, .98), rounded to 2 dp."""
    backend = _gauge_round(min(max(z / 4.5, 0.02), 0.98))
    return min(max(backend * 4.5 / 6.0, 0.02), 0.98)


def _new_zone_x(z: float) -> float:
    return min(max(z / 6.0, 0.02), 0.98)


def test_zone_gauge_port_reaches_the_safe_end_and_stays_monotonic():
    xs = [_new_zone_x(z) for z in (3.5, 4.5, 5.5)]
    assert xs == sorted(xs) and len(set(xs)) == 3, xs
    assert _new_zone_x(6.0) >= 0.9 and _new_zone_x(60.0) == 0.98
    # The defect being fixed: Z 4.5 and Z 60 drew at the same 73.5%.
    assert _old_zone_x(4.5) == _old_zone_x(60.0) == pytest.approx(0.735)
    # …and a distress-zone 1.78 snapped onto the 1.8 boundary (0.30).
    assert _old_zone_x(1.78) == pytest.approx(0.30)
    assert _new_zone_x(1.78) < 1.8 / 6.0


def test_gauge_marker_stays_on_the_track():
    """Port of `markerOffset`: the circle never hangs past either end."""
    def marker_offset(width: float, fraction: float, diameter: float = 14.0) -> float:
        r = diameter / 2
        if width <= diameter:
            return max(width / 2 - r, 0)
        centre = min(max(width * fraction, r), width - r)
        return centre - r

    for w in (248.0, 100.0, 15.0):
        for f in (0.0, 0.02, 0.5, 0.98, 1.0):
            off = marker_offset(w, f)
            assert 0 <= off and off + 14.0 <= max(w, 14.0) + 1e-9, (w, f, off)
    gauge = _read(_GAUGE)
    for view in ("gradientGauge", "zoneBasedGauge"):
        body = _block(gauge, rf"private\s+var\s+{view}\s*:\s*some\s+View\s*\{{")
        assert ".offset(x: markerOffset(width:" in body, f"{view} must use the clamped offset"


# ── #5 N/M rows ──────────────────────────────────────────────────────────────


def test_not_meaningful_token_matches_the_backend():
    from app.services.health_check_service import NOT_MEANINGFUL

    models = _read(_MODELS)
    m = re.search(r'static\s+let\s+notMeaningfulToken\s*=\s*"([^"]*)"', models)
    assert m and m.group(1) == NOT_MEANINGFUL


def test_not_meaningful_row_shows_no_ratio_and_no_gauge():
    metric = _block(_read(_MODELS), r"struct\s+HealthCheckMetric\s*:\s*Identifiable\s*\{")
    value = _block(metric, r"var\s+formattedValue\s*:\s*String\s*\{")
    assert re.search(r"if\s+isNotMeaningful\s*\{\s*return\s+Self\.notMeaningfulToken\s*\}", value)
    card = _read(_CARD)
    body = _block(card, r"var\s+body\s*:\s*some\s+View\s*\{")
    assert re.search(r"if\s+!metric\.isNotMeaningful\s*\{\s*gaugeSection\s*\}", body)


# ── #62 the threshold domain is fenced ───────────────────────────────────────


def test_threshold_domain_fences_the_data():
    chart = _read(_HISTORY)
    body = _block(chart, r"private\s+var\s+thresholdDomain\s*:\s*ClosedRange<Double>\?\s*\{")
    assert "ChartDomain.robust(" in body
    assert "companyValues.max()" not in body and "companyValues.min()" not in body
    area = _block(chart, r"private\s+func\s+chartArea\s*\(")
    assert "offScaleCompany" in area, "a pinned point needs its off-scale chevron"


def _make(values: List[float], include_zero=True, headroom=0.15,
          fallback=(0.0, 1.0)) -> Tuple[float, float]:
    """Port of ChartDomain.make."""
    finite = [v for v in values if v == v and abs(v) != float("inf")]
    if not finite:
        return fallback
    lower, upper = min(finite), max(finite)
    if include_zero:
        lower, upper = min(lower, 0), max(upper, 0)
    span = upper - lower
    pad = max(span * headroom, 0.5 if span == 0 else 0)
    if upper > 0 or span == 0:
        upper += pad
    if lower < 0:
        lower -= pad
    if upper - lower < 1.0:
        mid = (upper + lower) / 2
        lower, upper = mid - 0.5, mid + 0.5
        if include_zero:
            lower, upper = min(lower, 0), max(upper, 1.0)
    return lower, upper


def _robust(values: List[float], include_zero=True, headroom=0.15,
            fallback=(0.0, 1.0)) -> Tuple[float, float]:
    """Port of ChartDomain.robust (Tukey far-out fences, 3·IQR)."""
    finite = sorted(v for v in values if v == v and abs(v) != float("inf"))
    n = len(finite)
    if n < 4:
        return _make(finite, include_zero, headroom, fallback)
    q1 = finite[n // 4]
    q3 = finite[min((n * 3) // 4, n - 2)]
    iqr = max(q3 - q1, abs(q3) * 0.1, 1.0)
    lo = max(finite[0], q1 - 3 * iqr)
    hi = min(finite[-1], q3 + 3 * iqr)
    return _make([lo, hi], include_zero, headroom, fallback)


def _threshold_domain(values: List[float], thresholds=(1.8, 3.0)) -> Tuple[float, float]:
    """Port of MetricHistoryLineChart.thresholdDomain."""
    first_t, last_t = thresholds[0], thresholds[-1]
    d_lo, d_hi = _robust(values, True, 0.0, (0.0, 1.0))
    lo = min(0.0, d_lo, first_t)
    hi = max(d_hi, last_t)
    if hi <= lo:
        hi = lo + 1
    pad = (hi - lo) * 0.10
    hi += pad
    if lo < 0:
        lo -= pad
    return lo, hi


def _grey_share(domain: Tuple[float, float]) -> float:
    lo, hi = domain
    return (min(3.0, hi) - max(1.8, lo)) / (hi - lo)


def test_threshold_domain_port_keeps_the_zones_readable():
    high = _threshold_domain([3.1, 2.9, 3.4, 2.8, 120])
    assert _grey_share(high) >= 0.08, high
    assert high[1] < 120, "the outlier must pin, not set the domain"
    low = _threshold_domain([3.1, 2.9, 3.4, 2.8, -150])
    assert _grey_share(low) >= 0.08, low
    assert low[0] > -150
    # The pre-fix domain (raw min/max) squeezed the grey band to ~0.9% of the plot.
    raw_hi = max(120.0, 3.0) * 1.1
    assert (3.0 - 1.8) / raw_hi < 0.01


@pytest.mark.parametrize("values", [
    [40.0, 55.0, 62.0, 80.0],       # legitimately high every year: no cap
    [2.0, 2.5, 3.5],                # short series: plain span
    [1.2, 1.5, 1.4, 1.6, 1.3],      # all distress
    [-0.5, 0.2, 0.4, 0.3],          # modest negatives at true height
])
def test_threshold_domain_port_spans_ordinary_data(values):
    lo, hi = _threshold_domain(values)
    assert lo <= min(values) and hi >= max(values), (lo, hi)
    assert lo <= 1.8 and hi >= 3.0, "every zone and both cutoffs stay visible"


@pytest.mark.parametrize("values", [[], [2.4], [float("nan"), 2.0]])
def test_threshold_domain_port_degenerate_inputs(values):
    lo, hi = _threshold_domain(values)
    assert lo < hi and lo <= 0 and hi >= 3.0
