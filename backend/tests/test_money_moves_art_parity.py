"""Money Moves cover artwork — the guard rails the pipeline leans on.

Four separate things are pinned here and they fail differently:

1. THE SUBJECT RULES. The object must be the topic's own thing, never a branded product or
   a person, and a Battles article must show TWO of them. These are product decisions that
   a text model writes unattended via --auto, so they need a machine check.

2. THE BUCKET INVARIANT. Artwork lives in the PUBLIC `money-moves-images` bucket and must
   never be signed. Narration is Pro/Max and its bucket went private in migration 128;
   artwork is FREE and must render for a locked, signed-out reader. Nothing else in the
   codebase fails if someone adds this bucket to 128's flip list or to _SIGNABLE_BUCKETS,
   which is the single mistake the whole design exists to prevent.

3. REDACTION MUST LEAVE ARTWORK ALONE. `redact_money_moves` strips narration for a locked
   caller. If an image key ever joins that list, free content silently disappears behind
   the paywall and the article renders its gradient with no error anywhere.

4. THE PLATES THEMSELVES, when they exist. Tests that need the generated JPEGs skip when
   they are absent, so a fresh clone without backend/data/money_moves_art/ still passes.

Category 1 (pure) — no network, no Supabase.
"""
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ART_DIR = BACKEND / "data/money_moves_art"
ARTICLES_JSON = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
MODELS_SWIFT = REPO / "frontend/ios/ios/Models/MoneyMovesContentModels.swift"
ATOM_SWIFT = REPO / "frontend/ios/ios/Views/Atoms/MoneyMoveCoverImage.swift"
CARD_SWIFT = REPO / "frontend/ios/ios/Views/Molecules/MoneyMoveCard.swift"
MIGRATION_128 = BACKEND / "database/migrations/128_learn_media_buckets_private.sql"
MIGRATION_137 = BACKEND / "database/migrations/137_money_moves_images.sql"

BUCKET = "money-moves-images"
PUBLIC_PREFIX = f"/storage/v1/object/public/{BUCKET}/articles/"

sys.path.insert(0, str(BACKEND / "scripts"))
import generate_money_moves_art as art  # noqa: E402


def _authored_slugs() -> set[str]:
    return {a["slug"] for a in json.loads(ARTICLES_JSON.read_text())["articles"]}


def _manifests() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(ART_DIR.glob("*.manifest.json"))]


