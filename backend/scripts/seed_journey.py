"""
Seed the Investor Journey content into Supabase.

Steps:
  1. Upload each lesson's narration clips (from backend/data/journey_audio/, produced
     by generate_journey_audio.py) to the public 'journey-media' Storage bucket.
  2. Build each lesson's story_content JSON (the iOS LessonStoryContent shape) with the
     public audio URLs baked into the cards.
  3. Upsert one row per lesson into public.lessons (deterministic id per lesson key, so
     re-running updates in place instead of duplicating).

Prerequisites:
  - Migration 061_journey_media_bucket.sql applied (creates the bucket + policies).
  - generate_journey_audio.py already run (clips present in backend/data/journey_audio/).
  - backend/.env with SUPABASE service-role credentials (uses app.database.get_supabase()).

Usage (from backend/):
    ./venv/bin/python scripts/seed_journey.py
    ./venv/bin/python scripts/seed_journey.py --dry-run     # build + print, no writes
"""
import json
import re
import sys
import uuid
from pathlib import Path

# Make `app` importable when run from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import get_supabase  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
# Frontend tree is the source of truth locally; on Railway only backend/ ships,
# so fall back to the vendored copy at backend/data/journey_lessons.json.
_FRONTEND_JSON = REPO / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
JSON_PATH = _FRONTEND_JSON if _FRONTEND_JSON.exists() else BACKEND / "data/journey_lessons.json"
AUDIO_DIR = BACKEND / "data/journey_audio"

BUCKET = "journey-media"
# Lesson artwork lives in its OWN public bucket, never in journey-media. Narration is
# Pro/Max and journey-media is destined to go private (migration 128); artwork is free
# for everyone, so a signed URL would work for paying users and 404 for free ones —
# the exact inverse of the intent. See migration 136 for the full reasoning.
IMAGE_BUCKET = "journey-images"
IMAGE_PREFIX = "lessons"
ART_DIR = BACKEND / "data/journey_art"
# Stable namespace so the same lesson key always maps to the same lessons.id.
NS = uuid.UUID("a1b2c3d4-0000-4000-8000-000000000000")
DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv   # overwrite existing bucket audio (needed to replace the old Gemini clips)

_LEVEL_TOTALS: dict[str, int] = {}
_EXISTING_AUDIO: set[str] = set()   # objects already in journey-media/audio/ — skip re-upload
_EXISTING_IMAGES: set[str] = set()  # objects already in journey-images/lessons/


def lesson_key(cards: list[dict]) -> str:
    """Derive the lesson key from the first card's audioClip ('mr_market_01' -> 'mr_market')."""
    for c in cards:
        clip = c.get("audioClip")
        if clip:
            return re.sub(r"_\d+$", "", clip)
    return ""


def upload_audio(sb, clip: str) -> str | None:
    """Upload <clip>.m4a to journey-media/audio/ (skipping if already there) and return its public URL."""
    path = f"audio/{clip}.m4a"
    local = AUDIO_DIR / f"{clip}.m4a"
    in_bucket = f"{clip}.m4a" in _EXISTING_AUDIO
    if in_bucket and not FORCE:
        # Already in the bucket from a prior run — reuse its URL even if the local clip is absent,
        # so a re-seed from an env without backend/data/journey_audio/ never wipes a good audioUrl.
        return sb.storage.from_(BUCKET).get_public_url(path)
    if not local.exists():
        if in_bucket:
            return sb.storage.from_(BUCKET).get_public_url(path)  # --force but no local clip: keep existing
        print(f"    ! missing audio {local.name} — skipping (audioUrl will be null)")
        return None
    if not DRY:
        sb.storage.from_(BUCKET).upload(
            path,
            local.read_bytes(),
            {"content-type": "audio/mp4", "upsert": "true"},
        )
        _EXISTING_AUDIO.add(f"{clip}.m4a")
        print(f"    + uploaded {clip}.m4a")
    return sb.storage.from_(BUCKET).get_public_url(path)


