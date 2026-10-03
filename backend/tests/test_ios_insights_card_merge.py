"""Source-scan guards: the Updates Insights card has no "Why it moved" row any more.

The card used to lead its body with a grounded "why it moved" catalyst (a bolt + "Why it
moved" in the header, `InsightCatalystBullet` as the first bullet, `InsightPriceMove` on the
model). That catalyst came from a Google Search grounded answer, retired 2026-10-02: its terms
forbid caching a grounded answer or showing it to anyone but the user who asked. The backend
serves `"price_move": null` on every card (`news_insight_service._row_to_card`), so the row
could never render again — and it was removed rather than left as a dormant branch that a
future payload could switch back on.

What stays: `AIInsightCardDTO.priceMove` / `PriceMoveDTO`, OPTIONAL, so any payload still
carrying the key decodes (a non-optional field there would blank the whole Insights sheet).
It is decoded and never mapped.

Comments are stripped before every assertion (`.claude/rules/testing.md` §3): the comments
around this code quote "price_move" and the retired names, and an un-stripped scan would trip
or pass on prose. `test_the_scanners_are_not_vacuous` proves the helpers bite.
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

# Every token the retired row was built from.
_RETIRED = ("Why it moved", "bolt.fill", "InsightCatalystBullet", "InsightPriceMove",
            "priceMove", "whyItMovedRow", "catalyst")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. See the module docstring."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("///") or line.strip().startswith("//"):
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


# ── 1. Neither surface renders the retired row ─────────────────────────


@pytest.mark.parametrize("path", [_CARD, _DETAIL], ids=["card", "detail"])
def test_no_surface_renders_a_why_it_moved_row(path):
    """Whole file, comments stripped — previews included: a preview that still built the
    row would need the model type back."""
    src = _strip_comments(path.read_text())
    for token in _RETIRED:
        assert token not in src, (
            f"{path.name} references {token!r} again — the grounded 'why it moved' row was "
            "retired 2026-10-02 and the backend never sends one"
        )


def test_the_retired_view_and_model_are_gone():
    assert not _BULLET.exists(), "InsightCatalystBullet.swift is back"
    models = _strip_comments(_MODELS.read_text())
    assert "struct InsightPriceMove" not in models
    summary = _decl_block(_MODELS.read_text(), "struct NewsInsightSummary: Identifiable")
    assert "priceMove" not in summary, "NewsInsightSummary carries a price move again"
    mapping = _decl_block(_MODELS.read_text(), "init?(dto: AIInsightCardDTO)")
    assert "priceMove" not in mapping and "PriceMove" not in mapping, (
        "the DTO's price_move is mapped onto the card again"
    )


# ── 2. …but a payload that still carries the key decodes ──────────────


def test_the_dto_still_decodes_price_move_as_optional():
    """A non-optional field would fail the whole card's decode on `"price_move": null`."""
    dto = _decl_block(_MODELS.read_text(), "struct AIInsightCardDTO: Codable")
    assert re.search(r"\blet priceMove: PriceMoveDTO\?", dto), (
        "AIInsightCardDTO.priceMove is gone or no longer Optional"
    )
    assert 'case priceMove = "price_move"' in dto
    pm = _decl_block(_MODELS.read_text(), "struct PriceMoveDTO: Codable")
    assert "let changePercent: Double?" in pm and "let catalystTag: String?" in pm


# ── 3. The bullet cap ──────────────────────────────────────────────────


@pytest.mark.parametrize("path", [_CARD, _DETAIL], ids=["card", "detail"])
def test_the_card_never_grows_past_five_body_rows(path):
    """The trim keeps the LAST bullet (the conclusion): `prefix` used to drop it and put
    the arrow on a plain fact."""
    body = _decl_block(path.read_text(), "private var visibleBullets")
    assert "keepingConclusion(limit: 5)" in body, "the bullet cap is gone or changed"
    assert ".prefix(" not in body, "a plain prefix cap is back — it cuts off the conclusion"


def test_card_and_detail_trim_bullets_identically():
    card = re.sub(r"\s+", " ", _decl_block(_CARD.read_text(), "private var visibleBullets"))
    detail = re.sub(r"\s+", " ", _decl_block(_DETAIL.read_text(), "private var visibleBullets"))
    assert card == detail


# ── 4. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    """Prove the helpers bite: a guard that passes on prose proves nothing."""
    assert _strip_comments("// InsightCatalystBullet(move)\ncode()") == "code()"
    assert _strip_comments("code() // Why it moved") == "code()"
    assert _strip_comments("/// Text(\"Why it moved\")\nreal()") == "real()"

    fake = 'struct X {\n  var body: some View {\n    A()\n  }\n}\nfunc other() { bolt.fill }'
    block = _decl_block(fake, "var body: some View")
    assert "A()" in block and "bolt.fill" not in block, "_decl_block leaked past the declaration"

    for path in (_CARD, _DETAIL, _MODELS):
        assert path.exists(), f"{path} moved — every scan above would silently pass"