def _strip_swift_comments(src: str) -> str:
    """A rule quoted in a `//` comment must never satisfy an assertion about the code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def _sql_body(path: Path) -> str:
    """Same idea for SQL: 137's header DISCUSSES 128's flip list at length, and 128's header
    names every bucket it does not touch. Either would satisfy a naive substring search."""
    return "\n".join(l for l in path.read_text().splitlines()
                     if not l.lstrip().startswith("--"))


# --------------------------------------------------------------------------
# The subject table
# --------------------------------------------------------------------------
def test_no_orphan_subject_entries():
    """A typo'd key generates art nothing points at. The reverse is legal on purpose —
    an article with no entry is exactly what --auto exists to fill."""
    orphan = set(art.ARTICLES) - _authored_slugs()
    assert not orphan, f"ARTICLES keys not present in money_moves.json: {sorted(orphan)}"


def test_every_entry_has_a_known_treatment_and_matching_category():
    meta = {a["slug"]: a for a in json.loads(ARTICLES_JSON.read_text())["articles"]}
    for slug, (category, treatment, subject) in art.ARTICLES.items():
        assert treatment in art.TREATMENTS, f"{slug}: unknown treatment {treatment!r}"
        assert category in art.PALETTES, f"{slug}: unknown category {category!r}"
        assert meta[slug]["category"] == category, (
            f"{slug}: art table says {category!r}, money_moves.json says "
            f"{meta[slug]['category']!r} — the plate would be lit for one section and "
            f"filed under another")
        assert len(subject) > 40, f"{slug}: subject too thin to render: {subject!r}"


def test_battles_subjects_are_two_up():
    """Battles must show TWO of the product both rivals actually sell — "if we have coke and
    pepsi, then show 2 can of drink". One object, however good, cannot say "versus", and this
    is the rule most likely to be lost when a subject is rewritten or auto-written."""
    battles = {s: sub for s, (c, _t, sub) in art.ARTICLES.items() if c == "battles"}
    assert battles, "no battles articles — this test would pass vacuously"
    for slug, subject in battles.items():
        head = subject[:90].lower()
        assert head.startswith("two ") or head.startswith("the components of an"), (
            f"{slug}: a Battles subject must open by naming TWO objects, got: {subject[:90]!r}")


def test_battles_shape_instruction_demands_two_identical_objects():
    """The rule also has to reach the model that writes subjects unattended."""
    shape = art._SHAPE["battles"]
    assert "TWO IDENTICAL" in shape
    assert "same size" in shape and "unbranded" in shape


def test_no_brand_or_person_in_any_subject():
    """These articles are about Amazon, Apple, Tesla and Visa, and about Enron, Theranos and
    FTX. A real logo or a recognisable person in the frame is a trademark/likeness problem,
    not a style problem — so the subject may never name one."""
    for slug, (_c, _t, subject) in art.ARTICLES.items():
        hit = art._BANNED_SUBJECT.search(subject)
        assert hit is None, f"{slug}: subject names {hit.group(0)!r}: {subject[:110]!r}"


def test_base_prompt_still_forbids_text_people_and_marks():
    """The plate carries no words on purpose: a re-roll redraws them, and the model cannot
    spell. Real type is composited afterwards."""
    base = art.BASE
    for token in ("NO text", "logos", "trademarks", "No human faces", "no people",
                  "completely blank"):
        assert token in base, f"BASE lost the {token!r} clause"


def test_every_treatment_is_actually_used():
    """A treatment nobody assigns is dead prose in the prompt table, and it silently rots."""
    used = {t for _c, t, _s in art.ARTICLES.values()}
    unused = set(art.TREATMENTS) - used
    assert not unused, f"unused treatments: {sorted(unused)}"


def test_treatment_spread():
    """Style varies per article by design. If one treatment swallows the set, the range that
    justifies having eleven of them is gone."""
    from collections import Counter

    counts = Counter(t for _c, t, _s in art.ARTICLES.values())
    worst, n = counts.most_common(1)[0]
    assert n <= max(2, len(art.ARTICLES) // 4), (
        f"{worst} is used {n}x of {len(art.ARTICLES)} — the set is collapsing to one look")


def test_auto_treatment_is_deterministic_and_safe():
    """A random treatment would change on every run, change the prompt hash with it, and
    re-roll the whole set at full cost. It must also never pick a treatment that cannot
    survive an unknown subject."""
    fragile = {"exploded_kit", "blueprint_cyanotype", "found_artefact", "specimen_plate"}
    for slug in ("some-new-article", "another-one", "third"):
        for category in art.PALETTES:
            first = art.default_treatment(slug, category)
            assert first == art.default_treatment(slug, category), "not deterministic"
            assert first in art.TREATMENTS
            assert first not in fragile - {"specimen_plate"}, (
                f"{first} needs an author-chosen subject and degrades badly on a guess")


# --------------------------------------------------------------------------
# The bucket invariant
# --------------------------------------------------------------------------
def test_money_moves_images_is_never_signable():
    """Artwork is FREE and its bucket is PUBLIC. Signing it would rotate the URL every few
    hours, defeat iOS URLCache, and re-download every plate several times a day — while
    narration, which is Pro/Max, genuinely needs signing."""
    from app.services import learn_audio_urls

    assert BUCKET not in learn_audio_urls._SIGNABLE_BUCKETS, (
        f"{BUCKET} must never be signable — see migration 137")
    assert "money-moves-media" in learn_audio_urls._SIGNABLE_BUCKETS, (
        "narration must stay signable — its bucket is private")


def test_migration_128_does_not_flip_the_image_bucket_private():
    body = _sql_body(MIGRATION_128)
    assert BUCKET not in body, (
        "128 makes Learn media buckets PRIVATE; adding the image bucket there would 404 "
        "every plate into the gradient fallback with no log and no toast")
    assert "money-moves-media" in body, "128 should still be flipping the narration bucket"


def test_migration_137_creates_a_public_bucket_and_the_column():
    body = _sql_body(MIGRATION_137)
    assert f"'{BUCKET}'" in body
    assert re.search(r"INSERT INTO storage\.buckets.*public", body, re.S)
    assert "true" in body.split("VALUES", 1)[1][:120], "bucket must be created PUBLIC"
    assert "money_moves_images_public_read" in body
    assert "money_moves_images_service_write" in body
    assert "ADD COLUMN IF NOT EXISTS image_url" in body


def test_seeder_targets_the_public_image_bucket():
    import seed_money_moves

    assert seed_money_moves.IMAGE_BUCKET == BUCKET
    assert seed_money_moves.IMAGE_PREFIX == "articles"
    assert seed_money_moves.IMAGE_BUCKET != seed_money_moves.BUCKET, (
        "artwork must not share the narration bucket — migration 128 flips that one private")


# --------------------------------------------------------------------------
# Redaction must leave artwork alone
# --------------------------------------------------------------------------
def test_image_keys_survive_redaction():
    """Narration is Pro/Max; ARTWORK IS FREE. A locked caller must still get every image
    key, or the paywall silently eats content it was never meant to gate."""
    from app.schemas.money_moves import MoneyMovesResponse
    from app.services.learn_audio_gate import redact_money_moves

    article = {
        "slug": "x", "title": "X", "subtitle": "s", "category": "valueTraps",
        "audioUrl": "https://example.com/a.m4a", "audioDurationSeconds": 12,
        "imageUrl": "https://example.com/x.hero.jpg",
        "imageCardUrl": "https://example.com/x.card.jpg",
        "heroGradientColors": ["DC2626", "991B1B"],
        "relatedArticles": [{"title": "Y", "imageCardUrl": "https://example.com/y.card.jpg"}],
        "sections": [{"title": "s", "content": [
            {"type": "paragraph", "text": "t", "readAlong": [{"text": "t", "start": 0, "end": 1}]}
        ]}],
    }
    out = redact_money_moves(MoneyMovesResponse(articles=[article]), "pro")
    got = out.articles[0]

    assert got["imageUrl"] == article["imageUrl"], "artwork was stripped from a locked caller"
    assert got["imageCardUrl"] == article["imageCardUrl"]
    assert got["relatedArticles"][0]["imageCardUrl"], "related-tile artwork was stripped"
    # ...and the narration really was withheld, or this test proves nothing.
    assert "audioUrl" not in got and "audioDurationSeconds" not in got
    assert "readAlong" not in got["sections"][0]["content"][0]


def test_image_keys_are_absent_from_the_audio_strip_lists():
    from app.services import learn_audio_gate

    keys = set(learn_audio_gate._MONEY_MOVES_ARTICLE_AUDIO_KEYS) | \
        set(learn_audio_gate._MONEY_MOVES_BLOCK_AUDIO_KEYS)
    for k in ("imageUrl", "imageCardUrl"):
        assert k not in keys, f"{k} must never be treated as narration"


def test_service_selects_and_overlays_the_image_column():
    src = (BACKEND / "app/services/money_moves_content_service.py").read_text()
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "image_url" in body.split(".select(", 1)[1][:200], (
        "image_url must be in the SELECT or the column can never reach the client")
    assert 'content["imageUrl"] = image_url' in body


# --------------------------------------------------------------------------
# The iOS contract
# --------------------------------------------------------------------------
def _article_dto_coding_keys() -> str:
    """The CodingKeys list belonging to MoneyMoveArticleDTO specifically.

    MoneyMovesContentModels.swift declares SEVEN CodingKeys enums, and the first one in the
    file belongs to `MoneyMovesContentFile` (`case version, articles`). Splitting on the
    bare string therefore reads the wrong struct and passes while asserting nothing — which
    is precisely what the first version of this test did, until
    test_source_scan_helpers_are_not_vacuous caught it.
    """
    src = _strip_swift_comments(MODELS_SWIFT.read_text())
    ext = src.split("extension MoneyMoveArticleDTO {", 1)
    assert len(ext) == 2, "MoneyMoveArticleDTO extension not found"
    return ext[1].split("private enum CodingKeys", 1)[1].split("}", 1)[0]


def test_ios_dto_declares_the_image_keys_and_decodes_them_leniently():
    """A key absent from CodingKeys is silently never decoded, and a strict decode would
    drop the whole article for a row that predates artwork."""
    src = _strip_swift_comments(MODELS_SWIFT.read_text())
    keys_block = _article_dto_coding_keys()
    for k in ("imageUrl", "imageCardUrl"):
        assert k in keys_block, f"{k} missing from MoneyMoveArticleDTO.CodingKeys"
        assert f"let {k}: String?" in src, f"{k} must be Optional on the DTO"
        assert f"{k} = c.flexibleString(forKey: .{k})" in src, (
            f"{k} must decode leniently — a strict decode drops the whole article")


def test_ios_card_art_is_not_gated_on_showIcon():
    """The See-All grid passes `showIcon: false` to suppress the category badge. Gating the
    plate on the same flag would leave the browsing screen the only one with no pictures."""
    src = _strip_swift_comments(CARD_SWIFT.read_text())
    art_block = src.split("MoneyMoveCoverImage", 1)[0]
    tail = art_block.rsplit("if let imageUrl", 1)
    assert len(tail) == 2, "MoneyMoveCard no longer renders MoneyMoveCoverImage"
    assert "showIcon" not in tail[1], (
        "artwork is gated on showIcon — See-All would render no plates")


def test_ios_cover_atom_falls_back_to_the_gradient_on_error():
    """A failed or missing plate must degrade to exactly the previous design, not to a hole.
    The AsyncImage error phase and the loading phase both have to reach `fallback`."""
    src = _strip_swift_comments(ATOM_SWIFT.read_text())
    assert "AsyncImage" in src
    phase = src.split("AsyncImage", 1)[1].split("private var fallback", 1)[0]
    assert "else {" in phase and "fallback" in phase, (
        "the non-image AsyncImage phases must fall back to the gradient")
    assert "AppColors.cardEdge" in src, (
        "the light-mode hairline is what separates a near-white plate from the #F4F5F8 page")


# --------------------------------------------------------------------------
# The plates themselves
# --------------------------------------------------------------------------
def test_manifests_carry_every_derivative():
    mans = _manifests()
    if not mans:
        pytest.skip("no art generated yet")
    for man in mans:
        masters = man.get("masters") or {}
        for name, (w, h) in art.DERIVATIVES.items():
            rec = masters.get(name)
            assert rec, f"{man['slug']}: manifest has no masters.{name}"
            assert (rec["width"], rec["height"]) == (w, h), (
                f"{man['slug']}.{name}: {rec['width']}x{rec['height']} != {w}x{h}")
            assert rec["sha256"], f"{man['slug']}.{name}: no content hash to skip uploads on"


def test_derivatives_share_the_master_aspect():
    """Both shipping files are 16:9 and so is the master, so a derivative is a pure
    downscale. If that ever stops being true, something is being cropped and the composition
    rule in the prompt no longer describes what ships."""
    aw, ah = (int(v) for v in art.ART_ASPECT.split(":"))
    for name, (w, h) in art.DERIVATIVES.items():
        assert abs((w / h) - (aw / ah)) < 0.01, f"{name} is not {art.ART_ASPECT}"


def test_plate_files_are_small_enough_for_the_bucket():
    mans = _manifests()
    if not mans:
        pytest.skip("no art generated yet")
    for man in mans:
        for name, rec in (man.get("masters") or {}).items():
            path = ART_DIR / rec["file"]
            if not path.exists():
                continue
            # Migration 137 caps the bucket at 2 MB; anything near it means the derive step
            # regressed, since these run 30-160 KB.
            assert path.stat().st_size < 900_000, f"{rec['file']} is {path.stat().st_size} B"


def test_light_ground_plates_are_reported_so_the_hairline_can_be_drawn():
    """Not a failure — a rendering fact. Eight of thirteen plates have a near-white ground
    and rely on AppColors.cardEdge to separate the card from the light-mode page."""
    mans = [m for m in _manifests() if m.get("plate")]
    if not mans:
        pytest.skip("no art generated yet")
    for man in mans:
        plate = man["plate"]
        assert "needs_hairline" in plate and "ring_median_luma" in plate
        assert plate["needs_hairline"] == (
            plate["ring_median_luma"] >= art.LIGHT_GROUND_LUMA)


# --------------------------------------------------------------------------
def test_source_scan_helpers_are_not_vacuous():
    """A source scan that finds nothing because it looked in the wrong place is worse than
    no test. Prove every window is real and non-empty, and that comment-stripping actually
    strips — 137's header discusses 128's flip list by name, which a naive substring search
    would happily accept as the code doing the right thing."""
    assert MODELS_SWIFT.exists() and ATOM_SWIFT.exists() and CARD_SWIFT.exists()
    assert MIGRATION_128.exists() and MIGRATION_137.exists()

    stripped = _strip_swift_comments("// let imageUrl: String?\nlet real: Int\n")
    assert "imageUrl" not in stripped and "real" in stripped

    # 137's PROSE names the private-flip migration; its BODY must not.
    assert "128" in MIGRATION_137.read_text()
    assert "money-moves-media" in MIGRATION_137.read_text()
    assert "money-moves-media" not in _sql_body(MIGRATION_137), (
        "comment stripping failed — the header's discussion of 128 is leaking into the body")

    # The DTO window must be the ARTICLE's CodingKeys, not the seven-way ambiguous first
    # match in the file. This assertion is what caught the original bug: the naive split
    # landed on MoneyMovesContentFile's `case version, articles`.
    keys_block = _article_dto_coding_keys()
    assert "slug" in keys_block and "heroGradientColors" in keys_block, (
        f"CodingKeys window is the wrong struct:\n{keys_block}")
    assert len(keys_block) > 120, f"CodingKeys window looks wrong:\n{keys_block}"
    naive = _strip_swift_comments(MODELS_SWIFT.read_text()) \
        .split("private enum CodingKeys", 1)[1].split("}", 1)[0]
    assert "slug" not in naive, (
        "the naive split now happens to hit the right struct — this meta-assertion has "
        "stopped proving anything and the helper's reason for existing should be re-checked")

    # The Battles two-object check must be looking at real subjects, not an empty dict.
    battles = [s for s, (c, _t, _sub) in art.ARTICLES.items() if c == "battles"]
    assert len(battles) >= 3, f"only {len(battles)} battles subjects to check"

    # And the deny-list must actually reject something, or it is decoration.
    assert art._BANNED_SUBJECT.search("a shelf of amazon boxes")
    assert art._BANNED_SUBJECT.search("two hands holding a coin")
    # ...while still allowing the legitimate senses that broke it once.
    assert not art._BANNED_SUBJECT.search("a coin standing on its edge, its face engraved")
    assert not art._BANNED_SUBJECT.search("a hand-cranked brass machine")
