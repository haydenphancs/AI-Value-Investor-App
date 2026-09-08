"""iOS guard: an unreadable analyst grade must not become a real "Neutral" rating.

There is no XCTest target, so this scans the Swift source per `.claude/rules/testing.md` §3
— comment-stripped and brace-bounded, so it cannot pass on the prose next to the fix.

The defect: `AnalystAction.newRating` was a NON-Optional `AnalystRatingType`, so
`StockRepository.toDisplayModel` wrote `Self.mapRatingType(dto.newRating) ?? .neutral`.
`_build_actions` emits `newGrade or "N/A"`, and FMP also ships labels neither backend table
recognises ("mixed", "top pick", "average", "mkt perform" — enumerated in
`_analyst_common`). Every one of them rendered as "-> Neutral" and, through
`AnalystRatingsData.groundingLines`, was injected into a CREDIT-CHARGED Cay AI turn as a
real rating: "<Firm> UPGRADE to Neutral".

The backend explicitly refuses that fold — `classify_grade` returns
`RATING_CATEGORY_UNKNOWN` so an unreadable opinion never reaches the distribution — and the
actions list walked straight past its own policy.

Dormant today (`section_available` is False while the Analyst Ratings package is unbought),
which is exactly why it needs a guard: `fmp_entitlements` advertises repurchase as a
one-line change, so this goes live the moment someone buys the package back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_REPO_FILE = _REPO / "frontend/ios/ios/Core/Repositories/StockRepository.swift"
_MODELS = _REPO / "frontend/ios/ios/Models/TickerDetailModels.swift"
_CARD = _REPO / "frontend/ios/ios/Views/Molecules/AnalystActionCard.swift"


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text()


def _strip_comments(src: str) -> str:
    """Drop `//` lines and `///` doc comments.

    Mandatory: the explanatory comment beside this fix names every token the assertions
    grep for, so an un-stripped scan would pass on prose after the code is reverted.
    """
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("//")
    )


def _decl_body(src: str, header: str) -> str:
    """The brace-bounded body of one declaration.

    Scanning a whole file passes when the token lives in a different type — which is how a
    fix to a preview-only duplicate once looked like a fix to the live screen.
    """
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


def test_the_rating_is_optional_so_unmappable_is_representable():
    # The exact declaration, not the `struct AnalystAction` PREFIX — that matches
    # `struct AnalystActionsSummary` first, and the scan then proves nothing about the
    # type under test.
    body = _decl_body(_strip_comments(_src(_MODELS)), "struct AnalystAction: Identifiable")

    assert re.search(r"let newRating:\s*AnalystRatingType\?", body), (
        "AnalystAction.newRating must be Optional — a non-Optional forces the caller to "
        "invent a rating for a grade that maps to nothing"
    )
    assert re.search(r"let newRatingLabel:\s*String", body), (
        "the backend's own wording must be kept so an unmapped grade can be shown verbatim"
    )


def test_the_mapper_never_defaults_an_unmappable_grade_to_neutral():
    src = _strip_comments(_src(_REPO_FILE))

    assert not re.search(r"mapRatingType\([^)]*\)\s*\?\?\s*\.neutral", src), (
        "`?? .neutral` fabricates an opinion — including for the backend's \"N/A\" sentinel"
    )
    assert "newRatingLabel: dto.newRating" in src, (
        "the raw grade string must be carried through for display"
    )


def test_the_ai_grounding_omits_an_action_whose_grade_did_not_map():
    """Mirrors the backend's `RATING_CATEGORY_UNKNOWN`: an unreadable opinion is not data.

    This string reaches a credit-charged Cay AI turn, so a fabricated "to Neutral" is a
    paid-for misstatement, not just a UI blemish.
    """
    body = _decl_body(_strip_comments(_src(_MODELS)), "var groundingLines")

    assert "compactMap" in body, "an unmapped action must be dropped, not defaulted"
    assert "guard let rating = action.newRating else { return nil }" in body
    assert not re.search(r"\$0\.newRating\.rawValue", body), (
        "force-unwrapping the rating in the grounding line is the bug this replaced"
    )


def test_the_card_shows_the_firms_own_wording_rather_than_hiding_or_relabelling():
    body = _strip_comments(_src(_CARD))

    assert body.count("action.newRating?.rawValue ?? action.newRatingLabel") == 2, (
        "both arms of the card (with and without a previous rating) must fall back to the "
        "raw label"
    )
    assert not re.search(r"action\.newRating\.rawValue", body)
