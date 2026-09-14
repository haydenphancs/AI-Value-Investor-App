"""Four small iOS guards from the 2026-09-12 widened deep-check (source-scan, comments
stripped, brace-bound where a type matters; each mutation-tested by hand).

* `AVATAR_UPLOAD_FAILED` reaches `.apiError` (backend copy + `.retry`), not a generic 503.
* `MarqueeChipRow`'s pan is inert when the chips fit (a 5 pt swipe folded the whole row
  off-screen through `wrapped`, and nothing ever restored it).
* `ChartSettingsSheet` highlights the EFFECTIVE chart type (a persisted Candle on a crypto
  screen left no row selected while a line was drawn).
* `SparklineView` has a real no-reference mode: `referencePrice: nil` means "anchor to the
  first point", so the pulse card's "no dashed reference" for an unknown change was false.
"""
import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    start = src.find(header)
    assert start != -1, f"{header!r} not found"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced braces")


def test_avatar_upload_failed_keeps_the_backend_copy():
    src = _strip((_IOS / "Core/Utilities/AppError.swift").read_text())
    arm = src[src.index("case .businessError(let code, let message)"):]
    arm = arm[:arm.index("case .decodingError")] if "case .decodingError" in arm else arm[:6000]
    assert 'code == "AVATAR_UPLOAD_FAILED"' not in arm, "the generic 503 mapping is back"


def test_the_marquee_pan_is_inert_when_the_row_fits():
    src = _strip((_IOS / "Views/Molecules/MarqueeChipRow.swift").read_text())
    pan = _block(src, "private var pan")
    changed, _, ended = pan.partition(".onEnded")
    assert "guard overflows else { return }" in changed
    assert "guard overflows else { return }" in ended


def test_the_chart_type_sheet_highlights_the_effective_type():
    src = _strip((_IOS / "Views/Molecules/Chart/ChartSettingsSheet.swift").read_text())
    assert "let effectiveChartType = assetContext.allowedChartTypes.contains(chartSettings.chartType)" in src
    assert src.count("effectiveChartType == type") == 3
    assert "chartSettings.chartType == type" not in src


def test_sparkline_has_a_real_no_reference_mode_and_the_pulse_card_uses_it():
    spark = _strip((_IOS / "Views/Atoms/SparklineView.swift").read_text())
    assert "var showReference: Bool = true" in spark
    body = _block(spark, "var body: some View")
    assert "if !showReference {" in body
    # the neutral branch draws no reference line and one tone by the caller's direction
    neutral = body[body.index("if !showReference {"):body.index("} else {")]
    assert "refLine" not in neutral and "aboveClip" not in neutral
    assert "let tone = isPositive ? AppColors.bullish : AppColors.bearish" in neutral
    card = _strip((_IOS / "Views/Molecules/MarketPulseCard.swift").read_text())
    assert "showReference: item.changeKnown," in card