def upload_image(sb, slug: str) -> str | None:
    """Upload a lesson's hero art to journey-images/lessons/ and return its public URL.

    Mirrors upload_audio's never-wipe-a-good-URL behaviour, with one deliberate
    difference: it skips on the content SHA256 recorded in the manifest, not on the
    filename. Artwork gets iterated on — the same <slug>.hero.jpg is regenerated
    repeatedly — so name-based skipping would silently upload nothing after a re-roll
    and leave the old picture live. seed_book_covers.py deviates the same way and for
    the same reason.

    Every failure path returns None or a pre-existing URL and never raises: during
    rollout most lessons legitimately have no art yet, and one lesson's Storage error
    must not abort the other twenty-six.
    """
    man_path = ART_DIR / f"{slug}.manifest.json"
    if not man_path.exists():
        return None                                    # no art yet — the normal case

    try:
        man = json.loads(man_path.read_text())
    except Exception as exc:                           # noqa: BLE001
        print(f"    ! unreadable manifest {man_path.name}: {type(exc).__name__}: {exc}")
        return None

    rec = (man.get("masters") or {}).get("hero") or {}
    name = rec.get("file")
    if not name:
        print(f"    ! {slug}: manifest has no masters.hero — run generate_journey_art.py")
        return None

    path = f"{IMAGE_PREFIX}/{name}"
    # `or None` guards the empty string: iOS checks `!name.isEmpty` but an empty imageUrl
    # would still reserve the slot and render the placeholder rather than nothing.
    public = (sb.storage.from_(IMAGE_BUCKET).get_public_url(path).rstrip("?") or None)
    local = ART_DIR / name
    in_bucket = name in _EXISTING_IMAGES

    if not local.exists():
        # Seeding from a box without backend/data/journey_art/ must not wipe a live URL.
        return public if in_bucket else None
    if man.get("uploaded", {}).get("hero") == rec.get("sha256") and in_bucket and not FORCE:
        return public                                  # byte-identical to what is live

    if not DRY:
        try:
            sb.storage.from_(IMAGE_BUCKET).upload(
                path,
                local.read_bytes(),
                # cache-control 300 while the art is still being iterated: the filename is
                # stable across re-rolls, so a long TTL pins a superseded image on device.
                {"content-type": "image/jpeg", "upsert": "true", "cache-control": "300"},
            )
        except Exception as exc:                       # noqa: BLE001
            print(f"    ! image upload failed for {slug}: {type(exc).__name__}: {exc}")
            return public if in_bucket else None
        _EXISTING_IMAGES.add(name)
        man.setdefault("uploaded", {})["hero"] = rec.get("sha256")
        man["image_url"] = public
        man_path.write_text(json.dumps(man, indent=2) + "\n")
        print(f"    + uploaded {name}")
    return public


def build_story_content(sb, lesson: dict, key: str) -> dict:
    title = lesson["title"]
    level = lesson["level"]
    sort_order = lesson["sortOrder"]
    # One lookup per lesson, not per card: the hero belongs to the title card only.
    image_url = upload_image(sb, key)
    cards_out = []
    for card in lesson["cards"]:
        clip = card.get("audioClip")
        audio_url = upload_audio(sb, clip) if clip else None
        # Hoisted out of the dict literal on purpose — test_journey_schema_parity slices
        # that literal and regexes it for "word": pairs to pin the card contract, so an
        # inline expression containing a colon would corrupt what it reads.
        is_title = card["type"] == "title"
        cards_out.append({
            "type": card["type"],
            "headline": card.get("headline"),
            "text": card.get("text"),          # keeps **highlight** markup for iOS
            "audioUrl": audio_url,
            "imageUrl": image_url if is_title else None,
            "videoUrl": None,
            "cta": card.get("cta"),
            # Per-word read-along timings (from align_journey_audio.py); null until aligned.
            "readAlongWords": card.get("readAlongWords"),
        })
    return {
        "lessonLabel": f"LESSON {sort_order}: {title.upper()}",
        "lessonNumber": sort_order,
        "totalLessonsInLevel": _LEVEL_TOTALS[level],
        "estimatedMinutes": lesson.get("estimatedMinutes"),
        "cards": cards_out,
    }


