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


def test_placeholder_topics_are_disjoint_from_authored_articles():
    """A slug in both tables would carry two different subjects and whichever won would be
    luck. And once a teaser IS authored it must move into ARTICLES, because from then on the
    DB row is what iOS reads and the Swift static is dead."""
    authored = _authored_slugs()
    assert not (set(art.PLACEHOLDER_TOPICS) & set(art.ARTICLES))
    promoted = set(art.PLACEHOLDER_TOPICS) & authored
    assert not promoted, f"{sorted(promoted)} are authored now — promote them into ARTICLES"


def test_placeholder_subjects_obey_the_same_rules():
    for slug, (category, treatment, subject) in art.PLACEHOLDER_TOPICS.items():
        assert category in art.PALETTES, f"{slug}: unknown category"
        assert treatment in art.TREATMENTS, f"{slug}: unknown treatment"
        hit = art._BANNED_SUBJECT.search(subject)
        assert hit is None, f"{slug}: subject names {hit.group(0)!r}"
        assert len(subject) > 40, f"{slug}: subject too thin"


def test_every_placeholder_card_in_swift_has_a_generated_plate():
    """The teaser URL is COMPILED into MoneyMove.sampleData rather than served, so nothing at
    runtime can notice a typo — the image just silently 404s to the gradient. This is the
    only thing standing between the two lists.

    It also pins the slugs themselves. A placeholder's slug is the one its article will take
    when written, so a mismatch here means the future row and the already-published plate end
    up at different paths.

    ⚠️ The mechanism is RETIRED as of 2026-08-21 — all seven teasers were authored and
    promoted into ARTICLES, so `PLACEHOLDER_TOPICS` is empty and Swift carries no
    `placeholderArt(...)` calls. This test must NOT become a skip: an empty generator table
    with live Swift teasers (or the reverse) is exactly the drift it exists to catch, so the
    retired state is asserted in BOTH directions instead. Delete the branch, not the test,
    if teasers ever come back.
    """
    swift = _strip_swift_comments(
        (REPO / "frontend/ios/ios/Models/LearnModels.swift").read_text())
    sample = swift.split("static let sampleData: [MoneyMove]", 1)[1].split("\n    ]", 1)[0]
    used = set(re.findall(r'placeholderArt\("([^"]+)"\)', sample))

    if not art.PLACEHOLDER_TOPICS:
        assert not used, (
            "PLACEHOLDER_TOPICS is empty but MoneyMove.sampleData still compiles in teaser "
            f"artwork for {sorted(used)} — those cards have no article and no generator "
            "entry, so they render the fabricated filler article")
        assert "func placeholderArt" not in swift, (
            "placeholderArt() survives with no teaser using it — remove it, or the next "
            "person will wire a card to it without a generator entry")
        return

    assert used, "no placeholder cards reference artwork at all"

    assert used == set(art.PLACEHOLDER_TOPICS), (
        f"Swift and the generator disagree — only in Swift: "
        f"{sorted(used - set(art.PLACEHOLDER_TOPICS))}, only in the generator: "
        f"{sorted(set(art.PLACEHOLDER_TOPICS) - used)}")

    # Each of those cards must also carry the matching slug, or completion tracking breaks:
    # MoneyMovesProgressStore keys on slug, so cards sharing the default "" would all be
    # marked complete together the first time any one of them was finished.
    for slug in used:
        assert f'slug: "{slug}"' in sample, f"{slug} has artwork but no slug on its card"


def test_placeholder_art_url_points_at_the_public_bucket():
    """Retired with the teaser mechanism (2026-08-21). Guarded, not skipped: if
    `placeholderArt` ever returns it must still point at the PUBLIC bucket, and the
    disjointness test above is what pins the empty-table state itself."""
    swift = (REPO / "frontend/ios/ios/Models/LearnModels.swift").read_text()
    if "private static func placeholderArt" not in swift:
        assert not art.PLACEHOLDER_TOPICS, (
            f"the generator still lists teasers {sorted(art.PLACEHOLDER_TOPICS)} but Swift "
            "has no placeholderArt() to render them")
        return
    fn = swift.split("private static func placeholderArt", 1)[1].split("\n    }", 1)[0]
    assert "/storage/v1/object/public" in fn, "teaser art must use the PUBLIC path"
    assert BUCKET in fn
    assert "/articles/" in fn and ".card.jpg" in fn
    assert "?token" not in fn, "artwork is never signed — see migration 137"


