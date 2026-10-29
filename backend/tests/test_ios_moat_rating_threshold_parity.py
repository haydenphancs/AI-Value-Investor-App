"""The Wide / Narrow / No-moat thresholds agree between iOS and the backend.

iOS draws the chip from `MoatOverallRating.from(dimensions:)` (TickerReportModels.swift); the
backend grades the report's moat vital (`_derive_moat_vital`) and the Insight's tone
(`narrative_prompts._moat_durability_note_prompt`) from `moat_scoring_service.MOAT_RATING_WIDE`
/ `MOAT_RATING_NARROW`. They were three hand copies of 8.5 / 7.0 with nothing tying them
together, so a change on one side would have made the Insight say "narrow" under a "Wide
Moat" chip. Source scan: comments stripped, bounded to the one function's braces.

Both sides also grade the SAME pillars (owner decision 2026-10-28, "Show it, but exclude it
from backend verdicts"): an AI-estimated pillar (`source` "ai_legacy") stays on the radar but
never sets the chip or the Primary Driver. iOS used to drop `source` at the DTO mapping and
grade on every pillar, so a JOBY report's AI Switching Costs read "Wide Moat" while the
backend vital ignored it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from app.services.moat_scoring_service import MOAT_RATING_NARROW, MOAT_RATING_WIDE

ROOT = Path(__file__).resolve().parents[2]
_SWIFT = ROOT / "frontend/ios/ios/Models/TickerReportModels.swift"
_COLLECTOR = ROOT / "backend/app/services/agents/ticker_report_data_collector.py"
_PROMPTS = ROOT / "backend/app/services/agents/narrative_prompts.py"
_SWIFT_DTO = ROOT / "frontend/ios/ios/Models/TickerReportResponse.swift"
_SWIFT_SECTION = ROOT / "frontend/ios/ios/Views/Organisms/ReportMoatCompetitionSection.swift"
_SWIFT_RADAR = ROOT / "frontend/ios/ios/Views/Molecules/ReportMoatRadarChart.swift"


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(line.split("//", 1)[0] for line in src.splitlines())


def _brace_body(src: str, signature: str) -> str:
    start = src.index(signature)
    open_at = src.index("{", start)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at:i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _ios_thresholds() -> tuple:
    src = _strip_swift_comments(_SWIFT.read_text())
    body = _brace_body(src, "static func from(dimensions: [MoatDimension]) -> MoatOverallRating")
    wide = re.search(r"maxScore\s*>=\s*([0-9.]+)\s*\{\s*return\s+\.wide", body)
    narrow = re.search(r"maxScore\s*>=\s*([0-9.]+)\s*\{\s*return\s+\.narrow", body)
    assert wide and narrow, "MoatOverallRating.from no longer reads `maxScore >= x { return .wide|.narrow`"
    return float(wide.group(1)), float(narrow.group(1))


def test_ios_chip_thresholds_match_the_backend_constants():
    assert _ios_thresholds() == (MOAT_RATING_WIDE, MOAT_RATING_NARROW)


def _function_node(path: Path, func: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return node
    raise AssertionError(f"{func} not found in {path.name}")


def _names(node: ast.AST) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_backend_graders_read_the_constants_not_literals():
    """AST, not a text scan: a reverted literal (`s >= 8.5`, `7.0 <= max_score`,
    `max_score > 8.4`) fails here even though the function still imports the constants.
    Every comparison of a pillar score must use MOAT_RATING_WIDE / MOAT_RATING_NARROW;
    only the composite's own status bands (another scale) may carry a literal."""
    for path, func in ((_COLLECTOR, "_derive_moat_vital"),
                       (_PROMPTS, "_moat_durability_note_prompt")):
        node = _function_node(path, func)
        used = _names(node)
        assert {"MOAT_RATING_WIDE", "MOAT_RATING_NARROW"} <= used, func
        for cmp in (n for n in ast.walk(node) if isinstance(n, ast.Compare)):
            operands = [cmp.left, *cmp.comparators]
            literal = [o for o in operands
                       if isinstance(o, ast.Constant) and isinstance(o.value, (int, float))
                       and not isinstance(o.value, bool)]
            if not literal:
                continue
            if "composite" in _names(cmp):
                continue            # the 0-10 composite's good / critical bands
            # The rating is read off the top pillar: no literal may grade `max_score`, and
            # no pillar comparison may restate a rating value (the 6.0 tag cut-off is its
            # own rule, not a rating threshold).
            rating_value = any(float(o.value) in (MOAT_RATING_WIDE, MOAT_RATING_NARROW)
                               for o in literal)
            assert "max_score" not in _names(cmp) and not rating_value, (
                f"{func} compares a pillar score with a literal "
                f"({ast.unparse(cmp)}); use MOAT_RATING_WIDE / MOAT_RATING_NARROW"
            )


# ── measured pillars only ────────────────────────────────────────────────────

def _squash(code: str) -> str:
    return re.sub(r"\s+", " ", code).strip()


def _member_body(src: str, type_signature: str, member_signature: str) -> str:
    """The member's braces, searched only inside the one type's braces (a same-named
    member on another type must not satisfy the scan)."""
    return _brace_body(_brace_body(src, type_signature), member_signature)