def main():
    data = json.loads(JSON_PATH.read_text())
    lessons = data["lessons"]
    for l in lessons:
        _LEVEL_TOTALS[l["level"]] = _LEVEL_TOTALS.get(l["level"], 0) + 1

    sb = get_supabase()

    # Learn which audio objects already exist so we only upload new ones.
    try:
        listed = sb.storage.from_(BUCKET).list("audio", {"limit": 2000})
        _EXISTING_AUDIO.update(item["name"] for item in listed)
        print(f"{len(_EXISTING_AUDIO)} audio file(s) already in Storage — will skip those.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not list existing audio: {exc})\n")

    # Same for artwork. Its own try/except: a missing journey-images bucket (migration
    # 136 not applied yet) must print a note and let the audio-only seed proceed, not
    # abort the whole run.
    try:
        listed = sb.storage.from_(IMAGE_BUCKET).list(IMAGE_PREFIX, {"limit": 200})
        _EXISTING_IMAGES.update(item["name"] for item in listed)
        print(f"{len(_EXISTING_IMAGES)} lesson image(s) already in Storage — "
              f"will skip byte-identical ones.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not list {IMAGE_BUCKET} — has migration 136 been applied? {exc})\n")

    rows = []
    for lesson in lessons:
        # Prefer the AUTHORED slug. The row id is `uuid5(NS, key)`, and deriving that key from
        # the first card's `audioClip` tied a lesson's database identity to an audio filename:
        # renaming a clip (or reordering cards so a different one is first) minted a NEW id, so
        # the upsert INSERTED a second row instead of updating. `lessons` has no UNIQUE on
        # title, so both survive, `GET /learn/journey` returns 28 lessons with two "Mr. Market",
        # and — since `_fetch_rows` issues no ORDER BY — which one the reader gets is decided by
        # PostgREST's physical row order. A lesson silently reverts to its old content.
        #
        # Every authored slug is set to today's derived key, so existing ids are preserved
        # exactly and this migrates with zero orphans.
        key = (lesson.get("slug") or "").strip() or lesson_key(lesson["cards"])
        print(f"[{lesson['level']}/{lesson['sortOrder']}] {lesson['title']} ({key})")
        # `key` is the authored slug — the same value the row id is derived from, and the
        # same one the artwork is filed under, so the Storage path and the database
        # identity are guaranteed to agree.
        story = build_story_content(sb, lesson, key)
        rows.append({
            "id": str(uuid.uuid5(NS, key)),
            "title": lesson["title"],
            "description": lesson.get("description"),
            "level": lesson["level"],
            "duration_minutes": lesson.get("estimatedMinutes"),
            "category": lesson.get("category", "standard"),
            "sort_order": lesson["sortOrder"],
            "story_content": story,
        })

    if DRY:
        print(f"\n[dry-run] built {len(rows)} lesson rows; no writes performed.")
        print(json.dumps(rows[0]["story_content"], indent=2)[:800])
        return

    # Refuse to half-publish. Two lessons sharing a key (a blank slug AND a blank audioClip,
    # or a copy-pasted slug) collapse to one id; Postgres then aborts the whole upsert with
    # SQLSTATE 21000 ("ON CONFLICT DO UPDATE command cannot affect row a second time"), which
    # says nothing about WHICH lessons collided. Name them instead.
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        seen: dict[str, str] = {}
        collisions = []
        for row in rows:
            if row["id"] in seen:
                collisions.append(f"{seen[row['id']]!r} + {row['title']!r}")
            seen[row["id"]] = row["title"]
        raise SystemExit(
            "seed_journey: lessons share a derived id — give each a distinct `slug` in "
            f"journey_lessons.json. Collisions: {'; '.join(collisions)}"
        )

    sb.table("lessons").upsert(rows, on_conflict="id").execute()
    print(f"\nUpserted {len(rows)} lessons into public.lessons.")


if __name__ == "__main__":
    main()
