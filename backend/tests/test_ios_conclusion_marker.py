"""Source-scan guards: the conclusion bullet is marked by an ICON, not by words.

TestFlight, build 1.0 (7), Updates -> Market card: *"For the conclusion, don't use words like
'takeaway or conclusion,…' use a simple icon or something. Users can know it right away, but
don't make it too fancy."*

The final bullet read *"The takeaway for everyday investors, While AI drives innovation…"* —
eleven words of scaffolding in front of the point, on a card built to be skimmed. It is now a
turn-down arrow (`arrow.turn.down.right`) and the words are gone.

WHY THE CLIENT-SIDE STRIP IS NOT OPTIONAL. Two backend prompts wrote that lead-in, and they
invalidate very differently:

  * `news_insight_service._build_prompt` -> the Insights card. Governed by PROMPT_VERSION, so a
    bump regenerates every cached card exactly once.
  * `news_cache_service._batch_enrich_articles` -> every per-article news card. **No version, no
    invalidation of any kind.** Re-enrichment is gated on the boolean `ai_processed`, and
    `expires_at` is re-stamped on every refresh, so an article that keeps circulating in the FMP
    feed keeps its old bullets indefinitely.

So changing the prompts alone would put the new icon directly in front of the words it replaces:
"-> The takeaway for everyday investors, While AI…". `strippingConclusionLeadIn()` covers that
backlog, exactly as `normalizingLeadInColon()` (its fallback) already did for colons.

BEHAVIOUR IS PROVEN SEPARATELY. There is no XCTest target, so these scans pin STRUCTURE only.
The stripper's logic was exercised through the `swift` interpreter over this table, all passing:

    "The takeaway for everyday investors, While AI drives…" -> "While AI drives…"
    "The takeaway: Investors should watch…"                 -> "Investors should watch…"
    "In short, watch two things: capex and buybacks"        -> "Watch two things: capex and buybacks"
    "So what: this changes very little…"                    -> "This changes very little…"
    "Sony, the electronics maker, raised…"                  -> UNCHANGED
    "So the Fed cut rates, and the market rallied…"         -> UNCHANGED
    "The takeaway, buy."                                    -> UNCHANGED (remainder too short)

The last three are the ones that matter: a raw prefix match on the stem `so` eats a company name
and a real sentence, and the failure would read as a model error rather than a formatting bug.

Comments are stripped before every assertion — the comments beside this change quote
"The takeaway,", "arrow.turn.down.right" and `strippingConclusionLeadIn` verbatim, so an
un-stripped scan would pass on prose after the code was reverted (`.claude/rules/testing.md` §3).
`test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IOS = _ROOT / "frontend/ios/ios"
_FORMAT = _IOS / "Core/Utilities/BulletTextFormatting.swift"
_GLYPH = _IOS / "Views/Atoms/SummaryBulletGlyph.swift"
_CARD = _IOS / "Views/Organisms/InsightsSummaryCard.swift"
_DETAIL = _IOS / "Views/Screens/InsightsDetailView.swift"
_NEWS_ATOM = _IOS / "Views/Atoms/NewsCardBulletPoint.swift"
_NEWS_LIST = _IOS / "Views/Molecules/TickerNewsExpandedContent.swift"
_NEWS_DETAIL_VM = _IOS / "ViewModels/NewsDetailViewModel.swift"
_CATALYST = _IOS / "Views/Molecules/InsightCatalystBullet.swift"

_INSIGHT_PROMPT = _ROOT / "backend/app/services/news_insight_service.py"
_ARTICLE_PROMPT = _ROOT / "backend/app/services/news_cache_service.py"

# Every opener the two prompts used to ask for, plus the form observed in production.
# The stripper must know all of them or the words survive on screen.
_OPENERS_THE_PROMPTS_ONCE_ASKED_FOR = [
    "the takeaway",
    "in short",
    "ultimately",
    "so",
    "so what",
]


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace : i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


# ── 1. The prompts stopped asking for it ──────────────────────────────


@pytest.mark.parametrize("path", [_INSIGHT_PROMPT, _ARTICLE_PROMPT],
                         ids=["insights-card", "per-article"])
def test_neither_prompt_still_requests_a_lead_in(path):
    src = path.read_text()
    assert "NO LEAD-IN" in src, (
        f"{path.name} no longer forbids the conclusion lead-in — the model will go back to "
        "writing 'The takeaway,' in front of a bullet the app already marks with an icon"
    )


def test_the_stem_list_covers_every_opener_the_prompts_once_asked_for():
    """If the prompts named it, the stripper must know it — cached text still has them."""
    src = _strip_comments(_FORMAT.read_text())
    missing = [o for o in _OPENERS_THE_PROMPTS_ONCE_ASKED_FOR
               if f'"{o.split()[-1]}"' not in src.lower()]
    assert not missing, f"stems missing for openers the prompts used to request: {missing}"
    # The production form: "The takeaway for everyday investors," — matched via the
    # open-noun class, which allows a continuation after the stem.
    assert "conclusionLeadInOpenNounStems" in src, (
        "the open-noun stem class is gone; only an exact 'The takeaway,' would match and the "
        "form actually seen in production ('The takeaway for everyday investors,') survives"
    )


# ── 2. The stripper's two safety rails ────────────────────────────────


def test_stems_are_matched_as_whole_words_not_as_a_prefix():
    """`Sony,` opens with the letters of the stem `so`."""
    body = _decl_block(_FORMAT.read_text(), "func strippingConclusionLeadIn")
    assert "split(whereSeparator:" in body and "isLetter" in body, (
        "the clause is no longer tokenised into words — a raw prefix test would strip the "
        "company name out of 'Sony, the electronics maker, …'"
    )


def test_the_exact_and_open_noun_classes_are_kept_separate():
    """`So,` is a lead-in; `So the Fed cut rates,` is a sentence."""
    body = _decl_block(_FORMAT.read_text(), "func strippingConclusionLeadIn")
    assert "conclusionLeadInExactStems.contains(words)" in body, (
        "the exact class no longer requires the stem to BE the whole clause, so 'So the Fed "
        "cut rates, and…' would have its first clause deleted"
    )
    assert "conclusionLeadInOpenNounStems" in body


def test_a_degenerate_remainder_is_left_alone():
    body = _decl_block(_FORMAT.read_text(), "func strippingConclusionLeadIn")
    assert "rest.count >= 20" in body, (
        "the short-remainder guard is gone — 'The takeaway, buy.' would render as 'Buy.' or, "
        "worse, blank the one line the reader most needs"
    )
    assert "normalizingLeadInColon()" in body, (
        "the colon fallback is gone; an unrecognised 'Bottom line: X' would render as a label"
    )


# ── 3. Every conclusion-rendering surface routes through it ───────────


@pytest.mark.parametrize(
    "path,header",
    [
        (_CARD, "var body: some View"),
        (_DETAIL, "private var summarySection"),
        (_NEWS_LIST, "var body: some View"),
        (_NEWS_DETAIL_VM, "private static func takeaways"),
    ],
    ids=["insights-card", "insights-detail", "news-card", "news-detail"],
)
def test_the_last_bullet_is_stripped_on_every_surface(path, header):
    block = _decl_block(path.read_text(), header)
    assert "strippingConclusionLeadIn()" in block, (
        f"{path.name} still renders the conclusion's lead-in words verbatim"
    )
    assert "normalizingLeadInColon()" not in block, (
        f"{path.name} still calls the old colon-only normaliser, which leaves the words in "
        "place next to the icon that replaced them"
    )


# ── 4. The marker itself ──────────────────────────────────────────────


def test_the_conclusion_marker_is_the_turn_down_arrow():
    block = _decl_block(_GLYPH.read_text(), "private var marker")
    assert 'Image(systemName: "arrow.turn.down.right")' in block
    # Reserved elsewhere: sparkle = AI provenance, bolt = "Why it moved",
    # lightbulb = the Wiser tab.
    for taken in ("sparkles", "bolt.fill", "lightbulb"):
        assert taken not in block, f"the conclusion marker collides with {taken!r}"


def test_the_glyph_centres_itself_on_the_neighbouring_text():
    """No hardcoded top padding: it is wrong for 12pt news cards and drifts at large type.

    Icons cap at 1.25x while reading text caps at 1.4x, so a constant tuned at the default
    size pulls apart as the user scales up. Sizing the column from an invisible line of the
    ADJACENT text re-centres it at every size, on every surface, with nothing to maintain.
    """
    body = _decl_block(_GLYPH.read_text(), "var body: some View")
    assert "Text(\" \")" in body and ".hidden()" in body and ".overlay(marker)" in body, (
        "the glyph no longer takes its height from the neighbouring text"
    )
    assert ".padding(.top," not in body, (
        "a hardcoded top padding is back — it cannot be correct for both the 14pt insight "
        "card and the 12pt news card, and it drifts at large Dynamic Type"
    )


@pytest.mark.parametrize(
    "path", [_CARD, _DETAIL, _NEWS_ATOM, _CATALYST],
    ids=["insights-card", "insights-detail", "news-atom", "catalyst"],
)
def test_no_surface_still_draws_its_own_bullet_dot(path):
    """One atom, or the four drift — which is how the skeleton/row pair diverged before."""
    src = _strip_comments(path.read_text())
    assert "SummaryBulletGlyph(" in src, f"{path.name} does not use the shared glyph"
    assert ".frame(width: 5, height: 5)" not in src, (
        f"{path.name} draws its own 5pt dot again; the conclusion arrow is ~2x that width, so "
        "a private dot means this surface's text column no longer lines up with the others"
    )


def test_the_news_card_atom_defaults_to_a_plain_bullet():
    """Its other callers must be unaffected by the new parameter."""
    src = _strip_comments(_NEWS_ATOM.read_text())
    assert "var isConclusion: Bool = false" in src, (
        "isConclusion lost its default, so every other NewsCardBulletPoint call site would "
        "have to opt out of being a conclusion"
    )


# ── 5. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    assert _strip_comments('// strippingConclusionLeadIn()\ncode()') == "code()"
    assert _strip_comments('code() // arrow.turn.down.right') == "code()"

    fake = 'struct X {\n  var body: some View {\n    A()\n  }\n}\nfunc other() { B() }'
    block = _decl_block(fake, "var body: some View")
    assert "A()" in block and "B()" not in block, "_decl_block leaked past the declaration"

    for path in (_FORMAT, _GLYPH, _CARD, _DETAIL, _NEWS_ATOM, _NEWS_LIST,
                 _NEWS_DETAIL_VM, _CATALYST, _INSIGHT_PROMPT, _ARTICLE_PROMPT):
        assert path.exists(), f"{path} moved — every scan above would silently pass"