def test_ios_chip_grades_only_measured_pillars():
    src = _strip_swift_comments(_SWIFT.read_text())
    body = _brace_body(src, "static func from(dimensions: [MoatDimension]) -> MoatOverallRating")
    kept = re.search(r"let\s+(\w+)\s*=\s*dimensions\.filter\(\s*\\\.isMeasured\s*\)", body)
    assert kept, "MoatOverallRating.from no longer filters `dimensions` on `isMeasured`"
    assert re.search(rf"maxScore\s*=\s*{kept.group(1)}\.map\b", body), (
        "MoatOverallRating.from takes its max over something other than the measured pillars")
    assert not re.search(r"\bdimensions\s*\.\s*(map|max|sorted|first|reduce)\b", body), (
        "MoatOverallRating.from reads the UNFILTERED dimensions again")
    assert re.search(r"\.max\(\)\s*else\s*\{\s*return\s+\.notMeasured\b", body), (
        "with no measured pillar the chip must say .notMeasured, never grade as .none")

    # `isMeasured` is exactly "no tag (an old report) or deterministic": any other tag —
    # ai_legacy, the retired grounded, an unknown value — stays out, like the backend.
    measured = _member_body(src, "struct MoatDimension: Identifiable", "var isMeasured: Bool")
    assert _squash(measured) == '{ source == nil || source == "deterministic" }', measured

    driver = _member_body(src, "struct ReportMoatCompetitionData",
                          "var primaryDriver: MoatDimension?")
    assert re.search(r"dimensions\.filter\(\s*\\\.isMeasured\s*\)\.max\(", driver), (
        "primaryDriver must read off the same measured pillars as the chip")


def test_backend_measured_tag_is_the_one_ios_trusts():
    """iOS's `isMeasured` literal and the backend vital's filter name the same tag."""
    node = _function_node(_COLLECTOR, "_derive_moat_vital")
    compared = {o.value for cmp in ast.walk(node) if isinstance(cmp, ast.Compare)
                for o in (cmp.left, *cmp.comparators)
                if isinstance(o, ast.Constant) and isinstance(o.value, str)}
    assert "deterministic" in compared, "_derive_moat_vital no longer filters on the tag"
    src = _strip_swift_comments(_SWIFT.read_text())
    measured = _member_body(src, "struct MoatDimension: Identifiable", "var isMeasured: Bool")
    assert '"deterministic"' in measured


def test_ios_carries_both_provenance_tags_from_the_wire():
    src = _strip_swift_comments(_SWIFT_DTO.read_text())
    dto = _brace_body(src, "struct MoatDimensionDTO: Codable")
    assert re.search(r'case\s+peerSource\s*=\s*"peer_source"', dto)
    # A wrong-typed tag must not fail the whole report's decode.
    assert re.search(r"self\.source\s*=\s*try\?\s*c\.decodeIfPresent", dto)
    assert re.search(r"self\.peerSource\s*=\s*try\?\s*c\.decodeIfPresent", dto)
    mapping = re.search(r"MoatDimension\(([^()]*)\)", src[src.index("moatCompetition.dimensions.map"):])
    assert mapping, "moat dimension mapping not found"
    args = _squash(mapping.group(1))
    assert "source: d.source" in args and "peerSource: d.peerSource" in args, args


def test_ios_header_never_grades_ai_guesses_or_an_undrawable_radar():
    src = _strip_swift_comments(_SWIFT_SECTION.read_text())
    body = _member_body(src, "struct ReportMoatCompetitionSection: View", "var body: some View")
    assert re.search(r"if\s+data\.dimensions\.count\s*>=\s*3\s*\{\s*moatRatingHeader\s*\}", body), (
        "the chip / legend / Primary Driver must wait for a drawable (>= 3 pillar) radar")
    row = _member_body(src, "struct ReportMoatCompetitionSection: View",
                       "private var primaryDriverRow: some View")
    assert ".notMeasured" in row and '"Unknown"' not in row
    assert '"Strongest pillar:"' in row and re.search(r"driver\.score\s*>\s*0", row)


_STRING_LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def test_the_moat_section_never_labels_ai():
    """Owner 2026-10-29: "no need to label AI" — the verdict ignores unmeasured pillars, but
    the radar draws every company vertex alike and no user-visible string names AI. Only the
    5.0 placeholder peer point is drawn hollow (and drops the peer fill / shows the legend key);
    an "ai_estimate" peer draws filled, like an industry average."""
    radar = _strip_swift_comments(_SWIFT_RADAR.read_text())
    section = _strip_swift_comments(_SWIFT_SECTION.read_text())
    assert "isMeasured" not in radar, "the radar must not style a vertex by who scored it"
    for name, code in (("radar", radar), ("section", section)):
        ai = [lit for lit in _STRING_LITERAL.findall(code) if re.search(r"\bAI\b", lit)]
        assert not ai, f"the moat {name} shows AI wording: {ai}"

    models = _strip_swift_comments(_SWIFT.read_text())
    placeholder = _member_body(models, "struct MoatDimension: Identifiable",
                               "var peerIsPlaceholder: Bool")
    assert _squash(placeholder) == '{ peerSource == "baseline" }', placeholder

    for code in (radar, section):
        gate = _brace_body(code, "private var hasPlaceholderPeer: Bool")
        assert re.fullmatch(r"\{\s*(data\.)?dimensions\.contains\(where:\s*\\\.peerIsPlaceholder\)\s*\}",
                            gate.strip()), gate
    dots = _brace_body(radar, "private var peerDots: some View")
    assert re.search(r"if\s+dimension\.peerIsPlaceholder\s*\{\s*Circle\(\)\s*\.strokeBorder", dots)
    header = _member_body(section, "struct ReportMoatCompetitionSection: View",
                          "private var moatRatingHeader: some View")
    assert re.search(r'if\s+hasPlaceholderPeer\s*\{\s*legendItem\(label:\s*"Not measured"\)', header)

