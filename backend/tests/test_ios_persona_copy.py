"""No invented testimonials and no real investor's name in the iOS persona / chat-mode copy.

`CommunityInsight.mockInsights` shipped two invented community posts in RELEASE builds as the
Research ViewModel's default: "Just completed a Buffett-style analysis … Highly recommend!" and
"The Cathie Wood persona nailed the $NVDA analysis … Worth every credit!" — a real investor's
name beside a fabricated endorsement of the product (FTC endorsement rules; App Store 5.2.1;
the migration-103 impersonation boundary). They were deleted, not renamed (owner decision,
2026-10-02): the ViewModel defaults to `[]`, and the two previews build one neutral sample
inline.

The literal scan reuses `test_legal_pages._swift_user_facing_strings` — it decodes `\\u{…}` and
`\\"` escapes, which a hand-written literal regex silently skipped (11 literals per legal file
went unscanned) — over COMMENT-STRIPPED source, because a doc comment may legitimately say what
used to be there. The names come from the shared `_persona_name_guard`, case-insensitive on word
boundaries, so a persona KEY like "warren_buffett" or an asset name like
"icon_persona_buffett" (an underscore is a word character) is not copy and does not trip it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import _persona_name_guard as guard
from test_ios_report_chat_agent_mode import _strip_comments  # string-aware; trailing `//` too
from test_legal_pages import _swift_user_facing_strings

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_RESEARCH_MODELS = _IOS / "Models/ResearchModels.swift"
_RESEARCH_VM = _IOS / "ViewModels/ResearchViewModel.swift"
_INSIGHT_ROW = _IOS / "Views/Molecules/CommunityInsightRow.swift"
_INSIGHTS_SECTION = _IOS / "Views/Organisms/CommunityInsightsSection.swift"

# The persona and chat-mode surfaces this change owns.
_SCANNED = [
    _RESEARCH_MODELS,
    _INSIGHT_ROW,
    _INSIGHTS_SECTION,
    _IOS / "Models/ChatConversationModels.swift",
    _IOS / "Views/Molecules/GroundedContextChip.swift",
]

# A product endorsement or a suitability claim, in any of those literals.
_ENDORSEMENT_OR_SUITABILITY = re.compile(
    r"highly recommend|worth every credit|nailed (?:the|it)|spot on"
    r"|\b(?:ideal|suited|suitable|perfect|right) for\b|\bconservative investors?\b",
    re.IGNORECASE,
)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {prefix!r}")


def _literals(path: Path, tmp_path: Path) -> str:
    """The shared Swift-literal scanner, run over the comment-stripped source."""
    probe = tmp_path / f"stripped_{path.name}"
    probe.write_text(_code(path), encoding="utf-8")
    return _swift_user_facing_strings(probe)


# ── the invented testimonials are gone ───────────────────────────────────────

def test_the_mock_testimonials_are_deleted_everywhere():
    users = [
        p.relative_to(_REPO) for p in _IOS.rglob("*.swift")
        if "mockInsights" in p.read_text(encoding="utf-8")
    ]
    assert not users, f"the invented testimonials are back: {users}"


def test_the_community_feed_defaults_to_empty():
    vm = _code(_RESEARCH_VM)
    assert re.search(r"@Published var communityInsights: \[CommunityInsight\] = \[\]", vm), (
        "the release-build default must be empty — never compiled-in sample posts"
    )


def test_no_sample_posts_are_compiled_into_the_model():
    model = _decl_block(_code(_RESEARCH_MODELS), "struct CommunityInsight: Identifiable")
    assert not re.search(r"\bstatic (?:let|var)\b", model), (
        "a static sample list on the model ships in release builds; build previews inline"
    )


@pytest.mark.parametrize("path", [_INSIGHT_ROW, _INSIGHTS_SECTION], ids=lambda p: p.name)
def test_each_preview_builds_its_own_neutral_sample(path):
    src = _code(path)
    preview = _decl_block(src, "#Preview")
    assert "CommunityInsight(" in preview, "build one neutral sample inline"
    assert "CommunityInsight." not in preview, "no shared static sample"


# ── no name, no endorsement, no suitability in the copy ──────────────────────

@pytest.mark.parametrize("path", _SCANNED, ids=lambda p: p.name)
def test_no_literal_names_a_real_investor_or_endorses_the_product(path, tmp_path):
    text = _literals(path, tmp_path)
    assert text.strip(), f"no literals scanned in {path.name} — the scanner went vacuous"
    names = guard.name_violations(text)
    assert not names, f"{path.name}: a real investor's name in shipped copy: {names}"
    claims = [m.group(0) for m in _ENDORSEMENT_OR_SUITABILITY.finditer(text)]
    assert not claims, f"{path.name}: an endorsement or suitability claim: {claims}"


def test_the_scan_catches_a_planted_testimonial_and_spares_keys(tmp_path):
    """Anti-vacuity: the old testimonial is caught (inside an escaped literal, too), while a
    persona key and an asset name — not copy — pass."""
    planted = tmp_path / "Planted.swift"
    planted.write_text(
        'let key = "warren_buffett"\n'
        'let icon = "icon_persona_buffett"\n'
        'let post = "The \\u{201C}Cathie Wood\\u{201D} persona nailed the analysis. Highly recommend!"\n'
        '// "Peter Lynch" in a comment is not copy\n'
        'let note = "a neutral line of copy" // "Michael Burry" in a TRAILING comment is not either\n',
        encoding="utf-8",
    )
    text = _literals(planted, tmp_path)
    assert guard.name_violations(text) == ["Cathie Wood"]
    claims = [m.group(0).lower() for m in _ENDORSEMENT_OR_SUITABILITY.finditer(text)]
    assert claims == ["nailed the", "highly recommend"]
