"""Source-scan guards: the Updates Insights card tells its story ONCE.

TestFlight read of the Updates screen (CRM card): *"we need somehow to combine them. they
look duplicate and redundancy. like right after 'Insights' add 'bolt icon' and 'Why it
moved?'."*

The duplication was structural, not a wording slip. The card's bullets and its "Why it moved"
block are produced by two INDEPENDENT model calls over the same day's evidence — the bullets
from the FMP news corpus (`news_insight_service`), the catalyst from a grounded web search
(`price_catalyst_service`) — and neither could see the other. On an earnings day both
independently wrote "beat on revenue, raised guidance", so the reader saw it twice. Worse, the
prompt's `price_line` invited it: it stated the exact move and then said "mention this ONLY if
the articles explain it", which on precisely those days is an instruction to write the
catalyst a second time.

Two halves, and BOTH are needed — either alone leaves the duplication in place:

  * backend — the catalyst is handed to the roll-up prompt, which is told the line is already
    on screen and must not be restated. Pinned in `test_news_insight_service.py`.
  * iOS (here) — the separate inset box is gone; the catalyst is the FIRST bullet of one
    continuous body, with the bolt + label moved into the card header.

The string the prompt quotes back to the model MUST be the string the user actually sees. If
`InsightPriceMove.displayLine` and `news_insight_service.catalyst_display_line` drift, the
prompt starts describing a sentence nobody reads and the de-duplication silently stops
working with every test still green — so the two are asserted to agree here.

Comments are stripped before every assertion: the comments beside this change quote
"Why it moved", "bolt.fill", `whyItMovedRow` and `InsightCatalystBullet` verbatim, and an
un-stripped scan would pass on that prose after the code was reverted
(`.claude/rules/testing.md` §3). `test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IOS = _ROOT / "frontend/ios/ios"
_CARD = _IOS / "Views/Organisms/InsightsSummaryCard.swift"
_DETAIL = _IOS / "Views/Screens/InsightsDetailView.swift"
_BULLET = _IOS / "Views/Molecules/InsightCatalystBullet.swift"
_MODELS = _IOS / "Models/UpdatesModels.swift"


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. See the module docstring."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("///") or line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped.

    Brace-bounding matters here specifically: this card file also contains three
    `#Preview` blocks that construct an `InsightPriceMove`, so a whole-file scan
    for the catalyst would pass on preview data alone.
    """
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


def _card_body() -> str:
    return _decl_block(_CARD.read_text(), "var body: some View")


# ── 1. One body: the inset box is gone ────────────────────────────────


def test_the_separate_why_it_moved_box_is_gone():
    """The box below the bullets is what read as a second card."""
    src = _strip_comments(_CARD.read_text())
    assert "whyItMovedRow" not in src, (
        "the inset 'Why it moved' block is back. It renders the catalyst a second time, "
        "below bullets that already say it — which is the duplication this change removed."
    )


def test_the_catalyst_is_the_first_bullet_of_the_body():
    body = _card_body()
    assert "InsightCatalystBullet(move: move)" in body, (
        "the catalyst is no longer rendered in the card body at all — a big mover would "
        "lose its grounded explanation entirely."
    )
    bullets_at = body.find("InsightCatalystBullet(move: move)")
    loop_at = body.find("ForEach(Array(visibleBullets")
    assert loop_at != -1, "the bullet loop no longer reads visibleBullets"
    assert bullets_at < loop_at, (
        "the catalyst must LEAD the body; rendering it after the bullets recreates the "
        "'two blocks saying the same thing' layout."
    )


def test_the_card_never_grows_past_five_body_rows():
    """The catalyst takes a bullet slot, so the model's budget shrinks by one."""
    body = _decl_block(_CARD.read_text(), "private var visibleBullets")
    assert "prefix(catalyst == nil ? 5 : 4)" in body, (
        "the bullet cap is gone or changed: with a catalyst the card would render "
        "1 + up to 5 = six rows."
    )


# ── 2. The header signposts it ────────────────────────────────────────


