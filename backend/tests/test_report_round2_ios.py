"""Round 2 (G2a) — iOS source-scan guards for the report's honesty fixes.

  * R29/R45 — an OLD persisted report (no wire `result`) whose quarter is not a beat and
    whose stored surprise rounds to 0.0 is drawn NEUTRAL (`.inLine`: "0.0%", dash glyph,
    textSecondary). It used to be a red "<0.1%" miss — for what is usually an exact match.
    It is not drawn as "met" either: the legacy pair also covers sub-0.05% misses.
  * R21/R24 — an unmeasured share-count change reads "—" in neutral colour, decided by the
    wire `share_count_change_known` when present, else by the SoC rule over the points.

Comment-stripped and brace-bound (testing.md); each guard was mutation-tested once by hand.
No XCTest target exists — these read the Swift source.
"""

from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_REPORT_MODELS = _IOS / "Models/TickerReportModels.swift"
_REPORT_DTOS = _IOS / "Models/TickerReportResponse.swift"


def _strip_comments(src: str) -> str:
    out, i, n, in_str = [], 0, len(src), False
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


def _code(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_body(src: str, decl_regex: str) -> str:
    m = re.search(decl_regex, src)
    assert m, f"declaration not found: {decl_regex}"
    start = src.index("{", m.end() - 1 if src[m.end() - 1] == "{" else m.end())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {decl_regex!r}")


def _point() -> str:
    return _decl_body(_code(_REPORT_MODELS), r"struct EarningsTrackRecordPoint\s*:\s*Identifiable\s*\{")


# ── R29 / R45 ────────────────────────────────────────────────────────────────


def test_the_outcome_enum_has_a_neutral_legacy_case():
    enum = _decl_body(_code(_REPORT_MODELS), r"enum EarningsTrackRecordOutcome\s*\{")
    assert re.search(r"\bcase inLine\b", enum)


def test_a_legacy_non_beat_that_rounds_to_zero_is_in_line_not_a_miss():
    outcome = _decl_body(_point(), r"var outcome:\s*EarningsTrackRecordOutcome\s*\{")
    default = outcome[outcome.index("default:"):]
    # Only the legacy arm (no wire `result`) may infer; it checks the beat flag first,
    # then the zero-rounding surprise, and only then falls to `.miss`.
    beat_at = default.index("if beat { return .beat }")
    in_line = re.search(r"magnitude\s*<\s*0\.05\s*\{\s*return \.inLine\s*\}", default)
    assert in_line, "a legacy (0.0, beat=false) quarter must not be drawn as a miss"
    miss_at = default.index("return .miss")
    assert beat_at < in_line.start() < miss_at
    assert "abs(surprisePercent)" in default
    # The wire result still wins outright.
    assert 'case "met": return .met' in outcome and 'case "miss": return .miss' in outcome


def test_in_line_is_drawn_neutral_everywhere():
    point = _point()
    color = _decl_body(point, r"var outcomeColor:\s*Color\s*\{")
    assert re.search(r"case \.inLine:\s*return AppColors\.textSecondary", color)
    symbol = _decl_body(point, r"var outcomeSymbol:\s*String\s*\{")
    assert re.search(r'case \.inLine:\s*return "minus"', symbol)
    label = _decl_body(point, r"var outcomeLabel:\s*String\s*\{")
    in_line_label = re.search(r'case \.inLine:\s*return "([^"]*)"', label)
    assert in_line_label and "miss" not in in_line_label.group(1)
    assert "met the" not in in_line_label.group(1)
    text = _decl_body(point, r"var surpriseText:\s*String\s*\{")
    assert re.search(r'if outcome == \.inLine \{ return "0\.0%" \}', text)
    # "<0.1%" stays reserved for rows carrying a real direction.
    assert text.index(".inLine") < text.index('"<0.1%"')


# ── R21 / R24 ────────────────────────────────────────────────────────────────


def _capital() -> str:
    return _decl_body(_code(_REPORT_MODELS), r"struct ReportCapitalAllocation\s*\{")


def test_the_share_count_cell_reads_a_dash_when_unmeasured():
    ca = _capital()
    assert re.search(r"var shareCountChangeKnown:\s*Bool\s*=\s*true", ca)
    verdict = _decl_body(ca, r"var shareCountVerdictText:\s*String\s*\{")
    assert re.search(r'guard shareCountChangeKnown else \{ return "—" \}', verdict)
    # Neither verdict can fire on the placeholder 0.0.
    assert re.search(r"_shareCountDiluting:\s*Bool\s*\{\s*shareCountChangeKnown\s*&&", ca)
    assert re.search(r"_shareCountReducing:\s*Bool\s*\{\s*shareCountChangeKnown\s*&&", ca)


def test_the_measured_rule_matches_the_soc_summary():
    ca = _capital()
    rule = _decl_body(
        ca, r"static func isShareCountChangeMeasured\(points:\s*\[SignalOfConfidenceDataPoint\]\?\)\s*->\s*Bool\s*\{"
    )
    assert "guard let points else { return true }" in rule
    assert re.search(r"\(\$0\.sharesOutstanding \?\? 0\) > 0", rule)
    assert re.search(r"reported >= 2", rule)


def test_the_dto_decodes_the_flag_optionally_and_the_mapper_falls_back_to_the_rule():
    src = _code(_REPORT_DTOS)
    dto = _decl_body(src, r"struct CapitalAllocationDTO\s*:\s*Codable\s*\{")
    assert re.search(r"let shareCountChangeKnown:\s*Bool\?", dto)
    assert re.search(r'case shareCountChangeKnown = "share_count_change_known"', dto)
    mapper = src[src.index("capitalAllocation: insiderData.capitalAllocation.map"):]
    mapper = mapper[: mapper.index("insiderFlow:")]
    assert re.search(
        r"c\.shareCountChangeKnown\s*\?\?\s*ReportCapitalAllocation\.isShareCountChangeMeasured\(points:\s*points\)",
        mapper,
    )
    assert re.search(r"shareCountChangeKnown:\s*known", mapper)