def test_palette_override_replaces_only_its_own_slug():
    """The override exists because the category palette is a default, not a law — a luxury
    house rendered in Blueprints' gunmetal reads as a hardware company. It must not touch any
    other article's prompt, because that would change its hash and re-roll approved art."""
    for slug in art.PALETTE_OVERRIDES:
        assert slug in art.ARTICLES or slug in art.PLACEHOLDER_TOPICS, (
            f"{slug} has a palette override but no subject anywhere")
        assert art.PALETTE_OVERRIDES[slug] not in art.PALETTES.values()
        assert art.PALETTE_OVERRIDES[slug] in art.prompt_for_slug(slug)
    # An article WITHOUT an override still gets its category brief, byte for byte.
    untouched = next(s for s in art.ARTICLES if s not in art.PALETTE_OVERRIDES)
    cat = art.ARTICLES[untouched][0]
    assert art.PALETTES[cat] in art.prompt_for_slug(untouched)


def test_prompt_for_default_palette_is_byte_identical_to_the_old_signature():
    """`palette` was added as an OPTIONAL parameter specifically so every prompt written
    before overrides existed still hashes the same. If that ever stops being true, every
    approved plate silently re-rolls on the next run."""
    subject, cat, tr = "a thing", "battles", "cutout_grid"
    assert art.prompt_for(subject, cat, tr) == art.prompt_for(subject, cat, tr, None)
    assert art.PALETTES[cat] in art.prompt_for(subject, cat, tr)


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


def test_ios_card_always_draws_a_cover_plate():
    """The plate is UNCONDITIONAL — no `if`, no `showIcon`, no category-badge branch.

    Previously the cover was drawn only `if let imageUrl`, with a badge as the else-branch and
    that badge itself gated on `showIcon`. The See-All carousels pass no artwork before the
    backend prefetch resolves (the bundled money_moves.json carries none) AND used to pass
    `showIcon: false`, so every card there took NEITHER branch: it began at the title and then
    reflowed taller when the art landed. MoneyMoveCoverImage already falls back to the
    category gradient for a nil url, so drawing it unconditionally makes all three states —
    absent, loading, arrived — occupy the same 16:9 box.
    """
    src = _strip_swift_comments(CARD_SWIFT.read_text())
    assert "MoneyMoveCoverImage" in src, "MoneyMoveCard no longer renders MoneyMoveCoverImage"

    # Nothing may stand between the VStack opening and the atom — that is what "unconditional"
    # means here, and an `if` re-appearing is exactly the regression.
    body = src.split("VStack(alignment: .leading", 1)[1].split("MoneyMoveCoverImage", 1)[0]
    assert "if " not in body, (
        f"the cover plate is conditional again — found a branch before it: {body.strip()!r}")

    # MoneyMoveCard must not DECLARE a showIcon of its own — it had no reader, and a parameter
    # that is accepted and ignored lies to its callers.
    #
    # Scoped to the declaration, not to the bare string: `ReadTimeLabel` has its own unrelated
    # `showIcon:`, which the meta row legitimately passes when ViewThatFits falls back to the
    # compact layout. Asserting the string never appears conflated the two and failed on
    # correct code.
    assert "var showIcon" not in src, (
        "showIcon is back as a property on MoneyMoveCard")
    struct_head = src.split("struct MoneyMoveCard: View {", 1)[1].split("var body", 1)[0]
    assert "showIcon" not in struct_head, (
        f"showIcon reappeared among MoneyMoveCard's stored properties: {struct_head.strip()!r}")
    # url: is passed straight through, Optional and all — not force-unwrapped behind a guard.
    assert "url: moneyMove.imageUrl" in src