def test_the_header_carries_the_bolt_and_the_label():
    body = _card_body()
    header = body[: body.find("Text(summary.headline)")]
    assert 'Image(systemName: "bolt.fill")' in header, "the bolt icon is not in the header"
    assert 'Text("Why it moved")' in header, "the 'Why it moved' label is not in the header"


def test_the_header_label_is_gated_on_actually_having_a_catalyst():
    """Most tickers never move enough to earn one; their header stays 'Insights'."""
    body = _card_body()
    label_at = body.find('Text("Why it moved")')
    assert label_at != -1
    gate_at = body.rfind("if catalyst != nil {", 0, label_at)
    assert gate_at != -1, (
        "the header label is unconditional — a calm ticker would advertise an explanation "
        "the card does not contain."
    )


def test_the_catalyst_is_never_claimed_on_the_fallback_card():
    """The deterministic 'Latest headlines' card is not model-written and has no catalyst."""
    block = _decl_block(_CARD.read_text(), "private var catalyst: InsightPriceMove?")
    assert "summary.isAIGenerated" in block, (
        "the catalyst is no longer gated on isAIGenerated — the fallback card would show a "
        "cited explanation nothing generated."
    )


# ── 3. Card and tap-through cannot disagree ───────────────────────────


def test_the_detail_screen_renders_the_same_body():
    """It previously rendered NO price move, so opening Sources dropped the catalyst."""
    body = _decl_block(_DETAIL.read_text(), "private var summarySection")
    assert "InsightCatalystBullet(move: move)" in body, (
        "the Insights detail screen has stopped rendering the catalyst; tapping the card "
        "would silently lose it again."
    )
    assert "visibleBullets" in body, "the detail screen no longer applies the same bullet cap"


# ── 4. The prompt quotes what the user actually sees ──────────────────


def test_ios_and_backend_build_the_same_catalyst_line():
    """`_build_prompt` quotes this line back to the model as 'already on screen'."""
    from app.services.news_insight_service import catalyst_display_line

    swift = _decl_block(_MODELS.read_text(), "var displayLine: String")
    assert '"\\(tag) — \\(reason)"' in swift, (
        "iOS no longer renders '<tag> — <reason>'; the backend prompt quotes that exact "
        "shape as the line the reader can already see."
    )
    assert (
        catalyst_display_line({"catalyst_tag": "Q2 Beat", "reason": "EPS beat."})
        == "Q2 Beat — EPS beat."
    )
    assert catalyst_display_line({"catalyst_tag": None, "reason": "Sector selloff."}) == (
        "Sector selloff."
    ), "a tagless broad-market move must render the bare reason, with no dangling dash"
    assert catalyst_display_line({"catalyst_tag": "x", "reason": "  "}) == ""
    assert catalyst_display_line(None) == ""


def test_the_change_percent_direction_comes_from_the_number():
    """Colour must never disagree with the printed sign."""
    block = _decl_block(_BULLET.read_text(), "private var changeRun")
    assert "move.isPositive ? AppColors.bullish : AppColors.bearish" in block
    assert "move.formattedChange" in block, (
        "the change is no longer read through formattedChange, which is the non-finite guard"
    )


# ── 5. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    """Prove the helpers bite: a guard that passes on prose proves nothing."""
    assert _strip_comments("// whyItMovedRow(move)\ncode()") == "code()"
    assert _strip_comments("code() // whyItMovedRow") == "code()"
    assert _strip_comments("/// Text(\"Why it moved\")\nreal()") == "real()"

    fake = 'struct X {\n  var body: some View {\n    A()\n  }\n}\nfunc other() { bolt.fill }'
    block = _decl_block(fake, "var body: some View")
    assert "A()" in block and "bolt.fill" not in block, (
        "_decl_block leaked past the declaration; a whole-file scan would pass on the "
        "#Preview blocks that construct an InsightPriceMove."
    )

    for path in (_CARD, _DETAIL, _BULLET, _MODELS):
        assert path.exists(), f"{path} moved — every scan above would silently pass"
