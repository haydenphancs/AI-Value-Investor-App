"""Growth round 2 (2026-09-30), iOS half — finding R34.

R34: the shared tick list put gridlines at EXACT thirds of the domain while the label
formatter rounds a sub-1,000 value to the cent. Near-break-even EPS [0.01, 0.02, 0.04]
drew gridlines at 0.0153 / 0.0307 / 0.046 labelled "0.02" / "0.03" / "0.05": the 0.02
bar topped out ~22pt above its own "0.02" line. Every tick is now snapped to the
precision its label prints (`snapToLabel`), the domain ends toward zero so the outer
gridlines stay inside the plot.

No XCTest target: a source-scan guard over the Swift (comments stripped, brace-bound —
testing.md §3; mutation-tested by hand: replacing the snapped candidates with the raw
thirds fails `test_every_tick_candidate_is_snapped_to_its_label`) plus the Python port
in test_growth_deepcheck_ios.py, which these invariants run against.
"""

from __future__ import annotations

import math
import re
from typing import List

import pytest

from tests.test_growth_deepcheck_ios import (
    _CHART,
    _IOS,
    _body,
    _code,
    _domain,
    _growth_format,
    _label_center,
    _swift_constant,
    _ticks,
)

_COMPACT = _IOS / "Core" / "Utilities" / "CompactNumberFormat.swift"


# ── source scan: the Swift really snaps every candidate ──────────────────────


def test_every_tick_candidate_is_snapped_to_its_label():
    ticks = _body(_code(_CHART), "private var yTicks: [Double]")
    # The two domain ends snap TOWARD zero (an outward round would put the top or
    # bottom gridline outside the plot); the four interior thirds to the nearest value.
    # Clamped to the domain too (an ulp of round-up in `value * 100`).
    assert re.search(
        r"candidates\.append\(\s*Swift\.min\(\s*snapToLabel\(\s*hi\s*,\s*\.towardZero\s*\)\s*,\s*hi\s*\)\s*\)",
        ticks,
    )
    assert re.search(
        r"candidates\.append\(\s*Swift\.max\(\s*snapToLabel\(\s*lo\s*,\s*\.towardZero\s*\)\s*,\s*lo\s*\)\s*\)",
        ticks,
    )
    for third in (r"2 \* hi / 3", r"hi / 3", r"lo / 3", r"2 \* lo / 3"):
        assert re.search(
            rf"snapToLabel\(\s*{third}\s*,\s*\.toNearestOrAwayFromZero\s*\)", ticks
        ), f"interior third {third!r} is not snapped to its label"
    # No raw (unsnapped) candidate anywhere: every `hi` / `lo` mention that is not a
    # comparison or the domain read sits inside a snapToLabel call.
    assert not re.search(r"candidates\.append\(\s*(hi|lo)\s*\)", ticks)
    assert not re.search(r"candidates\s*\+=\s*\[\s*2\s*\*\s*(hi|lo)\s*/\s*3", ticks)
    assert ticks.count("snapToLabel(") == 6, "six candidates, six snaps"
    # The spacing filter still runs on the SNAPPED values (a snapped duplicate or a
    # candidate snapped onto 0 is distance 0 from a kept tick and is dropped).
    assert re.search(r"allSatisfy\s*\{[^}]*>=\s*minGap\s*\}", ticks)
    # A snapped interior third outside the domain (a sub-cent side rounding outward)
    # is dropped, never drawn outside the plot.
    assert re.search(
        r"for\s+candidate\s+in\s+candidates\s+where\s+candidate\.isFinite\s*&&\s*"
        r"candidate\s*>=\s*lo\s*&&\s*candidate\s*<=\s*hi\s*\{", ticks,
    )


def test_snap_precision_matches_the_label_formatter():
    """The snap and the label must agree on the precision, or the bug comes back
    through a formatter change: cents below 1,000 (formatLargeNumber's own rounding),
    CompactNumberFormat's one-decimal-below-10 rule above it."""
    code = _code(_CHART)
    snap = _body(code, "private func snapToLabel(_ value: Double, _ rule: FloatingPointRoundingRule) -> Double")
    fmt = _body(code, "private func formatLargeNumber(_ number: Double) -> String")
    assert re.search(r"magnitude\s*<\s*1_000", snap) and re.search(r"abs\(number\)\s*<\s*1_000", fmt)
    assert re.search(r"\(value \* 100\)\.rounded\(rule\) / 100", snap)
    assert re.search(r"\(number \* 100\)\.rounded\(\) / 100", fmt)
    for tier in ("1_000_000_000_000", "1_000_000_000", "1_000_000", "1_000"):
        assert tier in snap, f"tier {tier} missing from the snap"
    assert re.search(r"magnitude\s*/\s*unit\s*>=\s*10\s*\?\s*unit\s*:\s*unit\s*/\s*10", snap)
    scaled = _body(_code(_COMPACT), "private static func scaled(_ v: Double) -> String")
    assert re.search(r"if\s+v\s*>=\s*10\s*\{", scaled), \
        "CompactNumberFormat's whole-unit threshold moved — update snapToLabel to match"
    assert re.search(r"\(v \* 10\)\.rounded\(\) / 10", scaled)