def test_no_caller_passes_showIcon_to_a_money_move_card():
    """A leftover `showIcon:` argument is a compile error, but the failure message points at
    the call site rather than the reason — so pin the reason here."""
    offenders = []
    for path in (REPO / "frontend/ios/ios/Views").rglob("*.swift"):
        src = _strip_swift_comments(path.read_text())
        for chunk in src.split("MoneyMoveCard(")[1:]:
            if "showIcon" in chunk.split(")", 1)[0]:
                offenders.append(path.name)
    assert not offenders, f"MoneyMoveCard no longer takes showIcon; still passed by {offenders}"


def test_the_card_model_carries_no_dead_audio_flag():
    """`MoneyMove.hasAudio` drove the headphones badge. The badge is gone (pinned by the test
    below), so the flag became write-only: set by toCard(), read by nothing. A model field
    nothing reads is a false promise to the next person who greps for it."""
    models = _strip_swift_comments((REPO / "frontend/ios/ios/Models/LearnModels.swift").read_text())
    dto = _strip_swift_comments(MODELS_SWIFT.read_text())
    card_struct = models.split("struct MoneyMove:", 1)[1].split("\n}", 1)[0]
    assert "hasAudio" not in card_struct, "MoneyMove.hasAudio is back but still has no reader"
    assert "hasAudio:" not in dto, "toCard() still writes the dead hasAudio flag"
    # The live flag on the ARTICLE model is a different thing and must survive.
    assert "hasAudioVersion" in dto


def test_ios_card_meta_row_carries_the_completion_mark_and_no_headphones():
    """The completion mark moved down into the meta row and the headphones badge went
    entirely — they cost a whole header band for two glyphs, and a headphones badge that is
    present on all thirteen narrated articles distinguishes nothing."""
    src = _strip_swift_comments(CARD_SWIFT.read_text())
    assert "headphones" not in src, "the headphones badge is back on the card"
    assert "checkmark.circle.fill" in src
    meta = src.split("ReadTimeLabel", 1)[1]
    assert "checkmark.circle.fill" in meta, (
        "the completion mark must sit in the meta row, after ReadTimeLabel")


def test_ios_card_ordering_is_purely_newest_first():
    """Ordering is by DATE ALONE, on both surfaces, because the section says "Most Recent".

    This replaces an unread-first partition (completed moves slid to the tail, newest-first
    nested inside each group). The two keys disagreed the moment a reader finished the newest
    article: it left the front of a row that claims to be ordered by recency. Completion
    feedback is now the checkmark alone.

    The tiebreak survives: `sorted(by:)` is not stable in Swift, so same-day articles would
    otherwise shuffle between renders.
    """
    vm = _strip_swift_comments(
        (REPO / "frontend/ios/ios/ViewModels/LearnViewModel.swift").read_text())
    assert "static func newestFirst" in vm
    body = vm.split("static func newestFirst", 1)[1].split("\n    }", 1)[0]
    assert "createdAt >" in body, "newestFirst must sort DESCENDING by createdAt"
    assert "$0.title <" in body, (
        "needs a deterministic tiebreak — Swift's sorted(by:) is not stable")

    assert "sortedIncompleteFirst" not in vm, (
        "the unread-first partition is back — the row would stop matching its 'Most Recent' "
        "label as soon as anything is completed")
    # No partition on completion anywhere in the ordering path.
    loader = vm.split("private func loadMoneyMoves", 1)[1].split("\n    }", 1)[0]
    assert "isCompleted" not in loader, "loadMoneyMoves partitions on completion again"
    assert "newestFirst" in loader

    # And the See-All screen must share the very same sorter rather than reimplementing it.
    detail = _strip_swift_comments(
        (REPO / "frontend/ios/ios/Views/Screens/MoneyMovesDetailView.swift").read_text())
    assert "LearnViewModel.newestFirst" in detail
    assert "incompleteFirst" not in detail, (
        "the See-All screen reintroduced its own unread-first partition; the two surfaces must "
        "not drift into disagreeing about the order of the same cards")


def test_ios_section_subtitle_matches_the_ordering():
    """The label and the sort are one claim. "Most Read" was wrong twice over — nothing counts
    reads, and the order changed under the reader as they completed things."""
    section = _strip_swift_comments(
        (REPO / "frontend/ios/ios/Views/Organisms/MoneyMovesSection.swift").read_text())
    assert 'Text("Most Recent")' in section, "the Money Moves section subtitle is not Most Recent"
    assert "Most Read" not in section, "the Money Moves section still claims Most Read"


