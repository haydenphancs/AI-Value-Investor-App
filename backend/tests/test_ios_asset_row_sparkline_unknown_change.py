"""F19-4 — the Tracking row's sparkline must not draw a reference line for an UNKNOWN change.

F20-4 gave `SparklineView` a real no-reference mode (`showReference`) and wired it into
`MarketPulseCard` only. `AssetRow` — the second reader of the same series — kept passing
`isPositive: asset.isPositive` and `referencePrice: asset.previousClose` with no flag. When
`change_known` is false the backend sends no `previous_close`, so `TrackedAsset.previousClose`
is nil, `SparklineView` falls back to `data[0]` as the reference, and the reference branch
painted a dashed line at the first bar with red segments and a red end dot beneath it —
a fabricated intraday decline beside a `PriceChangeLabel` that said "—". And because
`asset.isPositive` is hard-false when unknown, even the neutral mode would have stroked
the whole line red.

Source-scan guard: comments stripped BEFORE bounding, the `SparklineView(` call bounded by
its parentheses INSIDE the brace-bound `struct AssetRow` (not the whole file — the preview
builds rows too), mutation-tested by hand.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _bounded(src: str, header: str, open_ch: str, close_ch: str) -> str:
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_at = src.index(open_ch, start)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[open_at:i + 1]
    pytest.fail(f"unbalanced {open_ch}{close_ch} after {header!r}")


def _asset_row_sparkline_call() -> str:
    src = _strip((_IOS / "Views/Molecules/AssetRow.swift").read_text(encoding="utf-8"))
    body = _bounded(src, "struct AssetRow: View", "{", "}")
    assert body.count("SparklineView(") == 1, "AssetRow builds more than one sparkline — re-read this test"
    return _bounded(body, "SparklineView(", "(", ")")


def test_asset_row_hides_the_reference_when_the_change_is_unknown():
    call = _asset_row_sparkline_call()
    assert "showReference: asset.changeKnown" in call, (
        "no `showReference` — an unknown change draws a dashed baseline at data[0] with "
        "red segments and a red dot beneath it, a fabricated intraday decline under a '—'"
    )
    assert "referencePrice: asset.changeKnown ? asset.previousClose : nil" in call


def test_asset_row_colours_the_line_by_the_series_when_the_change_is_unknown():
    call = _asset_row_sparkline_call()
    # `asset.isPositive` is `changeKnown && …` — hard-false when unknown — so it cannot be
    # the sole colour input: the neutral mode strokes ONE tone by this flag.
    assert re.search(
        r"isPositive:\s*asset\.changeKnown\s*\?\s*asset\.isPositive\s*:\s*"
        r"\(\(asset\.sparklineData\.last \?\? 0\) >= \(asset\.sparklineData\.first \?\? 0\)\)",
        call,
    ), "isPositive is not derived from the series when the change is unknown — the line goes red"
    assert "isPositive: asset.isPositive," not in call


def test_the_flag_is_honest_on_the_model_side():
    """Anti-vacuity: `changeKnown` really is the three-state flag (`Bool?` on the wire,
    `?? true`), `previousClose` really is nil when unknown, and `SparklineView` really has
    the no-reference mode this call relies on."""
    model = _strip((_IOS / "Models/TrackingModels.swift").read_text(encoding="utf-8"))
    assert 'case changeKnown = "change_known"' in model
    prev = _bounded(model, "var previousClose: Double?", "{", "}")
    assert "guard changeKnown, priceKnown else { return nil }" in prev
    spark = _strip((_IOS / "Views/Atoms/SparklineView.swift").read_text(encoding="utf-8"))
    assert "var showReference: Bool = true" in spark
    assert "if !showReference {" in spark


def test_the_pulse_card_precedent_still_stands():
    """The rule this row now shares. If MarketPulseCard drops it the two readers diverge."""
    src = _strip((_IOS / "Views/Molecules/MarketPulseCard.swift").read_text(encoding="utf-8"))
    call = _bounded(src, "SparklineView(", "(", ")")
    assert "showReference: item.changeKnown" in call
