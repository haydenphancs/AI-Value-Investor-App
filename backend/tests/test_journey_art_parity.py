"""Journey lesson artwork — the guard rails the pipeline leans on.

Two things are pinned here, and they fail differently:

1. The ART ITSELF. Every hero renders on an always-white plate in iOS
   (AppColors.mediaSurface, #FFFFFF in both appearances), so a background that is
   merely NEAR white shows a visible rectangle seam inside the plate in dark mode.
   That is invisible in light mode and un-catchable by eye across 27 images, which is
   exactly why it is a test.

2. The BUCKET INVARIANT. Lesson artwork must stay in the PUBLIC `journey-images`
   bucket and must never be signed. Migration 136's header and
   JourneyContentStore.swift both cite this file by name as the enforcement — before
   it existed, nothing failed the build if someone added journey-images to migration
   128's private-flip list or to _SIGNABLE_BUCKETS, which is the single mistake the
   whole design exists to prevent. See test_journey_image_urls_are_never_signed.

Category 1 (pure) — no network, no Supabase. Tests that need the generated JPEGs skip
when they are absent, so a fresh clone without backend/data/journey_art/ still passes.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ART_DIR = BACKEND / "data/journey_art"
LESSONS_JSON = REPO / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
STORE_SWIFT = REPO / "frontend/ios/ios/Services/JourneyContentStore.swift"
COMPLETION_SWIFT = REPO / "frontend/ios/ios/Views/Molecules/LessonCompletionCard.swift"

sys.path.insert(0, str(BACKEND / "scripts"))
import generate_journey_art as art  # noqa: E402

HERO_W, HERO_H = 1536, 864
MAX_HERO_BYTES = 250 * 1024
BUCKET = "journey-images"
PUBLIC_PREFIX = f"/storage/v1/object/public/{BUCKET}/lessons/"


def _authored_slugs() -> set[str]:
    return {l["slug"] for l in json.loads(LESSONS_JSON.read_text())["lessons"]}


def _manifests() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(ART_DIR.glob("*.manifest.json"))]


def _derived_slug(title: str) -> str:
    """Reimplementation of JourneyContentStore.slug(_:) — lowercase, non-alphanumerics
    collapsed to underscores. Used only to prove the art table does NOT key on it."""
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


# --------------------------------------------------------------------------
# The subject table must cover exactly the lessons that exist
# --------------------------------------------------------------------------

def test_every_lesson_has_a_subject_entry():
    """A 28th lesson must never ship with a blank hero."""
    missing = _authored_slugs() - set(art.LESSONS)
    assert not missing, f"lessons with no artwork subject: {sorted(missing)}"


def test_no_orphan_subject_entries():
    """A typo'd key would generate art that nothing ever points at."""
    orphans = set(art.LESSONS) - _authored_slugs()
    assert not orphans, f"subjects for lessons that do not exist: {sorted(orphans)}"


def test_subject_keys_are_the_authored_slug_not_the_derived_slug():
    """Three slug spaces coexist and 14 of 27 disagree.

    The authored `slug` drives both the Storage path and the lessons row id (uuid5), so
    they cannot drift. The TITLE-derived slug is for bundled asset names only. Keying
    artwork on the derived one would 404 fourteen lessons — silently, because the iOS
    AsyncImage error phase just renders the placeholder.
    """
    lessons = json.loads(LESSONS_JSON.read_text())["lessons"]
    divergent = {l["slug"] for l in lessons if _derived_slug(l["title"]) != l["slug"]}
    assert len(divergent) >= 10, (
        "expected many authored/derived slug divergences; if this dropped to zero the "
        "trap is gone and this test is vacuous")
    for slug in divergent:
        assert slug in art.LESSONS, f"{slug} keyed on the derived slug, not the authored one"


def test_every_lesson_has_a_known_palette():
    for slug, (_level, palette, _subject) in art.LESSONS.items():
        assert palette in art.PALETTES, f"{slug} uses undefined palette {palette!r}"


def test_style_is_single_valued_across_the_set():
    """A set is only a set if one style made all of it."""
    mans = _manifests()
    if not mans:
        pytest.skip("no generated art on disk")
    styles = {m["style"] for m in mans}
    assert styles == {art.STYLE}, f"mixed styles in the set: {styles}"


def test_each_level_has_a_dominant_palette():
    """Colour carries the level. Overrides are deliberate and content-driven, but a
    level whose own colour is in the minority has lost the signal."""
    by_level: dict[str, list[str]] = {}
    for _slug, (level, palette, _s) in art.LESSONS.items():
        by_level.setdefault(level, []).append(palette)
    for level, palettes in by_level.items():
        want = art.LEVEL_PALETTE[level]
        on_theme = sum(1 for p in palettes if p.startswith(want))
        assert on_theme > len(palettes) / 2, (
            f"{level}: only {on_theme}/{len(palettes)} lessons carry {want}")


# --------------------------------------------------------------------------
# The rendered files
# --------------------------------------------------------------------------

def test_every_manifest_has_a_hero_master():
    mans = _manifests()
    if not mans:
        pytest.skip("no generated art on disk")
    for m in mans:
        assert (m.get("masters") or {}).get("hero"), f"{m['slug']}: no masters.hero"


def test_master_hashes_match_the_bytes_on_disk():
    """The manifest sha256 is the seeder's upload-skip key. If it drifts from the actual
    bytes, a re-rolled image is silently never uploaded and the old one stays live."""
    mans = _manifests()
    if not mans:
        pytest.skip("no generated art on disk")
    for m in mans:
        rec = m["masters"]["hero"]
        f = ART_DIR / rec["file"]
        if not f.exists():
            pytest.skip(f"{rec['file']} not present")
        assert hashlib.sha256(f.read_bytes()).hexdigest() == rec["sha256"], (
            f"{m['slug']}: manifest sha256 does not match {rec['file']}")