def test_ios_card_shows_a_publication_date_before_the_read_time():
    """The date is the first thing in the meta row, and it is OMITTED when unknown.

    That omission is live behaviour, not a defensive branch: seven "coming soon" placeholder
    cards ship with `createdAt == .distantPast`, which any naive format renders as "Jan 1, 1".
    """
    src = _strip_swift_comments(CARD_SWIFT.read_text())
    assert "MoneyMoveDateFormatting.label" in src, "the card no longer formats a date"
    assert "TimeAgoLabel" in src, "the date is not rendered through the shared atom"

    # Position: the date must precede the read time in the row.
    meta = src.split("MoneyMoveDateFormatting.label", 1)[1]
    assert "ReadTimeLabel" in meta, "the date label is not before ReadTimeLabel"

    # Optionality: an `if let` (or `guard`) around it, so nil means no label at all.
    assert "if let" in src.split("MoneyMoveDateFormatting.label", 1)[0].rsplit("\n", 3)[-1] \
        or "if let date = MoneyMoveDateFormatting.label" in src, (
        "the date must be conditionally rendered — .distantPast formats as 'Jan 1, 1'")

    # Ink parity with the label it sits beside.
    assert "color: AppColors.textSecondary" in src, (
        "the date must take ReadTimeLabel's ink; two greys in a two-item row reads as a bug")


def test_ios_card_meta_row_survives_large_dynamic_type():
    """The row overflows at xLarge — a NON-accessibility size — because AppSpacing is unscaled
    while the text scales to readingCap 1.4x. Inside a horizontal ScrollView that wraps
    silently (every card grows a line together) rather than clipping visibly."""
    src = _strip_swift_comments(CARD_SWIFT.read_text())
    assert "ViewThatFits" in src, "the meta row has no fallback layout for large text"
    # The two candidates, in order: full first (preferred), compact second.
    fits = src.split("ViewThatFits", 1)[1].split("\n    }", 1)[0]
    assert ".full" in fits and ".short" in fits, (
        f"both a full and an abbreviated candidate must be offered, got: {fits.strip()!r}")
    assert fits.index(".full") < fits.index(".short"), (
        "the FULL candidate must come first — ViewThatFits takes the first that fits")
    assert "showClock: false" in fits, (
        "the compact candidate must drop the clock glyph — that is where its width comes from")
    # ...and the flag actually has to reach the atom.
    assert "showIcon: showClock" in src, "showClock is computed but never passed to ReadTimeLabel"


def test_the_dead_learner_count_branch_is_gone():
    """`learnerCount` is always empty (the backend never writes it; two test files forbid
    non-blank engagement numbers), and the badge needed ~184pt in a 176pt row. A branch that
    can only ever break the layout is worse than no branch."""
    src = _strip_swift_comments(CARD_SWIFT.read_text())
    assert "LearnerCountBadge" not in src, "the dead learner-count badge is back on the card"
    assert "learnerCount" not in src


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


def test_prompt_hash_matches_every_generated_manifest():
    """THE re-roll guard. Every plate is paid and nondeterministic, so an approved one can
    never be recovered — only replaced by a different picture.

    `generate()` decides a slug is up to date by comparing sha1(prompt)[:12] against the
    manifest's `art_prompt_sha1`. So any edit to BASE, TREATMENTS, PALETTES, a subject string
    or a palette override silently re-rolls every plate it touches on the next run. That
    includes a promotion from PLACEHOLDER_TOPICS into ARTICLES: moving the entry is free,
    but retyping the subject instead of moving it is not.

    Drift here means "the next generate run will spend money and change approved artwork".
    """
    import hashlib

    mans = _manifests()
    if not mans:
        pytest.skip("no art generated yet")
    drift = []
    for man in mans:
        recorded = man.get("art_prompt_sha1")
        if not recorded:
            continue
        slug = man["slug"]
        current = hashlib.sha1(art.prompt_for_slug(slug).encode()).hexdigest()[:12]
        if current != recorded:
            drift.append(f"{slug}: manifest {recorded} != current {current}")
    assert not drift, (
        "the prompt changed for an already-generated plate — the next generate run would "
        "re-roll it at full cost:\n  " + "\n  ".join(drift))


