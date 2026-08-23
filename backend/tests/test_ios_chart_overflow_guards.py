"""A Swift Charts `AreaMark` under a zoomed y-scale must be pinned to the visible floor.

WHY THIS EXISTS — TestFlight feedback AKQ69kkOVcQoYTB_6Nfo-N0 (2026-08-22), "Chart got wrong!":
the AI-chat stock card drew its gradient with the TWO-ARGUMENT `AreaMark(x:y:)`, which baselines
the fill at ZERO IN DATA SPACE. With `.chartYScale(domain: 298...344)` that zero sits ~8x the plot
height below the plot rect, so a translucent red wash painted over the Day High / Day Low / Volume
/ Market Cap rows AND over the assistant's answer text below the card. Nothing clipped it:
`.cardSurface()` is `.background` + `.overlay` with no `clipShape`, and neither the message VStack
nor the chat `LazyVStack` clips.

The rest of the app already gets this right (`ReportPriceChart`, `SmartMoneyFlowChart`,
`MetricHistoryLineChart`, `ProfitabilityChartView` all use `yStart:`/`yEnd:`), and
`ReportHiddenMarketSignalsSection` fixed the identical class for `BarMark`. The chat card was the
one file that missed it, and no test could see that.

DERIVED, not enumerated (`project_source_scan_guard_vacuity`): the scan finds every Swift file
under `Views/` that sets a CUSTOM `.chartYScale(domain:` and declares an `AreaMark`, so a new chart
is covered without anyone remembering to add it here. Files with no custom domain are exempt on
purpose — Charts' automatic domain includes zero, so the baseline is inside the plot and the
two-argument form is correct there (e.g. `MoneyMoveArticleSectionContent`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_VIEWS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Views"
_CHAT_CARD = _VIEWS / "Molecules" / "ChatStockWidgetView.swift"


def _code_only(src: str) -> str:
    """Drop `//` comments.

    The explanatory comment next to a fix names every token this scan greps for, so an
    un-stripped file keeps passing on prose after the code is reverted.
    """
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            out.append("")
            continue
        # Trailing comment, ignoring `//` inside a string literal (rare here, but cheap to honour).
        depth_safe = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        idx = depth_safe.find("//")
        out.append(line[:idx] if idx != -1 else line)
    return "\n".join(out)


def _balanced(src: str, start: int, opener: str, closer: str) -> str:
    """Text from `start` (which must sit on `opener`) through its matching `closer`."""
    depth = 0
    for i in range(start, len(src)):
        if src[i] == opener:
            depth += 1
        elif src[i] == closer:
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    return src[start:]


def _area_mark_calls(src: str):
    """Every `AreaMark(...)` argument list, bounded by its own matching paren.

    Bounded by the PARENS, not by `yStart` — bounding a window with the token you are asserting
    is circular: delete the token and the window just grows until it finds another.
    """
    for m in re.finditer(r"\bAreaMark\s*\(", src):
        yield _balanced(src, m.end() - 1, "(", ")")


def _declaration_body(src: str, declaration: str) -> str:
    """The brace-bounded body of `declaration`, so a token in a SIBLING view can't satisfy it."""
    idx = src.find(declaration)
    assert idx != -1, f"declaration not found: {declaration!r}"
    brace = src.index("{", idx + len(declaration) - 1)
    return _balanced(src, brace, "{", "}")


def _zoomed_area_chart_files():
    """Swift files that set a custom y-domain AND draw an area — the at-risk set."""
    hits = []
    for f in sorted(_VIEWS.rglob("*.swift")):
        src = _code_only(f.read_text(encoding="utf-8"))
        if "chartYScale(domain" in src and "AreaMark(" in src:
            hits.append((f, src))
    return hits


def test_there_are_zoomed_area_charts_to_check():
    """Anti-vacuity: if the sweep finds nothing, the parametrised test below proves nothing."""
    found = _zoomed_area_chart_files()
    assert len(found) >= 4, (
        f"only {len(found)} zoomed area chart(s) found — the sweep is probably broken: "
        f"{[f.name for f, _ in found]}"
    )


@pytest.mark.parametrize(
    "path",
    [pytest.param(f, id=f.name) for f, _ in _zoomed_area_chart_files()],
)
def test_area_mark_under_a_custom_y_scale_pins_its_baseline(path: Path):
    src = _code_only(path.read_text(encoding="utf-8"))
    calls = list(_area_mark_calls(src))
    assert calls, f"{path.name}: sweep matched the file but found no AreaMark( call"
    for call in calls:
        assert "yStart:" in call and "yEnd:" in call, (
            f"{path.name} sets a custom .chartYScale(domain:) but uses the two-argument "
            f"AreaMark(x:y:), which baselines the fill at data-space ZERO — far below the plot "
            f"rect, where it paints over whatever follows the chart. Use "
            f"AreaMark(x:, yStart: <domain floor>, yEnd: <value>) like ReportPriceChart.swift. "
            f"Offending call: {' '.join(call.split())[:160]}"
        )


def test_chat_stock_card_clips_its_plot_area():
    """Belt-and-braces behind the `yStart` fix, scoped to the card that actually shipped broken."""
    body = _declaration_body(
        _code_only(_CHAT_CARD.read_text(encoding="utf-8")),
        "private var chartSection: some View",
    )
    assert "chartPlotStyle" in body and "clipped()" in body, (
        "ChatStockWidgetView.chartSection must keep .chartPlotStyle { $0.clipped() } so no future "
        "mark or interpolation change can escape the 140pt frame again."
    )


def test_chat_stock_card_does_not_interpolate_prices_that_never_traded():
    """`.catmullRom` overshoots the data extrema.

    On a percentage or ratio chart that is cosmetic. On a PRICE series it draws a price the stock
    never traded at — and the overshoot is what escaped the top of this plot. `.monotone` is
    shape-preserving and looks equally smooth.
    """
    body = _declaration_body(
        _code_only(_CHAT_CARD.read_text(encoding="utf-8")),
        "private var chartSection: some View",
    )
    assert "catmullRom" not in body, (
        "ChatStockWidgetView plots real prices — use .monotone, not .catmullRom, which overshoots "
        "the data extrema and invents highs/lows that never happened."
    )
    assert "interpolationMethod(.monotone)" in body, (
        "ChatStockWidgetView.chartSection lost its shape-preserving interpolation."
    )