def test_hero_dimensions_and_size():
    mans = _manifests()
    if not mans:
        pytest.skip("no generated art on disk")
    for m in mans:
        rec = m["masters"]["hero"]
        assert (rec["width"], rec["height"]) == (HERO_W, HERO_H), (
            f"{m['slug']}: {rec['width']}x{rec['height']}, expected {HERO_W}x{HERO_H}")
        assert rec["bytes"] <= MAX_HERO_BYTES, (
            f"{m['slug']}: {rec['bytes'] // 1024} KB exceeds the {MAX_HERO_BYTES // 1024} KB budget")


def test_hero_background_is_pure_white():
    """The iOS plate is #FFFFFF. A 250-254 background is invisible on its own and shows
    as a seam inside the plate in dark mode."""
    mans = _manifests()
    if not mans:
        pytest.skip("no generated art on disk")
    for m in mans:
        bg = m.get("background") or {}
        assert bg, f"{m['slug']}: manifest has no background report — run --recheck"
        assert bg["is_white"], (
            f"{m['slug']}: background min_channel={bg['min_channel']} "
            f"cast={bg['max_cast']}, expected a pure white border ring")


def test_no_subject_runs_off_the_frame_edge():
    """A dark or saturated border pixel means the artwork is cropped by the frame."""
    mans = _manifests()
    if not mans:
        pytest.skip("no generated art on disk")
    offenders = [m["slug"] for m in mans if (m.get("background") or {}).get("art_at_edge")]
    assert not offenders, f"artwork touching the frame edge: {offenders}"


# --------------------------------------------------------------------------
# The bucket invariant — the reason migration 136 cites this file
# --------------------------------------------------------------------------

def test_seeder_targets_the_public_journey_images_bucket():
    import seed_journey

    assert seed_journey.IMAGE_BUCKET == BUCKET
    assert seed_journey.IMAGE_PREFIX == "lessons"
    assert seed_journey.IMAGE_BUCKET != seed_journey.BUCKET, (
        "artwork must not share the narration bucket — migration 128 flips that one private")


def test_journey_images_is_never_signed():
    """The direct guard against the migration-128 trap.

    journey-media is destined to go private because narration is Pro/Max. Artwork is
    free, and locked callers go through redact_journey (which deliberately does NOT
    strip imageUrl), so a signed artwork URL would work for paying users and fail for
    free ones — the exact inverse of the intent.
    """
    from app.services import learn_audio_urls

    assert BUCKET not in learn_audio_urls._SIGNABLE_BUCKETS, (
        f"{BUCKET} must never be signable — see migration 136")


def test_redaction_keeps_image_url_for_locked_users():
    """Artwork is free. If imageUrl ever joins the audio-strip list, every free-tier
    reader loses the art with no error anywhere."""
    from app.services import learn_audio_gate

    assert "imageUrl" not in learn_audio_gate._JOURNEY_CARD_AUDIO_KEYS


def test_manifest_image_urls_point_at_the_public_bucket():
    """Only checks manifests the seeder has already stamped; a fresh set has none."""
    stamped = [m for m in _manifests() if m.get("image_url")]
    if not stamped:
        pytest.skip("nothing seeded yet")
    for m in stamped:
        url = m["image_url"]
        assert url.startswith("https://"), f"{m['slug']}: {url!r}"
        assert PUBLIC_PREFIX in url, f"{m['slug']}: not a public journey-images URL: {url}"
        assert "/object/sign/" not in url, f"{m['slug']}: signed URL: {url}"
        assert "journey-media" not in url, f"{m['slug']}: wrong bucket: {url}"


# --------------------------------------------------------------------------
# iOS side — source scans, brace-bounded so they cannot pass vacuously
# --------------------------------------------------------------------------

def _completion_card_call() -> str:
    """The `.completionCard(` argument list in makeCard, bounded by its own parens."""
    src = STORE_SWIFT.read_text()
    start = src.index(".completionCard(")
    depth, i = 0, src.index("(", start)
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError("unbalanced parens after .completionCard(")


def test_completion_card_never_receives_an_image():
    """The closing card carries no artwork by product decision. Passing an imageName
    there also resurrects the 'Image coming soon' placeholder, because the fallback
    asset names it used have never existed in Assets.xcassets."""
    call = _completion_card_call()
    assert "imageName" not in call, (
        f"completionCard is being given an image again:\n{call}")


def test_no_phantom_journey_asset_fallbacks_remain():
    """`journey_<slug>_title` / `_completion` never resolved — there is not one
    journey_* imageset in the catalog — so these fallbacks only ever produced the
    placeholder. They are also the last place the TITLE-derived slug touched artwork."""
    src = STORE_SWIFT.read_text()
    assert "journey_\\(slug)_completion" not in src
    assert 'journey_\\(slug)_\\(card.type)' not in src


def test_source_scan_helpers_are_not_vacuous():
    """A source scan that finds nothing because it looked in the wrong place is worse
    than no test. Prove the window is real and non-empty before trusting the assertions
    above."""
    call = _completion_card_call()
    assert call.startswith("(") and call.endswith(")")
    assert "title:" in call, f"completionCard window looks wrong:\n{call}"
    assert STORE_SWIFT.exists() and COMPLETION_SWIFT.exists()