def test_scratch_art_dirs_are_ignored_and_shipping_plates_are_not():
    """`_compare/`, `_prev/` and `_style/` are scratch; the plates and manifests are not.

    `_prev/` matters most: generate() writes the outgoing master there on EVERY re-roll, so
    with no rule each regeneration quietly adds another ~1 MB blob to history forever.

    ⚠️ `*.subject.txt` must NOT be ignored — resolve() reads it back for any --auto-written
    article, which makes it an input to art_prompt_sha1. Ignoring it would re-roll that plate
    on a fresh clone, i.e. the exact failure the test above exists to catch.

    `--no-index` is required: without it `git check-ignore` reports a TRACKED file as
    not-ignored regardless of the rules, so this test would pass vacuously until someone
    actually untracks the directories.
    """
    import subprocess

    def ignored(rel: str) -> bool:
        r = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", rel],
            cwd=REPO, capture_output=True,
        )
        if r.returncode not in (0, 1):
            pytest.skip(f"git check-ignore unavailable: {r.stderr.decode().strip()}")
        return r.returncode == 0

    base = "backend/data/money_moves_art"
    for scratch in ("_compare/x.jpg", "_compare/_rejected/x.jpg", "_prev/x.art.jpg",
                    "_style/index.json"):
        assert ignored(f"{base}/{scratch}"), f"{scratch} should be gitignored scratch"
    for shipped in ("the-fall-of-enron.art.jpg", "the-fall-of-enron.hero.jpg",
                    "the-fall-of-enron.card.jpg", "the-fall-of-enron.manifest.json",
                    "the-fall-of-enron.subject.txt"):
        assert not ignored(f"{base}/{shipped}"), (
            f"{shipped} is gitignored but it ships — a fresh clone could not reproduce or "
            f"re-upload the plate")


def test_light_ground_plates_are_reported_so_the_hairline_can_be_drawn():
    """Not a failure — a rendering fact. Most plates have a near-white ground and rely on
    AppColors.cardEdge to separate the card from the light-mode page."""
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


def test_ios_dto_decodes_published_at_and_both_paths_consume_it():
    """The real timestamp has to survive the wire AND reach both surfaces.

    Three edits are required and any one missing is silent: the property, the CodingKeys entry
    (a key absent there is NEVER decoded — same trap as the image keys), and a lenient decode.

    And it must feed BOTH `toCard()` and `toArticle()`. They used to derive the date separately
    from `publishedDaysAgo`, with a comment claiming "a card and the article it opens can never
    disagree about how old the piece is" — a claim maintained by hand across two expressions.
    The card renders the date now, so a divergence would be visible rather than latent.
    """
    src = _strip_swift_comments(MODELS_SWIFT.read_text())
    keys_block = _article_dto_coding_keys()

    assert "publishedAt" in keys_block, "publishedAt missing from MoneyMoveArticleDTO.CodingKeys"
    assert "let publishedAt: String?" in src, "publishedAt must be Optional on the DTO"
    assert "c.flexibleString(forKey: .publishedAt)" in src, (
        "publishedAt must decode leniently")

    assert "var resolvedPublishedAt: Date" in src, (
        "the shared resolver is gone — the two paths will drift apart again")
    resolver = src.split("var resolvedPublishedAt: Date", 1)[1].split("\n    }", 1)[0]
    assert "MoneyMoveDateFormatting.parseISO8601" in resolver, (
        "the resolver must prefer the served timestamp")
    assert "publishedDaysAgo" in resolver, (
        "the derived estimate must remain as the fallback for bundled/pre-seeder content")

    for fn in ("func toArticle", "func toCard"):
        body = src.split(fn, 1)[1].split("\n    }", 1)[0]
        assert "resolvedPublishedAt" in body, f"{fn} does not use the shared resolver"
        assert "byAdding: .day" not in body, (
            f"{fn} still derives its own date — that is how the card and article drift apart")
