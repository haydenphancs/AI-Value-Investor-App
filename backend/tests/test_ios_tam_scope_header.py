"""iOS guard: the TAM column's "US"/"Global" prefix appears only over a shown TAM.

There is no XCTest target, so this scans the Swift source per `.claude/rules/testing.md` §3
— comment-stripped and brace-bounded, so it cannot pass on the prose next to the fix.

The defect: since 2026-10-01 the backend sets `market_dynamics.tam_scope` only when a TAM
is shown, but `research_reports` rows are point-in-time snapshots that are never re-patched
on read. Older rows still carry `tam_scope = "us"` with `current_tam = 0`, and
`MarketDynamics.tamHeaderLabel` keyed the prefix off `scopeLabel` alone — so the report
rendered "US - Market Size (TAM)" over "—", a scope claim about a figure that does not
exist. The header must also require `tamIsAvailable`, the same predicate that chooses
between the TAM values and "—" in `ReportMoatCompetitionSection`.

2026-10-22: a measured row's header is the server's `market_size_label` ("US industry
revenue"); the same guard comes first, and a company quote keeps the plain header.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MODELS = _REPO / "frontend/ios/ios/Models/TickerReportModels.swift"
_SECTION = _REPO / "frontend/ios/ios/Views/Organisms/ReportMoatCompetitionSection.swift"


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text()


def _strip_comments(src: str) -> str:
    """Drop `//` lines and `///` doc comments.

    Mandatory: the doc comment beside this fix names every token the assertions grep for,
    so an un-stripped scan would pass on prose after the code is reverted.
    """
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


def _decl_body(src: str, header: str) -> str:
    """The brace-bounded body of one declaration (a whole-file scan passes on another type)."""
    start = src.index(header)
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _market_dynamics() -> str:
    return _decl_body(_strip_comments(_src(_MODELS)), "struct MarketDynamics {")


def test_the_scope_prefix_requires_a_shown_tam():
    header = _decl_body(_market_dynamics(), "var tamHeaderLabel: String")

    guard = re.search(r'guard\s+tamIsAvailable\s+else\s*\{\s*return\s+"Market Size \(TAM\)"\s*\}', header)
    assert guard, (
        "tamHeaderLabel must open with `guard tamIsAvailable else { return \"Market Size (TAM)\" }` "
        "— persisted reports carry tam_scope='us' with a zero TAM and would show 'US' over '—'"
    )
    # Nothing that names a scope or a label may be read before that guard (2026-10-22: the
    # server's "US industry revenue" label rides in front of the legacy scope prefix).
    for token in ("marketSizeLabel", "scopeLabel"):
        assert token in header, f"tamHeaderLabel no longer reads {token}: re-check this guard"
        assert header.index(token) > guard.end(), f"{token} is read before the tamIsAvailable guard"
    assert re.search(r'guard\s+let\s+\w+\s*=\s*scopeLabel\s+else\s*\{\s*return\s+"Market Size \(TAM\)"\s*\}',
                     header), "an unknown scope must still return the unprefixed header"
    # The server's label is a measured row's header only — never over a company's own figure.
    assert re.search(r"if\s+basis\s*==\s*\.measured\s*,\s*let\s+\w+\s*=\s*Self\.trimmed\(marketSizeLabel\)",
                     header)


def test_availability_is_the_predicate_the_view_uses():
    # The guard is only right if `tamIsAvailable` is the same test that swaps the values
    # for "—"; pin both halves so neither drifts on its own.
    available = _decl_body(_market_dynamics(), "var tamIsAvailable: Bool")
    assert re.search(r"currentTAM\s*>\s*0\s*\|\|\s*futureTAM\s*>\s*0", available)

    section = _strip_comments(_src(_SECTION))
    assert "data.marketDynamics.tamHeaderLabel" in section
    assert "if data.marketDynamics.tamIsAvailable {" in section
