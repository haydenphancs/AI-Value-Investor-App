"""The Wide / Narrow / No-moat thresholds agree between iOS and the backend.

iOS draws the chip from `MoatOverallRating.from(dimensions:)` (TickerReportModels.swift); the
backend grades the report's moat vital (`_derive_moat_vital`) and the Insight's tone
(`narrative_prompts._moat_durability_note_prompt`) from `moat_scoring_service.MOAT_RATING_WIDE`
/ `MOAT_RATING_NARROW`. They were three hand copies of 8.5 / 7.0 with nothing tying them
together, so a change on one side would have made the Insight say "narrow" under a "Wide
Moat" chip. Source scan: comments stripped, bounded to the one function's braces.
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