# ── the port: every gridline is the exact number its label prints ───────────


_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _parse(label: str) -> float:
    if label[-1] in _SUFFIX:
        return float(label[:-1]) * _SUFFIX[label[-1]]
    return float(label)


def _label(t: float) -> str:
    return "0" if t == 0 else _growth_format(t)


@pytest.fixture(scope="module")
def geo():
    code = _code(_CHART)
    return {"plot_h": _swift_constant(code, "chartHeight"),
            "min_gap": _swift_constant(code, "minTickSpacing"), "text_h": 14.0}


_CASES: List[List[float]] = [
    [0.01, 0.02, 0.04],            # the finding: labels 0.05/0.03/0.02 on 0.046/0.0307/0.0153
    [-0.01, 0.02, 0.03],           # mixed sign: "-0.01" sat on -0.0115
    [0.12, 0.15, 0.2],             # "0.08" sat on 0.0767
    [3.21, 3.87, 4.02],            # ordinary EPS
    [-1.37, -0.42, 0.88],
    [0.004],                       # below a cent: only the zero baseline survives
    [-0.003, 0.002],
    [-0.0067, 0.0212, 0.0101, 0.0242],  # 2·lo/3 = -0.0052 rounds OUT to -0.01 < lo: dropped
    [9.1e9, 4.0e9, 6.2e9],         # hi = 10.465B: "10B" sat 9.8pt off its gridline
    [-23.7e9, 5e9, 10e9],
    [474e9, 391e9, 365e9],
    [0.5e9, -27e9],
    [870.0, 12.0],                 # crosses the cents / K boundary at the top
    [8_700.0, 2_100.0],            # 10.005K top
    [2.6e12, 1.9e12],
]


@pytest.mark.parametrize("values", _CASES)
def test_each_gridline_is_exactly_the_value_its_label_prints(values, geo):
    lo, hi = _domain(values)
    ticks = _ticks(lo, hi, geo["plot_h"], geo["min_gap"])
    assert 0.0 in ticks
    labels = [_label(t) for t in ticks]
    assert len(set(labels)) == len(labels), f"duplicate labels {labels}"
    for t, lab in zip(ticks, labels):
        assert math.isfinite(t)
        assert abs(_parse(lab) - t) <= 1e-9 * max(1.0, abs(t)), (values, t, lab)
        # Inside the plot: the domain ends were snapped toward zero.
        assert lo - 1e-12 * max(1.0, abs(lo)) <= t <= hi + 1e-12 * max(1.0, abs(hi)), (t, lo, hi)
    ys = sorted((hi - t) / (hi - lo) * geo["plot_h"] for t in ticks)
    assert all(b - a >= geo["min_gap"] - 1e-9 for a, b in zip(ys, ys[1:])), "labels stack"


def test_the_finding_case_no_longer_mislabels_the_bar(geo):
    """The 0.02 bar's top and the gridline labelled '0.02' are the same y now (the
    port of the old exact-thirds list put them ~22pt apart)."""
    lo, hi = _domain([0.01, 0.02, 0.04])
    ticks = _ticks(lo, hi, geo["plot_h"], geo["min_gap"])
    by_label = {_label(t): t for t in ticks}
    assert set(by_label) == {"0.04", "0.03", "0.02", "0"}
    bar_top = (hi - 0.02) / (hi - lo) * geo["plot_h"]
    line = (hi - by_label["0.02"]) / (hi - lo) * geo["plot_h"]
    assert abs(bar_top - line) < 1e-6


def test_old_exact_thirds_were_wrong(geo):
    """Pins the defect: the exact thirds are NOT the numbers their labels print."""
    lo, hi = _domain([0.01, 0.02, 0.04])
    exact = [hi, 2 * hi / 3, hi / 3]
    assert any(abs(_parse(_label(t)) - t) > 0.002 for t in exact)


@pytest.mark.parametrize("values", _CASES)
def test_snapped_labels_still_sit_on_their_gridlines(values, geo):
    """#71's invariant survives the snap: interior labels centre on their gridline,
    the two edge labels are nudged by at most half a text height."""
    lo, hi = _domain(values)
    for t in _ticks(lo, hi, geo["plot_h"], geo["min_gap"]):
        raw = (hi - t) / (hi - lo) * geo["plot_h"]
        centre = _label_center(t, lo, hi, geo["plot_h"], geo["text_h"])
        assert abs(centre - raw) <= geo["text_h"] / 2 + 1e-9
